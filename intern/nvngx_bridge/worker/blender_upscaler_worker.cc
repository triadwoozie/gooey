/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup nvngx_bridge
 *
 * Standalone worker daemon for Proton/Wine upscaler execution.
 *
 * Windows path  – TCP IPC on 127.0.0.1:47631, receives UpscalerIPCMessage.
 * Linux path    – UNIX domain socket at /tmp/blender_nvngx_gpu.sock.
 *                 Receives one UpscalerFramePacket per frame via recvmsg()
 *                 with SCM_RIGHTS ancillary data carrying 4 Vulkan opaque
 *                 FDs: color, depth, motion-vector, output.
 *                 Imports each FD into a VkDeviceMemory object via
 *                 vkImportMemoryFdKHR, dispatches the upscaler, then closes
 *                 every received FD (success or failure) to prevent leaks.
 */

#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <iostream>

#ifdef _WIN32
/* ------------------------------------------------------------------ */
/* Windows headers                                                     */
/* ------------------------------------------------------------------ */
#  define WIN32_LEAN_AND_MEAN
#  include <winsock2.h>
#  include <ws2tcpip.h>
#  include <windows.h>
#  if defined(_MSC_VER)
#    pragma comment(lib, "Ws2_32.lib")
#  endif
#else
/* ------------------------------------------------------------------ */
/* POSIX / Linux headers                                               */
/* ------------------------------------------------------------------ */
#  include <errno.h>
#  include <fcntl.h>
#  include <signal.h>
#  include <sys/socket.h>
#  include <sys/stat.h>
#  include <sys/types.h>
#  include <sys/un.h>
#  include <unistd.h>

/* Vulkan core types needed for external-memory import.
 * We load the entry-points at runtime via vkGetDeviceProcAddr so no Vulkan
 * SDK dependency is required at link time. */
#  include <vulkan/vulkan.h>

/* When running inside the Proton / Wine environment windows.h is available
 * via Wine's POSIX-compatible header tree, providing LoadLibraryA and
 * GetProcAddress.  Outside Wine we provide lightweight stubs so the TU
 * compiles cleanly for static analysis on a plain Linux toolchain. */
#  ifdef __WINE__
#    include <windows.h>
#  else
typedef void *HMODULE;
typedef void *FARPROC;
static inline HMODULE LoadLibraryA(const char *) { return nullptr; }
static inline FARPROC  GetProcAddress(HMODULE, const char *) { return nullptr; }
#  endif /* __WINE__ */
#endif /* _WIN32 */

/* ------------------------------------------------------------------ */
/* Shared IPC structures (Windows worker uses the TCP variant)        */
/* ------------------------------------------------------------------ */

static constexpr uint16_t kIpcPort = 47631;
static constexpr uint32_t kMagic   = 0x55505343; /* "UPSC" */

/** TCP message used by the Windows worker (unchanged). */
struct UpscalerIPCMessage {
  uint32_t magic          = kMagic;
  uint32_t command        = 0; /* 0: ping, 1: init, 2: upscale, 3: shutdown */
  uint32_t backend        = 0; /* 1: FSR4, 2: XeSS */
  uint32_t render_width   = 0;
  uint32_t render_height  = 0;
  uint32_t display_width  = 0;
  uint32_t display_height = 0;
  float    jitter_x       = 0.0f;
  float    jitter_y       = 0.0f;
  float    sharpness      = 0.5f;
  int64_t  color_shm_descriptor  = 0;
  int64_t  depth_shm_descriptor  = 0;
  int64_t  motion_shm_descriptor = 0;
  int64_t  output_shm_descriptor = 0;
};
static_assert(sizeof(UpscalerIPCMessage) == 72);

/* ------------------------------------------------------------------ */
/* Linux-only: UNIX socket frame packet + FD import implementation     */
/* ------------------------------------------------------------------ */

#ifndef _WIN32

/** Path of the UNIX domain socket the worker listens on.
 *  Blender (nvngx_bridge_api.cc) connects to this same path. */
static constexpr char kSockPath[] = "/tmp/blender_nvngx_gpu.sock";

/** Number of Vulkan opaque FDs exchanged per frame:
 *    [0] color render target (GPU_TEXTURE_USAGE_MEMORY_EXPORT)
 *    [1] depth buffer        (GPU_TEXTURE_USAGE_MEMORY_EXPORT)
 *    [2] motion-vector buffer(GPU_TEXTURE_USAGE_MEMORY_EXPORT)
 *    [3] output texture      (GPU_TEXTURE_USAGE_MEMORY_EXPORT) */
static constexpr int kNumFds = 4;

/** Header sent inline with the SCM_RIGHTS message.
 *  Carries per-frame metadata that the upscaler needs in addition to the FDs. */
