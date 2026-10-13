/* SPDX-FileCopyrightText: 2026 Goo Engine Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

void node_light_evaluation(vec3 position,
                           vec3 normal,
                           float roughness,
                           float size_x,
                           float size_y,
                           float ior,
                           vec3 tangent,
                           vec3 bitangent,
                           float light_mode,
                           float function_mode,
                           float param_mode,
                           float anisotropy,
                           out vec4 color,
                           out float energy,
                           out float factor,
                           out float light_elevation,
                           out float light_spherical_area)
{
  vec3 N = (length(normal) > 0.0001) ? normalize(normal) : vec3(0.0, 0.0, 1.0);
  vec3 L = vec3(0.0, 0.0, 1.0);
  float NdotL = max(dot(N, L), 0.0);

  /* Function Mode:
   * 0: Cosine Hemisphere (Lambert)
   * 1: Cosine Sphere (Half-Lambert)
   * 2: Uniform Hemisphere (Toon)
   * 3: Uniform Sphere
   */
  float f = NdotL;
  if (function_mode > 0.5 && function_mode < 1.5) {
    /* Half-Lambert */
    f = pow(NdotL * 0.5 + 0.5, 2.0);
  }
  else if (function_mode >= 1.5 && function_mode < 2.5) {
    /* Toon step */
    f = smoothstep(0.0, 0.05, NdotL);
  }
  else if (function_mode >= 2.5) {
    /* Uniform sphere */
    f = NdotL * 0.5 + 0.5;
  }

  factor = clamp(f, 0.0, 1.0);
  energy = factor;
  color = vec4(vec3(factor), 1.0);
  light_elevation = asin(clamp(N.z, -1.0, 1.0));
  light_spherical_area = max(size_x * size_y * 3.14159265, 0.001);
}
