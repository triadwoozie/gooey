/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "gpu_shader_compositor_texture_utilities.glsl"

void main()
{
  ivec2 texel = ivec2(gl_GlobalInvocationID.xy);
  ivec2 output_size = imageSize(output_img);
  if (any(greaterThanEqual(texel, output_size))) {
    return;
  }

  vec4 base_color = texture_load(base_tx, texel);
  vec4 upscaled_color = texture_load(upscaled_tx, texel);
  float mask = texture_load(mask_tx, texel).x;

  /* Blend upscaled buffer with base image using mask factor.
   * mask = 1.0 -> full upscaled, mask = 0.0 -> base original */
  vec4 blended = mix(base_color, upscaled_color, clamp(mask, 0.0, 1.0));
  imageStore(output_img, texel, blended);
}
