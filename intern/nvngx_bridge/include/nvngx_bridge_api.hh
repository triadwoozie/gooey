/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup nvngx_bridge
 *
 * Public C++ API for NVNGX / OptiScaler / XeSS / FSR 3.1 Temporal Upscaling Bridge.
 * Supports:
 * - FSR31: AMD FSR 3.1 Native Linux Vulkan Pipeline
 * - FSR4:  AMD FSR 4.1 via auto-spawned Proton worker daemon (blender_upscaler_worker.exe)
 * - XESS:  Intel XeSS via auto-spawned Proton worker daemon
 * - DLSS:  NVIDIA DLSS Native NVNGX Pipeline
 */

#pragma once

#include <cstdint>
#include <string>
#include <unistd.h>

#include <vulkan/vulkan.h>

namespace blender::nvngx {

struct Fsr3Context;

enum class GPUVendor : uint32_t {
  UNKNOWN = 0,
  NVIDIA = 0x10DE,
  AMD = 0x1002,
  INTEL = 0x8086,
};

enum class UpscalerBackend {
  AUTO = 0,              /* Auto-select optimal backend based on vendor */
  DLSS = 1,              /* NVIDIA DLSS (Native NVNGX) */
  FSR4 = 2,              /* AMD FSR 4.1 (Proton Bridge) */
  XESS = 3,              /* Intel XeSS (Proton Bridge) */
  FSR31 = 4,             /* AMD FSR 3.1 (Native Linux Vulkan) */
  FALLBACK_TEMPORAL = 5, /* Fallback standard temporal reconstructor */
};

enum class UpscalerQuality {
  QUALITY = 0,      /* 1.5x scaling (~67% render resolution) */
  BALANCED = 1,     /* 1.7x scaling (~59% render resolution) */
  PERFORMANCE = 2,  /* 2.0x scaling (50% render resolution) */
  ULTRA_PERF = 3,   /* 3.0x scaling (33% render resolution) */
};

struct NVNGXInitParams {
  uint64_t app_id = 100334311;
  std::string app_data_path = "";
  std::string custom_library_path = "";
  std::string proton_binary_path = "";
  UpscalerBackend preferred_backend = UpscalerBackend::FSR31;
  GPUVendor force_vendor = GPUVendor::UNKNOWN;
  float render_scale = 0.67f;
  bool use_final_render = false;
  bool show_watermark = false;
  bool use_custom_prefix = false;
  std::string custom_prefix_path = "";
};

struct NVNGXFeatureDesc {
  int render_width = 0;
  int render_height = 0;
  int display_width = 0;
  int display_height = 0;
  float sharpness = 0.5f;
  bool is_hdr = true;
  bool low_res_mv = true;
  bool depth_inverted = false;
  UpscalerQuality quality = UpscalerQuality::QUALITY;
};

struct NVNGXEvalParams {
  uint32_t ipc_command = 2; /* 1 = INIT, 2 = UPSCALE */
  void *color_texture = nullptr;
  void *depth_texture = nullptr;
  void *motion_vectors = nullptr;
  void *output_texture = nullptr;
  VkInstance vk_instance = VK_NULL_HANDLE;
  VkPhysicalDevice vk_physical_device = VK_NULL_HANDLE;
  VkDevice vk_device = VK_NULL_HANDLE;
  VkCommandBuffer vk_command_buffer = VK_NULL_HANDLE;
  VkImage color_image = VK_NULL_HANDLE;
  VkFormat color_format = VK_FORMAT_UNDEFINED;
  VkImage depth_image = VK_NULL_HANDLE;
  VkFormat depth_format = VK_FORMAT_UNDEFINED;
  VkImage motion_vectors_image = VK_NULL_HANDLE;
  VkFormat motion_vectors_format = VK_FORMAT_UNDEFINED;
  VkImage output_image = VK_NULL_HANDLE;
  VkFormat output_format = VK_FORMAT_UNDEFINED;
  void *fsr_interface_handle = nullptr;
  float jitter_offset_x = 0.0f;
  float jitter_offset_y = 0.0f;
  float motion_scale_x = 1.0f;
  float motion_scale_y = 1.0f;
  float sharpness = 0.5f;
  bool reset = false;
  uint64_t color_alloc_size = 0;
  uint64_t depth_alloc_size = 0;
  uint64_t mv_alloc_size = 0;
  uint64_t output_alloc_size = 0;
  int render_width = 0;
  int render_height = 0;
  int display_width = 0;
  int display_height = 0;

