/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

/** \file
 * \ingroup cmpnodes
 */

#include <cmath>

#include "BLI_math_base.hh"
#include "BLI_math_vector.hh"
#include "BLI_math_vector_types.hh"
#include "BLI_task.hh"

#include "DNA_node_types.h"

#include "GPU_shader.hh"

#include "MEM_guardedalloc.h"

#include "UI_interface.hh"
#include "UI_resources.hh"

#include "COM_node_operation.hh"
#include "COM_utilities.hh"

#include "node_composite_util.hh"

namespace blender::nodes::node_composite_fsr1_cc {

using namespace blender::compositor;

static float fsr1_luma(const float3 color)
{
  return color.z * 0.5f + (color.x * 0.5f + color.y);
}

static void fsr1_easu_set(float2 &direction,
                          float &length,
                          const float2 fraction,
                          const int quadrant,
                          const float l_a,
                          const float l_b,
                          const float l_c,
                          const float l_d,
                          const float l_e)
{
  float weight;
  switch (quadrant) {
    case 0:
      weight = (1.0f - fraction.x) * (1.0f - fraction.y);
      break;
    case 1:
      weight = fraction.x * (1.0f - fraction.y);
      break;
    case 2:
      weight = (1.0f - fraction.x) * fraction.y;
      break;
    default:
      weight = fraction.x * fraction.y;
      break;
  }

  const float length_x = math::min(
      math::abs(l_d - l_b) /
          math::max(math::max(math::abs(l_d - l_c), math::abs(l_c - l_b)), 1.0e-8f),
      1.0f);
  direction.x += (l_d - l_b) * weight;
  length += length_x * length_x * weight;

  const float length_y = math::min(
      math::abs(l_e - l_a) /
          math::max(math::max(math::abs(l_e - l_c), math::abs(l_c - l_a)), 1.0e-8f),
      1.0f);
  direction.y += (l_e - l_a) * weight;
  length += length_y * length_y * weight;
}

static void fsr1_easu_tap(float3 &accumulated_color,
                          float &accumulated_weight,
                          const float2 pixel_offset,
                          const float2 direction,
                          const float2 anisotropic_length,
                          const float negative_lobe_strength,
                          const float clipping_point,
                          const float3 color)
{
  float2 rotated_offset = float2(pixel_offset.x * direction.x + pixel_offset.y * direction.y,
                                 pixel_offset.x * -direction.y + pixel_offset.y * direction.x);
  rotated_offset *= anisotropic_length;
  const float distance_squared = math::min(math::dot(rotated_offset, rotated_offset),
                                           clipping_point);
  float weight_b = 0.4f * distance_squared - 1.0f;
  const float weight_a = negative_lobe_strength * distance_squared - 1.0f;
  weight_b = (25.0f / 16.0f) * weight_b * weight_b - (25.0f / 16.0f - 1.0f);
  const float weight = weight_b * weight_a * weight_a;
  accumulated_color += color * weight;
  accumulated_weight += weight;
}

static float4 fsr1_easu_pixel(const Result &input,
                              const int2 output_pixel,
                              const float2 scale_ratio)
{
  const float2 input_position = (float2(output_pixel) + float2(0.5f)) * scale_ratio - float2(0.5f);
  const float2 floored_position = math::floor(input_position);
  const int2 base = int2(floored_position);
  const float2 fraction = input_position - floored_position;
  const float3 b = input.load_pixel_extended<float4>(base + int2(0, -1)).xyz();
  const float3 c = input.load_pixel_extended<float4>(base + int2(1, -1)).xyz();
  const float3 e = input.load_pixel_extended<float4>(base + int2(-1, 0)).xyz();
  const float3 f = input.load_pixel_extended<float4>(base).xyz();
  const float3 g = input.load_pixel_extended<float4>(base + int2(1, 0)).xyz();
  const float3 h = input.load_pixel_extended<float4>(base + int2(2, 0)).xyz();
  const float3 i = input.load_pixel_extended<float4>(base + int2(-1, 1)).xyz();
  const float3 j = input.load_pixel_extended<float4>(base + int2(0, 1)).xyz();
  const float3 k = input.load_pixel_extended<float4>(base + int2(1, 1)).xyz();
  const float3 l = input.load_pixel_extended<float4>(base + int2(2, 1)).xyz();
  const float3 n = input.load_pixel_extended<float4>(base + int2(0, 2)).xyz();
  const float3 o = input.load_pixel_extended<float4>(base + int2(1, 2)).xyz();

  const float b_l = fsr1_luma(b);
  const float c_l = fsr1_luma(c);
  const float e_l = fsr1_luma(e);
  const float f_l = fsr1_luma(f);
  const float g_l = fsr1_luma(g);
  const float h_l = fsr1_luma(h);
  const float i_l = fsr1_luma(i);
  const float j_l = fsr1_luma(j);
  const float k_l = fsr1_luma(k);
  const float l_l = fsr1_luma(l);
  const float n_l = fsr1_luma(n);
  const float o_l = fsr1_luma(o);

  float2 direction(0.0f);
  float length = 0.0f;
  fsr1_easu_set(direction, length, fraction, 0, b_l, e_l, f_l, g_l, j_l);
  fsr1_easu_set(direction, length, fraction, 1, c_l, f_l, g_l, h_l, k_l);
  fsr1_easu_set(direction, length, fraction, 2, f_l, i_l, j_l, k_l, n_l);
  fsr1_easu_set(direction, length, fraction, 3, g_l, j_l, k_l, l_l, o_l);

  const float direction_length_squared = math::dot(direction, direction);
  if (direction_length_squared < (1.0f / 32768.0f)) {
    direction = float2(1.0f, 0.0f);
  }
  else {
    direction *= 1.0f / std::sqrt(direction_length_squared);
  }
  length = 0.25f * length * length;
  const float stretch = math::dot(direction, direction) /
                        math::max(math::max(math::abs(direction.x), math::abs(direction.y)),
                                  1.0e-8f);
  const float2 anisotropic_length(1.0f + (stretch - 1.0f) * length, 1.0f - 0.5f * length);
  const float negative_lobe_strength = 0.5f + ((0.25f - 0.04f) - 0.5f) * length;
  const float clipping_point = 1.0f / negative_lobe_strength;

  const float3 minimum_color = math::min(math::min(f, g), math::min(j, k));
  const float3 maximum_color = math::max(math::max(f, g), math::max(j, k));
  float3 accumulated_color(0.0f);
  float accumulated_weight = 0.0f;
  fsr1_easu_tap(accumulated_color,
                accumulated_weight,
                float2(0.0f, -1.0f) - fraction,
                direction,
                anisotropic_length,
                negative_lobe_strength,
                clipping_point,
                b);
  fsr1_easu_tap(accumulated_color,
                accumulated_weight,
                float2(1.0f, -1.0f) - fraction,
                direction,
                anisotropic_length,
                negative_lobe_strength,
                clipping_point,
                c);
  fsr1_easu_tap(accumulated_color,
                accumulated_weight,
                float2(-1.0f, 1.0f) - fraction,
                direction,
                anisotropic_length,
                negative_lobe_strength,
                clipping_point,
                i);
  fsr1_easu_tap(accumulated_color,
                accumulated_weight,
                float2(0.0f, 1.0f) - fraction,
                direction,
                anisotropic_length,
                negative_lobe_strength,
                clipping_point,
                j);
  fsr1_easu_tap(accumulated_color,
                accumulated_weight,
                float2(0.0f, 0.0f) - fraction,
                direction,
                anisotropic_length,
                negative_lobe_strength,
                clipping_point,
                f);
  fsr1_easu_tap(accumulated_color,
                accumulated_weight,
                float2(-1.0f, 0.0f) - fraction,
                direction,
                anisotropic_length,
                negative_lobe_strength,
                clipping_point,
                e);
  fsr1_easu_tap(accumulated_color,
                accumulated_weight,
                float2(1.0f, 1.0f) - fraction,
                direction,
                anisotropic_length,
                negative_lobe_strength,
                clipping_point,
                k);
  fsr1_easu_tap(accumulated_color,
                accumulated_weight,
                float2(2.0f, 1.0f) - fraction,
                direction,
                anisotropic_length,
                negative_lobe_strength,
                clipping_point,
                l);
  fsr1_easu_tap(accumulated_color,
                accumulated_weight,
                float2(2.0f, 0.0f) - fraction,
                direction,
                anisotropic_length,
                negative_lobe_strength,
                clipping_point,
                h);
  fsr1_easu_tap(accumulated_color,
                accumulated_weight,
                float2(1.0f, 0.0f) - fraction,
                direction,
                anisotropic_length,
                negative_lobe_strength,
                clipping_point,
                g);
  fsr1_easu_tap(accumulated_color,
                accumulated_weight,
                float2(1.0f, 2.0f) - fraction,
                direction,
                anisotropic_length,
                negative_lobe_strength,
                clipping_point,
                o);
  fsr1_easu_tap(accumulated_color,
                accumulated_weight,
                float2(0.0f, 2.0f) - fraction,
                direction,
                anisotropic_length,
                negative_lobe_strength,
                clipping_point,
                n);

  const float3 upscaled_color = math::clamp(
      accumulated_color / math::max(accumulated_weight, 1.0e-8f), minimum_color, maximum_color);
  const float alpha = math::interpolate(
      math::interpolate(input.load_pixel_extended<float4>(base).w,
                        input.load_pixel_extended<float4>(base + int2(1, 0)).w,
                        fraction.x),
      math::interpolate(input.load_pixel_extended<float4>(base + int2(0, 1)).w,
                        input.load_pixel_extended<float4>(base + int2(1, 1)).w,
                        fraction.x),
      fraction.y);
  return float4(upscaled_color, alpha);
}

static float fsr1_safe_divide(const float numerator, const float denominator)
{
  return math::abs(denominator) < 1.0e-8f ? 0.0f : numerator / denominator;
}

static float4 fsr1_rcas_pixel(const Result &input, const int2 pixel, const float sharpness)
{
  const float4 north = input.load_pixel_extended<float4>(pixel + int2(0, -1));
  const float4 west = input.load_pixel_extended<float4>(pixel + int2(-1, 0));
  const float4 center = input.load_pixel_extended<float4>(pixel);
  const float4 east = input.load_pixel_extended<float4>(pixel + int2(1, 0));
  const float4 south = input.load_pixel_extended<float4>(pixel + int2(0, 1));

  const float3 minimum_color = math::min(math::min(north.xyz(), west.xyz()),
                                         math::min(east.xyz(), south.xyz()));
  const float3 maximum_color = math::max(math::max(north.xyz(), west.xyz()),
                                         math::max(east.xyz(), south.xyz()));
  const float3 hit_minimum = float3(fsr1_safe_divide(minimum_color.x, 4.0f * maximum_color.x),
                                    fsr1_safe_divide(minimum_color.y, 4.0f * maximum_color.y),
                                    fsr1_safe_divide(minimum_color.z, 4.0f * maximum_color.z));
  const float3 hit_maximum = float3(
      fsr1_safe_divide(1.0f - maximum_color.x, 4.0f * minimum_color.x - 4.0f),
      fsr1_safe_divide(1.0f - maximum_color.y, 4.0f * minimum_color.y - 4.0f),
      fsr1_safe_divide(1.0f - maximum_color.z, 4.0f * minimum_color.z - 4.0f));
  const float3 lobes = math::max(-hit_minimum, hit_maximum);
  float lobe = math::max(-0.1875f,
                         math::min(math::max(lobes.x, math::max(lobes.y, lobes.z)), 0.0f));
  lobe *= std::exp2(-sharpness);
  const float reciprocal_lobe = 1.0f / math::max(4.0f * lobe + 1.0f, 1.0e-8f);
  const float3 sharpened =
      (lobe * (north.xyz() + west.xyz() + east.xyz() + south.xyz()) + center.xyz()) *
      reciprocal_lobe;
  return float4(sharpened, center.w);
}

static void node_declare(NodeDeclarationBuilder &b)
{
  b.add_input<decl::Color>("Image")
      .default_value({1.0f, 1.0f, 1.0f, 1.0f})
      .compositor_realization_mode(CompositorInputRealizationMode::None);
  b.add_input<decl::Float>("Mask")
      .default_value(1.0f)
      .min(0.0f)
      .max(1.0f)
      .subtype(PROP_FACTOR)
      .compositor_realization_mode(CompositorInputRealizationMode::None);
  b.add_input<decl::Float>("Sharpness")
      .default_value(0.2f)
      .min(0.0f)
      .max(2.0f)
      .compositor_expects_single_value()
      .hide_value();
  b.add_input<decl::Float>("Scale")
      .default_value(2.0f)
      .min(1.0f)
      .max(4.0f)
      .compositor_expects_single_value()
      .hide_value();
  b.add_output<decl::Color>("Image");
}

static void node_init(bNodeTree * /*ntree*/, bNode *node)
{
  NodeFSR1 *data = MEM_callocN<NodeFSR1>(__func__);
  data->sharpness = 0.2f;
  data->scale = 2.0f;
  data->mode = CMP_NODE_FSR1_MODE_FULL;
  node->storage = data;
}

static void node_buts_fsr1(uiLayout *layout, bContext * /*C*/, PointerRNA *ptr)
{
  layout->prop(ptr, "mode", UI_ITEM_R_SPLIT_EMPTY_NAME, "", ICON_NONE);
  const int mode = RNA_enum_get(ptr, "mode");
  if (mode != CMP_NODE_FSR1_MODE_RCAS_ONLY) {
    layout->prop(ptr, "scale", UI_ITEM_R_SPLIT_EMPTY_NAME, "", ICON_NONE);
  }
  if (mode != CMP_NODE_FSR1_MODE_EASU_ONLY) {
    layout->prop(ptr, "sharpness", UI_ITEM_R_SPLIT_EMPTY_NAME, "", ICON_NONE);
  }
}

class FSR1Operation : public NodeOperation {
 public:
  using NodeOperation::NodeOperation;

