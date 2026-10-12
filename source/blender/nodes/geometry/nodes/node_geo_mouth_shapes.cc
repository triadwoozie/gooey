/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "BKE_audio_analysis.hh"
#include "BKE_mesh.hh"
#include "BKE_scene.hh"

#include "BLI_math_vector.hh"

#include "DEG_depsgraph_query.hh"

#include "node_geometry_util.hh"

#include <algorithm>
#include <cmath>

namespace blender::nodes::node_geo_mouth_shapes_cc {

static void node_declare(NodeDeclarationBuilder &b)
{
  b.use_custom_socket_order();

  /* Geometry In / Out */
  b.add_input<decl::Geometry>("Geometry")
      .description("Character face or mouth geometry to deform for speech lipsync");
  b.add_output<decl::Geometry>("Geometry")
      .description("Deformed character geometry driven by Aubio speech analysis");

  /* Viseme Factor Outputs */
  b.add_output<decl::Float>("Jaw Open")
      .description("Open vowel 'A' factor [0.0 to 1.0] for vertical jaw drop");
  b.add_output<decl::Float>("Lip Wide")
      .description("Wide vowel 'E' factor [0.0 to 1.0] for lateral smile stretch");
  b.add_output<decl::Float>("Lip Round")
      .description("Round vowel 'O' factor [0.0 to 1.0] for forward lip pucker");
  b.add_output<decl::Vector>("Viseme Vector")
      .description("Combined 3D viseme weights: (X=Open, Y=Wide, Z=Round)");

  /* Audio & Speaker Inputs */
  b.add_input<decl::Object>("Speaker")
      .description("Speaker object containing dialog or singing audio stream");

  b.add_input<decl::Float>("Time")
      .default_value(-1.0f)
      .min(-1.0f)
      .description("Playback time in seconds (-1 for current scene time)");

  /* Deformation Region & Falloff */
  b.add_input<decl::Vector>("Mouth Center")
      .default_value(float3(0.0f, 0.0f, 0.0f))
      .description("Local coordinates of mouth / lips center");

  b.add_input<decl::Float>("Influence Radius")
      .default_value(0.25f)
      .min(0.001f)
      .max(10.0f)
      .description("Spherical falloff radius for lipsync vertex displacement");

  /* Viseme Displacement Amplitudes */
  b.add_input<decl::Float>("Jaw Open Scale")
      .default_value(0.04f)
      .min(0.0f)
      .max(1.0f)
      .description("Downward vertical displacement for open vowel 'A' (meters)");

  b.add_input<decl::Float>("Lip Wide Scale")
      .default_value(0.025f)
      .min(0.0f)
      .max(1.0f)
      .description("Lateral horizontal stretch for wide vowel 'E' (meters)");

  b.add_input<decl::Float>("Lip Round Scale")
      .default_value(0.03f)
      .min(0.0f)
      .max(1.0f)
      .description("Forward protrusion displacement for round vowel 'O' (meters)");

  /* Aubio Detection Parameters */
  b.add_input<decl::Float>("Sensitivity")
      .default_value(0.5f)
      .min(0.01f)
      .max(1.0f)
      .description("Aubio formant and onset detection sensitivity");

  b.add_input<decl::Float>("Smoothing")
      .default_value(0.25f)
      .min(0.0f)
      .max(0.95f)
      .description("Temporal attack/decay smoothing to eliminate jitter");
}

static void node_geo_exec(GeoNodeExecParams params)
{
  const Scene *scene = DEG_get_evaluated_scene(params.depsgraph());
  if (!scene) {
    params.set_default_remaining_outputs();
    return;
  }

  const Object *speaker_ob = params.extract_input<Object *>("Speaker");
  float time = params.extract_input<float>("Time");
  if (time < 0.0f) {
    const float scene_ctime = BKE_scene_ctime_get(scene);
    const double frame_rate = double(scene->r.frs_sec) / double(scene->r.frs_sec_base);
    time = float(scene_ctime / frame_rate);
  }

  const float3 mouth_center = params.extract_input<float3>("Mouth Center");
  const float radius = std::max(0.001f, params.extract_input<float>("Influence Radius"));
  const float jaw_open_scale = params.extract_input<float>("Jaw Open Scale");
  const float lip_wide_scale = params.extract_input<float>("Lip Wide Scale");
  const float lip_round_scale = params.extract_input<float>("Lip Round Scale");
  const float sensitivity = params.extract_input<float>("Sensitivity");
  const float smoothing = params.extract_input<float>("Smoothing");

  /* Evaluate Aubio feature extraction */
  AudioAnalysisResult result;
  BKE_audio_analysis_evaluate(scene, speaker_ob, time, sensitivity, smoothing, &result);

  const float jaw_open = result.viseme[0];
  const float lip_wide = result.viseme[1];
  const float lip_round = result.viseme[2];

  GeometrySet geometry_set = params.extract_input<GeometrySet>("Geometry");
  if (geometry_set.has_mesh()) {
    Mesh *mesh = geometry_set.get_mesh_for_write();
    if (mesh) {
      MutableSpan<float3> positions = mesh->vert_positions_for_write();
      const float inv_radius = 1.0f / radius;

      for (float3 &pos : positions) {
        const float3 delta = pos - mouth_center;
        const float dist = math::length(delta);
        if (dist < radius) {
          /* Smoothstep falloff */
          const float r = dist * inv_radius;
          const float weight = (1.0f - r * r * (3.0f - 2.0f * r));

          /* Vertical Jaw Open: -Z displacement */
          pos.z -= jaw_open * jaw_open_scale * weight;

          /* Lateral Lip Wide: spread along X away from mouth center */
          const float sign_x = delta.x >= 0.0f ? 1.0f : -1.0f;
          pos.x += sign_x * lip_wide * lip_wide_scale * weight;

          /* Forward Lip Round: +Y forward protrusion */
          pos.y += lip_round * lip_round_scale * weight;
        }
      }
    }
  }

  params.set_output("Geometry", std::move(geometry_set));
  params.set_output("Jaw Open", jaw_open);
  params.set_output("Lip Wide", lip_wide);
  params.set_output("Lip Round", lip_round);
  params.set_output("Viseme Vector", float3(jaw_open, lip_wide, lip_round));
}

static void node_register()
{
  static blender::bke::bNodeType ntype;

  geo_node_type_base(&ntype, "GeometryNodeMouthShapes");
  ntype.ui_name = "Speech Viseme Deformer";
  ntype.ui_description =
      "Deform character mouth geometry automatically driven by Aubio acoustic speech visemes "
      "(Open 'A', Wide 'E', Round 'O')";
  ntype.nclass = NODE_CLASS_GEOMETRY;
  ntype.geometry_node_execute = node_geo_exec;
  ntype.declare = node_declare;
  blender::bke::node_register_type(ntype);
}
NOD_REGISTER_NODE(node_register)

}  // namespace blender::nodes::node_geo_mouth_shapes_cc
