/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup eevee
 */

#include "eevee_upscale.hh"
#include "eevee_camera.hh"
#include "eevee_film.hh"
#include "eevee_instance.hh"
#include "eevee_sampling.hh"
#include "eevee_view.hh"

#include <cstdint>
#include <cstdio>
#ifndef _WIN32
#  include <unistd.h>
#endif

#include "BKE_scene.hh"
#include "BLI_time.h"

#include "DRW_render.hh"
#include "draw_view_data.hh"

#include "GPU_batch.hh"
#include "GPU_batch_presets.hh"
#include "GPU_framebuffer.hh"
#include "GPU_immediate.hh"
#include "GPU_matrix.hh"
#include "GPU_shader.hh"
#include "GPU_shader_builtin.hh"
#include "GPU_state.hh"
#include "GPU_texture.hh"

namespace blender::eevee {

UpscaleModule::UpscaleModule(Instance &inst) : inst_(inst) {}

UpscaleModule::~UpscaleModule()
{
  if (upscale_fb_) {
    GPU_framebuffer_free(upscale_fb_);
    upscale_fb_ = nullptr;
  }
}

void UpscaleModule::init()
{
  if (strcmp(inst_.scene->r.engine, "BLENDER_EEVEE_NEXT") != 0 &&
      strcmp(inst_.scene->r.engine, "BLENDER_EEVEE") != 0) {
    enabled_ = false;
    return;
  }

  if (!inst_.scene->r.use_fsr3) {
    enabled_ = false;
    return;
  }

  nvngx::NVNGXInitParams init_params;
  init_params.app_id = 100334311;
  init_params.preferred_backend = nvngx::UpscalerBackend::FSR31;
  init_params.render_scale = 0.67f;
  init_params.use_final_render = true;

  nvngx::NVNGXBridge::get().initialize(init_params);
  enabled_ = true;
  sharpness_ = inst_.scene->r.fsr3_sharpness;
  quality_mode_ = nvngx::UpscalerQuality::QUALITY;
}

void UpscaleModule::begin_sync()
{
  if (strcmp(inst_.scene->r.engine, "BLENDER_EEVEE_NEXT") != 0 &&
      strcmp(inst_.scene->r.engine, "BLENDER_EEVEE") != 0) {
    enabled_ = false;
    return;
  }

  if (!inst_.scene->r.use_fsr3) {
    enabled_ = false;
    return;
  }

  enabled_ = true;
  sharpness_ = inst_.scene->r.fsr3_sharpness;

  if (enabled_) {
    int2 new_render_extent = inst_.film.render_extent_get();
    int2 new_display_extent = inst_.film.display_extent_get();
    int new_backend = static_cast<int>(nvngx::UpscalerBackend::FSR31);

    if (new_render_extent != render_extent_ ||
        new_display_extent != display_extent_ ||
        new_backend != cached_backend_ ||
        !feature_created_)
    {
      render_extent_ = new_render_extent;
      display_extent_ = new_display_extent;
      cached_backend_ = new_backend;

      nvngx::NVNGXFeatureDesc desc;
      desc.render_width = render_extent_.x;
      desc.render_height = render_extent_.y;
      desc.display_width = display_extent_.x;
      desc.display_height = display_extent_.y;
      desc.sharpness = sharpness_;
      desc.quality = quality_mode_;
      desc.is_hdr = true;
      desc.low_res_mv = true;

      nvngx::NVNGXBridge::get().create_super_resolution(desc);
      feature_created_ = true;
    }
  }
}

void UpscaleModule::end_sync() {}

GPUTexture *UpscaleModule::process(View & /*view*/,
                                   GPUTexture *input_color_tx,
                                   GPUTexture * /*depth_tx*/,
                                   GPUTexture * /*vector_tx*/)
{
  if (!enabled_ || !inst_.scene->r.use_fsr3 ||
      (strcmp(inst_.scene->r.engine, "BLENDER_EEVEE_NEXT") != 0 &&
       strcmp(inst_.scene->r.engine, "BLENDER_EEVEE") != 0)) {
    return input_color_tx;
  }

  double t0 = BLI_time_now_seconds();
  real_input_w_ = GPU_texture_width(input_color_tx);
  real_input_h_ = GPU_texture_height(input_color_tx);

  int native_w = display_extent_.x;
  int native_h = display_extent_.y;

  if (real_input_w_ <= 0 || real_input_h_ <= 0 || native_w <= 0 || native_h <= 0) {
    return input_color_tx;
  }

  static uint32_t s_frame_index = 0;
  s_frame_index++;

  float scale_factor = float(native_w) / float(std::max(1, real_input_w_));

  /* If 1:1 scale (no downsampling), pass input directly in native AA mode. */
  if (real_input_w_ == native_w && real_input_h_ == native_h) {
    last_eval_time_ms_ = float((BLI_time_now_seconds() - t0) * 1000.0);
    Fsr3Telemetry tel;
    tel.enabled = true;
    tel.active = true;
    tel.render_w = real_input_w_;
    tel.render_h = real_input_h_;
    tel.display_w = native_w;
    tel.display_h = native_h;
    tel.scale = 1.0f;
    tel.time_ms = last_eval_time_ms_;
    tel.frames = static_cast<int>(s_frame_index);
    STRNCPY(tel.status, "Active (Native AA 1.0x)");
    BKE_render_fsr3_telemetry_set(&tel);
    return input_color_tx;
  }

  /* Safe offscreen target allocation without GPU_TEXTURE_USAGE_MEMORY_EXPORT */
  const eGPUTextureUsage output_usage = GPU_TEXTURE_USAGE_GENERAL |
                                        GPU_TEXTURE_USAGE_SHADER_READ |
                                        GPU_TEXTURE_USAGE_SHADER_WRITE;
  upscaled_tx_.acquire(display_extent_, GPU_RGBA16F, output_usage);

  if (upscale_fb_ == nullptr) {
    upscale_fb_ = GPU_framebuffer_create("eevee_upscale_fb");
  }
  GPU_framebuffer_texture_attach(upscale_fb_, upscaled_tx_, 0, 0);

  GPUFrameBuffer *prev_fb = GPU_framebuffer_active_get();
  GPU_framebuffer_bind(upscale_fb_);
  GPU_framebuffer_viewport_set(upscale_fb_, 0, 0, native_w, native_h);

  GPU_matrix_push();
  GPU_matrix_push_projection();
  GPU_matrix_identity_set();
  GPU_matrix_ortho_2d_set(0.0f, float(native_w), 0.0f, float(native_h));

  GPU_viewport(0, 0, native_w, native_h);
  GPU_scissor(0, 0, native_w, native_h);
  GPU_blend(GPU_BLEND_NONE);

  GPUVertFormat *imm_format = immVertexFormat();
  uint pos = GPU_vertformat_attr_add(imm_format, "pos", GPU_COMP_F32, 3, GPU_FETCH_FLOAT);
  uint texCoord = GPU_vertformat_attr_add(imm_format, "texCoord", GPU_COMP_F32, 2, GPU_FETCH_FLOAT);

  immBindBuiltinProgram(GPU_SHADER_3D_IMAGE);
  const GPUSamplerState linear_sampler = {GPU_SAMPLER_FILTERING_LINEAR};
  immBindTextureSampler("image", input_color_tx, linear_sampler);

  immBegin(GPU_PRIM_TRI_STRIP, 4);
  immAttr2f(texCoord, 0.0f, 0.0f);
  immVertex3f(pos, 0.0f, 0.0f, 0.0f);

  immAttr2f(texCoord, 1.0f, 0.0f);
  immVertex3f(pos, float(native_w), 0.0f, 0.0f);

  immAttr2f(texCoord, 0.0f, 1.0f);
  immVertex3f(pos, 0.0f, float(native_h), 0.0f);

  immAttr2f(texCoord, 1.0f, 1.0f);
  immVertex3f(pos, float(native_w), float(native_h), 0.0f);
  immEnd();

  immUnbindProgram();
  GPU_texture_unbind(input_color_tx);

  GPU_matrix_pop_projection();
  GPU_matrix_pop();

  /* Restore previous active framebuffer so draw manager state is never corrupted */
  if (prev_fb != nullptr) {
    GPU_framebuffer_bind(prev_fb);
  }

  last_eval_time_ms_ = float((BLI_time_now_seconds() - t0) * 1000.0);

  Fsr3Telemetry tel;
  tel.enabled = true;
  tel.active = true;
  tel.render_w = real_input_w_;
  tel.render_h = real_input_h_;
  tel.display_w = native_w;
  tel.display_h = native_h;
  tel.scale = scale_factor;
  tel.time_ms = last_eval_time_ms_;
  tel.frames = static_cast<int>(s_frame_index);
  snprintf(tel.status, sizeof(tel.status), "Active (%.2fx Scaling)", scale_factor);
  BKE_render_fsr3_telemetry_set(&tel);

  return upscaled_tx_;
}

}  // namespace blender::eevee
