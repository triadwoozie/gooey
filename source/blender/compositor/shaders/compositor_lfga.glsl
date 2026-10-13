/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "gpu_shader_compositor_texture_utilities.glsl"

float lfga_hash(vec2 p)
{
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}

void main()
{
  ivec2 texel = ivec2(gl_GlobalInvocationID.xy);
  ivec2 size = imageSize(output_img);
  if (any(greaterThanEqual(texel, size))) {
    return;
  }

  vec4 color = texture_load(input_tx, texel);

  /* Linear Rec.709 scene-space luminance */
  float Y = dot(color.rgb, vec3(0.2126, 0.7152, 0.0722));
  float Y_clamped = clamp(Y, 0.0, 1.0);

  /* Midtone weighting: 4.0 * Y * (1.0 - Y), keeping blacks and whites clean */
  float midtone_weight = 4.0 * Y_clamped * (1.0 - Y_clamped);

  /* Procedural spatial grain sample centered at 0 (-0.5 to 0.5) */
  vec2 grain_coord = vec2(texel) / max(grain_size, 0.001);
  float noise = lfga_hash(grain_coord + vec2(seed, seed * 1.6180339)) - 0.5;

  /* Apply midtone-weighted procedural linear film grain */
  vec3 grain_color = color.rgb + vec3(noise * intensity * midtone_weight);
  grain_color = max(grain_color, vec3(0.0));

  imageStore(output_img, texel, vec4(grain_color, color.a));
}