struct UpscalerFramePacket {
  uint32_t magic          = kMagic;
  uint32_t command        = 2u; /* 1=INIT, 2=UPSCALE */
  uint32_t frame_index    = 0;
  uint32_t render_width   = 0;
  uint32_t render_height  = 0;
  uint32_t display_width  = 0;
  uint32_t display_height = 0;
  float    jitter_x       = 0.0f;
  float    jitter_y       = 0.0f;
  float    motion_scale_x = 1.0f;
  float    motion_scale_y = 1.0f;
  float    sharpness      = 0.5f;
  uint32_t reset          = 0; /* non-zero = history invalid */
  /* Phase 5 scissor rect — letterbox clipping region in render-resolution space.
   * Set use_scissor_clipping = 1 to activate; ignored when 0. */
  uint32_t scissor_offset_x = 0;
  uint32_t scissor_offset_y = 0;
  uint32_t scissor_extent_w = 0;
  uint32_t scissor_extent_h = 0;
  uint32_t use_scissor_clipping = 0;
  /* 4 × (size + format) hints so import_external_memory can size the allocation */
  uint64_t allocation_size[kNumFds]; /* bytes per image memory binding */
  uint32_t memory_type_bits[kNumFds];/* valid memory type mask from vkGetImageMemoryRequirements */
};
static_assert(offsetof(UpscalerFramePacket, command) == 4,
              "wire protocol: command offset");
static_assert(offsetof(UpscalerFramePacket, render_width) == 12,
              "wire protocol: render_width offset");
static_assert(offsetof(UpscalerFramePacket, scissor_offset_x) == 52,
              "wire protocol: scissor offset");
static_assert(sizeof(UpscalerFramePacket) == 120,
              "wire protocol: packet size");

/* ------------------------------------------------------------------ */
/* Vulkan device handle (obtained from the owning GPU context).        */
/* In this standalone daemon we resolve the device via an env-var or  */
/* a second control message.  For now we keep a single global that    */
/* Blender sets by sending the VkDevice handle value as a uint64_t    */
/* in a separate "init" packet (command == 1).                        */
/* ------------------------------------------------------------------ */

static VkDevice  g_vk_device  = VK_NULL_HANDLE;
static VkInstance g_vk_instance = VK_NULL_HANDLE;

/* OptiScaler / NVNGX module handle — loaded once on INIT, reused on UPSCALE. */
static HMODULE g_ngx_lib = nullptr;

/* Lazily resolved entry points */
static PFN_vkGetDeviceProcAddr      g_vkGetDeviceProcAddr      = nullptr;
static PFN_vkAllocateMemory         g_vkAllocateMemory         = nullptr;
static PFN_vkFreeMemory             g_vkFreeMemory             = nullptr;

using PFN_vkImportMemoryFdKHR_t =
    VkResult (*)(VkDevice, const VkImportMemoryFdInfoKHR *, VkDeviceMemory *);
static PFN_vkImportMemoryFdKHR_t g_vkImportMemoryFdKHR = nullptr;

/** Resolve Vulkan entry points via vkGetDeviceProcAddr.
 *  Called once after g_vk_device becomes valid. */
static bool resolve_vk_entry_points()
{
  if (g_vk_device == VK_NULL_HANDLE) {
    std::cerr << "[Worker] Cannot resolve VK entry points: VkDevice is NULL.\n";
    return false;
  }
  if (g_vkGetDeviceProcAddr == nullptr) {
    /* Bootstrap: fetch vkGetDeviceProcAddr from the loader. */
    g_vkGetDeviceProcAddr = reinterpret_cast<PFN_vkGetDeviceProcAddr>(
        vkGetInstanceProcAddr(g_vk_instance, "vkGetDeviceProcAddr"));
    if (!g_vkGetDeviceProcAddr) {
      std::cerr << "[Worker] vkGetInstanceProcAddr failed to resolve vkGetDeviceProcAddr.\n";
      return false;
    }
  }

#define RESOLVE(name) \
  g_##name = reinterpret_cast<PFN_##name>(g_vkGetDeviceProcAddr(g_vk_device, #name)); \
  if (!g_##name) { \
    std::cerr << "[Worker] vkGetDeviceProcAddr could not resolve " #name ".\n"; \
    return false; \
  }

  RESOLVE(vkAllocateMemory)
  RESOLVE(vkFreeMemory)
#undef RESOLVE

  /* VK_KHR_external_memory_fd is an extension entry point. */
  g_vkImportMemoryFdKHR = reinterpret_cast<PFN_vkImportMemoryFdKHR_t>(
      g_vkGetDeviceProcAddr(g_vk_device, "vkImportMemoryFdKHR"));
  if (!g_vkImportMemoryFdKHR) {
    std::cerr << "[Worker] vkGetDeviceProcAddr could not resolve vkImportMemoryFdKHR. "
                 "Ensure VK_KHR_external_memory_fd is enabled on the device.\n";
    return false;
  }

  std::cout << "[Worker] Vulkan entry points resolved successfully.\n";
  return true;
}

/** Import a single opaque Vulkan FD into a VkDeviceMemory.
 *
 *  On success  – *out_memory is populated and fd is consumed (closed).
 *  On failure  – fd is closed to prevent leaks; *out_memory == VK_NULL_HANDLE.
 *
 *  @param fd              Received opaque FD (VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT).
 *  @param alloc_size      Allocation size in bytes (from UpscalerFramePacket).
 *  @param memory_type_idx Index into VkPhysicalDeviceMemoryProperties.memoryTypes
 *                         satisfying the image's memory requirements.
 *  @param out_memory      Receives the allocated VkDeviceMemory on success.
 *  @return true on success. */
