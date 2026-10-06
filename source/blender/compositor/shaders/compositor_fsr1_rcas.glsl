/* SPDX-FileCopyrightText: 2024 Advanced Micro Devices, Inc.
 *
 * SPDX-License-Identifier: MIT
 *
 * Adapted from FidelityFX FSR 1's RCAS reference implementation.
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

ivec2 fsr_rcas_clamp_coordinate(ivec2 coordinate, ivec2 input_size)
{
  return clamp(coordinate, ivec2(0), input_size - ivec2(1));
}

vec4 fsr_rcas_load(ivec2 coordinate, ivec2 input_size)
{
  return texelFetch(input_tx, fsr_rcas_clamp_coordinate(coordinate, input_size), 0);
}

void main()
{
  ivec2 output_pixel = ivec2(gl_GlobalInvocationID.xy);
  ivec2 output_size = imageSize(output_img);
  if (any(greaterThanEqual(output_pixel, output_size))) {
    return;
  }

  ivec2 input_size = textureSize(input_tx, 0);
  vec4 north = fsr_rcas_load(output_pixel + ivec2(0, -1), input_size);
  vec4 west = fsr_rcas_load(output_pixel + ivec2(-1, 0), input_size);
  vec4 center = fsr_rcas_load(output_pixel, input_size);
  vec4 east = fsr_rcas_load(output_pixel + ivec2(1, 0), input_size);
  vec4 south = fsr_rcas_load(output_pixel + ivec2(0, 1), input_size);

  vec3 minimum_color = min(min(north.rgb, west.rgb), min(east.rgb, south.rgb));
  vec3 maximum_color = max(max(north.rgb, west.rgb), max(east.rgb, south.rgb));
  vec3 hit_minimum = minimum_color / max(4.0 * maximum_color, vec3(1.0e-8));
  vec3 hit_maximum_denominator = 4.0 * minimum_color - 4.0;
  vec3 hit_maximum_safe_denominator = sign(hit_maximum_denominator) *
                                      max(abs(hit_maximum_denominator), vec3(1.0e-8));
  hit_maximum_safe_denominator = mix(hit_maximum_safe_denominator,
                                     vec3(-1.0e-8),
                                     equal(hit_maximum_denominator, vec3(0.0)));
  vec3 hit_maximum = (1.0 - maximum_color) / hit_maximum_safe_denominator;
  vec3 lobes = max(-hit_minimum, hit_maximum);
  float lobe = max(-0.1875, min(max(lobes.r, max(lobes.g, lobes.b)), 0.0));
  /* Modulate sharpening attenuation so cel lines and high-frequency textures sharpen cleanly
   * without ringing smooth NPR ramps. */
  lobe *= exp2(-sharpness);

  float reciprocal_lobe = 1.0 / max(4.0 * lobe + 1.0, 1.0e-8);
  vec3 sharpened = (lobe * (north.rgb + west.rgb + east.rgb + south.rgb) + center.rgb) *
                   reciprocal_lobe;
  imageStore(output_img, output_pixel, vec4(sharpened, center.a));
}
