/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#pragma once

#include "BLI_sys_types.h"
#include <stdbool.h>

struct Scene;
struct Object;

#ifdef __cplusplus
extern "C" {
#endif

typedef struct AudioAnalysisResult {
  float volume;      /* RMS energy [0.0, 1.0] */
  float onset;       /* Attack / transient impulse spike [0.0, 1.0] */
  float pitch;       /* Detected fundamental frequency in Hz (YIN) */
  bool is_beat;      /* Beat onset trigger */
  float beat_phase;  /* Continuous 0.0 to 1.0 sawtooth phase ramp per beat */
  float bpm;         /* Estimated tempo */
  float viseme[3];   /* Formant/viseme factors: [0]=A/Open, [1]=E/Wide, [2]=O/Round */
} AudioAnalysisResult;

/**
 * Evaluate real-time audio features using Aubio algorithms at a given scene time.
 */
void BKE_audio_analysis_evaluate(const struct Scene *scene,
                                 const struct Object *speaker_ob,
                                 float time,
                                 float sensitivity,
                                 float smoothing,
                                 AudioAnalysisResult *r_result);

#ifdef __cplusplus
}
#endif
