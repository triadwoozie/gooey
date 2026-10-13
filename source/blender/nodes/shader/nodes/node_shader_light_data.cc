/* SPDX-FileCopyrightText: 2026 Goo Engine Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup shdnodes
 */

#include "DNA_light_types.h"
#include "DNA_object_types.h"

#include "BKE_context.hh"
#include "BKE_node.hh"
#include "BKE_object.hh"

#include "GPU_material.hh"
#include "UI_interface.hh"

#include "node_shader_util.hh"

namespace blender::nodes::node_shader_light_data_cc {

static void node_declare(NodeDeclarationBuilder &b)
{
  b.add_output<decl::Color>("Color");
  b.add_output<decl::Float>("Power");
  b.add_output<decl::Vector>("Position");
  b.add_output<decl::Vector>("Direction");
  b.add_output<decl::Float>("Distance");
  b.add_output<decl::Float>("Attenuation");
}

static void node_shader_draw_light_data(uiLayout *layout, bContext * /*C*/, PointerRNA *ptr)
{
  layout->prop(ptr, "light_object", UI_ITEM_R_SPLIT_EMPTY_NAME, "Light", ICON_LIGHT);
}

static int node_shader_gpu_light_data(GPUMaterial *mat,
                                      bNode *node,
                                      bNodeExecData * /*execdata*/,
                                      GPUNodeStack *in,
                                      GPUNodeStack *out)
{
  Object *ob = (Object *)node->id;

  static float default_color[4] = {1.0f, 1.0f, 1.0f, 1.0f};
  static float default_power = 100.0f;
  static float default_pos[3] = {0.0f, 0.0f, 5.0f};
  static float default_dir[3] = {0.0f, 0.0f, -1.0f};

  GPUNodeLink *color_link;
  GPUNodeLink *power_link;
  GPUNodeLink *pos_link;
  GPUNodeLink *dir_link;

  if (ob && ob->type == OB_LAMP && ob->data != nullptr) {
    Light *light = (Light *)ob->data;
    float color[4] = {light->r, light->g, light->b, 1.0f};
    float power = light->energy;
    float pos[3] = {ob->loc[0], ob->loc[1], ob->loc[2]};
    float dir[3] = {0.0f, 0.0f, -1.0f};

    color_link = GPU_uniform(color);
    power_link = GPU_uniform(&power);
    pos_link = GPU_uniform(pos);
    dir_link = GPU_uniform(dir);
  }
  else {
    color_link = GPU_uniform(default_color);
    power_link = GPU_uniform(&default_power);
    pos_link = GPU_uniform(default_pos);
    dir_link = GPU_uniform(default_dir);
  }

  return GPU_stack_link(mat,
                        node,
                        "node_light_data",
                        in,
                        out,
                        color_link,
                        power_link,
                        pos_link,
                        dir_link);
}

}  // namespace blender::nodes::node_shader_light_data_cc

void register_node_type_sh_light_data()
{
  namespace file_ns = blender::nodes::node_shader_light_data_cc;

  static blender::bke::bNodeType ntype;

  sh_node_type_base(&ntype, "ShaderNodeLightData", SH_NODE_LIGHT_DATA);
  ntype.ui_name = "Light Data";
  ntype.ui_description = "Retrieve parameters from evaluating lights in the stylized lighting graph";
  ntype.enum_name_legacy = "LIGHT_DATA";
  ntype.nclass = NODE_CLASS_INPUT;
  ntype.declare = file_ns::node_declare;
  ntype.draw_buttons = file_ns::node_shader_draw_light_data;
  ntype.gpu_fn = file_ns::node_shader_gpu_light_data;

  blender::bke::node_register_type(ntype);
}