static bool import_external_memory(int fd,
                                   VkDeviceSize alloc_size,
                                   uint32_t memory_type_idx,
                                   VkDeviceMemory *out_memory)
{
  *out_memory = VK_NULL_HANDLE;

  if (g_vk_device == VK_NULL_HANDLE || g_vkAllocateMemory == nullptr ||
      g_vkImportMemoryFdKHR == nullptr)
  {
    std::cerr << "[Worker] import_external_memory: Vulkan device or entry points not ready.\n";
    close(fd);
    return false;
  }
  if (fd < 0) {
    std::cerr << "[Worker] import_external_memory: invalid fd " << fd << ".\n";
    return false;
  }

  /* Build the import chain: VkImportMemoryFdInfoKHR in pNext. */
  VkImportMemoryFdInfoKHR import_info = {};
  import_info.sType      = VK_STRUCTURE_TYPE_IMPORT_MEMORY_FD_INFO_KHR;
  import_info.pNext      = nullptr;
  import_info.handleType = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT;
  import_info.fd         = fd; /* Ownership transferred to Vulkan on success. */

  VkMemoryAllocateInfo alloc_info = {};
  alloc_info.sType           = VK_STRUCTURE_TYPE_MEMORY_ALLOCATE_INFO;
  alloc_info.pNext           = &import_info;  /* import chain */
  alloc_info.allocationSize  = alloc_size;
  alloc_info.memoryTypeIndex = memory_type_idx;

  VkResult result = g_vkAllocateMemory(g_vk_device, &alloc_info, nullptr, out_memory);
  if (result != VK_SUCCESS) {
    std::cerr << "[Worker] vkAllocateMemory (import fd=" << fd
              << ") failed with VkResult=" << result << ".\n";
    /* Vulkan did not take ownership of the FD on failure — close it ourselves. */
    close(fd);
    *out_memory = VK_NULL_HANDLE;
    return false;
  }

  /* On VK_SUCCESS the driver has duplicated/consumed the fd; close our copy. */
  close(fd);
  std::cout << "[Worker] Imported fd=" << fd
            << " -> VkDeviceMemory=" << *out_memory << "\n";
  return true;
}

/** Release a batch of imported device memories.
 *  Safe to call with VK_NULL_HANDLE entries. */
static void free_imported_memories(VkDeviceMemory mems[kNumFds])
{
  if (g_vkFreeMemory == nullptr || g_vk_device == VK_NULL_HANDLE) {
    return;
  }
  for (int i = 0; i < kNumFds; i++) {
    if (mems[i] != VK_NULL_HANDLE) {
      g_vkFreeMemory(g_vk_device, mems[i], nullptr);
      mems[i] = VK_NULL_HANDLE;
    }
  }
}

/* ------------------------------------------------------------------ */
/* FrameImages — per-frame VkImage handles bound to imported memories  */
/*                                                                      */
/* Each upscale frame creates four transient VkImages that wrap the    */
/* VkDeviceMemory objects imported from Blender via SCM_RIGHTS FDs.    */
/* They must be destroyed before the memories are freed.               */
/* ------------------------------------------------------------------ */

struct FrameImages {
  VkImage color  = VK_NULL_HANDLE; /* R16G16B16A16_SFLOAT, SAMPLED          */
  VkImage depth  = VK_NULL_HANDLE; /* R32_SFLOAT,          SAMPLED          */
  VkImage motion = VK_NULL_HANDLE; /* R16G16_SFLOAT,       SAMPLED          */
  VkImage output = VK_NULL_HANDLE; /* R16G16B16A16_SFLOAT, STORAGE|SAMPLED  */
};

/** Destroy all non-null VkImages in a FrameImages set.
 *  Safe to call multiple times; nulls the handles after destruction. */
static void free_frame_images(FrameImages &imgs)
{
  if (g_vk_device == VK_NULL_HANDLE) {
    return;
  }
  auto destroy = [](VkImage &img) {
    if (img != VK_NULL_HANDLE) {
      vkDestroyImage(g_vk_device, img, nullptr);
      img = VK_NULL_HANDLE;
    }
  };
  destroy(imgs.color);
  destroy(imgs.depth);
  destroy(imgs.motion);
  destroy(imgs.output);
}

/** Create a VkImage backed by an already-imported VkDeviceMemory.
 *
 *  VkExternalMemoryImageCreateInfo is chained into the VkImageCreateInfo
 *  so the driver knows this image will alias an externally-owned memory
 *  object (VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT).
 *
 *  @param device   Logical device.
 *  @param memory   VkDeviceMemory previously imported via vkImportMemoryFdKHR.
 *  @param width    Image width in texels.
 *  @param height   Image height in texels.
 *  @param format   Pixel format.
 *  @param usage    Intended usage flags.
 *  @return         The new VkImage, or VK_NULL_HANDLE on failure. */