  void execute() override
  {
    Result &input = get_input("Image");
    Result &mask = get_input("Mask");
    Result &output = get_result("Image");
    if (input.is_single_value()) {
      output.share_data(input);
      return;
    }

    const NodeFSR1 &settings = *static_cast<const NodeFSR1 *>(bnode().storage);
    const int mode = settings.mode;
    const float raw_scale = (mode == CMP_NODE_FSR1_MODE_RCAS_ONLY) ?
                                1.0f :
                                get_parameter("Scale", settings.scale);
    const float scale = (mode == CMP_NODE_FSR1_MODE_RCAS_ONLY) ? 1.0f : raw_scale;
    const float sharpness = get_parameter("Sharpness", settings.sharpness);
    const int2 input_size = input.domain().size;
    const int2 output_size = (mode == CMP_NODE_FSR1_MODE_RCAS_ONLY) ?
                                 input_size :
                                 int2(float2(input_size) * scale + float2(0.5f));
    const float2 input_to_output_scale = float2(input_size) / float2(output_size);

    Domain output_domain = input.domain();
    output_domain.size = output_size;
    /* Increase pixel density without changing the image's compositor-space footprint. */
    float3x3 pixel_scale = float3x3::identity();
    pixel_scale[0][0] = input_to_output_scale.x;
    pixel_scale[1][1] = input_to_output_scale.y;
    output_domain.transformation = input.domain().transformation * pixel_scale;

    output.allocate_texture(output_domain);

    const bool has_mask = node().input_by_identifier("Mask")->is_logically_linked();
    const bool is_gpu = context().use_gpu();

    if (mode == CMP_NODE_FSR1_MODE_RCAS_ONLY) {
      /* RCAS-only pass: run directly on input at native resolution. */
      if (has_mask) {
        Result sharpened = context().create_result(ResultType::Color);
        sharpened.allocate_texture(output_domain);

        if (is_gpu) {
          execute_rcas_gpu(input, sharpened, output_domain, sharpness);
          execute_blend_gpu(input, sharpened, mask, output, output_domain);
        }
        else {
          parallel_for(output_size, [&](const int2 pixel) {
            sharpened.store_pixel(pixel, fsr1_rcas_pixel(input, pixel, sharpness));
          });
          execute_blend_cpu(input, sharpened, mask, output, output_size, false);
        }
        sharpened.release();
      }
      else {
        if (is_gpu) {
          execute_rcas_gpu(input, output, output_domain, sharpness);
        }
        else {
          parallel_for(output_size, [&](const int2 pixel) {
            output.store_pixel(pixel, fsr1_rcas_pixel(input, pixel, sharpness));
          });
        }
      }
    }
    else if (mode == CMP_NODE_FSR1_MODE_EASU_ONLY) {
      /* EASU-only pass: spatial upsampling without sharpening. */
      if (has_mask) {
        Result easu_result = context().create_result(ResultType::Color);
        easu_result.allocate_texture(output_domain);

        if (is_gpu) {
          execute_easu_gpu(input, easu_result, output_domain, input_to_output_scale);
          execute_blend_gpu(input, easu_result, mask, output, output_domain);
        }
        else {
          parallel_for(output_size, [&](const int2 pixel) {
            easu_result.store_pixel(pixel,
                                    fsr1_easu_pixel(input, pixel, input_to_output_scale));
          });
          execute_blend_cpu(input, easu_result, mask, output, output_size, true);
        }
        easu_result.release();
      }
      else {
        if (is_gpu) {
          execute_easu_gpu(input, output, output_domain, input_to_output_scale);
        }
        else {
          parallel_for(output_size, [&](const int2 pixel) {
            output.store_pixel(pixel, fsr1_easu_pixel(input, pixel, input_to_output_scale));
          });
        }
      }
    }
    else {
      /* Full mode: EASU spatial upsampling followed by RCAS sharpening. */
      Result intermediate = context().create_result(ResultType::Color);
      intermediate.allocate_texture(output_domain);

      if (has_mask) {
        Result sharpened = context().create_result(ResultType::Color);
        sharpened.allocate_texture(output_domain);

        if (is_gpu) {
          execute_easu_gpu(input, intermediate, output_domain, input_to_output_scale);
          execute_rcas_gpu(intermediate, sharpened, output_domain, sharpness);
          execute_blend_gpu(input, sharpened, mask, output, output_domain);
        }
        else {
          parallel_for(output_size, [&](const int2 pixel) {
            intermediate.store_pixel(pixel,
                                     fsr1_easu_pixel(input, pixel, input_to_output_scale));
          });
          parallel_for(output_size, [&](const int2 pixel) {
            sharpened.store_pixel(pixel, fsr1_rcas_pixel(intermediate, pixel, sharpness));
          });
          execute_blend_cpu(input, sharpened, mask, output, output_size, true);
        }
        sharpened.release();
      }
      else {
        if (is_gpu) {
          execute_easu_gpu(input, intermediate, output_domain, input_to_output_scale);
          execute_rcas_gpu(intermediate, output, output_domain, sharpness);
        }
        else {
          parallel_for(output_size, [&](const int2 pixel) {
            intermediate.store_pixel(pixel,
                                     fsr1_easu_pixel(input, pixel, input_to_output_scale));
          });
          parallel_for(output_size, [&](const int2 pixel) {
            output.store_pixel(pixel, fsr1_rcas_pixel(intermediate, pixel, sharpness));
          });
        }
      }
      intermediate.release();
    }
  }

