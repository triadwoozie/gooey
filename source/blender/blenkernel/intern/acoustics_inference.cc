/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#ifdef WITH_STEAM_AUDIO

#include "BKE_acoustics_inference.hh"

#include "DNA_material_types.h"
#include "DNA_mesh_types.h"
#include "DNA_node_types.h"
#include "DNA_object_types.h"
#include "DNA_scene_types.h"
#include "DNA_rigidbody_types.h"

#include "BKE_material.hh"
#include "BKE_mesh.hh"
#include "BKE_node.hh"
#include "BKE_node_legacy_types.hh"
#include "BKE_object.hh"
#include "BKE_rigidbody.h"

#include "BLI_listbase.h"
#include "BLI_math_matrix.hh"
#include "BLI_math_vector.h"
#include "BLI_math_vector.hh"
#include "BLI_string.h"
#include "BLI_ustring.hh"

#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_query.hh"

#include "RBI_api.h"

#include "plugins/phonon/phonon_dsp.h"
#include "plugins/phonon/phonon_modal_synth.h"
#include "plugins/phonon/phonon_simulator.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <vector>

namespace blender {

bool BKE_acoustics_infer_material(const Object *ob, AcousticMaterialProperties *r_props)
{
  if (!r_props) {
    return false;
  }

  /* Default base material properties */
  float metallic = 0.0f;
  float roughness = 0.5f;
  float transmission = 0.0f;
  float ior = 1.45f;

  if (ob) {
    const Material *mat = nullptr;
    if (ob->totcol > 0) {
      short act = ob->actcol > 0 ? ob->actcol : 1;
      mat = BKE_object_material_get(const_cast<Object *>(ob), act);
    }

    if (mat) {
      metallic = mat->metallic;
      roughness = mat->roughness;

      /* Inspect Principled BSDF node if node tree is active */
      if (mat->nodetree) {
        for (const bNode &node : mat->nodetree->nodes) {
          if (node.type_legacy == SH_NODE_BSDF_PRINCIPLED ||
              STREQ(node.idname, "ShaderNodeBsdfPrincipled"))
          {
            const bNodeSocket *sock_met = blender::bke::node_find_socket(node, SOCK_IN, "Metallic"_ustr);
            if (sock_met && sock_met->default_value) {
              metallic = static_cast<const bNodeSocketValueFloat *>(sock_met->default_value)->value;
            }

            const bNodeSocket *sock_rough = blender::bke::node_find_socket(node, SOCK_IN, "Roughness"_ustr);
            if (sock_rough && sock_rough->default_value) {
              roughness = static_cast<const bNodeSocketValueFloat *>(sock_rough->default_value)->value;
            }

            const bNodeSocket *sock_trans = blender::bke::node_find_socket(node, SOCK_IN, "Transmission Weight"_ustr);
            if (!sock_trans) {
              sock_trans = blender::bke::node_find_socket(node, SOCK_IN, "Transmission"_ustr);
            }
            if (sock_trans && sock_trans->default_value) {
              transmission = static_cast<const bNodeSocketValueFloat *>(sock_trans->default_value)->value;
            }

            const bNodeSocket *sock_ior = blender::bke::node_find_socket(node, SOCK_IN, "IOR"_ustr);
            if (sock_ior && sock_ior->default_value) {
              ior = static_cast<const bNodeSocketValueFloat *>(sock_ior->default_value)->value;
            }
            break;
          }
        }
      }
    }
  }

  metallic = std::clamp(metallic, 0.0f, 1.0f);
  roughness = std::clamp(roughness, 0.0f, 1.0f);
  transmission = std::clamp(transmission, 0.0f, 1.0f);
  ior = std::clamp(ior, 1.0f, 2.5f);

  r_props->metallic = metallic;
  r_props->roughness = roughness;
  r_props->transmission = transmission;
  r_props->ior = ior;

  /* Vitreous / brittle transient detection: high transmission and glass/crystal IOR */
  const bool is_vitreous = (transmission > 0.35f && ior >= 1.35f && ior <= 1.70f);
  r_props->is_vitreous = is_vitreous;

  /* Speed of sound proxy c_m (m/s) based on inferred physical composition */
  if (metallic > 0.5f) {
    r_props->speed_of_sound = 5100.0f; /* Steel / aluminum */
  }
  else if (is_vitreous) {
    r_props->speed_of_sound = 4200.0f; /* Glass / quartz / ceramic */
  }
  else if (roughness > 0.4f && metallic < 0.2f) {
    r_props->speed_of_sound = 3400.0f; /* Wood / fibrous organics */
  }
  else {
    r_props->speed_of_sound = 1750.0f; /* Plastics / polymers / rubber */
  }

  /* Internal damping & modal Q-factor derivation */
  if (metallic > 0.5f) {
    /* High metallicity: ultra-low damping, sustained ringing resonance */
    r_props->q_factor = 200.0f + 600.0f * (1.0f - roughness) * metallic;
    r_props->damping = 2.0f + 10.0f * roughness;
    r_props->inharmonicity = 1.15f;
  }
  else if (is_vitreous) {
    /* Glass / ceramic: bright ringing, inharmonic plate chimes */
    r_props->q_factor = 150.0f + 350.0f * (1.0f - roughness);
    r_props->damping = 3.5f + 14.0f * roughness;
    r_props->inharmonicity = 1.35f;
  }
  else if (roughness > 0.4f && metallic < 0.2f) {
    /* Wood / fibrous: moderate acoustic damping */
    r_props->q_factor = 30.0f + 50.0f * (1.0f - roughness);
    r_props->damping = 22.0f + 45.0f * roughness;
    r_props->inharmonicity = 1.05f;
  }
  else {
    /* Heavy internal acoustic damping (plastic / organics) */
    r_props->q_factor = 10.0f + 25.0f * (1.0f - roughness);
    r_props->damping = 45.0f + 95.0f * roughness;
    r_props->inharmonicity = 1.0f;
  }

  return true;
}

bool BKE_acoustics_infer_geometry(const Object *ob,
                                  float *r_thickness,
                                  float *r_f0,
                                  const AcousticMaterialProperties *mat_props)
{
  if (!r_thickness || !r_f0) {
    return false;
  }

  float dims[3] = {1.0f, 1.0f, 1.0f};
  if (ob) {
    BKE_object_dimensions_get(ob, dims);
  }

  dims[0] = std::max(std::abs(dims[0]), 0.005f);
  dims[1] = std::max(std::abs(dims[1]), 0.005f);
  dims[2] = std::max(std::abs(dims[2]), 0.005f);

  /* Sort dimensions d_min <= d_mid <= d_max */
  float d_min = std::min({dims[0], dims[1], dims[2]});
  float d_max = std::max({dims[0], dims[1], dims[2]});
  float d_mid = dims[0] + dims[1] + dims[2] - d_min - d_max;

  /* Minimum thickness 2 mm */
  float thickness = std::max(d_min, 0.002f);
  *r_thickness = thickness;

  const float c_m = mat_props ? mat_props->speed_of_sound : 3400.0f;

  /* Resonant fundamental frequency f0 estimation:
   * Flexural plate / beam equation: f0 ~ (c_m * thickness) / (2 * L^2) */
  float f0 = 440.0f;
  if (d_min < 0.25f * d_max) {
    /* Plate / shell structure */
    f0 = (c_m * thickness) / (2.0f * d_max * d_mid);
  }
  else {
    /* Solid 3D bulk geometry */
    f0 = c_m / (2.0f * (d_max + thickness));
  }

  /* Clamp to audible musical range */
  f0 = std::clamp(f0, 45.0f, 3800.0f);
  *r_f0 = f0;

  return true;
}

void BKE_acoustics_calculate_modal_params(const AcousticMaterialProperties *mat_props,
                                          float thickness,
                                          float f0,
                                          PhononModalParams *r_params)
{
  if (!r_params) {
    return;
  }

  (void)thickness;

  const float eta = mat_props ? mat_props->inharmonicity : 1.0f;
  const float d0 = mat_props ? mat_props->damping : 20.0f;
  const bool is_vitreous = mat_props ? mat_props->is_vitreous : false;
  const float metallic = mat_props ? mat_props->metallic : 0.0f;

  r_params->num_modes = 6;
  r_params->is_vitreous = is_vitreous;
  r_params->inharmonicity = eta;

  const float mode_ratios[6] = {
      1.0f,
      1.55f * eta,
      2.45f * eta,
      3.80f * eta,
      5.35f * eta,
      7.15f * eta,
  };

  const float mode_base_amps[6] = {1.0f, 0.72f, 0.50f, 0.35f, 0.22f, 0.14f};

  for (int k = 0; k < 6; k++) {
    r_params->frequencies[k] = std::clamp(f0 * mode_ratios[k], 20.0f, 22000.0f);

    /* Higher modes decay progressively faster */
    const float mode_damping_factor = 1.0f + 0.22f * static_cast<float>(k) * (1.0f - 0.4f * metallic);
    r_params->dampings[k] = d0 * mode_damping_factor;

    float amp = mode_base_amps[k];
    if (is_vitreous && k >= 2) {
      amp *= 1.4f; /* Vitreous shimmer */
    }
    r_params->amplitudes[k] = amp;
  }

  for (int k = 6; k < PHONON_MODAL_MAX_MODES; k++) {
    r_params->frequencies[k] = 0.0f;
    r_params->dampings[k] = 100.0f;
    r_params->amplitudes[k] = 0.0f;
  }
}

int BKE_acoustics_infer_collision_sound(const Object *ob_a,
                                        const Object *ob_b,
                                        const AcousticContactEvent *contact,
                                        float *out_pcm_buffer,
                                        int num_samples,
                                        int sample_rate)
{
  if (!out_pcm_buffer || num_samples <= 0) {
    return 0;
  }

  if (sample_rate <= 0) {
    sample_rate = 48000;
  }

  /* 1. Infer material properties for body A */
  AcousticMaterialProperties props_a;
  BKE_acoustics_infer_material(ob_a, &props_a);
  float thickness_a = 0.02f, f0_a = 440.0f;
  BKE_acoustics_infer_geometry(ob_a, &thickness_a, &f0_a, &props_a);

  PhononModalParams params_a;
  BKE_acoustics_calculate_modal_params(&props_a, thickness_a, f0_a, &params_a);

  /* 2. Infer material properties for body B */
  AcousticMaterialProperties props_b;
  BKE_acoustics_infer_material(ob_b, &props_b);
  float thickness_b = 0.02f, f0_b = 440.0f;
  BKE_acoustics_infer_geometry(ob_b, &thickness_b, &f0_b, &props_b);

  PhononModalParams params_b;
  BKE_acoustics_calculate_modal_params(&props_b, thickness_b, f0_b, &params_b);

  /* 3. Create synthesizers and trigger impact */
  PhononModalSynth *synth_a = phonon_modal_synth_create(sample_rate);
  PhononModalSynth *synth_b = phonon_modal_synth_create(sample_rate);

  phonon_modal_synth_set_params(synth_a, &params_a);
  phonon_modal_synth_set_params(synth_b, &params_b);

  const float energy = contact ? contact->impact_energy : 1.0f;
  const float v_norm = contact ? contact->normal_velocity : 1.0f;
  const bool vitreous = props_a.is_vitreous || props_b.is_vitreous;

  phonon_modal_synth_trigger_impact(synth_a, energy, v_norm, vitreous);
  phonon_modal_synth_trigger_impact(synth_b, energy, v_norm, vitreous);

  std::vector<float> buf_a(num_samples, 0.0f);
  std::vector<float> buf_b(num_samples, 0.0f);

  phonon_modal_synth_render(synth_a, buf_a.data(), num_samples);
  phonon_modal_synth_render(synth_b, buf_b.data(), num_samples);

  /* Mix both bodies' modal response */
  for (int i = 0; i < num_samples; i++) {
    out_pcm_buffer[i] = std::tanh(0.5f * (buf_a[i] + buf_b[i]));
  }

  phonon_modal_synth_destroy(synth_a);
  phonon_modal_synth_destroy(synth_b);

  return 1;
}

int BKE_acoustics_infer_friction_sound(const Object *ob_a,
                                       const Object *ob_b,
                                       const AcousticContactEvent *contact,
                                       float *out_pcm_buffer,
                                       int num_samples,
                                       int sample_rate)
{
  if (!out_pcm_buffer || num_samples <= 0) {
    return 0;
  }

  if (sample_rate <= 0) {
    sample_rate = 48000;
  }

  AcousticMaterialProperties props_a;
  BKE_acoustics_infer_material(ob_a, &props_a);
  float thickness_a = 0.02f, f0_a = 440.0f;
  BKE_acoustics_infer_geometry(ob_a, &thickness_a, &f0_a, &props_a);

  PhononModalParams params_a;
  BKE_acoustics_calculate_modal_params(&props_a, thickness_a, f0_a, &params_a);

  PhononModalSynth *synth = phonon_modal_synth_create(sample_rate);
  phonon_modal_synth_set_params(synth, &params_a);

  const float v_tan = contact ? contact->tangential_velocity : 0.5f;
  const float rough = std::max(props_a.roughness, (ob_b ? 0.5f : 0.0f));
  const float fn = contact ? contact->normal_force : 1.0f;

  phonon_modal_synth_set_friction(synth, v_tan, rough, fn);
  int res = phonon_modal_synth_render(synth, out_pcm_buffer, num_samples);

  phonon_modal_synth_destroy(synth);
  return res;
}

struct ContactBatchContext {
  Depsgraph *depsgraph;
  Scene *scene;
  int contact_count;
};

static void rigidbody_contact_callback(void *user_data,
                                       void *body0_userdata,
                                       void *body1_userdata,
                                       const float pos[3],
                                       const float normal[3],
                                       float applied_impulse,
                                       float distance)
{
  (void)distance;
  ContactBatchContext *ctx = static_cast<ContactBatchContext *>(user_data);
  if (!ctx) {
    return;
  }

  /* Filter out minor resting contacts */
  if (applied_impulse < 0.02f) {
    return;
  }

  const Object *ob_a = static_cast<const Object *>(body0_userdata);
  const Object *ob_b = static_cast<const Object *>(body1_userdata);

  AcousticContactEvent event;
  copy_v3_v3(event.contact_point, pos);
  copy_v3_v3(event.normal, normal);
  event.normal_velocity = applied_impulse * 2.0f;
  event.tangential_velocity = 0.0f;
  event.impact_energy = applied_impulse * applied_impulse * 0.5f;
  event.normal_force = applied_impulse * 60.0f;

  /* Synthesize impact buffer */
  const int block_size = 512;
  float pcm_block[512];
  BKE_acoustics_infer_collision_sound(ob_a, ob_b, &event, pcm_block, block_size, 48000);

  ctx->contact_count++;
}

void BKE_acoustics_process_rigidbody_contacts(Depsgraph *depsgraph, Scene *scene)
{
  if (!scene || !scene->rigidbody_world) {
    return;
  }

  RigidBodyWorld *rbw = scene->rigidbody_world;
  rbDynamicsWorld *physics_world = BKE_rigidbody_world_physics(rbw);
  if (!physics_world) {
    return;
  }

  ContactBatchContext ctx;
  ctx.depsgraph = depsgraph;
  ctx.scene = scene;
  ctx.contact_count = 0;

  RB_dworld_get_contacts(physics_world, rigidbody_contact_callback, &ctx);

  if (ctx.contact_count > 0) {
    static int last_logged_frame = -1;
    if (scene->r.cfra != last_logged_frame) {
      last_logged_frame = scene->r.cfra;
      printf("[Acoustics Inference] Frame %d: Evaluated %d contact impact sounds\n",
             scene->r.cfra,
             ctx.contact_count);
    }
  }
}

}  // namespace blender

#endif /* WITH_STEAM_AUDIO */
