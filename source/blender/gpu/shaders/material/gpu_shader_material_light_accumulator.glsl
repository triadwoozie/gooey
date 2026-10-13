/* SPDX-FileCopyrightText: 2026 Goo Engine Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

void node_light_accumulator(vec4 diffuse, vec4 glossy, vec4 transmission, out Closure result)
{
  vec3 total = diffuse.rgb + glossy.rgb + transmission.rgb;
  ClosureEmission emission_data;
  emission_data.weight = 1.0;
  emission_data.emission = max(total, vec3(0.0));

  result = closure_eval(emission_data);
}
