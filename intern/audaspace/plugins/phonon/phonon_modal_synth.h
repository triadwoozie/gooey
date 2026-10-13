/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#pragma once

#ifdef WITH_STEAM_AUDIO

#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define PHONON_MODAL_MAX_MODES 8

/**
 * Acoustic physical parameters for modal sound synthesis.
 */
typedef struct PhononModalParams {
  int num_modes;                             /* 4 to 8 resonant poles */
  float frequencies[PHONON_MODAL_MAX_MODES]; /* Resonant frequencies in Hz */
  float dampings[PHONON_MODAL_MAX_MODES];    /* Modal decay factors d_k in 1/s */
  float amplitudes[PHONON_MODAL_MAX_MODES];  /* Relative mode amplitudes */
  bool is_vitreous;                          /* Glass/vitreous brittle transient flag */
  float inharmonicity;                       /* Mode ratio spreading factor */
} PhononModalParams;

typedef struct PhononModalSynth PhononModalSynth;

/**
 * Create a new modal synthesizer instance.
 */
PhononModalSynth *phonon_modal_synth_create(int sample_rate);

/**
 * Destroy a modal synthesizer instance and free memory.
 */
void phonon_modal_synth_destroy(PhononModalSynth *synth);

/**
 * Reset internal oscillator states to silence.
 */
void phonon_modal_synth_reset(PhononModalSynth *synth);

/**
 * Configure modal resonance frequencies, dampings, and amplitudes.
 */
void phonon_modal_synth_set_params(PhononModalSynth *synth, const PhononModalParams *params);

/**
 * Excite the resonator bank with a normal kinetic impact impulse.
 * \param impact_energy Kinetic energy (1/2 * m * v^2)
 * \param impact_velocity Normal impact speed (m/s)
 * \param is_vitreous If true, injects high-frequency brittle click transient
 */
void phonon_modal_synth_trigger_impact(PhononModalSynth *synth,
                                       float impact_energy,
                                       float impact_velocity,
                                       bool is_vitreous);

/**
 * Set continuous tangential friction / scraping parameters.
 * \param tangential_velocity Relative tangential slip speed (m/s)
 * \param roughness Surface roughness [0.0, 1.0]
 * \param normal_force Normal contact force in Newtons
 */
void phonon_modal_synth_set_friction(PhononModalSynth *synth,
                                     float tangential_velocity,
                                     float roughness,
                                     float normal_force);

/**
 * Render synthesized PCM audio samples (single-channel float buffer).
 * \param buffer Output float PCM buffer
 * \param num_samples Number of samples to render
 * \return 1 if audio was generated, 0 if synth is completely silent/decayed
 */
int phonon_modal_synth_render(PhononModalSynth *synth, float *buffer, int num_samples);

/**
 * Returns true if synth is actively vibrating / decaying above audible threshold.
 */
bool phonon_modal_synth_is_active(const PhononModalSynth *synth);

#ifdef __cplusplus
}
#endif

#endif /* WITH_STEAM_AUDIO */