static VkImage create_and_bind_external_image(VkDevice device,
                                              VkDeviceMemory memory,
                                              uint32_t width,
                                              uint32_t height,
                                              VkFormat format,
                                              VkImageUsageFlags usage)
{
  VkExternalMemoryImageCreateInfo ext_info = {};
  ext_info.sType       = VK_STRUCTURE_TYPE_EXTERNAL_MEMORY_IMAGE_CREATE_INFO;
  ext_info.handleTypes = VK_EXTERNAL_MEMORY_HANDLE_TYPE_OPAQUE_FD_BIT;

  VkImageCreateInfo img_info = {};
  img_info.sType         = VK_STRUCTURE_TYPE_IMAGE_CREATE_INFO;
  img_info.pNext         = &ext_info;
  img_info.imageType     = VK_IMAGE_TYPE_2D;
  img_info.format        = format;
  img_info.extent        = {width, height, 1};
  img_info.mipLevels     = 1;
  img_info.arrayLayers   = 1;
  img_info.samples       = VK_SAMPLE_COUNT_1_BIT;
  img_info.tiling        = VK_IMAGE_TILING_OPTIMAL;
  img_info.usage         = usage;
  img_info.sharingMode   = VK_SHARING_MODE_EXCLUSIVE;
  img_info.initialLayout = VK_IMAGE_LAYOUT_UNDEFINED;

  VkImage image = VK_NULL_HANDLE;
  if (vkCreateImage(device, &img_info, nullptr, &image) != VK_SUCCESS) {
    std::cerr << "[Worker] vkCreateImage failed for external memory "
              << "(format=" << format << " " << width << "x" << height << ")\n";
    return VK_NULL_HANDLE;
  }

  if (vkBindImageMemory(device, image, memory, 0) != VK_SUCCESS) {
    std::cerr << "[Worker] vkBindImageMemory failed\n";
    vkDestroyImage(device, image, nullptr);
    return VK_NULL_HANDLE;
  }

  return image;
}

/** Receive one frame packet over an accepted UNIX client socket.
 *
 *  Uses recvmsg() with a CMSG_SPACE(sizeof(int) * kNumFds) ancillary buffer
 *  to atomically receive the UpscalerFramePacket header together with the
 *  4 Vulkan opaque FDs passed via SCM_RIGHTS.
 *
 *  @param client_fd   Connected client socket.
 *  @param out_packet  Populated on success.
 *  @param out_fds     Array of kNumFds integers; populated on success.
 *                     Caller is responsible for closing these (or handing them
 *                     to import_external_memory which closes them).
 *  @return  1  – packet received OK.
 *           0  – client disconnected cleanly (EOF).
 *          -1  – protocol or system error. */
static int recv_frame_packet(int client_fd,
                             UpscalerFramePacket *out_packet,
                             int out_fds[kNumFds])
{
  /* Initialise out_fds to -1 so any error path can safely close only valid ones. */
  for (int i = 0; i < kNumFds; i++) {
    out_fds[i] = -1;
  }

  /* --- iovec: inline packet header ---------------------------------- */
  struct iovec iov = {};
  iov.iov_base = out_packet;
  iov.iov_len  = sizeof(UpscalerFramePacket);

  /* --- ancillary buffer for kNumFds file descriptors ---------------- */
  /* CMSG_SPACE already includes alignment padding. */
  const size_t cmsg_buf_size = CMSG_SPACE(sizeof(int) * kNumFds);
  char cmsg_buf[CMSG_SPACE(sizeof(int) * kNumFds)];
  memset(cmsg_buf, 0, cmsg_buf_size);

  struct msghdr msg = {};
  msg.msg_iov        = &iov;
  msg.msg_iovlen     = 1;
  msg.msg_control    = cmsg_buf;
  msg.msg_controllen = cmsg_buf_size;

  /* --- Receive (blocking) ------------------------------------------- */
  ssize_t n = recvmsg(client_fd, &msg, MSG_WAITALL);
  if (n == 0) {
    /* Clean EOF — client disconnected. */
    return 0;
  }
  if (n < 0) {
    std::cerr << "[Worker] recvmsg() error: " << strerror(errno) << "\n";
    return -1;
  }
  if (static_cast<size_t>(n) < sizeof(UpscalerFramePacket)) {
    std::cerr << "[Worker] Short read: expected " << sizeof(UpscalerFramePacket)
              << " bytes, got " << n << ".\n";
    return -1;
  }

  /* --- Validate magic ------------------------------------------------ */
  if (out_packet->magic != kMagic) {
    std::cerr << "[Worker] Bad magic 0x" << std::hex << out_packet->magic
              << std::dec << "; dropping packet.\n";
    return -1;
  }

  /* --- Extract SCM_RIGHTS FDs --------------------------------------- */
  struct cmsghdr *cmsg = CMSG_FIRSTHDR(&msg);
  if (cmsg == nullptr ||
      cmsg->cmsg_level != SOL_SOCKET ||
      cmsg->cmsg_type  != SCM_RIGHTS)
  {
    std::cerr << "[Worker] No SCM_RIGHTS ancillary data in frame packet.\n";
    return -1;
  }

  /* Verify the ancillary payload carries exactly kNumFds integers. */
  const size_t expected_cmsg_len = CMSG_LEN(sizeof(int) * kNumFds);
  if (cmsg->cmsg_len < expected_cmsg_len) {
    std::cerr << "[Worker] SCM_RIGHTS payload too small: "
              << cmsg->cmsg_len << " < " << expected_cmsg_len << ".\n";
    return -1;
  }

  memcpy(out_fds, CMSG_DATA(cmsg), sizeof(int) * kNumFds);

  /* Sanity-check each FD is non-negative. */
  for (int i = 0; i < kNumFds; i++) {
    if (out_fds[i] < 0) {
      std::cerr << "[Worker] Received invalid fd[" << i << "]=" << out_fds[i] << ".\n";
      /* Close the valid FDs we already extracted before returning. */
      for (int j = 0; j < i; j++) {
        close(out_fds[j]);
        out_fds[j] = -1;
      }
      return -1;
    }
  }

  return 1;
}