  /* Viewport sub-rect bounds for scissor and letterbox handling (Phase 5).
   * When use_scissor_clipping is true the upscaler restricts its output to
   * the rectangle [scissor_offset_x, scissor_offset_y,
   *                scissor_offset_x + scissor_extent_w,
   *                scissor_offset_y + scissor_extent_h]
   * within the display surface.  Use this for split-screen viewports and
   * non-16:9 letterboxed camera views to prevent scaling distortion
   * outside the active picture area. */
  int32_t scissor_offset_x    = 0;
  int32_t scissor_offset_y    = 0;
  int32_t scissor_extent_w    = 0;
  int32_t scissor_extent_h    = 0;
  bool    use_scissor_clipping = false;
};

class NVNGXBridge {
 private:
  bool is_initialized_ = false;
  bool feature_created_ = false;
  GPUVendor detected_vendor_ = GPUVendor::UNKNOWN;
  UpscalerBackend active_backend_ = UpscalerBackend::FSR31;
  std::string proton_binary_path_ = "";
  bool use_custom_prefix_ = false;
  std::string custom_prefix_path_ = "";
  pid_t worker_pid_ = -1;
  bool worker_active_ = false;

  void *ngx_handle_ = nullptr;
  void *ngx_parameters_ = nullptr;
  void *lib_handle_ = nullptr;
  void *xess_handle_ = nullptr;
  Fsr3Context *fsr3_context_ = nullptr;
  std::string active_feature_name_ = "";
  std::string optiscaler_config_path_ = "";

  NVNGXFeatureDesc current_feature_ = {};

  GPUVendor detect_gpu_vendor();
  void write_optiscaler_config(const std::string &upscaler_name);

 public:
  static NVNGXBridge &get();

  NVNGXBridge();
  ~NVNGXBridge();

  bool initialize(const NVNGXInitParams &params = {});
  bool is_initialized() const { return is_initialized_; }
  bool is_feature_created() const { return feature_created_; }
  GPUVendor detected_vendor() const { return detected_vendor_; }
  UpscalerBackend active_backend() const { return active_backend_; }
  const std::string &active_feature_name() const { return active_feature_name_; }

  /* Worker Daemon Lifecycle */
  bool is_worker_running() const;
  pid_t worker_pid() const { return worker_pid_; }
  bool spawn_worker(const std::string &proton_path);
  void stop_worker();
  std::string resolve_prefix_directory() const;
  std::string resolve_proton_binary(const std::string &user_path) const;

  bool create_super_resolution(const NVNGXFeatureDesc &desc);
  bool evaluate_super_resolution(const NVNGXEvalParams &params);
  bool evaluate_fsr31_vulkan(const NVNGXEvalParams &params);
  bool evaluate_proton_worker(const NVNGXEvalParams &params);

  void release_feature();
  void shutdown();

  /* Query optimal input render resolution for a given display resolution. */
  void query_optimal_settings(int display_w,
                              int display_h,
                              UpscalerQuality quality,
                              int &out_render_w,
                              int &out_render_h,
                              float &out_sharpness);
};

bool fsr3_create_context(Fsr3Context &context,
                         const NVNGXFeatureDesc &feature,
                         const NVNGXEvalParams &params);
bool fsr3_dispatch_upscale(Fsr3Context &context, const NVNGXEvalParams &params);
void fsr3_destroy_context(Fsr3Context &context);

}  // namespace blender::nvngx