 private:
  float get_parameter(const StringRef identifier, const float stored_value)
  {
    if (!node().input_by_identifier(identifier)->is_logically_linked()) {
      return identifier == "Scale" ? math::clamp(stored_value, 1.0f, 4.0f) :
                                     math::clamp(stored_value, 0.0f, 2.0f);
    }
    const float linked_value = get_input(identifier).get_single_value_default(stored_value);
    return identifier == "Scale" ? math::clamp(linked_value, 1.0f, 4.0f) :
                                   math::clamp(linked_value, 0.0f, 2.0f);
  }

  void execute_easu_gpu(const Result &input,
                        Result &output,
                        const Domain &output_domain,
                        const float2 input_to_output_scale)
  {
    GPUShader *easu_shader = context().get_shader("compositor_fsr1_easu");
    GPU_shader_bind(easu_shader);
    GPU_shader_uniform_2fv(easu_shader, "input_to_output_scale", input_to_output_scale);
    input.bind_as_texture(easu_shader, "input_tx");
    output.bind_as_image(easu_shader, "output_img");
    compute_dispatch_threads_at_least(easu_shader, output_domain.size);
    input.unbind_as_texture();
    output.unbind_as_image();
    GPU_shader_unbind();
  }

  void execute_rcas_gpu(const Result &input,
                        Result &output,
                        const Domain &output_domain,
                        const float sharpness)
  {
    GPUShader *rcas_shader = context().get_shader("compositor_fsr1_rcas");
    GPU_shader_bind(rcas_shader);
    GPU_shader_uniform_1f(rcas_shader, "sharpness", sharpness);
    input.bind_as_texture(rcas_shader, "input_tx");
    output.bind_as_image(rcas_shader, "output_img");
    compute_dispatch_threads_at_least(rcas_shader, output_domain.size);
    input.unbind_as_texture();
    output.unbind_as_image();
    GPU_shader_unbind();
  }

