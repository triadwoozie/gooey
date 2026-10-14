/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#pragma once

#ifdef WITH_STEAM_AUDIO

#include "BLI_sys_types.h"

struct PhononModalParams;

namespace blender {

struct Depsgraph;
struct Object;
struct Scene;

/**
 * Evaluated physical material properties inferred from surface BSDF nodes.
 */
typedef struct AcousticMaterialProperties {
  float metallic;        /* 0.0 to 1.0 */
  float roughness;       /* 0.0 to 1.0 */
  float transmission;    /* 0.0 to 1.0 */
  float ior;             /* Index of refraction (1.0 to 2.5) */
  float damping;         /* Modal decay factor (1/s) */
  float q_factor;        /* Modal quality factor (10 to 1000) */
  float speed_of_sound;  /* Acoustic speed of sound proxy in m/s */
  float inharmonicity;   /* Mode frequency spreading factor */
  bool is_vitreous;      /* Glass/brittle vitreous transient flag */
} AcousticMaterialProperties;

/**
 * Physics contact event description between two colliding or sliding bodies.
 */
typedef struct AcousticContactEvent {
  float contact_point[3];     /* World space contact coordinate */
  float normal[3];            /* Contact normal vector */
  float normal_velocity;      /* Relative velocity along contact normal (m/s) */
  float tangential_velocity;  /* Relative slip velocity on contact plane (m/s) */
  float impact_energy;        /* Kinetic impact energy (Joules) */
  float normal_force;         /* Contact normal reaction force (Newtons) */
} AcousticContactEvent;

/**
 * Infer acoustic material parameters (internal damping, Q-factor, speed of sound,
 * vitreous transient detection) from object's active material and Principled BSDF nodes.
 */
bool BKE_acoustics_infer_material(const struct Object *ob, AcousticMaterialProperties *r_props);

/**
 * Infer effective geometric thickness and fundamental resonant frequency (f0)
 * from evaluated mesh bounds and acoustic speed proxy.
 */
bool BKE_acoustics_infer_geometry(const struct Object *ob,
                                  float *r_thickness,
                                  float *r_f0,
                                  const AcousticMaterialProperties *mat_props);

/**
 * Calculate 4-to-8 pole modal resonator bank parameters from material and geometry.
 */
void BKE_acoustics_calculate_modal_params(const AcousticMaterialProperties *mat_props,
                                          float thickness,
                                          float f0,
                                          PhononModalParams *r_params);

/**
 * Synthesize physical collision sound buffer between two objects based on
 * inferred material BSDF parameters and kinetic impact dynamics.
 * \param out_pcm_buffer Buffer of size num_samples to receive synthesized float audio.
 * \return 1 if audio synthesized, 0 if silent.
 */
int BKE_acoustics_infer_collision_sound(const struct Object *ob_a,
                                        const struct Object *ob_b,
                                        const AcousticContactEvent *contact,
                                        float *out_pcm_buffer,
                                        int num_samples,
                                        int sample_rate);

/**
 * Synthesize continuous tangential friction / scrape sound buffer.
 * \param out_pcm_buffer Buffer of size num_samples to receive synthesized float audio.
 * \return 1 if audio synthesized, 0 if silent.
 */
int BKE_acoustics_infer_friction_sound(const struct Object *ob_a,
                                       const struct Object *ob_b,
                                       const AcousticContactEvent *contact,
                                       float *out_pcm_buffer,
                                       int num_samples,
                                       int sample_rate);

/**
 * Traverse active rigid body collision contacts in the physics world,
 * infer material and modal acoustic properties, and synthesize contact audio.
 */
void BKE_acoustics_process_rigidbody_contacts(struct Depsgraph *depsgraph, struct Scene *scene);

}  // namespace blender

#endif /* WITH_STEAM_AUDIO */
