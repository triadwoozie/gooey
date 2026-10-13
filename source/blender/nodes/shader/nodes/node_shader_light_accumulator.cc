/* SPDX-FileCopyrightText: 2026 Goo Engine Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup shdnodes
 */

#include "node_shader_util.hh"

namespace blender::nodes::node_shader_light_accumulator_cc {

static void node_declare(NodeDeclarationBuilder &b)
{
  b.add_input<decl::Color>("Diffuse").default_value({0.8f, 0.8f, 0.8f, 1.0f});
  b.add_input<decl::Color>("Glossy").default_value({0.0f, 0.0f, 0.0f, 1.0f});
  b.add_input<decl::Color>("Transmission").default_value({0.0f, 0.0f, 0.0f, 1.0f});
  b.add_output<decl::Shader>("Closure");
}

static int node_shader_gpu_light_accumulator(GPUMaterial *mat,
                                             bNode *node,
                                             bNodeExecData * /*execdata*/,
                                             GPUNodeStack *in,
                                             GPUNodeStack *out)
{
  GPU_material_flag_set(mat, GPU_MATFLAG_EMISSION);
  return GPU_stack_link(mat, node, "node_light_accumulator", in, out);
}

}  // namespace blender::nodes::node_shader_light_accumulator_cc

void register_node_type_sh_light_accumulator()
{
  namespace file_ns = blender::nodes::node_shader_light_accumulator_cc;

  static blender::bke::bNodeType ntype;

  sh_node_type_base(&ntype, "ShaderNodeLightAccumulator", SH_NODE_LIGHT_ACCUMULATOR);
  ntype.ui_name = "Light Accumulator";
  ntype.ui_description = "Accumulate stylized diffuse, glossy and transmission lighting contributions into a surface closure";
  ntype.enum_name_legacy = "LIGHT_ACCUMULATOR";
  ntype.nclass = NODE_CLASS_SHADER;
  ntype.declare = file_ns::node_declare;
  ntype.gpu_fn = file_ns::node_shader_gpu_light_accumulator;

  blender::bke::node_register_type(ntype);
}
