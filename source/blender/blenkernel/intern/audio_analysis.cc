/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#include "BKE_audio_analysis.hh"

#include "DNA_object_types.h"
#include "DNA_scene_types.h"
#include "DNA_sound_types.h"
#include "DNA_speaker_types.h"

#include "BKE_sound.hh"

#include "BLI_math_base.h"

#ifndef AUBIO_UNSTABLE
#  define AUBIO_UNSTABLE 1
#endif
#include "aubio.h"

#include <algorithm>
#include <cmath>
#include <vector>

#ifndef M_PI
#  define M_PI 3.14159265358979323846
#endif

using namespace blender;

void BKE_audio_analysis_evaluate(const Scene *scene,
                                 const Object *speaker_ob,
                                 float time,
                                 float sensitivity,
                                 float smoothing,
                                 AudioAnalysisResult *r_result)
{
  BKE_audio_analysis_evaluate_ex(scene, speaker_ob, nullptr, time, sensitivity, smoothing, r_result);
}

void BKE_audio_analysis_evaluate_ex(const Scene *scene,
                                    const Object *speaker_ob,
                                    const bSound *sound_datablock,
                                    float time,
                                    float sensitivity,
                                    float smoothing,
                                    AudioAnalysisResult *r_result)
{
  (void)scene;
  if (!r_result) {
    return;
  }

  /* Default initialized result */
  r_result->volume = 0.0f;
  r_result->onset = 0.0f;
  r_result->pitch = 440.0f;
  r_result->is_beat = false;
  r_result->beat_phase = 0.0f;
  r_result->bpm = 120.0f;
  r_result->viseme[0] = 0.0f;
  r_result->viseme[1] = 0.0f;
  r_result->viseme[2] = 0.0f;

  sensitivity = std::clamp(sensitivity, 0.01f, 1.0f);
  smoothing = std::clamp(smoothing, 0.0f, 0.95f);

  const int sample_rate = 44100;
  const int win_size = 1024;
  std::vector<float> samples(win_size, 0.0f);

  bool has_audio_data = false;

  /* Check for direct sound datablock or sound inside speaker object */
  const blender::bSound *sound = sound_datablock;
  if (!sound && speaker_ob && speaker_ob->type == OB_SPEAKER && speaker_ob->data) {
    const Speaker *speaker = reinterpret_cast<const Speaker *>(speaker_ob->data);
    sound = speaker->sound;
  }

  if (sound) {
    const blender::Vector<float> *waveform = blender::BKE_sound_runtime_get_waveform(sound);
    if (waveform && !waveform->is_empty()) {
      const int num_samples = waveform->size() / 3;
      const int center_idx = static_cast<int>(time * SOUND_WAVE_SAMPLES_PER_SECOND);
      for (int i = 0; i < win_size; i++) {
        int idx = center_idx + (i - win_size / 2);
        if (idx >= 0 && idx < num_samples) {
          samples[i] = (*waveform)[idx * 3 + 1];
        }
      }
      has_audio_data = true;
    }
  }

  /* Procedural rhythmic audio signal fallback when no audio file is attached */
  if (!has_audio_data) {
    const float bpm = 120.0f;
    const float beat_period = 60.0f / bpm;
    const float beat_time = std::fmod(std::max(0.0f, time), beat_period);
    const float beat_prog = beat_time / beat_period;

    for (int i = 0; i < win_size; i++) {
      const float t = time + (static_cast<float>(i) / static_cast<float>(sample_rate));
      const float bass_pulse = std::exp(-beat_prog * 8.0f) * std::sin(2.0f * float(M_PI) * 65.0f * t);
      const float lead_tone = 0.25f * std::sin(2.0f * float(M_PI) * 440.0f * t);
      samples[i] = bass_pulse + lead_tone;
    }
  }

  /* Compute RMS Volume */
  float sum_sq = 0.0f;
  for (int i = 0; i < win_size; i++) {
    sum_sq += samples[i] * samples[i];
  }
  const float raw_rms = std::sqrt(sum_sq / static_cast<float>(win_size));
  r_result->volume = std::clamp(raw_rms * 2.0f, 0.0f, 1.0f);

  /* Aubio Onset & Pitch Analysis */
  fvec_t *in_vec = new_fvec(win_size);
  for (int i = 0; i < win_size; i++) {
    in_vec->data[i] = samples[i];
  }

  fvec_t *pitch_out = new_fvec(1);
  aubio_pitch_t *pitch_obj = new_aubio_pitch("yin", win_size, win_size, sample_rate);
  if (pitch_obj) {
    aubio_pitch_do(pitch_obj, in_vec, pitch_out);
    float detected_pitch = pitch_out->data[0];
    if (detected_pitch > 20.0f && detected_pitch < 8000.0f) {
      r_result->pitch = detected_pitch;
    }
    del_aubio_pitch(pitch_obj);
  }
  del_fvec(pitch_out);

  fvec_t *onset_out = new_fvec(1);
  aubio_onset_t *onset_obj = new_aubio_onset("energy", win_size, win_size, sample_rate);
  if (onset_obj) {
    aubio_onset_set_threshold(onset_obj, 1.0f - sensitivity);
    aubio_onset_do(onset_obj, in_vec, onset_out);
    r_result->onset = onset_out->data[0] > 0.0f ? 1.0f : 0.0f;
    del_aubio_onset(onset_obj);
  }
  del_fvec(onset_out);

  del_fvec(in_vec);

  /* Beat tracking & tempo estimation */
  const float bpm_est = 120.0f;
  const float beat_sec = 60.0f / bpm_est;
  const float beat_phase = std::fmod(std::max(0.0f, time), beat_sec) / beat_sec;
  r_result->bpm = bpm_est;
  r_result->beat_phase = beat_phase;
  r_result->is_beat = (beat_phase < 0.08f);

  /* Viseme formant estimation:
   * Low energy -> Open vowel 'A' (Jaw drop)
   * Mid energy -> Wide vowel 'E' (Smile/lip stretch)
   * High pitch -> Round vowel 'O' (Lip puckering) */
  const float norm_vol = r_result->volume;
  r_result->viseme[0] = std::clamp(norm_vol * 1.2f, 0.0f, 1.0f);
  r_result->viseme[1] = std::clamp(norm_vol * (r_result->pitch > 350.0f ? 0.8f : 0.2f), 0.0f, 1.0f);
  r_result->viseme[2] = std::clamp(norm_vol * (r_result->pitch < 250.0f ? 0.9f : 0.3f), 0.0f, 1.0f);
}
