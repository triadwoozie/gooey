/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#pragma once

#ifdef WITH_STEAM_AUDIO

#include <phonon.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct PhononDSPProcessor PhononDSPProcessor;

/**
 * Creates a Steam Audio DSP effect processor (direct effect + binaural spatializer).
 * \param sampling_rate Sample rate in Hz (e.g. 48000).
 * \param frame_size Frame processing buffer size in samples (e.g. 512).
 * \param in_channels Number of input channels (1 for mono, 2 for stereo).
 * \return Pointer to allocated processor or NULL on failure.
 */
PhononDSPProcessor *phonon_dsp_create(int sampling_rate, int frame_size, int in_channels);

/**
 * Destroys and frees all resources for a DSP processor.
 */
void phonon_dsp_destroy(PhononDSPProcessor *dsp);

/**
 * Dynamically updates acoustic parameters for real-time DSP evaluation.
 * \param directivity Directivity attenuation factor (0.0 to 1.0).
 * \param distance_attenuation Distance attenuation factor (0.0 to 1.0).
 * \param occlusion Occlusion factor (0.0 = unoccluded, 1.0 = fully blocked).
 * \param transmission 3-band transmission coefficients for low, mid, high frequencies.
 * \param rel_direction Unit direction vector from listener to source in listener space.
 */
void phonon_dsp_set_parameters(PhononDSPProcessor *dsp,
                               float directivity,
                               float distance_attenuation,
                               float occlusion,
                               const float transmission[3],
                               const float rel_direction[3]);

/**
 * Process a buffer of audio through direct simulation (occlusion, transmission, distance)
 * and binaural HRTF spatialization.
 * \param dsp Processor instance.
 * \param in_interleaved Input interleaved audio samples (in_channels * num_samples).
 * \param in_channels Number of input channels (1 or 2).
 * \param out_interleaved Output interleaved stereo audio samples (2 * num_samples).
 * \param num_samples Number of audio samples per channel to process.
 */
void phonon_dsp_process(PhononDSPProcessor *dsp,
                        const float *in_interleaved,
                        int in_channels,
                        float *out_interleaved,
                        int num_samples);

/**
 * Speaker DSP management helpers by object pointer key.
 */
PhononDSPProcessor *phonon_speaker_dsp_get_or_create(const void *speaker_key);
void phonon_speaker_dsp_update(const void *speaker_key,
                               float occlusion,
                               const float transmission[3],
                               float distance_attenuation,
                               const float rel_direction[3]);
void phonon_speaker_dsp_destroy(const void *speaker_key);

#ifdef __cplusplus
}
#endif

#endif /* WITH_STEAM_AUDIO */
