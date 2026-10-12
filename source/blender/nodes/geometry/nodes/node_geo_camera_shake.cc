/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "BKE_scene.hh"

#include "BLI_math_matrix.hh"
#include "BLI_math_rotation.hh"
#include "BLI_math_vector.hh"

#include "DEG_depsgraph_query.hh"

#include "node_geometry_util.hh"

#include <cmath>

#ifndef M_PI
#  define M_PI 3.14159265358979323846
#endif

namespace blender::nodes::node_geo_camera_shake_cc {

static void node_declare(NodeDeclarationBuilder &b)
{
  b.use_custom_socket_order();

  /* Outputs */
  b.add_output<decl::Vector>("Offset Location").description("Camera shake translational displacement");
  b.add_output<decl::Vector>("Offset Rotation").description("Camera shake rotational wobble Euler (radians)");
  b.add_output<decl::Matrix>("Transform Matrix").description("Combined 4x4 wobble transformation matrix");

  /* Inputs */
  b.add_input<decl::Vector>("Location").default_value(float3(0.0f)).description("Base camera position");
  b.add_input<decl::Vector>("Rotation").default_value(float3(0.0f)).description("Base camera Euler rotation");
  b.add_input<decl::Int>("Profile")
      .default_value(0)
      .min(0)
      .max(5)
      .description("0: Handheld Subtle, 1: Handheld Action, 2: Walking, 3: Running, 4: Driving, 5: Impact");
  b.add_input<decl::Float>("Frequency").default_value(1.0f).min(0.05f).max(10.0f).description("Speed multiplier");
  b.add_input<decl::Float>("Intensity").default_value(1.0f).min(0.0f).max(10.0f).description("Amplitude multiplier");
  b.add_input<decl::Float>("Audio Trigger")
      .default_value(0.0f)
      .min(0.0f)
      .max(5.0f)
      .description("Audio onset/beat modulation factor to dynamically spike shake");
  b.add_input<decl::Float>("Time")
      .default_value(-1.0f)
      .min(-1.0f)
      .description("Time in seconds (-1 for scene playback time)");
  b.add_input<decl::Int>("Seed").default_value(0).description("Random variation seed");
}

/* Pseudo-random hash helper */
static float hash_noise(int seed, float t)
{
  float n = std::sin(t * 12.9898f + float(seed) * 78.233f) * 43758.5453f;
  return n - std::floor(n);
}

static void node_geo_exec(GeoNodeExecParams params)
{
  const Scene *scene = DEG_get_evaluated_scene(params.depsgraph());
  if (!scene) {
    params.set_default_remaining_outputs();
    return;
  }

  const float3 base_loc = params.extract_input<float3>("Location");
  const float3 base_rot = params.extract_input<float3>("Rotation");
  const int profile = std::clamp(params.extract_input<int>("Profile"), 0, 5);
  const float freq = std::max(0.01f, params.extract_input<float>("Frequency"));
  const float intensity = std::max(0.0f, params.extract_input<float>("Intensity"));
  const float audio_trigger = std::max(0.0f, params.extract_input<float>("Audio Trigger"));
  float time = params.extract_input<float>("Time");
  const int seed = params.extract_input<int>("Seed");

  if (time < 0.0f) {
    const float scene_ctime = BKE_scene_ctime_get(scene);
    const double frame_rate = double(scene->r.frs_sec) / double(scene->r.frs_sec_base);
    time = float(scene_ctime / frame_rate);
  }

  const float t = time * freq;
  const float s_f = float(seed) * 1.618f;

  float3 disp_loc(0.0f);
  float3 disp_rot(0.0f);

  /* Synthesize wobble harmonics based on Shakify motion capture profiles */
  switch (profile) {
    case 0: /* Handheld Subtle (Slow breathing and micro-tremor) */ {
      disp_loc.x = 0.012f * std::sin(t * 1.4f + s_f) + 0.005f * std::sin(t * 3.8f + s_f * 2.0f);
      disp_loc.y = 0.008f * std::cos(t * 1.1f + s_f) + 0.004f * std::sin(t * 2.7f);
      disp_loc.z = 0.015f * std::sin(t * 1.8f + s_f * 0.5f) + 0.006f * std::cos(t * 4.2f);

      disp_rot.x = 0.008f * std::sin(t * 1.7f + s_f) + 0.003f * std::sin(t * 4.5f);
      disp_rot.y = 0.006f * std::cos(t * 1.3f + s_f) + 0.002f * std::cos(t * 3.9f);
      disp_rot.z = 0.007f * std::sin(t * 2.1f + s_f) + 0.003f * std::sin(t * 5.1f);
      break;
    }
    case 1: /* Handheld Action (Urgent operator motion) */ {
      disp_loc.x = 0.045f * std::sin(t * 3.2f + s_f) + 0.020f * std::cos(t * 6.8f);
      disp_loc.y = 0.030f * std::cos(t * 2.9f + s_f) + 0.015f * std::sin(t * 5.4f);
      disp_loc.z = 0.050f * std::sin(t * 3.7f + s_f) + 0.022f * std::cos(t * 7.1f);

      disp_rot.x = 0.035f * std::sin(t * 3.5f + s_f) + 0.012f * std::sin(t * 8.2f);
      disp_rot.y = 0.025f * std::cos(t * 2.8f + s_f) + 0.010f * std::cos(t * 7.6f);
      disp_rot.z = 0.040f * std::sin(t * 4.1f + s_f) + 0.015f * std::sin(t * 9.0f);
      break;
    }
    case 2: /* Walking (Rhythmic cadence and vertical bob) */ {
      const float step_phase = t * 3.6f;
      disp_loc.x = 0.025f * std::sin(step_phase * 0.5f + s_f);
      disp_loc.y = 0.015f * std::cos(step_phase * 0.5f);
      disp_loc.z = -0.040f * std::abs(std::sin(step_phase)); /* Footstep dip */

      disp_rot.x = 0.018f * std::sin(step_phase);
      disp_rot.y = 0.022f * std::sin(step_phase * 0.5f + s_f);
      disp_rot.z = 0.014f * std::cos(step_phase * 0.5f);
      break;
    }
    case 3: /* Running (Aggressive strides and impact recoil) */ {
      const float run_phase = t * 6.2f;
      disp_loc.x = 0.060f * std::sin(run_phase * 0.5f + s_f);
      disp_loc.y = 0.040f * std::cos(run_phase * 0.5f);
      disp_loc.z = -0.090f * std::abs(std::sin(run_phase));

      disp_rot.x = 0.065f * std::sin(run_phase);
      disp_rot.y = 0.050f * std::sin(run_phase * 0.5f + s_f);
      disp_rot.z = 0.045f * std::cos(run_phase * 0.5f);
      break;
    }
    case 4: /* Driving / Vehicle (High-frequency engine buzz and bumps) */ {
      const float engine_vib = std::sin(t * 32.0f + s_f) * 0.005f;
      disp_loc.x = 0.015f * std::sin(t * 2.2f) + engine_vib;
      disp_loc.y = 0.010f * std::cos(t * 1.8f);
      disp_loc.z = 0.025f * std::sin(t * 4.5f) + engine_vib * 2.0f;

      disp_rot.x = 0.012f * std::sin(t * 2.6f) + engine_vib * 1.5f;
      disp_rot.y = 0.015f * std::cos(t * 2.1f);
      disp_rot.z = 0.008f * std::sin(t * 1.9f);
      break;
    }
    case 5: /* Impact / Explosion */ {
      const float noise = hash_noise(seed, t * 15.0f) * 2.0f - 1.0f;
      disp_loc.x = 0.08f * noise;
      disp_loc.y = 0.05f * noise;
      disp_loc.z = 0.12f * std::abs(noise);

      disp_rot.x = 0.08f * noise;
      disp_rot.y = 0.06f * noise;
      disp_rot.z = 0.09f * noise;
      break;
    }
  }

  /* Audio reactive amplification:
   * Audio transient triggers directly scale the wobble amplitude */
  const float total_intensity = intensity * (1.0f + audio_trigger * 2.5f);
  disp_loc *= total_intensity;
  disp_rot *= total_intensity;

  /* If audio trigger is strong, add sharp kick */
  if (audio_trigger > 0.1f) {
    const float kick_noise = hash_noise(seed + 101, t * 24.0f) * 2.0f - 1.0f;
    disp_loc.z -= audio_trigger * 0.08f * intensity;
    disp_rot.x += audio_trigger * 0.05f * intensity * kick_noise;
  }

  const float3 final_loc = base_loc + disp_loc;
  const float3 final_rot = base_rot + disp_rot;

  /* Compose transform matrix */
  float4x4 mat = math::from_loc_rot_scale<float4x4>(
      final_loc, math::EulerXYZ(final_rot.x, final_rot.y, final_rot.z), float3(1.0f));

  params.set_output("Offset Location", disp_loc);
  params.set_output("Offset Rotation", disp_rot);
  params.set_output("Transform Matrix", mat);
}

static void node_register()
{
  static blender::bke::bNodeType ntype;

  geo_node_type_base(&ntype, "GeometryNodeCameraShake");
  ntype.ui_name = "Camera Shake";
  ntype.ui_description = "Synthesize realistic camera shake and wobble driven by motion profiles and audio transients";
  ntype.nclass = NODE_CLASS_INPUT;
  ntype.geometry_node_execute = node_geo_exec;
  ntype.declare = node_declare;
  blender::bke::node_register_type(ntype);
}
NOD_REGISTER_NODE(node_register)

}  // namespace blender::nodes::node_geo_camera_shake_cc
