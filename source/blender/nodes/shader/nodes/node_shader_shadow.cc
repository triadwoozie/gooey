/* SPDX-FileCopyrightText: 2026 Goo Engine Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup shdnodes
 */

#include "BKE_context.hh"
#include "BKE_node.hh"

#include "GPU_material.hh"
#include "UI_interface.hh"

#include "node_shader_util.hh"

namespace blender::nodes::node_shader_shadow_cc {

static void node_declare(NodeDeclarationBuilder &b)
{
  b.add_input<decl::Vector>("Position");
  b.add_input<decl::Vector>("Direction");
  b.add_input<decl::Float>("Length").default_value(100.0f).min(0.0f);
  b.add_input<decl::Float>("Spread").default_value(0.0f).min(0.0f).max(1.0f);
  b.add_output<decl::Color>("Color");
}

static void node_shader_init_shadow(bNodeTree * /*ntree*/, bNode *node)
{
  node->custom1 = 1; /* ray_count default */
}

static void node_shader_draw_shadow(uiLayout *layout, bContext * /*C*/, PointerRNA *ptr)
{
  layout->prop(ptr, "ray_count", UI_ITEM_R_SPLIT_EMPTY_NAME, "Ray Count", ICON_NONE);
}

static int node_shader_gpu_shadow(GPUMaterial *mat,
                                  bNode *node,
                                  bNodeExecData * /*execdata*/,
                                  GPUNodeStack *in,
                                  GPUNodeStack *out)
{
  float ray_count = float(node->custom1 > 0 ? node->custom1 : 1);
  return GPU_stack_link(mat, node, "node_shadow", in, out, GPU_constant(&ray_count));
}

}  // namespace blender::nodes::node_shader_shadow_cc

void register_node_type_sh_shadow()
{
  namespace file_ns = blender::nodes::node_shader_shadow_cc;

  static blender::bke::bNodeType ntype;

  sh_node_type_base(&ntype, "ShaderNodeShadow", SH_NODE_SHADOW);
  ntype.ui_name = "Shadow";
  ntype.ui_description = "Evaluate dedicated cone-filtered shadow casting for stylized shading";
  ntype.enum_name_legacy = "SHADOW";
  ntype.nclass = NODE_CLASS_INPUT;
  ntype.declare = file_ns::node_declare;
  ntype.initfunc = file_ns::node_shader_init_shadow;
  ntype.draw_buttons = file_ns::node_shader_draw_shadow;
  ntype.gpu_fn = file_ns::node_shader_gpu_shadow;

  blender::bke::node_register_type(ntype);
}
