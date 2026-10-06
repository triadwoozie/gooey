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

  nvngx::NVNGXInitParams init_params;
  init_params.app_id = 100334311;
  init_params.preferred_backend = nvngx::UpscalerBackend::FSR31;
  init_params.render_scale = 0.67f;
  init_params.use_final_render = true;

  nvngx::NVNGXBridge::get().initialize(init_params);
  enabled_ = true;
  sharpness_ = 0.5f;
  quality_mode_ = nvngx::UpscalerQuality::QUALITY;
}

void UpscaleModule::begin_sync()
{
  if (strcmp(inst_.scene->r.engine, "BLENDER_EEVEE_NEXT") != 0 &&
      strcmp(inst_.scene->r.engine, "BLENDER_EEVEE") != 0) {
    enabled_ = false;
    return;
  }

  enabled_ = true;

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
                                     GPUTexture *depth_tx,
                                     GPUTexture *vector_tx)
{
  if (!enabled_ ||
      (strcmp(inst_.scene->r.engine, "BLENDER_EEVEE_NEXT") != 0 &&
       strcmp(inst_.scene->r.engine, "BLENDER_EEVEE") != 0)) {
    return input_color_tx;
  }

  double t0 = BLI_time_now_seconds();
  real_input_w_ = GPU_texture_width(input_color_tx);
  real_input_h_ = GPU_texture_height(input_color_tx);

  int native_w = display_extent_.x;
  int native_h = display_extent_.y;

  printf("[EEVEE TRACE] UpscaleModule::process:\n"
         "  in_width: %d, in_height: %d\n"
         "  out_width: %d, out_height: %d\n"
         "  color_tx: %p, depth_tx: %p, vector_tx: %p\n",
         real_input_w_, real_input_h_, native_w, native_h,
         input_color_tx, depth_tx, vector_tx);
  fflush(stdout);

  const eGPUTextureUsage output_usage = GPU_TEXTURE_USAGE_GENERAL |
                                        GPU_TEXTURE_USAGE_MEMORY_EXPORT;
  upscaled_tx_.acquire(display_extent_, GPU_RGBA16F, output_usage);

  /* Set up destination framebuffer and reconstruct low-res color into high-res target */
  if (upscale_fb_ == nullptr) {
    upscale_fb_ = GPU_framebuffer_create("eevee_upscale_fb");
  }
  GPU_framebuffer_texture_attach(upscale_fb_, upscaled_tx_, 0, 0);
  GPU_framebuffer_bind(upscale_fb_);

  GPU_matrix_push();
  GPU_matrix_push_projection();
  GPU_matrix_identity_set();
  GPU_matrix_ortho_2d_set(0.0f, float(native_w), 0.0f, float(native_h));

  /* Explicitly reset the viewport and scissor rectangles to the native canvas dimensions */
  GPU_viewport(0, 0, native_w, native_h);
  GPU_scissor(0, 0, native_w, native_h);
  GPU_framebuffer_viewport_set(upscale_fb_, 0, 0, native_w, native_h);

  GPU_blend(GPU_BLEND_NONE);

  /* Render a full quad across [0, 0] to [native_w, native_h] covering 100% of the canvas. */
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

  const float4x4 &curr_viewmat = inst_.camera.data_get().viewmat;
  const float4x4 &curr_winmat = inst_.camera.data_get().winmat;
  bool camera_moved = (last_viewmat_ != curr_viewmat) || (last_winmat_ != curr_winmat);
  last_viewmat_ = curr_viewmat;
  last_winmat_ = curr_winmat;

  /* Halton base-2/base-3 subpixel jitter for temporal stability. */
  static uint32_t s_frame_index = 0;
  auto halton = [](uint32_t index, uint32_t base) -> float {
    float f = 1.0f, r = 0.0f;
    while (index > 0) {
      f /= base;
      r += f * (index % base);
      index /= base;
    }
    return r;
  };
  uint32_t fi = s_frame_index++ & 0x3F; /* 64-frame cycle. */

  nvngx::NVNGXEvalParams eval_params;
  struct ExportedTexture {
    int64_t fd = -1;
    uint64_t alloc_size = 0;
  };
  auto export_tex = [](GPUTexture * /*tex*/) -> ExportedTexture {
    return {-1, 0};
  };
  const ExportedTexture color_ex = export_tex(input_color_tx);
  const ExportedTexture depth_ex = export_tex(depth_tx);
  const ExportedTexture mv_ex = export_tex(vector_tx);
  const ExportedTexture output_ex = export_tex(upscaled_tx_);
  const int64_t color_fd = color_ex.fd;
  const int64_t depth_fd = depth_ex.fd;
  const int64_t motion_vectors_fd = mv_ex.fd;
  const int64_t output_fd = output_ex.fd;
  const int64_t native_fds[] = {color_fd, depth_fd, motion_vectors_fd, output_fd};
  if (color_fd < 0 || depth_fd < 0 || motion_vectors_fd < 0 || output_fd < 0) {
    return upscaled_tx_;
  }
  eval_params.color_texture = reinterpret_cast<void *>(static_cast<intptr_t>(color_fd));
  eval_params.depth_texture = reinterpret_cast<void *>(static_cast<intptr_t>(depth_fd));
  eval_params.motion_vectors = reinterpret_cast<void *>(static_cast<intptr_t>(motion_vectors_fd));
  eval_params.output_texture = reinterpret_cast<void *>(static_cast<intptr_t>(output_fd));
  eval_params.color_alloc_size = color_ex.alloc_size;
  eval_params.depth_alloc_size = depth_ex.alloc_size;
  eval_params.mv_alloc_size = mv_ex.alloc_size;
  eval_params.output_alloc_size = output_ex.alloc_size;
  eval_params.jitter_offset_x = halton(fi, 2) - 0.5f;
  eval_params.jitter_offset_y = halton(fi, 3) - 0.5f;
  eval_params.motion_scale_x = float(render_extent_.x) / float(display_extent_.x);
  eval_params.motion_scale_y = float(render_extent_.y) / float(display_extent_.y);
  eval_params.sharpness = sharpness_;
  eval_params.reset = inst_.sampling.is_reset() || (inst_.sampling.sample_index() <= 1) ||
                      !history_valid_ || camera_moved;
  eval_params.render_width = real_input_w_;
  eval_params.render_height = real_input_h_;
  eval_params.display_width = display_extent_.x;
  eval_params.display_height = display_extent_.y;

  /* Restrict upscaling to the active camera render bounds when letterboxed. */
  const int2 active_offset = inst_.film.film_offset_get();
  const int2 active_extent = inst_.film.film_extent_get();
  const bool has_active_rect = active_extent.x > 0 && active_extent.y > 0;
  eval_params.use_scissor_clipping =
      has_active_rect &&
      (active_offset.x != 0 || active_offset.y != 0 ||
       active_extent.x < display_extent_.x || active_extent.y < display_extent_.y);
  if (eval_params.use_scissor_clipping) {
    eval_params.scissor_offset_x = active_offset.x;
    eval_params.scissor_offset_y = active_offset.y;
    eval_params.scissor_extent_w = active_extent.x;
    eval_params.scissor_extent_h = active_extent.y;
  }
  else {
    eval_params.scissor_offset_x = 0;
    eval_params.scissor_offset_y = 0;
    eval_params.scissor_extent_w = display_extent_.x;
    eval_params.scissor_extent_h = display_extent_.y;
  }

  GPU_flush();
  const bool upscaled = nvngx::NVNGXBridge::get().evaluate_super_resolution(eval_params);
#ifndef _WIN32
  for (const int64_t fd : native_fds) {
    close(static_cast<int>(fd));
  }
#endif
  history_valid_ = upscaled;
  /* Preserve the rendered native-resolution image if the external upscaler is unavailable. */
  GPUTexture *output_tx = upscaled ? static_cast<GPUTexture *>(upscaled_tx_) : input_color_tx;

  /* When rendering in the 3D Viewport, present the upscaled result directly into
   * the viewport default framebuffer so that UI overlays (grids, gizmos, outlines)
   * draw cleanly over the full-resolution reconstructed scene. */
  if (inst_.is_viewport() && inst_.draw_ctx != nullptr) {
    DefaultFramebufferList *dfbl = inst_.draw_ctx->viewport_framebuffer_list_get();
    if (dfbl && dfbl->default_fb) {
      GPU_framebuffer_bind(dfbl->default_fb);
      GPU_framebuffer_viewport_set(dfbl->default_fb, 0, 0, native_w, native_h);

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
      immBindTextureSampler("image", output_tx, linear_sampler);

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
      GPU_texture_unbind(output_tx);

      GPU_matrix_pop_projection();
      GPU_matrix_pop();
    }
  }

  GPU_flush();
  last_eval_time_ms_ = float((BLI_time_now_seconds() - t0) * 1000.0);

  return output_tx;
}

}  // namespace blender::eevee
