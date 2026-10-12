/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#ifdef WITH_STEAM_AUDIO

#include "phonon_modal_synth.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>

#ifndef M_PI
#  define M_PI 3.14159265358979323846
#endif

struct ModalOscillator {
  float freq;     /* Hz */
  float damping;  /* 1/s */
  float amp;      /* Relative amplitude */
  float a1, a2;   /* Difference equation recursive coefficients */
  float b0;       /* Excitation scale */
  float y1, y2;   /* Previous state outputs */
};

struct PhononModalSynth {
  int sample_rate;
  int num_modes;
  ModalOscillator modes[PHONON_MODAL_MAX_MODES];

  bool is_vitreous;
  float inharmonicity;

  /* Impact excitation state */
  float impact_amp;
  int impulse_pos;
  int impulse_len;
  bool vitreous_click_active;
  int vitreous_click_pos;

  /* Friction excitation state */
  float friction_target_amp;
  float friction_cur_amp;
  float friction_filter_state;

  /* Activity flag */
  bool is_active;
};

static void update_oscillator_coeffs(ModalOscillator &osc, int sample_rate)
{
  if (sample_rate <= 0) {
    return;
  }

  const float dt = 1.0f / static_cast<float>(sample_rate);
  /* Pole radius r = exp(-damping * dt), clamped for strict stability */
  const float r = std::clamp(std::exp(-osc.damping * dt), 0.0f, 0.99998f);
  const float omega = 2.0f * static_cast<float>(M_PI) * osc.freq * dt;

  osc.a1 = 2.0f * r * std::cos(omega);
  osc.a2 = -(r * r);
  osc.b0 = osc.amp * (1.0f - r) * std::sin(omega) * 2.0f;
}

PhononModalSynth *phonon_modal_synth_create(int sample_rate)
{
  if (sample_rate <= 0) {
    sample_rate = 48000;
  }

  PhononModalSynth *synth = new PhononModalSynth();
  synth->sample_rate = sample_rate;
  synth->num_modes = 4;
  synth->is_vitreous = false;
  synth->inharmonicity = 1.0f;
  synth->impact_amp = 0.0f;
  synth->impulse_pos = 0;
  synth->impulse_len = 0;
  synth->vitreous_click_active = false;
  synth->vitreous_click_pos = 0;
  synth->friction_target_amp = 0.0f;
  synth->friction_cur_amp = 0.0f;
  synth->friction_filter_state = 0.0f;
  synth->is_active = false;

  /* Initialize default mode frequencies and dampings */
  const float default_freqs[4] = {440.0f, 880.0f, 1320.0f, 1760.0f};
  const float default_damps[4] = {20.0f, 28.0f, 38.0f, 52.0f};
  const float default_amps[4] = {1.0f, 0.7f, 0.4f, 0.2f};

  for (int i = 0; i < 4; i++) {
    synth->modes[i].freq = default_freqs[i];
    synth->modes[i].damping = default_damps[i];
    synth->modes[i].amp = default_amps[i];
    synth->modes[i].y1 = 0.0f;
    synth->modes[i].y2 = 0.0f;
    update_oscillator_coeffs(synth->modes[i], sample_rate);
  }

  return synth;
}

void phonon_modal_synth_destroy(PhononModalSynth *synth)
{
  if (synth) {
    delete synth;
  }
}

void phonon_modal_synth_reset(PhononModalSynth *synth)
{
  if (!synth) {
    return;
  }

  for (int i = 0; i < PHONON_MODAL_MAX_MODES; i++) {
    synth->modes[i].y1 = 0.0f;
    synth->modes[i].y2 = 0.0f;
  }

  synth->impact_amp = 0.0f;
  synth->impulse_pos = 0;
  synth->impulse_len = 0;
  synth->vitreous_click_active = false;
  synth->vitreous_click_pos = 0;
  synth->friction_target_amp = 0.0f;
  synth->friction_cur_amp = 0.0f;
  synth->friction_filter_state = 0.0f;
  synth->is_active = false;
}

void phonon_modal_synth_set_params(PhononModalSynth *synth, const PhononModalParams *params)
{
  if (!synth || !params) {
    return;
  }

  synth->num_modes = std::clamp(params->num_modes, 1, PHONON_MODAL_MAX_MODES);
  synth->is_vitreous = params->is_vitreous;
  synth->inharmonicity = std::max(0.5f, params->inharmonicity);

  for (int i = 0; i < synth->num_modes; i++) {
    synth->modes[i].freq = std::clamp(params->frequencies[i], 20.0f, 22000.0f);
    synth->modes[i].damping = std::clamp(params->dampings[i], 0.1f, 2000.0f);
    synth->modes[i].amp = std::max(0.0f, params->amplitudes[i]);
    update_oscillator_coeffs(synth->modes[i], synth->sample_rate);
  }
}

