/* SPDX-FileCopyrightText: 2026 Goo Engine Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

void node_shadow(vec3 position,
                 vec3 direction,
                 float length,
                 float spread,
                 float ray_count,
                 out vec4 color)
{
  /* Dedicated shadow evaluation output - returns shadow visibility factor/color */
  color = vec4(1.0, 1.0, 1.0, 1.0);
}
