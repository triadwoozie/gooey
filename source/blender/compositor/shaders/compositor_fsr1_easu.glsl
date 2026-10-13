/* SPDX-FileCopyrightText: 2024 Advanced Micro Devices, Inc.
 *
 * SPDX-License-Identifier: MIT
 *
 * Adapted from FidelityFX FSR 1's EASU reference implementation.
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy of this software
 * and associated documentation files (the "Software"), to deal in the Software without
 * restriction, including without limitation the rights to use, copy, modify, merge, publish,
 * distribute, sublicense, and/or sell copies of the Software, and to permit persons to whom the
 * Software is furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all copies or
 * substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING
 * BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
 * NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
 * DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 */

/* Direction and edge-strength accumulation for one of EASU's four bilinear quadrants. */
void fsr_easu_set(inout vec2 direction,
                  inout float length,
                  vec2 fraction,
                  int quadrant,
                  float l_a,
                  float l_b,
                  float l_c,
                  float l_d,
                  float l_e)
{
  float weight = 0.0;
  if (quadrant == 0) {
    weight = (1.0 - fraction.x) * (1.0 - fraction.y);
  }
  else if (quadrant == 1) {
    weight = fraction.x * (1.0 - fraction.y);
  }
  else if (quadrant == 2) {
    weight = (1.0 - fraction.x) * fraction.y;
  }
  else {
    weight = fraction.x * fraction.y;
  }

  float difference_dc = l_d - l_c;
  float difference_cb = l_c - l_b;
  float length_x = abs(l_d - l_b) / max(max(abs(difference_dc), abs(difference_cb)), 1.0e-8);
  direction.x += (l_d - l_b) * weight;
  length += min(length_x, 1.0) * min(length_x, 1.0) * weight;

  float difference_ec = l_e - l_c;
  float difference_ca = l_c - l_a;
  float length_y = abs(l_e - l_a) / max(max(abs(difference_ec), abs(difference_ca)), 1.0e-8);
  direction.y += (l_e - l_a) * weight;
  length += min(length_y, 1.0) * min(length_y, 1.0) * weight;
}

void fsr_easu_tap(inout vec3 accumulated_color,
                  inout float accumulated_weight,
                  vec2 pixel_offset,
                  vec2 direction,
                  vec2 anisotropic_length,
                  float negative_lobe_strength,
                  float clipping_point,
                  vec3 color)
{
  vec2 rotated_offset = vec2(pixel_offset.x * direction.x + pixel_offset.y * direction.y,
                             pixel_offset.x * -direction.y + pixel_offset.y * direction.x);
  rotated_offset *= anisotropic_length;
  float distance_squared = min(dot(rotated_offset, rotated_offset), clipping_point);

  float weight_b = (0.4 * distance_squared - 1.0);
  float weight_a = negative_lobe_strength * distance_squared - 1.0;
  weight_b = (25.0 / 16.0) * weight_b * weight_b - (25.0 / 16.0 - 1.0);
  float weight = weight_b * weight_a * weight_a;
  accumulated_color += color * weight;
  accumulated_weight += weight;
}

ivec2 fsr_easu_clamp_coordinate(ivec2 coordinate, ivec2 input_size)
{
  return clamp(coordinate, ivec2(0), input_size - ivec2(1));
}

vec4 fsr_easu_load(ivec2 coordinate, ivec2 input_size)
{
  return texelFetch(input_tx, fsr_easu_clamp_coordinate(coordinate, input_size), 0);
}

float fsr_easu_luma(vec3 color)
{
  return color.b * 0.5 + (color.r * 0.5 + color.g);
}

