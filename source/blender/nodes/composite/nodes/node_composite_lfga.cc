/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "BKE_node.hh"

#include "BLI_math_vector.hh"

#include "GPU_shader.hh"

#include "NOD_composite.hh"

#include "COM_context.hh"
#include "COM_node_operation.hh"
#include "COM_result.hh"

namespace blender::nodes::node_composite_lfga_cc {

static void node_declare(NodeDeclarationBuilder &b)
{
  b.add_input<decl::Color>("Image")
      .default_value({1.0f, 1.0f, 1.0f, 1.0f})
      .compositor_realization_mode(CompositorInputRealizationMode::None);
  b.add_input<decl::Float>("Intensity")
      .default_value(0.15f)
      .min(0.0f)
      .max(1.0f)
      .subtype(PROP_FACTOR);
  b.add_input<decl::Float>("Size")
      .default_value(1.0f)
      .min(0.1f)
      .max(10.0f);
  b.add_input<decl::Float>("Seed")
      .default_value(0.0f)
      .min(0.0f)
      .max(1000.0f);
  b.add_output<decl::Color>("Image");
}

using namespace blender::compositor;

static float lfga_hash_cpu(const float2 p)
{
  float3 p3 = math::fract(float3(p.x, p.y, p.x) * 0.1031f);
  p3 += math::dot(p3, float3(p3.y, p3.z, p3.x) + float3(33.33f));
  return math::fract((p3.x + p3.y) * p3.z);
}

class LFGAOperation : public NodeOperation {
 public:
  using NodeOperation::NodeOperation;

  void execute() override
  {
    Result &input = get_input("Image");
    Result &output = get_result("Image");

    if (input.is_single_value()) {
      output.share_data(input);
      return;
    }

    const Domain domain = compute_domain();
    output.allocate_texture(domain);

    const float intensity = get_input("Intensity").get_single_value_default(0.15f);
    const float grain_size = get_input("Size").get_single_value_default(1.0f);
    const float seed = get_input("Seed").get_single_value_default(0.0f);

    if (context().use_gpu()) {
      GPUShader *shader = context().get_shader("compositor_lfga");
      GPU_shader_bind(shader);
      GPU_shader_uniform_1f(shader, "intensity", intensity);
      GPU_shader_uniform_1f(shader, "grain_size", math::max(grain_size, 0.001f));
      GPU_shader_uniform_1f(shader, "seed", seed);

      input.bind_as_texture(shader, "input_tx");
      output.bind_as_image(shader, "output_img");

      compute_dispatch_threads_at_least(shader, domain.size);

      input.unbind_as_texture();
      output.unbind_as_image();
      GPU_shader_unbind();
    }
    else {
      parallel_for(domain.size, [&](const int2 pixel) {
        const float4 color = input.load_pixel<float4>(pixel);
        const float Y = math::dot(color.xyz(), float3(0.2126f, 0.7152f, 0.0722f));
        const float Y_clamped = math::clamp(Y, 0.0f, 1.0f);
        const float midtone_weight = 4.0f * Y_clamped * (1.0f - Y_clamped);

        const float2 grain_coord = float2(pixel) / math::max(grain_size, 0.001f);
        const float noise = lfga_hash_cpu(grain_coord + float2(seed, seed * 1.6180339f)) - 0.5f;

        const float3 grain_color = math::max(
            color.xyz() + float3(noise * intensity * midtone_weight), float3(0.0f));
        output.store_pixel(pixel, float4(grain_color, color.w));
      });
    }
  }
};

static NodeOperation *get_compositor_operation(Context &context, DNode node)
{
  return new LFGAOperation(context, node);
}

}  // namespace blender::nodes::node_composite_lfga_cc

static void register_node_type_cmp_lfga()
{
  namespace file_ns = blender::nodes::node_composite_lfga_cc;

  static blender::bke::bNodeType ntype;

  cmp_node_type_base(&ntype, "CompositorNodeLFGA", CMP_NODE_LFGA);
  ntype.ui_name = "Linear Film Grain (LFGA)";
  ntype.ui_description =
      "Linear scene-space midtone-weighted procedural film grain applicator (LFGA)";
  ntype.enum_name_legacy = "LFGA";
  ntype.nclass = NODE_CLASS_OP_FILTER;
  ntype.declare = file_ns::node_declare;
  ntype.get_compositor_operation = file_ns::get_compositor_operation;

  blender::bke::node_register_type(ntype);
}

NOD_REGISTER_NODE(register_node_type_cmp_lfga)
