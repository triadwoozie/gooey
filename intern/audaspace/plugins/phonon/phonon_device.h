/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#pragma once

#ifdef WITH_STEAM_AUDIO

#include <phonon.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Initialize global Steam Audio Phonon context, default HRTF, and simulation scene.
 * Returns true if successful or already initialized.
 */
bool phonon_device_init(int sampling_rate, int frame_size);

/**
 * Shut down and release all Steam Audio resources (scene, HRTF, context).
 */
void phonon_device_shutdown();

/**
 * Returns true if the Phonon device and context are currently active.
 */
bool phonon_device_is_initialized();

/**
 * Get active Steam Audio context.
 */
IPLContext phonon_device_get_context();

/**
 * Get active Steam Audio HRTF handle.
 */
IPLHRTF phonon_device_get_hrtf();

/**
 * Get active Steam Audio scene handle.
 */
IPLScene phonon_device_get_scene();

/**
 * Get audio settings currently configured.
 */
IPLAudioSettings phonon_device_get_audio_settings();

#ifdef __cplusplus
}
#endif

#endif /* WITH_STEAM_AUDIO */
