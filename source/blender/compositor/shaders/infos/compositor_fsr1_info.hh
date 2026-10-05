/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "gpu_shader_create_info.hh"

GPU_SHADER_CREATE_INFO(compositor_fsr1_easu)
LOCAL_GROUP_SIZE(16, 16)
PUSH_CONSTANT(VEC2, input_to_output_scale)
SAMPLER(0, FLOAT_2D, input_tx)
IMAGE(0, GPU_RGBA16F, WRITE, FLOAT_2D, output_img)
COMPUTE_SOURCE("compositor_fsr1_easu.glsl")
DO_STATIC_COMPILATION()
GPU_SHADER_CREATE_END()

GPU_SHADER_CREATE_INFO(compositor_fsr1_rcas)
LOCAL_GROUP_SIZE(16, 16)
PUSH_CONSTANT(FLOAT, sharpness)
SAMPLER(0, FLOAT_2D, input_tx)
IMAGE(0, GPU_RGBA16F, WRITE, FLOAT_2D, output_img)
COMPUTE_SOURCE("compositor_fsr1_rcas.glsl")
DO_STATIC_COMPILATION()
GPU_SHADER_CREATE_END()
