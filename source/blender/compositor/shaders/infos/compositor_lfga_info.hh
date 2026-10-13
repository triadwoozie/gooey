/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "gpu_shader_create_info.hh"

GPU_SHADER_CREATE_INFO(compositor_lfga)
LOCAL_GROUP_SIZE(16, 16)
PUSH_CONSTANT(float, intensity)
PUSH_CONSTANT(float, grain_size)
PUSH_CONSTANT(float, seed)
SAMPLER(0, sampler2D, input_tx)
IMAGE(0, GPU_RGBA16F, write, image2D, output_img)
COMPUTE_SOURCE("compositor_lfga.glsl")
DO_STATIC_COMPILATION()
GPU_SHADER_CREATE_END()