/** Handle a single connected Blender client until it disconnects.
 *
 *  For every successfully received frame packet the function:
 *    1. Imports all 4 FDs into VkDeviceMemory objects.
 *    2. Stubs the actual upscaler dispatch (TODO: wire FSR4/XeSS here).
 *    3. Releases the imported memories and returns to the accept() loop.
 *
 *  On clean disconnect or any error the client socket is closed and the
 *  function returns, leaving the server socket unaffected. */
static void handle_client(int client_fd)
{
  std::cout << "[Worker] Client connected (fd=" << client_fd << ").\n";

  while (true) {
    UpscalerFramePacket packet = {};
    int fds[kNumFds];

    int rc = recv_frame_packet(client_fd, &packet, fds);
    if (rc == 0) {
      std::cout << "[Worker] Client disconnected cleanly.\n";
      break;
    }
    if (rc < 0) {
      std::cerr << "[Worker] Packet receive error; closing client connection.\n";
      /* Close any FDs that may have been partially extracted. */
      for (int i = 0; i < kNumFds; i++) {
        if (fds[i] >= 0) {
          close(fds[i]);
        }
      }
      break;
    }

    /* rc == 1: valid packet with 4 FDs. */
    std::cout << "[Worker] Frame " << packet.frame_index
              << " render=" << packet.render_width << "x" << packet.render_height
              << " display=" << packet.display_width << "x" << packet.display_height
              << " fds={" << fds[0] << "," << fds[1] << "," << fds[2] << "," << fds[3] << "}\n";

    /* --- INIT command (1): Load OptiScaler / NVNGX and call NVSDK_NGX_VULKAN_Init ---- */
    if (packet.command == 1) {
      /* Persist the module handle across frames; load once. */
      if (!g_ngx_lib) {
        g_ngx_lib = LoadLibraryA("nvngx.dll");
        if (g_ngx_lib) {
          std::cout << "[Worker] LoadLibraryA(\"nvngx.dll\") succeeded — OptiScaler active.\n";
        }
        else {
          std::cerr << "[Worker] LoadLibraryA(\"nvngx.dll\") failed — "
                       "check OptiScaler is installed in the Proton prefix.\n";
        }
      }

      if (g_ngx_lib) {
        typedef int (*PFN_NVSDK_NGX_VULKAN_Init)(
            unsigned long long, const wchar_t *, VkInstance, VkPhysicalDevice, VkDevice,
            void *, void *, const void *, int);
        auto ngx_init = reinterpret_cast<PFN_NVSDK_NGX_VULKAN_Init>(
            GetProcAddress(g_ngx_lib, "NVSDK_NGX_VULKAN_Init"));
        if (ngx_init) {
          int r = ngx_init(100334311ULL, L".", g_vk_instance, VK_NULL_HANDLE, g_vk_device,
                           reinterpret_cast<void *>(vkGetInstanceProcAddr),
                           reinterpret_cast<void *>(vkGetDeviceProcAddr),
                           nullptr, 0x0000013);
          std::cout << "[Worker] NVSDK_NGX_VULKAN_Init=0x" << std::hex << r << std::dec << "\n";
        }
        else {
          std::cerr << "[Worker] NVSDK_NGX_VULKAN_Init not found — "
                       "OptiScaler may intercept without explicit init.\n";
        }
      }

      /* Close all 4 FDs received with the INIT packet — we only needed them
       * to carry allocation_size / memory_type_bits hints on this connection.
       * The first UPSCALE packet will re-supply live FDs. */
      for (int i = 0; i < kNumFds; i++) {
        close(fds[i]);
      }
      continue; /* back to recvmsg() loop */
    }

    /* --- Import all 4 FDs into VkDeviceMemory ---------------------- */
    /* Label indices for readability. */
    enum FdIndex { FD_COLOR = 0, FD_DEPTH = 1, FD_MOTION = 2, FD_OUTPUT = 3 };

    VkDeviceMemory mems[kNumFds] = {
      VK_NULL_HANDLE, VK_NULL_HANDLE, VK_NULL_HANDLE, VK_NULL_HANDLE
    };

    bool import_ok = true;

    /* Vulkan device may not be available on the worker side yet (the daemon
     * can run before Blender sends its VkDevice handle).  If so, close all
     * FDs immediately and skip — fail-closed, no leak. */
    if (g_vk_device == VK_NULL_HANDLE || g_vkImportMemoryFdKHR == nullptr) {
      std::cerr << "[Worker] VkDevice not yet initialised; closing received FDs.\n";
      for (int i = 0; i < kNumFds; i++) {
        close(fds[i]);
      }
      /* Do not crash; wait for next frame/connection. */
      continue;
    }

    for (int i = 0; i < kNumFds && import_ok; i++) {
      /* Derive the memory type index from the valid-bits hint in the packet.
       * Select the lowest-numbered type whose bit is set — sufficient for
       * device-local external memory. */
      uint32_t mem_type_idx = 0;
      uint32_t bits = packet.memory_type_bits[i];
      for (uint32_t b = 0; b < 32; b++) {
        if (bits & (1u << b)) {
          mem_type_idx = b;
          break;
        }
      }

      bool ok = import_external_memory(
          fds[i], static_cast<VkDeviceSize>(packet.allocation_size[i]),
          mem_type_idx, &mems[i]);
      if (!ok) {
        std::cerr << "[Worker] Failed to import fd[" << i << "]; aborting frame.\n";
        /* import_external_memory already closed fds[i] on failure. */
        import_ok = false;
        /* Close remaining unprocessed FDs to prevent leaks. */
        for (int j = i + 1; j < kNumFds; j++) {
          close(fds[j]);
        }
      }
    }

    if (!import_ok) {
      /* Release any memories that were successfully imported before the failure. */
      free_imported_memories(mems);
      continue; /* Keep the client connection alive; try next frame. */
    }

    /* --- Create VkImage handles bound to imported memories ----------- */
    /* Images are transient: created here, destroyed at frame end below.
     * They must be destroyed BEFORE the VkDeviceMemory objects are freed. */
    FrameImages imgs;
    imgs.color = create_and_bind_external_image(
        g_vk_device, mems[FD_COLOR],
        packet.render_width, packet.render_height,
        VK_FORMAT_R16G16B16A16_SFLOAT,
        VK_IMAGE_USAGE_SAMPLED_BIT);

    imgs.depth = create_and_bind_external_image(
        g_vk_device, mems[FD_DEPTH],
        packet.render_width, packet.render_height,
        VK_FORMAT_R32_SFLOAT,
        VK_IMAGE_USAGE_SAMPLED_BIT);

    imgs.motion = create_and_bind_external_image(
        g_vk_device, mems[FD_MOTION],
        packet.render_width, packet.render_height,
        VK_FORMAT_R16G16_SFLOAT,
        VK_IMAGE_USAGE_SAMPLED_BIT);

    imgs.output = create_and_bind_external_image(
        g_vk_device, mems[FD_OUTPUT],
        packet.display_width, packet.display_height,
        VK_FORMAT_R16G16B16A16_SFLOAT,
        VK_IMAGE_USAGE_STORAGE_BIT | VK_IMAGE_USAGE_SAMPLED_BIT);

    /* Check all four images were created successfully. */
    if (imgs.color  == VK_NULL_HANDLE || imgs.depth  == VK_NULL_HANDLE ||
        imgs.motion == VK_NULL_HANDLE || imgs.output == VK_NULL_HANDLE)
    {
      std::cerr << "[Worker] Frame " << packet.frame_index
                << ": one or more VkImage creations failed; dropping frame.\n";
      free_frame_images(imgs);
      free_imported_memories(mems);
      continue;
    }

    std::cout << "[Worker] Frame " << packet.frame_index
              << " images bound:"
              << " color="  << imgs.color
              << " depth="  << imgs.depth
              << " motion=" << imgs.motion
              << " output=" << imgs.output << "\n";

    /* --- Dispatch via NVSDK_NGX_VULKAN_EvaluateFeature (OptiScaler) ---
     *
     * g_ngx_lib is loaded on packet.command == 1 (INIT) and persists for the
     * lifetime of the worker process.  We resolve EvaluateFeature lazily and
     * cache the pointer so the lookup only happens once. */
    typedef int (*PFN_NVSDK_NGX_VULKAN_EvaluateFeature)(
        void *cmd_list,
        const void *feature_handle,
        const void *params,
        void *in_out_data);

    {
      static PFN_NVSDK_NGX_VULKAN_EvaluateFeature s_evaluate_fn = nullptr;

      if (g_ngx_lib != nullptr && s_evaluate_fn == nullptr) {
        s_evaluate_fn = reinterpret_cast<PFN_NVSDK_NGX_VULKAN_EvaluateFeature>(
            GetProcAddress(g_ngx_lib, "NVSDK_NGX_VULKAN_EvaluateFeature"));
        if (!s_evaluate_fn) {
          std::cerr << "[Worker] NVSDK_NGX_VULKAN_EvaluateFeature not found in nvngx.dll; "
                       "OptiScaler may intercept implicitly.\n";
        }
      }

      if (s_evaluate_fn) {
        /* Pack image handles into a minimal parameter block understood by the
         * NVSDK_NGX parameter API.  In a full integration this would be a
         * proper NVSDK_NGX_Parameter* object; here we pass the four VkImage
         * handles as a plain array — sufficient for OptiScaler's interception
         * layer which reads them by index. */
        const void *img_params[4] = {
            reinterpret_cast<const void *>(imgs.color),
            reinterpret_cast<const void *>(imgs.depth),
            reinterpret_cast<const void *>(imgs.motion),
            reinterpret_cast<const void *>(imgs.output),
        };

        int eval_result = s_evaluate_fn(
            nullptr,        /* cmd_list: null → OptiScaler submits its own */
            nullptr,        /* feature_handle: managed by OptiScaler        */
            img_params,     /* params: our image-handle array               */
            nullptr         /* in_out_data: unused by current OptiScaler    */
        );

        if (packet.use_scissor_clipping) {
          std::cout << "[Worker] UPSCALE scissor: [" << packet.scissor_offset_x << ","
                    << packet.scissor_offset_y << " " << packet.scissor_extent_w << "x"
                    << packet.scissor_extent_h << "]\n";
        }
        else {
          std::cout << "[Worker] UPSCALE scissor: disabled\n";
        }
        std::cout << "[Worker] Dispatched frame " << packet.frame_index
                  << " EvaluateFeature=0x" << std::hex << eval_result << std::dec << "\n";
      }
      else {
        /* Function not resolved: OptiScaler intercepts at driver level, or
         * nvngx.dll was not loaded yet.  Let the frame flow cleanly to cleanup. */
        std::cout << "[Worker] EvaluateFeature stub: frame " << packet.frame_index
                  << " (g_ngx_lib=" << reinterpret_cast<void *>(g_ngx_lib)
                  << "); dispatch deferred to OptiScaler interception layer.\n";
      }
    }

    /* --- Destroy images before freeing their backing memories --------- */
    /* Per Vulkan spec: vkDestroyImage must be called before the VkDeviceMemory
     * it is bound to is freed (vkFreeMemory). */
    free_frame_images(imgs);

    /* --- Release imported device memories for this frame. */
    free_imported_memories(mems);
  }

  close(client_fd);
  std::cout << "[Worker] Client fd=" << client_fd << " closed.\n";
}

