/* SPDX-FileCopyrightText: 2026 Goo Engine Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

void node_light_data(vec4 in_color,
                     float in_power,
                     vec3 in_pos,
                     vec3 in_dir,
                     out vec4 out_color,
                     out float out_power,
                     out vec3 out_position,
                     out vec3 out_direction,
                     out float out_distance,
                     out float out_attenuation)
{
  out_color = in_color;
  out_power = in_power;
  out_position = in_pos;
  out_direction = normalize(in_dir);
  out_distance = length(in_pos);
  out_attenuation = 1.0;
}
