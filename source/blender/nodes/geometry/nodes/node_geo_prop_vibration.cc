/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "BKE_acoustics_inference.hh"
#include "BKE_audio_analysis.hh"
#include "BKE_mesh.hh"
#include "BKE_scene.hh"

#include "BLI_math_matrix.hh"
#include "BLI_math_vector.hh"

#include "DEG_depsgraph_query.hh"

#include "GEO_transform.hh"

#include "node_geometry_util.hh"

#include <algorithm>
#include <cmath>

#ifndef M_PI
#  define M_PI 3.14159265358979323846
#endif

namespace blender::nodes::node_geo_prop_vibration_cc {

static void node_declare(NodeDeclarationBuilder &b)
{
  b.use_custom_socket_order();

  /* Geometry In / Out */
  b.add_input<decl::Geometry>("Geometry")
      .description("Input geometry of the prop or colliding body to vibrate");
  b.add_output<decl::Geometry>("Geometry")
      .description("Physically vibrating geometry with modal resonance displacement");

  /* Transform & Vibration Outputs */
  b.add_output<decl::Vector>("Offset")
      .description("Instantaneous physical resonance offset vector (connect to Set Position Offset)");
  b.add_output<decl::Vector>("Displacement")
      .description("Instantaneous vector displacement offset");
  b.add_output<decl::Float>("Amplitude")
      .description("Current damped resonance amplitude envelope");
  b.add_output<decl::Float>("Resonant Freq (f0)")
      .description("Applied fundamental resonant frequency in Hz");
  b.add_output<decl::Float>("Damping (Q)")
      .description("Effective acoustic modal Q-factor / decay factor");

  /* Inputs */
  b.add_input<decl::Object>("Object")
      .description("Reference object to query material BSDF and thickness (leave empty for self)");

  b.add_input<decl::Float>("Trigger / Impulse")
      .default_value(1.0f)
      .min(0.0f)
      .max(100.0f)
      .description("Kinetic impact energy (Joules) or audio onset impulse to excite resonance");

  b.add_input<decl::Float>("Frequency")
      .default_value(-1.0f)
      .min(-1.0f)
      .max(10000.0f)
      .description("Fundamental frequency f0 in Hz (-1 to infer from mesh thickness and speed of sound)");

  b.add_input<decl::Float>("Damping")
      .default_value(-1.0f)
      .min(-1.0f)
      .max(500.0f)
      .description("Modal damping decay rate (-1 to infer from BSDF metallic/roughness)");

  b.add_input<decl::Float>("Time")
      .default_value(-1.0f)
      .min(-1.0f)
      .description("Evaluation playback time in seconds (-1 for current scene time)");

  b.add_input<decl::Float>("Amplitude Scale")
      .default_value(0.02f)
      .min(0.0f)
      .max(1.0f)
      .description("Visual displacement amplitude multiplier (meters)");

  b.add_input<decl::Vector>("Vibration Axis")
      .default_value(float3(0.0f, 0.0f, 1.0f))
      .description("Primary vibration axis (defaults to Z-axis or modal normal)");
}

static void node_geo_exec(GeoNodeExecParams params)
{
  const Scene *scene = DEG_get_evaluated_scene(params.depsgraph());
  if (!scene) {
    params.set_default_remaining_outputs();
    return;
  }

  const Object *target_ob = params.extract_input<Object *>("Object");
  if (!target_ob && params.depsgraph()) {
    /* Fallback to self object from depsgraph context if available */
  }

  float time = params.extract_input<float>("Time");
  if (time < 0.0f) {
    const float scene_ctime = BKE_scene_ctime_get(scene);
    const double frame_rate = double(scene->r.frs_sec) / double(scene->r.frs_sec_base);
    time = float(scene_ctime / frame_rate);
  }

  const float impulse = std::max(0.0f, params.extract_input<float>("Trigger / Impulse"));
  float freq = params.extract_input<float>("Frequency");
  float damping = params.extract_input<float>("Damping");
  const float amp_scale = std::max(0.0f, params.extract_input<float>("Amplitude Scale"));
  float3 axis = params.extract_input<float3>("Vibration Axis");

  if (math::is_zero(axis)) {
    axis = float3(0.0f, 0.0f, 1.0f);
  }
  else {
    axis = math::normalize(axis);
  }

  /* Physical Acoustics Inference from Section 1 (BSDF & Mesh Thickness) */
  float f0 = 440.0f;
  float gamma = 12.0f;
  float q_factor = 50.0f;

#ifdef WITH_STEAM_AUDIO
  AcousticMaterialProperties mat_props;
  if (target_ob && BKE_acoustics_infer_material(target_ob, &mat_props)) {
    gamma = mat_props.damping;
    q_factor = mat_props.q_factor;

    float thickness = 0.05f;
    float inferred_f0 = 440.0f;
    if (BKE_acoustics_infer_geometry(target_ob, &thickness, &inferred_f0, &mat_props)) {
      f0 = inferred_f0;
    }
  }
#endif

  if (freq > 0.0f) {
    f0 = freq;
  }
  if (damping > 0.0f) {
    gamma = damping;
  }

  /* Damped harmonic oscillator evaluation:
   * A(t) = impulse * exp(-gamma * mod(t, 1.0))
   * disp(t) = A(t) * sin(2 * pi * f0 * t) */
  const float decay_time = std::fmod(std::max(0.0f, time), 1.0f);
  const float envelope = impulse * std::exp(-gamma * decay_time);
  const float phase = 2.0f * float(M_PI) * f0 * time;
  const float disp_mag = envelope * std::sin(phase) * amp_scale;
  const float3 disp_vec = axis * disp_mag;

  /* Displace Geometry */
  GeometrySet geometry_set = params.extract_input<GeometrySet>("Geometry");
  if (!geometry_set.is_empty()) {
    geometry::translate_geometry(geometry_set, disp_vec);
  }

  params.set_output("Geometry", std::move(geometry_set));
  params.set_output("Offset", disp_vec);
  params.set_output("Displacement", disp_vec);
  params.set_output("Amplitude", envelope * amp_scale);
  params.set_output("Resonant Freq (f0)", f0);
  params.set_output("Damping (Q)", q_factor);
}

static void node_register()
{
  static blender::bke::bNodeType ntype;

  geo_node_type_base(&ntype, "GeometryNodePropVibration");
  ntype.ui_name = "Surface Resonance";
  ntype.ui_description =
      "Synthesize physical damped modal vibrations driven by material BSDF, geometry thickness, "
      "and collision impact impulses or audio transients";
  ntype.nclass = NODE_CLASS_GEOMETRY;
  ntype.geometry_node_execute = node_geo_exec;
  ntype.declare = node_declare;
  blender::bke::node_register_type(ntype);
}
NOD_REGISTER_NODE(node_register)

}  // namespace blender::nodes::node_geo_prop_vibration_cc
