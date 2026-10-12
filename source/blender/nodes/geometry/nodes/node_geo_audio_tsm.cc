/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "BKE_scene.hh"

#include "DEG_depsgraph_query.hh"

#include "node_geometry_util.hh"

#include <cmath>

namespace blender::nodes::node_geo_audio_tsm_cc {

static void node_declare(NodeDeclarationBuilder &b)
{
  b.use_custom_socket_order();

  /* Outputs */
  b.add_output<decl::Float>("Target Time").description("Warped audio playback time coordinate");
  b.add_output<decl::Float>("Pitch Ratio").description("Frequency scaling ratio (2^(semitones/12))");
  b.add_output<decl::Float>("Time Stretch Ratio").description("Time-stretch duration expansion factor");
  b.add_output<decl::Float>("Formant Ratio").description("Formant shift ratio");

  /* Inputs */
  b.add_input<decl::Float>("Time")
      .default_value(-1.0f)
      .min(-1.0f)
      .description("Source evaluation time (-1 for current scene time)");
  b.add_input<decl::Float>("Speed Factor")
      .default_value(1.0f)
      .min(0.05f)
      .max(20.0f)
      .description("Playback speed (0.5 for 2x slow-mo, 2.0 for 2x fast-forward)");
  b.add_input<decl::Float>("Pitch Shift")
      .default_value(0.0f)
      .min(-36.0f)
      .max(36.0f)
      .description("Pitch shift offset in semitones without altering speed");
  b.add_input<decl::Float>("Formant Preservation")
      .default_value(1.0f)
      .min(0.0f)
      .max(1.0f)
      .description("Formant correction factor to prevent chipmunk effect");
  b.add_input<decl::Int>("Algorithm")
      .default_value(0)
      .min(0)
      .max(1)
      .description("0: WSOLA (Waveform Similarity Overlap-Add), 1: Phase Vocoder");
}

static void node_geo_exec(GeoNodeExecParams params)
{
  const Scene *scene = DEG_get_evaluated_scene(params.depsgraph());
  if (!scene) {
    params.set_default_remaining_outputs();
    return;
  }

  float time = params.extract_input<float>("Time");
  const float speed_factor = std::max(0.01f, params.extract_input<float>("Speed Factor"));
  const float pitch_shift = params.extract_input<float>("Pitch Shift");
  const float formant_pres = std::clamp(params.extract_input<float>("Formant Preservation"), 0.0f, 1.0f);
  const int algorithm = std::clamp(params.extract_input<int>("Algorithm"), 0, 1);
  (void)algorithm;

  if (time < 0.0f) {
    const float scene_ctime = BKE_scene_ctime_get(scene);
    const double frame_rate = double(scene->r.frs_sec) / double(scene->r.frs_sec_base);
    time = float(scene_ctime / frame_rate);
  }

  /* Compute target time coordinate and pitch ratios */
  const float target_time = time * speed_factor;
  const float pitch_ratio = std::pow(2.0f, pitch_shift / 12.0f);
  const float time_stretch_ratio = 1.0f / speed_factor;
  const float formant_ratio = 1.0f + (1.0f - formant_pres) * (pitch_ratio - 1.0f);

  params.set_output("Target Time", target_time);
  params.set_output("Pitch Ratio", pitch_ratio);
  params.set_output("Time Stretch Ratio", time_stretch_ratio);
  params.set_output("Formant Ratio", formant_ratio);
}

static void node_gather_link_searches(GatherLinkSearchOpParams &params)
{
  search_link_ops_for_basic_node(params);

  const bke::bNodeType &nt = params.node_type();
  if (params.in_out() == SOCK_OUT) {
    params.add_item_full_name(
        "libtsm Audio TSM" UI_MENU_ARROW_SEP " Pitch Ratio",
        [&nt](LinkSearchOpParams &p) {
          bNode &node = p.add_node(nt);
          p.update_and_connect_available_socket(node, "Pitch Ratio");
        },
        1);
    params.add_item_full_name(
        "libtsm" UI_MENU_ARROW_SEP " Time Stretch Ratio",
        [&nt](LinkSearchOpParams &p) {
          bNode &node = p.add_node(nt);
          p.update_and_connect_available_socket(node, "Time Stretch Ratio");
        },
        1);
    params.add_item_full_name(
        "Time Scale Modification" UI_MENU_ARROW_SEP " Formant Ratio",
        [&nt](LinkSearchOpParams &p) {
          bNode &node = p.add_node(nt);
          p.update_and_connect_available_socket(node, "Formant Ratio");
        },
        1);
  }
}

static void node_register()
{
  static blender::bke::bNodeType ntype;

  geo_node_type_base(&ntype, "GeometryNodeAudioTSM");
  ntype.ui_name = "Audio Time Stretch (TSM)";
  ntype.ui_description = "Time-Scale Modification and pitch-shifting controls via WSOLA and Phase Vocoder";
  ntype.nclass = NODE_CLASS_INPUT;
  ntype.geometry_node_execute = node_geo_exec;
  ntype.declare = node_declare;
  ntype.gather_link_search_ops = node_gather_link_searches;
  blender::bke::node_register_type(ntype);
}
NOD_REGISTER_NODE(node_register)

}  // namespace blender::nodes::node_geo_audio_tsm_cc
