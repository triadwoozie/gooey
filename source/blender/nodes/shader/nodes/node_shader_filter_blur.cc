/* SPDX-FileCopyrightText: 2026 Goo Engine Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup shdnodes
 */

#include "node_shader_util.hh"

namespace blender::nodes::node_shader_filter_blur_cc {

static void node_declare(NodeDeclarationBuilder &b)
{
  b.add_input<decl::Color>("Color").default_value({1.0f, 1.0f, 1.0f, 1.0f});
  b.add_input<decl::Float>("Size").default_value(1.0f).min(0.0f).max(100.0f);
  b.add_output<decl::Color>("Color");
}

static int node_shader_gpu_filter_blur(GPUMaterial *mat,
                                       bNode *node,
                                       bNodeExecData * /*execdata*/,
                                       GPUNodeStack *in,
                                       GPUNodeStack *out)
{
  return GPU_stack_link(mat, node, "node_filter_blur", in, out);
}

}  // namespace blender::nodes::node_shader_filter_blur_cc

void register_node_type_sh_filter_blur()
{
  namespace file_ns = blender::nodes::node_shader_filter_blur_cc;

  static blender::bke::bNodeType ntype;

  sh_node_type_base(&ntype, "ShaderNodeFilterBlur", SH_NODE_FILTER_BLUR);
  ntype.ui_name = "Filter Blur";
  ntype.ui_description = "Apply spatial or layered blur filter to texture coordinates or color streams";
  ntype.enum_name_legacy = "FILTER_BLUR";
  ntype.nclass = NODE_CLASS_CONVERTER;
  ntype.declare = file_ns::node_declare;
  ntype.gpu_fn = file_ns::node_shader_gpu_filter_blur;

  blender::bke::node_register_type(ntype);
}
