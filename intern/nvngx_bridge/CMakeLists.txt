# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

set(INC
  include
  include/nvsdk_ngx
  include/xess
  include/fsr31
)

set(INC_SYS)

set(SRC
  intern/nvngx_bridge_api.cc
  include/nvngx_bridge_api.hh
)

set(LIB
  ${CMAKE_DL_LIBS}
)

# Vulkan headers are bundled with the repository dependencies.
set(_nvngx_vulkan_inc "${CMAKE_SOURCE_DIR}/lib/linux_x64/vulkan/include")
if(NOT EXISTS "${_nvngx_vulkan_inc}/vulkan/vulkan.h")
  set(_nvngx_vulkan_inc "${CMAKE_SOURCE_DIR}/../lib/linux_x64/vulkan/include")
endif()

if(EXISTS "${_nvngx_vulkan_inc}/vulkan/vulkan.h")
  list(APPEND INC_SYS "${_nvngx_vulkan_inc}")
  message(STATUS "nvngx_bridge: using bundled Vulkan headers from ${_nvngx_vulkan_inc}")
else()
  message(WARNING "nvngx_bridge: bundled Vulkan headers not found at ${_nvngx_vulkan_inc}; build may fail")
endif()

blender_add_lib(bf_intern_nvngx_bridge "${SRC}" "${INC}" "${INC_SYS}" "${LIB}")

# _GNU_SOURCE exposes CMSG_SPACE/CMSG_LEN/CMSG_DATA, SCM_RIGHTS, struct ucred,
# and strerror_r on glibc — required for the sendmsg()+SCM_RIGHTS IPC path in
# nvngx_bridge_api.cc and for any future SO_PEERCRED credential checks.
if(NOT WIN32)
  target_compile_definitions(bf_intern_nvngx_bridge PRIVATE _GNU_SOURCE)
endif()

# ---------------------------------------------------------------------------
# blender_upscaler_worker — Proton/Wine PE worker executable or native Linux
# SCM_RIGHTS receiver, depending on the configured target platform.
# ---------------------------------------------------------------------------
if(WIN32 OR CMAKE_CROSSCOMPILING OR (UNIX AND NOT APPLE))
  add_executable(blender_upscaler_worker
    worker/blender_upscaler_worker.cc
  )
  target_include_directories(blender_upscaler_worker PRIVATE include)
  if(UNIX AND NOT APPLE)
    target_include_directories(blender_upscaler_worker SYSTEM PRIVATE
      "${_nvngx_vulkan_inc}"
    )
  endif()
  if(DEFINED VULKAN_INCLUDE_DIRS)
    target_include_directories(blender_upscaler_worker SYSTEM PRIVATE ${VULKAN_INCLUDE_DIRS})
  endif()
  if(DEFINED VULKAN_LIBRARIES)
    target_link_libraries(blender_upscaler_worker PRIVATE ${VULKAN_LIBRARIES})
  endif()
  set_target_properties(blender_upscaler_worker PROPERTIES
    CXX_STANDARD 17
    CXX_STANDARD_REQUIRED ON
  )
  # When cross-compiling from Linux the sysroot may be glibc-based;
  # _GNU_SOURCE is needed for CMSG_* macros and SCM_RIGHTS in the worker.
  if(NOT WIN32)
    target_compile_definitions(blender_upscaler_worker PRIVATE _GNU_SOURCE)
  endif()
endif()
