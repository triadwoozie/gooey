/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "gpu_shader_compositor_texture_utilities.glsl"

float tepd_hash(vec2 p)
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

  /* Low-discrepancy Weyl sequence temporal phase shift (phi ≈ 0.6180339887) */
  const float phi = 0.618033988749895;
  float phase = fract(frame_idx * phi);

  /* Generate two independent uniform random variables with temporal phase shift */
  float u1 = fract(tepd_hash(vec2(texel) * 0.1234 + vec2(1.0, 7.0)) + phase);
  float u2 = fract(tepd_hash(vec2(texel) * 0.5678 + vec2(3.0, 11.0)) + fract(phase * (1.0 + phi)));

  /* Triangular Probability Distribution Function (TPDF) kernel: mean is zero -> 0 integrated DC drift */
  float tpdf = u1 - u2;

  /* Quantization step size based on target bit depth */
  float steps = exp2(clamp(bit_depth, 4.0, 16.0)) - 1.0;
  float step_size = 1.0 / max(steps, 1.0);

  /* Dither and quantize */
  vec3 dithered = color.rgb + vec3(tpdf * step_size);
  dithered = floor(dithered * steps + 0.5) * step_size;
  dithered = clamp(dithered, vec3(0.0), vec3(1.0));

  imageStore(output_img, texel, vec4(dithered, color.a));
}