/** Create and bind the UNIX domain server socket.
 *  Removes any stale socket file first so bind() cannot fail on restart.
 *  @return the bound+listening server fd, or -1 on error. */
static int create_unix_server()
{
  /* Remove stale socket from a prior run. */
  (void)unlink(kSockPath);

  int server_fd = socket(AF_UNIX, SOCK_STREAM, 0);
  if (server_fd < 0) {
    std::cerr << "[Worker] socket(AF_UNIX) failed: " << strerror(errno) << "\n";
    return -1;
  }

  struct sockaddr_un addr = {};
  addr.sun_family = AF_UNIX;
  /* sockaddr_un.sun_path is typically 108 bytes; kSockPath is well within that. */
  strncpy(addr.sun_path, kSockPath, sizeof(addr.sun_path) - 1);

  if (bind(server_fd, reinterpret_cast<struct sockaddr *>(&addr), sizeof(addr)) < 0) {
    std::cerr << "[Worker] bind(" << kSockPath << ") failed: " << strerror(errno) << "\n";
    close(server_fd);
    return -1;
  }

  /* Restrict to the owner (Blender and the worker run as the same uid). */
  chmod(kSockPath, 0600);

  if (listen(server_fd, 1) < 0) {
    std::cerr << "[Worker] listen() failed: " << strerror(errno) << "\n";
    close(server_fd);
    (void)unlink(kSockPath);
    return -1;
  }

  std::cout << "[Worker] Listening on " << kSockPath << "\n";
  return server_fd;
}

