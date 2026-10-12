/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: GPL-2.0-or-later */

#include "BKE_audio_analysis.hh"
#include "BKE_scene.hh"

#include "DEG_depsgraph_query.hh"

#include "NOD_socket_search_link.hh"

#include "UI_interface.hh"
#include "UI_resources.hh"

#include "node_geometry_util.hh"

namespace blender::nodes::node_geo_audio_analysis_cc {

static void node_declare(NodeDeclarationBuilder &b)
{
  b.use_custom_socket_order();

  /* Outputs */
  b.add_output<decl::Float>("Volume").description("RMS envelope volume [0.0 to 1.0]");
  b.add_output<decl::Float>("Onset").description("Transient attack impulse spike [0.0 to 1.0]");
  b.add_output<decl::Float>("Pitch").description("Estimated fundamental frequency in Hz (YIN)");
  b.add_output<decl::Bool>("Is Beat").description("True on musical rhythm beat onset");
  b.add_output<decl::Float>("Beat Phase").description("Continuous 0.0 to 1.0 phase ramp per beat");
  b.add_output<decl::Float>("BPM").description("Estimated tempo in beats per minute");
  b.add_output<decl::Vector>("Viseme").description("Vowel shape factors: X=A/Open, Y=E/Wide, Z=O/Round");

  /* Inputs */
  b.add_input<decl::Object>("Speaker").description("Speaker object containing audio data");
  b.add_input<decl::Float>("Time")
      .default_value(-1.0f)
      .min(-1.0f)
      .description("Playback time in seconds (-1 for current scene time)");
  b.add_input<decl::Float>("Sensitivity")
      .default_value(0.5f)
      .min(0.01f)
      .max(1.0f)
      .description("Onset detection sensitivity threshold");
  b.add_input<decl::Float>("Smoothing")
      .default_value(0.25f)
      .min(0.0f)
      .max(0.95f)
      .description("Envelope attack/decay temporal smoothing");
}

static void node_geo_exec(GeoNodeExecParams params)
{
  const Scene *scene = DEG_get_evaluated_scene(params.depsgraph());
  if (!scene) {
    params.set_default_remaining_outputs();
    return;
  }

  const Object *speaker_obj = params.extract_input<Object *>("Speaker");
  float eval_time = params.extract_input<float>("Time");

  if (eval_time < 0.0f) {
    const float scene_ctime = BKE_scene_ctime_get(scene);
    const double frame_rate = double(scene->r.frs_sec) / double(scene->r.frs_sec_base);
    eval_time = float(scene_ctime / frame_rate);
  }

  const float sensitivity = params.extract_input<float>("Sensitivity");
  const float smoothing = params.extract_input<float>("Smoothing");

  AudioAnalysisResult result;
  BKE_audio_analysis_evaluate(scene, speaker_obj, eval_time, sensitivity, smoothing, &result);

  params.set_output("Volume", result.volume);
  params.set_output("Onset", result.onset);
  params.set_output("Pitch", result.pitch);
  params.set_output("Is Beat", result.is_beat);
  params.set_output("Beat Phase", result.beat_phase);
  params.set_output("BPM", result.bpm);
  params.set_output("Viseme", float3{result.viseme[0], result.viseme[1], result.viseme[2]});
}

static void node_gather_link_searches(GatherLinkSearchOpParams &params)
{
  search_link_ops_for_basic_node(params);

  const bke::bNodeType &nt = params.node_type();
  if (params.in_out() == SOCK_OUT) {
    params.add_item_full_name(
        "Aubio Audio Analysis" UI_MENU_ARROW_SEP " Pitch",
        [&nt](LinkSearchOpParams &p) {
          bNode &node = p.add_node(nt);
          p.update_and_connect_available_socket(node, "Pitch");
        },
        1);
    params.add_item_full_name(
        "Aubio" UI_MENU_ARROW_SEP " Onset Trigger",
        [&nt](LinkSearchOpParams &p) {
          bNode &node = p.add_node(nt);
          p.update_and_connect_available_socket(node, "Onset Trigger");
        },
        1);
    params.add_item_full_name(
        "Audio Feature Extraction" UI_MENU_ARROW_SEP " Volume",
        [&nt](LinkSearchOpParams &p) {
          bNode &node = p.add_node(nt);
          p.update_and_connect_available_socket(node, "Volume");
        },
        1);
  }
}

static void node_register()
{
  static blender::bke::bNodeType ntype;

  geo_node_type_base(&ntype, "GeometryNodeAudioAnalysis");
  ntype.ui_name = "Audio Analysis";
  ntype.ui_description = "Real-time Aubio audio feature extraction (volume, onsets, pitch, beats, visemes)";
  ntype.nclass = NODE_CLASS_INPUT;
  ntype.geometry_node_execute = node_geo_exec;
  ntype.declare = node_declare;
  ntype.gather_link_search_ops = node_gather_link_searches;
  blender::bke::node_register_type(ntype);
}
NOD_REGISTER_NODE(node_register)

}  // namespace blender::nodes::node_geo_audio_analysis_cc
