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

namespace blender::nodes::node_composite_tepd_cc {

static void node_declare(NodeDeclarationBuilder &b)
{
  b.add_input<decl::Color>("Image")
      .default_value({1.0f, 1.0f, 1.0f, 1.0f})
      .compositor_realization_mode(CompositorInputRealizationMode::None);
  b.add_input<decl::Float>("Bit Depth")
      .default_value(8.0f)
      .min(4.0f)
      .max(16.0f);
  b.add_input<decl::Float>("Frame")
      .default_value(1.0f)
      .min(0.0f)
      .max(100000.0f);
  b.add_output<decl::Color>("Image");
}

using namespace blender::compositor;

static float tepd_hash_cpu(const float2 p)
{
  float3 p3 = math::fract(float3(p.x, p.y, p.x) * 0.1031f);
  p3 += math::dot(p3, float3(p3.y, p3.z, p3.x) + float3(33.33f));
  return math::fract((p3.x + p3.y) * p3.z);
}

class TEPDOperation : public NodeOperation {
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

    const float bit_depth = get_input("Bit Depth").get_single_value_default(8.0f);
    const float frame_idx = get_input("Frame").get_single_value_default(1.0f);

    if (context().use_gpu()) {
      GPUShader *shader = context().get_shader("compositor_tepd");
      GPU_shader_bind(shader);
      GPU_shader_uniform_1f(shader, "bit_depth", bit_depth);
      GPU_shader_uniform_1f(shader, "frame_idx", frame_idx);

      input.bind_as_texture(shader, "input_tx");
      output.bind_as_image(shader, "output_img");

      compute_dispatch_threads_at_least(shader, domain.size);

      input.unbind_as_texture();
      output.unbind_as_image();
      GPU_shader_unbind();
    }
    else {
      const float phi = 0.618033988749895f;
      const float phase = math::fract(frame_idx * phi);
      const float steps = std::exp2(math::clamp(bit_depth, 4.0f, 16.0f)) - 1.0f;
      const float step_size = 1.0f / math::max(steps, 1.0f);

      parallel_for(domain.size, [&](const int2 pixel) {
        const float4 color = input.load_pixel<float4>(pixel);

        const float u1 = math::fract(
            tepd_hash_cpu(float2(pixel) * 0.1234f + float2(1.0f, 7.0f)) + phase);
        const float u2 = math::fract(
            tepd_hash_cpu(float2(pixel) * 0.5678f + float2(3.0f, 11.0f)) +
            math::fract(phase * (1.0f + phi)));
        const float tpdf = u1 - u2;

        float3 dithered = color.xyz() + float3(tpdf * step_size);
        dithered = math::floor(dithered * steps + 0.5f) * step_size;
        dithered = math::clamp(dithered, float3(0.0f), float3(1.0f));

        output.store_pixel(pixel, float4(dithered, color.w));
      });
    }
  }
};

static NodeOperation *get_compositor_operation(Context &context, DNode node)
{
  return new TEPDOperation(context, node);
}

}  // namespace blender::nodes::node_composite_tepd_cc

static void register_node_type_cmp_tepd()
{
  namespace file_ns = blender::nodes::node_composite_tepd_cc;

  static blender::bke::bNodeType ntype;

  cmp_node_type_base(&ntype, "CompositorNodeTEPD", CMP_NODE_TEPD);
  ntype.ui_name = "Temporal Dither (TEPD)";
  ntype.ui_description =
      "Temporal energy preserving dither using low-discrepancy Weyl sequence phase shifts and TPDF";
  ntype.enum_name_legacy = "TEPD";
  ntype.nclass = NODE_CLASS_OP_FILTER;
  ntype.declare = file_ns::node_declare;
  ntype.get_compositor_operation = file_ns::get_compositor_operation;

  blender::bke::node_register_type(ntype);
}

NOD_REGISTER_NODE(register_node_type_cmp_tepd)