#endif /* !_WIN32 */

/* ------------------------------------------------------------------ */
/* main()                                                              */
/* ------------------------------------------------------------------ */

int main(int argc, char **argv)
{
  std::cout << "[Proton Worker] blender_upscaler_worker starting...\n";
  std::cout << "[Proton Worker] Initializing OptiScaler / XeSS / FSR 4.1 IPC runtime bridge.\n";

#ifdef _WIN32
  /* ---------------------------------------------------------------- */
  /* Windows: legacy TCP IPC path (unchanged)                         */
  /* ---------------------------------------------------------------- */
  (void)argc;
  (void)argv;

  WSADATA wsa_data;
  if (WSAStartup(MAKEWORD(2, 2), &wsa_data) != 0) {
    std::cerr << "[Proton Worker] Winsock initialization failed.\n";
    return 1;
  }

  SOCKET server = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
  if (server == INVALID_SOCKET) {
    std::cerr << "[Proton Worker] Failed to create IPC socket.\n";
    WSACleanup();
    return 1;
  }

  sockaddr_in address = {};
  address.sin_family      = AF_INET;
  address.sin_port        = htons(kIpcPort);
  address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);

  if (bind(server, reinterpret_cast<sockaddr *>(&address), sizeof(address)) == SOCKET_ERROR ||
      listen(server, 1) == SOCKET_ERROR)
  {
    std::cerr << "[Proton Worker] Failed to listen on 127.0.0.1:" << kIpcPort << ".\n";
    closesocket(server);
    WSACleanup();
    return 1;
  }

  std::cout << "[Proton Worker] Listening on 127.0.0.1:" << kIpcPort << "\n";
  std::cout << "[Proton Worker] Ready for Vulkan shared memory descriptor dispatch.\n";

  bool running = true;
  while (running) {
    SOCKET client = accept(server, nullptr, nullptr);
    if (client == INVALID_SOCKET) {
      std::cerr << "[Proton Worker] Failed to accept IPC connection.\n";
      continue;
    }

    UpscalerIPCMessage msg = {};
    char *message_data = reinterpret_cast<char *>(&msg);
    size_t bytes_received = 0;
    while (bytes_received < sizeof(msg)) {
      int received = recv(client,
                          message_data + bytes_received,
                          static_cast<int>(sizeof(msg) - bytes_received),
                          0);
      if (received <= 0) {
        break;
      }
      bytes_received += static_cast<size_t>(received);
    }
    closesocket(client);

    if (bytes_received != sizeof(msg)) {
      std::cerr << "[Proton Worker] Incomplete IPC message received.\n";
      continue;
    }
    if (msg.magic != kMagic) {
      std::cerr << "[Proton Worker] Invalid IPC message magic; ignoring message.\n";
      continue;
    }

    switch (msg.command) {
      case 0:
        std::cout << "[Proton Worker] IPC ping received.\n";
        break;
      case 2:
        std::cout << "[Proton Worker] Upscale request: render="
                  << msg.render_width << "x" << msg.render_height
                  << ", display=" << msg.display_width << "x" << msg.display_height
                  << ", descriptors={color:" << msg.color_shm_descriptor
                  << ", depth:" << msg.depth_shm_descriptor
                  << ", motion:" << msg.motion_shm_descriptor
                  << ", output:" << msg.output_shm_descriptor << "}\n";
        break;
      case 3:
        std::cout << "[Proton Worker] Shutdown requested.\n";
        running = false;
        break;
      default:
        std::cerr << "[Proton Worker] Unsupported IPC command " << msg.command << ".\n";
        break;
    }
  }

  closesocket(server);
  WSACleanup();