  void execute_blend_gpu(const Result &base,
                         const Result &upscaled,
                         const Result &mask,
                         Result &output,
                         const Domain &output_domain)
  {
    GPUShader *blend_shader = context().get_shader("compositor_fsr1_blend");
    GPU_shader_bind(blend_shader);
    base.bind_as_texture(blend_shader, "base_tx");
    upscaled.bind_as_texture(blend_shader, "upscaled_tx");
    mask.bind_as_texture(blend_shader, "mask_tx");
    output.bind_as_image(blend_shader, "output_img");
    compute_dispatch_threads_at_least(blend_shader, output_domain.size);
    base.unbind_as_texture();
    upscaled.unbind_as_texture();
    mask.unbind_as_texture();
    output.unbind_as_image();
    GPU_shader_unbind();
  }

  void execute_blend_cpu(const Result &base,
                         const Result &upscaled,
                         const Result &mask,
                         Result &output,
                         const int2 output_size,
                         const bool resample_base)
  {
    parallel_for(output_size, [&](const int2 pixel) {
      float4 base_color;
      if (resample_base) {
        const float2 uv = (float2(pixel) + float2(0.5f)) / float2(output_size);
        base_color = base.sample_bilinear_extended(uv);
      }
      else {
        base_color = base.load_pixel<float4>(pixel);
      }
      const float4 upscaled_color = upscaled.load_pixel<float4>(pixel);
      float mask_val = 1.0f;
      if (mask.is_single_value()) {
        mask_val = mask.get_single_value_default(1.0f);
      }
      else {
        const float2 uv = (float2(pixel) + float2(0.5f)) / float2(output_size);
        mask_val = mask.sample_bilinear_extended(uv).x;
      }
      const float fac = math::clamp(mask_val, 0.0f, 1.0f);
      output.store_pixel(pixel, math::interpolate(base_color, upscaled_color, fac));
    });
  }
};

static NodeOperation *get_compositor_operation(Context &context, DNode node)
{
  return new FSR1Operation(context, node);
}

}  // namespace blender::nodes::node_composite_fsr1_cc

static void register_node_type_cmp_fsr1()
{
  namespace file_ns = blender::nodes::node_composite_fsr1_cc;

  static blender::bke::bNodeType ntype;

  cmp_node_type_base(&ntype, "CompositorNodeFSR1", CMP_NODE_FSR1);
  ntype.ui_name = "AMD FSR 1";
  ntype.ui_description =
      "Edge-Adaptive Spatial Upsampling (EASU) and Robust Contrast-Adaptive Sharpening (RCAS)";
  ntype.enum_name_legacy = "FSR1";
  ntype.nclass = NODE_CLASS_OP_FILTER;
  ntype.declare = file_ns::node_declare;
  ntype.initfunc = file_ns::node_init;
  ntype.draw_buttons = file_ns::node_buts_fsr1;
  ntype.get_compositor_operation = file_ns::get_compositor_operation;
  blender::bke::node_type_storage(ntype, "NodeFSR1", node_free_standard_storage, node_copy_standard_storage);

  blender::bke::node_register_type(ntype);
}

NOD_REGISTER_NODE(register_node_type_cmp_fsr1)
