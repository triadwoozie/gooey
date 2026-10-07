/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup eevee
 *
 * Temporal Upscaling (FSR 4.1 / OptiScaler / NVNGX bridge) module for EEVEE.
 */

#pragma once

#include "BLI_math_matrix.hh"
#include "BLI_math_vector_types.hh"
#include "DRW_gpu_wrapper.hh"

#include "nvngx_bridge_api.hh"

namespace blender::draw {
class View;
}  // namespace blender::draw

namespace blender::eevee {

class Instance;
using draw::View;

class UpscaleModule {
 private:
  Instance &inst_;
  bool enabled_ = false;
  float sharpness_ = 0.5f;
  nvngx::UpscalerQuality quality_mode_ = nvngx::UpscalerQuality::PERFORMANCE;

  int2 render_extent_ = int2(0);
  int2 display_extent_ = int2(0);
  int cached_backend_ = -1;
  bool feature_created_ = false;
  float last_eval_time_ms_ = 0.0f;
  int real_input_w_ = 0;
  int real_input_h_ = 0;

  GPUTexture *upscaled_tx_ = nullptr;
  GPUTexture *depth_target_tx_ = nullptr;
  GPUTexture *mv_target_tx_ = nullptr;
  GPUTexture *reactive_mask_tx_ = nullptr;
  GPUTexture *history_tx_[2] = {nullptr, nullptr};
  int history_ping_pong_ = 0;
  GPUFrameBuffer *upscale_fb_ = nullptr;

  int cached_w_ = 0;
  int cached_h_ = 0;
  int cached_render_w_ = 0;
  int cached_render_h_ = 0;
  int cached_quality_mode_ = -1;

  float4x4 last_viewmat_ = float4x4::identity();
  float4x4 last_winmat_ = float4x4::identity();
  bool history_valid_ = false;

  void free_targets();
  void ensure_targets(int render_w, int render_h, int display_w, int display_h, int quality_mode);

 public:
  UpscaleModule(Instance &inst);
  ~UpscaleModule();

  void init();
  void begin_sync();
  void end_sync();

  bool is_enabled() const { return enabled_; }
  float sharpness() const { return sharpness_; }
  nvngx::UpscalerQuality quality_mode() const { return quality_mode_; }

  int real_input_w() const { return real_input_w_; }
  int real_input_h() const { return real_input_h_; }
  int real_output_w() const { return display_extent_.x; }
  int real_output_h() const { return display_extent_.y; }
  float eval_time_ms() const { return last_eval_time_ms_; }

  /**
   * Process and upscale low-res scene radiance into full-res color buffer.
   * Hooks into NVNGX / OptiScaler runtime or applies high-quality reconstruction.
   */
  GPUTexture *process(View &view,
                      GPUTexture *input_color_tx,
                      GPUTexture *depth_tx,
                      GPUTexture *vector_tx);
};

}  // namespace blender::eevee