void phonon_modal_synth_trigger_impact(PhononModalSynth *synth,
                                       float impact_energy,
                                       float impact_velocity,
                                       bool is_vitreous)
{
  if (!synth) {
    return;
  }

  /* Calculate impulse amplitude from impact energy and normal velocity */
  const float energy_factor = std::sqrt(std::max(0.0f, impact_energy)) * 0.35f;
  const float vel_factor = std::abs(impact_velocity) * 0.20f;
  synth->impact_amp = std::clamp(energy_factor + vel_factor, 0.02f, 1.2f);

  /* Impulse pulse duration: approx 0.8 ms to 1.5 ms (e.g. 40-72 samples at 48 kHz) */
  synth->impulse_len = std::clamp(
      static_cast<int>(synth->sample_rate * 0.0012f), 16, 96);
  synth->impulse_pos = 0;

  synth->is_vitreous = is_vitreous || synth->is_vitreous;
  if (synth->is_vitreous) {
    synth->vitreous_click_active = true;
    synth->vitreous_click_pos = 0;
  }

  synth->is_active = true;
}

void phonon_modal_synth_set_friction(PhononModalSynth *synth,
                                     float tangential_velocity,
                                     float roughness,
                                     float normal_force)
{
  if (!synth) {
    return;
  }

  const float slip_speed = std::abs(tangential_velocity);
  const float rough = std::clamp(roughness, 0.0f, 1.0f);
  const float fn = std::max(0.0f, normal_force);

  if (slip_speed > 0.005f && rough > 0.02f) {
    synth->friction_target_amp = std::clamp(
        slip_speed * rough * std::sqrt(fn + 0.01f) * 0.25f, 0.0f, 0.75f);
    synth->is_active = true;
  }
  else {
    synth->friction_target_amp = 0.0f;
  }
}

int phonon_modal_synth_render(PhononModalSynth *synth, float *buffer, int num_samples)
{
  if (!synth || !buffer || num_samples <= 0) {
    return 0;
  }

  if (!synth->is_active && synth->friction_target_amp <= 0.0001f &&
      synth->friction_cur_amp <= 0.0001f)
  {
    std::memset(buffer, 0, sizeof(float) * num_samples);
    return 0;
  }

  float max_abs_val = 0.0f;

  for (int n = 0; n < num_samples; n++) {
    /* 1. Calculate impact excitation pulse */
    float x_impact = 0.0f;
    if (synth->impulse_pos < synth->impulse_len) {
      const float phase = static_cast<float>(M_PI) * static_cast<float>(synth->impulse_pos) /
                          static_cast<float>(synth->impulse_len);
      x_impact = synth->impact_amp * std::sin(phase);
      synth->impulse_pos++;
    }

    /* 2. Calculate vitreous brittle click transient */
    float x_vitreous = 0.0f;
    if (synth->vitreous_click_active) {
      if (synth->vitreous_click_pos == 0) {
        x_vitreous = synth->impact_amp * 0.9f;
      }
      else if (synth->vitreous_click_pos == 1) {
        x_vitreous = -synth->impact_amp * 0.75f;
      }
      else if (synth->vitreous_click_pos < 32) {
        const float noise = (static_cast<float>(rand()) / static_cast<float>(RAND_MAX) * 2.0f - 1.0f);
        const float decay = std::exp(-static_cast<float>(synth->vitreous_click_pos) / 5.0f);
        x_vitreous = synth->impact_amp * 0.4f * noise * decay;
      }
      else {
        synth->vitreous_click_active = false;
      }
      synth->vitreous_click_pos++;
    }

    /* 3. Smooth continuous friction excitation */
    synth->friction_cur_amp += 0.01f * (synth->friction_target_amp - synth->friction_cur_amp);
    float x_friction = 0.0f;
    if (synth->friction_cur_amp > 0.0005f) {
      const float white = (static_cast<float>(rand()) / static_cast<float>(RAND_MAX) * 2.0f - 1.0f);
      /* One-pole lowpass filter for surface texture scraping */
      synth->friction_filter_state += 0.35f * (white - synth->friction_filter_state);
      x_friction = synth->friction_cur_amp * synth->friction_filter_state;
    }

    /* Total excitation driving resonator bank */
    const float excitation = x_impact + x_vitreous + x_friction;

    /* 4. Resonator bank difference equations evaluation */
    float y_sum = 0.0f;
    for (int k = 0; k < synth->num_modes; k++) {
      ModalOscillator &osc = synth->modes[k];
      const float y = osc.a1 * osc.y1 + osc.a2 * osc.y2 + osc.b0 * excitation;
      osc.y2 = osc.y1;
      osc.y1 = y;
      y_sum += y;
    }

    /* Add vitreous transient high-frequency crack directly to output */
    y_sum += x_vitreous * 0.3f;

    /* 5. Soft limiting saturation */
    const float y_clipped = std::tanh(y_sum);
    buffer[n] = y_clipped;

    const float abs_val = std::abs(y_clipped);
    if (abs_val > max_abs_val) {
      max_abs_val = abs_val;
    }
  }

  /* Activity detection for early sleep */
  if (max_abs_val < 0.00005f && synth->impulse_pos >= synth->impulse_len &&
      synth->friction_cur_amp <= 0.0001f && !synth->vitreous_click_active)
  {
    synth->is_active = false;
  }
  else {
    synth->is_active = true;
  }

  return synth->is_active ? 1 : 0;
}

bool phonon_modal_synth_is_active(const PhononModalSynth *synth)
{
  return synth ? synth->is_active : false;
}

#endif /* WITH_STEAM_AUDIO */
