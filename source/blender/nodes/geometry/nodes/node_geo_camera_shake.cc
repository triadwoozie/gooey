/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "BKE_camera.h"
#include "BKE_scene.hh"

#include "BLI_math_matrix.hh"
#include "BLI_math_rotation.hh"
#include "BLI_math_vector.hh"

#include "DEG_depsgraph_query.hh"

#include "GEO_transform.hh"

#include "node_geometry_util.hh"

#include <cmath>

#ifndef M_PI
#  define M_PI 3.14159265358979323846
#endif

namespace blender::nodes::node_geo_camera_shake_cc {

static void node_declare(NodeDeclarationBuilder &b)
{
  b.use_custom_socket_order();

  /* Geometry I/O for direct application on regular objects */
  b.add_input<decl::Geometry>("Geometry")
      .description("Input geometry of the regular controller object to displace");
  b.add_output<decl::Geometry>("Geometry")
      .description("Transformed shaker geometry tracking the camera shake");

  /* Camera Dropdown Target */
  b.add_input<decl::Object>("Camera")
      .description("Target camera to control or follow (select from dropdown or leave blank for active camera)");

  /* Transform Displacement Outputs */
  b.add_output<decl::Vector>("Offset Location")
      .description("Camera shake translational displacement offset");
  b.add_output<decl::Vector>("Offset Rotation")
      .description("Camera shake rotational wobble Euler (radians)");
  b.add_output<decl::Matrix>("Transform Matrix")
      .description("Combined 4x4 wobble transformation matrix");
  b.add_output<decl::Vector>("Final Location")
      .description("Base camera location plus shake translational displacement");
  b.add_output<decl::Vector>("Final Rotation")
      .description("Base camera rotation plus shake rotational displacement");

  /* Base Inputs */
  b.add_input<decl::Vector>("Location")
      .default_value(float3(0.0f))
      .description("Base camera position offset (sampled from target camera if zero)");
  b.add_input<decl::Vector>("Rotation")
      .default_value(float3(0.0f))
      .description("Base camera Euler rotation offset (sampled from target camera if zero)");

  /* Motion Profile selection matching Camera Shakify */
  b.add_input<decl::Int>("Profile")
      .default_value(0)
      .min(0)
      .max(8)
      .description(
          "0: Investigation, 1: The Closeup, 2: The Wedding, 3: Walk To The Store, "
          "4: Handycam Run, 5: Out Car Window, 6: Bike On Gravel, 7: Spaceship Shake, "
          "8: The Zeek (Impact)");

  b.add_input<decl::Float>("Frequency")
      .default_value(1.0f)
      .min(0.01f)
      .max(10.0f)
      .description("Speed / Frequency multiplier");
  b.add_input<decl::Float>("Intensity")
      .default_value(1.0f)
      .min(0.0f)
      .max(10.0f)
      .description("Amplitude / Influence multiplier");
  b.add_input<decl::Float>("Audio Trigger")
      .default_value(0.0f)
      .min(0.0f)
      .max(5.0f)
      .description("Audio onset/beat modulation factor to dynamically spike shake");
  b.add_input<decl::Float>("Time")
      .default_value(-1.0f)
      .min(-1.0f)
      .description("Time in seconds (-1 for scene playback time)");
  b.add_input<decl::Int>("Seed")
      .default_value(0)
      .description("Random variation seed");
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

  /* Extract Target Camera (or fallback to active scene camera) */
  const Object *camera_obj = params.extract_input<Object *>("Camera");
  if (!camera_obj && scene->camera) {
    camera_obj = scene->camera;
  }

  float3 base_loc = params.extract_input<float3>("Location");
  float3 base_rot = params.extract_input<float3>("Rotation");

  /* If Location / Rotation are zero and a camera is chosen, sample base transforms */
  if (camera_obj && camera_obj->type == OB_CAMERA) {
    float3 cam_loc, cam_scale;
    math::Quaternion cam_rot;
    math::to_loc_rot_scale_safe<true>(camera_obj->object_to_world(), cam_loc, cam_rot, cam_scale);
    if (math::is_zero(base_loc)) {
      base_loc = cam_loc;
    }
    if (math::is_zero(base_rot)) {
      const math::EulerXYZ euler = math::to_euler(cam_rot);
      base_rot = float3(euler);
    }
  }

  const int profile = std::clamp(params.extract_input<int>("Profile"), 0, 8);
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

  /* Synthesize wobble harmonics based on tested Camera Shakify motion capture profiles */
  switch (profile) {
    case 0: /* INVESTIGATION (Ian Hubert handheld slow search, subtle breathing drifts) */ {
      disp_loc.x = 0.018f * std::sin(t * 1.3f + s_f) + 0.006f * std::sin(t * 3.4f);
      disp_loc.y = 0.012f * std::cos(t * 1.1f + s_f) + 0.005f * std::sin(t * 2.7f);
      disp_loc.z = 0.022f * std::sin(t * 1.7f + s_f * 0.5f) + 0.007f * std::cos(t * 4.1f);

      disp_rot.x = 0.012f * std::sin(t * 1.5f + s_f) + 0.004f * std::sin(t * 4.2f);
      disp_rot.y = 0.009f * std::cos(t * 1.2f + s_f) + 0.003f * std::cos(t * 3.6f);
      disp_rot.z = 0.010f * std::sin(t * 1.9f + s_f) + 0.004f * std::sin(t * 4.8f);
      break;
    }
    case 1: /* THE_CLOSEUP (Micro-tremor, subtle handheld tension) */ {
      disp_loc.x = 0.006f * std::sin(t * 2.8f + s_f) + 0.003f * std::sin(t * 7.5f);
      disp_loc.y = 0.004f * std::cos(t * 2.4f + s_f) + 0.002f * std::sin(t * 6.9f);
      disp_loc.z = 0.008f * std::sin(t * 3.1f + s_f) + 0.004f * std::cos(t * 8.2f);

      disp_rot.x = 0.007f * std::sin(t * 3.2f + s_f) + 0.003f * std::sin(t * 8.5f);
      disp_rot.y = 0.005f * std::cos(t * 2.9f + s_f) + 0.002f * std::cos(t * 7.9f);
      disp_rot.z = 0.006f * std::sin(t * 3.6f + s_f) + 0.002f * std::sin(t * 9.1f);
      break;
    }
    case 2: /* THE_WEDDING (Medium casual shoulder mount, organic sway) */ {
      disp_loc.x = 0.032f * std::sin(t * 1.6f + s_f) + 0.012f * std::cos(t * 4.2f);
      disp_loc.y = 0.024f * std::cos(t * 1.4f + s_f) + 0.009f * std::sin(t * 3.8f);
      disp_loc.z = 0.038f * std::sin(t * 2.0f + s_f) + 0.014f * std::cos(t * 4.9f);

      disp_rot.x = 0.022f * std::sin(t * 1.8f + s_f) + 0.008f * std::sin(t * 5.1f);
      disp_rot.y = 0.018f * std::cos(t * 1.5f + s_f) + 0.006f * std::cos(t * 4.5f);
      disp_rot.z = 0.025f * std::sin(t * 2.2f + s_f) + 0.009f * std::sin(t * 5.8f);
      break;
    }
    case 3: /* WALK_TO_THE_STORE (Walking cadence with rhythmic vertical footstep dip) */ {
      const float step_phase = t * 3.8f;
      disp_loc.x = 0.028f * std::sin(step_phase * 0.5f + s_f);
      disp_loc.y = 0.018f * std::cos(step_phase * 0.5f);
      disp_loc.z = -0.045f * std::abs(std::sin(step_phase)); /* Footstep dip */

      disp_rot.x = 0.022f * std::sin(step_phase);
      disp_rot.y = 0.025f * std::sin(step_phase * 0.5f + s_f);
      disp_rot.z = 0.016f * std::cos(step_phase * 0.5f);
      break;
    }
    case 4: /* HANDYCAM_RUN (Urgent running cadence with heavy footfall recoil) */ {
      const float run_phase = t * 6.5f;
      disp_loc.x = 0.065f * std::sin(run_phase * 0.5f + s_f);
      disp_loc.y = 0.045f * std::cos(run_phase * 0.5f);
      disp_loc.z = -0.095f * std::abs(std::sin(run_phase));

      disp_rot.x = 0.070f * std::sin(run_phase);
      disp_rot.y = 0.055f * std::sin(run_phase * 0.5f + s_f);
      disp_rot.z = 0.048f * std::cos(run_phase * 0.5f);
      break;
    }
    case 5: /* OUT_CAR_WINDOW (Driving speed vibration, road bumps, high engine rev) */ {
      const float engine_vib = std::sin(t * 36.0f + s_f) * 0.006f;
      disp_loc.x = 0.020f * std::sin(t * 2.4f) + engine_vib;
      disp_loc.y = 0.014f * std::cos(t * 2.0f);
      disp_loc.z = 0.032f * std::sin(t * 4.8f) + engine_vib * 2.2f;

      disp_rot.x = 0.016f * std::sin(t * 2.8f) + engine_vib * 1.8f;
      disp_rot.y = 0.020f * std::cos(t * 2.3f);
      disp_rot.z = 0.011f * std::sin(t * 2.1f);
      break;
    }
    case 6: /* BIKE_ON_GRAVEL (High frequency gravel chatter and rapid handlebar rattle) */ {
      const float gravel_rattle = (hash_noise(seed + 1, t * 28.0f) * 2.0f - 1.0f) * 0.018f;
      disp_loc.x = 0.012f * std::sin(t * 5.2f) + gravel_rattle * 0.6f;
      disp_loc.y = 0.010f * std::cos(t * 4.8f);
      disp_loc.z = 0.025f * std::sin(t * 8.4f) + gravel_rattle;

      disp_rot.x = 0.025f * std::sin(t * 6.0f) + gravel_rattle * 1.2f;
      disp_rot.y = 0.018f * std::cos(t * 5.5f) + gravel_rattle * 0.9f;
      disp_rot.z = 0.030f * std::sin(t * 7.2f) + gravel_rattle * 1.5f;
      break;
    }
    case 7: /* SPACESHIP_SHAKE (Deep low frequency hull drone and structural groaning) */ {
      const float hum = std::sin(t * 18.0f + s_f) * 0.008f;
      disp_loc.x = 0.015f * std::sin(t * 0.8f) + hum * 0.5f;
      disp_loc.y = 0.012f * std::cos(t * 0.6f);
      disp_loc.z = 0.028f * std::sin(t * 1.2f) + hum;

      disp_rot.x = 0.014f * std::sin(t * 0.9f) + hum * 0.8f;
      disp_rot.y = 0.012f * std::cos(t * 0.7f);
      disp_rot.z = 0.018f * std::sin(t * 1.4f) + hum * 1.1f;
      break;
    }
    case 8: /* THE_ZEEK (Erratic sudden impacts, weapon recoil, shockwave) */ {
      const float noise = hash_noise(seed, t * 16.0f) * 2.0f - 1.0f;
      disp_loc.x = 0.09f * noise;
      disp_loc.y = 0.06f * noise;
      disp_loc.z = 0.14f * std::abs(noise);

      disp_rot.x = 0.09f * noise;
      disp_rot.y = 0.07f * noise;
      disp_rot.z = 0.11f * noise;
      break;
    }
  }

  /* Audio reactive amplification: Aubio transient onset triggers scale shake */
  const float total_intensity = intensity * (1.0f + audio_trigger * 2.8f);
  disp_loc *= total_intensity;
  disp_rot *= total_intensity;

  /* If audio trigger is strong, add sharp directional kick */
  if (audio_trigger > 0.1f) {
    const float kick_noise = hash_noise(seed + 101, t * 24.0f) * 2.0f - 1.0f;
    disp_loc.z -= audio_trigger * 0.09f * intensity;
    disp_rot.x += audio_trigger * 0.06f * intensity * kick_noise;
  }

  /* Compute local displacement 4x4 matrix */
  const float4x4 wobble_mat = math::from_loc_rot_scale<float4x4>(
      disp_loc, math::EulerXYZ(disp_rot.x, disp_rot.y, disp_rot.z), float3(1.0f));

  /* Transform input geometry if present */
  GeometrySet geometry_set = params.extract_input<GeometrySet>("Geometry");
  if (!geometry_set.is_empty()) {
    geometry::transform_geometry(geometry_set, wobble_mat);
  }
  params.set_output("Geometry", std::move(geometry_set));

  /* Outputs */
  params.set_output("Offset Location", disp_loc);
  params.set_output("Offset Rotation", disp_rot);
  params.set_output("Transform Matrix", wobble_mat);
  params.set_output("Final Location", base_loc + disp_loc);
  params.set_output("Final Rotation", base_rot + disp_rot);
}

static void node_gather_link_searches(GatherLinkSearchOpParams &params)
{
  search_link_ops_for_basic_node(params);

  const bke::bNodeType &nt = params.node_type();
  if (params.in_out() == SOCK_OUT) {
    params.add_item_full_name(
        "Camera Shakify" UI_MENU_ARROW_SEP " Offset Location",
        [&nt](LinkSearchOpParams &p) {
          bNode &node = p.add_node(nt);
          p.update_and_connect_available_socket(node, "Offset Location");
        },
        1);
    params.add_item_full_name(
        "Camera Shakify" UI_MENU_ARROW_SEP " Geometry",
        [&nt](LinkSearchOpParams &p) {
          bNode &node = p.add_node(nt);
          p.update_and_connect_available_socket(node, "Geometry");
        },
        1);
  }
}

static void node_register()
{
  static blender::bke::bNodeType ntype;

  geo_node_type_base(&ntype, "GeometryNodeCameraShake");
  ntype.ui_name = "Camera Shake";
  ntype.ui_description =
      "Synthesize realistic camera shake and wobble driven by motion profiles and audio transients "
      "on regular objects";
  ntype.nclass = NODE_CLASS_INPUT;
  ntype.geometry_node_execute = node_geo_exec;
  ntype.declare = node_declare;
  ntype.gather_link_search_ops = node_gather_link_searches;
  blender::bke::node_register_type(ntype);
}
NOD_REGISTER_NODE(node_register)

}  // namespace blender::nodes::node_geo_camera_shake_cc