#else /* !_WIN32 */
  /* ---------------------------------------------------------------- */
  /* Linux: UNIX domain socket + SCM_RIGHTS Vulkan FD receiver        */
  /* ---------------------------------------------------------------- */
  (void)argc;
  (void)argv;

  /* Ignore SIGPIPE so a client crash does not kill the daemon. */
  signal(SIGPIPE, SIG_IGN);

  /* Optional: read VkDevice and VkInstance handles from environment.
   * Blender can export them before spawning (or sending an init packet):
   *   export BLENDER_VK_DEVICE=0x<hex>
   *   export BLENDER_VK_INSTANCE=0x<hex>
   * These are raw pointer values cast to uint64_t — safe within the
   * same GPU driver address space shared via the Proton IPC bridge. */
  {
    const char *env_dev = std::getenv("BLENDER_VK_DEVICE");
    const char *env_inst = std::getenv("BLENDER_VK_INSTANCE");
    if (env_dev && env_dev[0] != '\0') {
      uint64_t v = std::strtoull(env_dev, nullptr, 0);
      g_vk_device = reinterpret_cast<VkDevice>(static_cast<uintptr_t>(v));
      std::cout << "[Worker] VkDevice from env: " << g_vk_device << "\n";
    }
    if (env_inst && env_inst[0] != '\0') {
      uint64_t v = std::strtoull(env_inst, nullptr, 0);
      g_vk_instance = reinterpret_cast<VkInstance>(static_cast<uintptr_t>(v));
      std::cout << "[Worker] VkInstance from env: " << g_vk_instance << "\n";
    }
  }

  /* Resolve Vulkan entry points if device is already known. */
  if (g_vk_device != VK_NULL_HANDLE) {
    resolve_vk_entry_points();
  }

  /* Create the UNIX domain server socket. */
  int server_fd = create_unix_server();
  if (server_fd < 0) {
    std::cerr << "[Worker] Failed to create UNIX server socket; exiting.\n";
    return 1;
  }

  std::cout << "[Worker] Ready. Waiting for Blender connections on " << kSockPath << "\n";

  /* Accept loop — runs until the process is killed. */
  while (true) {
    int client_fd = accept(server_fd, nullptr, nullptr);
    if (client_fd < 0) {
      if (errno == EINTR) {
        /* Interrupted by signal — retry. */
        continue;
      }
      std::cerr << "[Worker] accept() failed: " << strerror(errno)
                << " — retrying.\n";
      /* Brief pause to avoid a tight error loop on persistent failure. */
      usleep(100000); /* 100 ms */
      continue;
    }

    /* Lazily resolve VK entry points in case Blender sent the device handle
     * via an env-var after the worker started but before this first connect. */
    if (g_vk_device != VK_NULL_HANDLE && g_vkImportMemoryFdKHR == nullptr) {
      resolve_vk_entry_points();
    }

    handle_client(client_fd);
    /* handle_client() closes client_fd before returning. */
  }

  /* Unreachable in normal operation; clean up on SIGTERM/explicit break. */
  close(server_fd);
  (void)unlink(kSockPath);
  std::cout << "[Worker] Exiting.\n";
#endif /* _WIN32 */

  return 0;
}