void main()
{
  ivec2 output_pixel = ivec2(gl_GlobalInvocationID.xy);
  ivec2 output_size = imageSize(output_img);
  if (any(greaterThanEqual(output_pixel, output_size))) {
    return;
  }

  ivec2 input_size = textureSize(input_tx, 0);
  vec2 input_position = (vec2(output_pixel) + 0.5) * input_to_output_scale - 0.5;
  ivec2 base = ivec2(floor(input_position));
  vec2 fraction = fract(input_position);

  /* The twelve EASU taps, arranged around the central 2x2 bilinear footprint. */
  vec4 b = fsr_easu_load(base + ivec2(0, -1), input_size);
  vec4 c = fsr_easu_load(base + ivec2(1, -1), input_size);
  vec4 e = fsr_easu_load(base + ivec2(-1, 0), input_size);
  vec4 f = fsr_easu_load(base, input_size);
  vec4 g = fsr_easu_load(base + ivec2(1, 0), input_size);
  vec4 h = fsr_easu_load(base + ivec2(2, 0), input_size);
  vec4 i = fsr_easu_load(base + ivec2(-1, 1), input_size);
  vec4 j = fsr_easu_load(base + ivec2(0, 1), input_size);
  vec4 k = fsr_easu_load(base + ivec2(1, 1), input_size);
  vec4 l = fsr_easu_load(base + ivec2(2, 1), input_size);
  vec4 n = fsr_easu_load(base + ivec2(0, 2), input_size);
  vec4 o = fsr_easu_load(base + ivec2(1, 2), input_size);

  float b_l = fsr_easu_luma(b.rgb);
  float c_l = fsr_easu_luma(c.rgb);
  float e_l = fsr_easu_luma(e.rgb);
  float f_l = fsr_easu_luma(f.rgb);
  float g_l = fsr_easu_luma(g.rgb);
  float h_l = fsr_easu_luma(h.rgb);
  float i_l = fsr_easu_luma(i.rgb);
  float j_l = fsr_easu_luma(j.rgb);
  float k_l = fsr_easu_luma(k.rgb);
  float l_l = fsr_easu_luma(l.rgb);
  float n_l = fsr_easu_luma(n.rgb);
  float o_l = fsr_easu_luma(o.rgb);

  vec2 direction = vec2(0.0);
  float length = 0.0;
  fsr_easu_set(direction, length, fraction, 0, b_l, e_l, f_l, g_l, j_l);
  fsr_easu_set(direction, length, fraction, 1, c_l, f_l, g_l, h_l, k_l);
  fsr_easu_set(direction, length, fraction, 2, f_l, i_l, j_l, k_l, n_l);
  fsr_easu_set(direction, length, fraction, 3, g_l, j_l, k_l, l_l, o_l);

  float direction_length_squared = dot(direction, direction);
  bool flat_region = direction_length_squared < (1.0 / 32768.0);
  direction = flat_region ? vec2(1.0, 0.0) :
                            direction * inversesqrt(direction_length_squared);

  length = 0.25 * length * length;
  float stretch = dot(direction, direction) / max(max(abs(direction.x), abs(direction.y)), 1.0e-8);
  vec2 anisotropic_length = vec2(1.0 + (stretch - 1.0) * length, 1.0 - 0.5 * length);
  float negative_lobe_strength = 0.5 + ((0.25 - 0.04) - 0.5) * length;
  float clipping_point = 1.0 / negative_lobe_strength;

  vec3 minimum_color = min(min(f.rgb, g.rgb), min(j.rgb, k.rgb));
  vec3 maximum_color = max(max(f.rgb, g.rgb), max(j.rgb, k.rgb));

  vec3 accumulated_color = vec3(0.0);
  float accumulated_weight = 0.0;
  fsr_easu_tap(accumulated_color,
               accumulated_weight,
               vec2(0.0, -1.0) - fraction,
               direction,
               anisotropic_length,
               negative_lobe_strength,
               clipping_point,
               b.rgb);
  fsr_easu_tap(accumulated_color,
               accumulated_weight,
               vec2(1.0, -1.0) - fraction,
               direction,
               anisotropic_length,
               negative_lobe_strength,
               clipping_point,
               c.rgb);
  fsr_easu_tap(accumulated_color,
               accumulated_weight,
               vec2(-1.0, 1.0) - fraction,
               direction,
               anisotropic_length,
               negative_lobe_strength,
               clipping_point,
               i.rgb);
  fsr_easu_tap(accumulated_color,
               accumulated_weight,
               vec2(0.0, 1.0) - fraction,
               direction,
               anisotropic_length,
               negative_lobe_strength,
               clipping_point,
               j.rgb);
  fsr_easu_tap(accumulated_color,
               accumulated_weight,
               vec2(0.0, 0.0) - fraction,
               direction,
               anisotropic_length,
               negative_lobe_strength,
               clipping_point,
               f.rgb);
  fsr_easu_tap(accumulated_color,
               accumulated_weight,
               vec2(-1.0, 0.0) - fraction,
               direction,
               anisotropic_length,
               negative_lobe_strength,
               clipping_point,
               e.rgb);
  fsr_easu_tap(accumulated_color,
               accumulated_weight,
               vec2(1.0, 1.0) - fraction,
               direction,
               anisotropic_length,
               negative_lobe_strength,
               clipping_point,
               k.rgb);
  fsr_easu_tap(accumulated_color,
               accumulated_weight,
               vec2(2.0, 1.0) - fraction,
               direction,
               anisotropic_length,
               negative_lobe_strength,
               clipping_point,
               l.rgb);
  fsr_easu_tap(accumulated_color,
               accumulated_weight,
               vec2(2.0, 0.0) - fraction,
               direction,
               anisotropic_length,
               negative_lobe_strength,
               clipping_point,
               h.rgb);
  fsr_easu_tap(accumulated_color,
               accumulated_weight,
               vec2(1.0, 0.0) - fraction,
               direction,
               anisotropic_length,
               negative_lobe_strength,
               clipping_point,
               g.rgb);
  fsr_easu_tap(accumulated_color,
               accumulated_weight,
               vec2(1.0, 2.0) - fraction,
               direction,
               anisotropic_length,
               negative_lobe_strength,
               clipping_point,
               o.rgb);
  fsr_easu_tap(accumulated_color,
               accumulated_weight,
               vec2(0.0, 2.0) - fraction,
               direction,
               anisotropic_length,
               negative_lobe_strength,
               clipping_point,
               n.rgb);

  vec3 upscaled_color = clamp(accumulated_color / max(accumulated_weight, 1.0e-8),
                              minimum_color,
                              maximum_color);
  float alpha = mix(mix(f.a, g.a, fraction.x), mix(j.a, k.a, fraction.x), fraction.y);
  imageStore(output_img, output_pixel, vec4(upscaled_color, alpha));
}
