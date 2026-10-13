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

namespace blender::nodes::node_shader_light_evaluation_cc {

static void node_declare(NodeDeclarationBuilder &b)
{
  b.add_input<decl::Vector>("Position");
  b.add_input<decl::Vector>("Normal");
  b.add_input<decl::Float>("Roughness").default_value(0.5f).min(0.0f).max(1.0f);
  b.add_input<decl::Float>("Size").default_value(1.0f).min(0.0f);
  b.add_input<decl::Float>("Size Y").default_value(1.0f).min(0.0f);
  b.add_input<decl::Float>("IOR").default_value(1.45f).min(0.0f);
  b.add_input<decl::Vector>("Tangent");
  b.add_input<decl::Vector>("BiTangent");

  b.add_output<decl::Color>("Color");
  b.add_output<decl::Float>("Energy");
  b.add_output<decl::Float>("Factor");
  b.add_output<decl::Float>("Light Elevation");
  b.add_output<decl::Float>("Light Spherical Area");
}

static void node_shader_init_light_eval(bNodeTree * /*ntree*/, bNode *node)
{
  /* custom1 packs:
   * bit 0: light_mode (0 = Area, 1 = Point)
   * bits 1..3: function_mode (0 = Cosine Hemisphere, 1 = Cosine Sphere, 2 = Uniform Hemisphere, 3 = Uniform Sphere)
   * bits 4..6: parameter_mode (0 = Reflection, 1 = Refraction, 2 = Custom)
   * bit 7: anisotropy (0 = Off, 1 = On)
   */
  node->custom1 = 0;
}

static void node_shader_draw_light_eval(uiLayout *layout, bContext * /*C*/, PointerRNA *ptr)
{
  layout->prop(ptr, "light_mode", UI_ITEM_R_SPLIT_EMPTY_NAME, "Light Mode", ICON_NONE);
  layout->prop(ptr, "function_mode", UI_ITEM_R_SPLIT_EMPTY_NAME, "Function Mode", ICON_NONE);
  layout->prop(ptr, "parameter_mode", UI_ITEM_R_SPLIT_EMPTY_NAME, "Parameter Mode", ICON_NONE);
  layout->prop(ptr, "anisotropy", UI_ITEM_R_SPLIT_EMPTY_NAME, "Anisotropy", ICON_NONE);
}

static int node_shader_gpu_light_eval(GPUMaterial *mat,
                                      bNode *node,
                                      bNodeExecData * /*execdata*/,
                                      GPUNodeStack *in,
                                      GPUNodeStack *out)
{
  int custom = node->custom1;
  float light_mode = float(custom & 1);
  float function_mode = float((custom >> 1) & 7);
  float param_mode = float((custom >> 4) & 7);
  float anisotropy = float((custom >> 7) & 1);

  return GPU_stack_link(mat,
                        node,
                        "node_light_evaluation",
                        in,
                        out,
                        GPU_constant(&light_mode),
                        GPU_constant(&function_mode),
                        GPU_constant(&param_mode),
                        GPU_constant(&anisotropy));
}

}  // namespace blender::nodes::node_shader_light_evaluation_cc

void register_node_type_sh_light_evaluation()
{
  namespace file_ns = blender::nodes::node_shader_light_evaluation_cc;

  static blender::bke::bNodeType ntype;

  sh_node_type_base(&ntype, "ShaderNodeLightEvaluation", SH_NODE_LIGHT_EVALUATION);
  ntype.ui_name = "Light Evaluation";
  ntype.ui_description = "Evaluate non-photorealistic lighting models (LTC, Toon, Half-Lambert) with surface parameters";
  ntype.enum_name_legacy = "LIGHT_EVALUATION";
  ntype.nclass = NODE_CLASS_CONVERTER;
  ntype.declare = file_ns::node_declare;
  ntype.initfunc = file_ns::node_shader_init_light_eval;
  ntype.draw_buttons = file_ns::node_shader_draw_light_eval;
  ntype.gpu_fn = file_ns::node_shader_gpu_light_eval;

  blender::bke::node_register_type(ntype);
}
