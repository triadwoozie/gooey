/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#ifdef WITH_STEAM_AUDIO

#include "phonon_dsp.h"
#include "phonon_device.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <mutex>
#include <vector>

struct PhononDSPProcessor {
  IPLDirectEffect direct_effect = nullptr;
  IPLBinauralEffect binaural_effect = nullptr;

  IPLAudioBuffer in_audio_buf{};
  IPLAudioBuffer direct_audio_buf{};
  IPLAudioBuffer binaural_audio_buf{};

  int sampling_rate = 48000;
  int frame_size = 512;
  int in_channels = 1;
  bool allocated = false;

  float directivity = 1.0f;
  float distance_attenuation = 1.0f;
  float occlusion = 0.0f;
  float transmission[3] = {1.0f, 1.0f, 1.0f};
  float rel_direction[3] = {0.0f, 0.0f, -1.0f};
};

static std::mutex g_speaker_dsp_mutex;
static std::map<const void *, PhononDSPProcessor *> g_speaker_dsps;

PhononDSPProcessor *phonon_dsp_create(int sampling_rate, int frame_size, int in_channels)
{
  if (!phonon_device_is_initialized()) {
    if (!phonon_device_init(sampling_rate, frame_size)) {
      return nullptr;
    }
  }

  IPLContext context = phonon_device_get_context();
  IPLHRTF hrtf = phonon_device_get_hrtf();
  if (!context || !hrtf) {
    return nullptr;
  }

  PhononDSPProcessor *dsp = new PhononDSPProcessor();
  dsp->sampling_rate = sampling_rate > 0 ? sampling_rate : 48000;
  dsp->frame_size = frame_size > 0 ? frame_size : 512;
  dsp->in_channels = (in_channels > 1) ? 2 : 1;

  IPLAudioSettings audio_settings{};
  audio_settings.samplingRate = dsp->sampling_rate;
  audio_settings.frameSize = dsp->frame_size;

  /* Direct effect */
  IPLDirectEffectSettings direct_settings{};
  direct_settings.numChannels = dsp->in_channels;
  IPLerror err = iplDirectEffectCreate(context, &audio_settings, &direct_settings, &dsp->direct_effect);
  if (err != IPL_STATUS_SUCCESS) {
    fprintf(stderr, "[Steam Audio DSP] Failed to create direct effect: %d\n", err);
    delete dsp;
    return nullptr;
  }

  /* Binaural effect */
  IPLBinauralEffectSettings binaural_settings{};
  binaural_settings.hrtf = hrtf;
  err = iplBinauralEffectCreate(context, &audio_settings, &binaural_settings, &dsp->binaural_effect);
  if (err != IPL_STATUS_SUCCESS) {
    fprintf(stderr, "[Steam Audio DSP] Failed to create binaural effect: %d\n", err);
    iplDirectEffectRelease(&dsp->direct_effect);
    delete dsp;
    return nullptr;
  }

  /* Allocate internal audio buffers */
  err = iplAudioBufferAllocate(context, dsp->in_channels, dsp->frame_size, &dsp->in_audio_buf);
  if (err != IPL_STATUS_SUCCESS) {
    iplBinauralEffectRelease(&dsp->binaural_effect);
    iplDirectEffectRelease(&dsp->direct_effect);
    delete dsp;
    return nullptr;
  }
  iplAudioBufferAllocate(context, dsp->in_channels, dsp->frame_size, &dsp->direct_audio_buf);
  iplAudioBufferAllocate(context, 2, dsp->frame_size, &dsp->binaural_audio_buf);
  dsp->allocated = true;

  return dsp;
}

void phonon_dsp_destroy(PhononDSPProcessor *dsp)
{
  if (!dsp) {
    return;
  }

  IPLContext context = phonon_device_get_context();
  if (context && dsp->allocated) {
    iplAudioBufferFree(context, &dsp->in_audio_buf);
    iplAudioBufferFree(context, &dsp->direct_audio_buf);
    iplAudioBufferFree(context, &dsp->binaural_audio_buf);
    dsp->allocated = false;
  }

  if (dsp->binaural_effect) {
    iplBinauralEffectRelease(&dsp->binaural_effect);
    dsp->binaural_effect = nullptr;
  }

  if (dsp->direct_effect) {
    iplDirectEffectRelease(&dsp->direct_effect);
    dsp->direct_effect = nullptr;
  }

  delete dsp;
}

void phonon_dsp_set_parameters(PhononDSPProcessor *dsp,
                               float directivity,
                               float distance_attenuation,
                               float occlusion,
                               const float transmission[3],
                               const float rel_direction[3])
{
  if (!dsp) {
    return;
  }

  dsp->directivity = std::clamp(directivity, 0.0f, 1.0f);
  dsp->distance_attenuation = std::clamp(distance_attenuation, 0.0f, 1.0f);
  dsp->occlusion = std::clamp(occlusion, 0.0f, 1.0f);
  if (transmission) {
    dsp->transmission[0] = std::clamp(transmission[0], 0.0f, 1.0f);
    dsp->transmission[1] = std::clamp(transmission[1], 0.0f, 1.0f);
    dsp->transmission[2] = std::clamp(transmission[2], 0.0f, 1.0f);
  }
  if (rel_direction) {
    dsp->rel_direction[0] = rel_direction[0];
    dsp->rel_direction[1] = rel_direction[1];
    dsp->rel_direction[2] = rel_direction[2];
  }
}

void phonon_dsp_process(PhononDSPProcessor *dsp,
                        const float *in_interleaved,
                        int in_channels,
                        float *out_interleaved,
                        int num_samples)
{
  if (!dsp || !in_interleaved || !out_interleaved || num_samples <= 0) {
    return;
  }

  IPLContext context = phonon_device_get_context();
  IPLHRTF hrtf = phonon_device_get_hrtf();
  if (!context || !hrtf) {
    return;
  }

  int processed = 0;
  while (processed < num_samples) {
    int chunk_size = std::min(num_samples - processed, dsp->frame_size);

    /* Deinterleave input audio into IPLAudioBuffer */
    const float *chunk_in = in_interleaved + processed * in_channels;
    iplAudioBufferDeinterleave(context, const_cast<float *>(chunk_in), &dsp->in_audio_buf);

    /* 1. Apply Direct Effect (occlusion, transmission, distance attenuation) */
    IPLDirectEffectParams direct_params{};
    direct_params.flags = static_cast<IPLDirectEffectFlags>(
        IPL_DIRECTEFFECTFLAGS_APPLYDISTANCEATTENUATION |
        IPL_DIRECTEFFECTFLAGS_APPLYAIRABSORPTION |
        IPL_DIRECTEFFECTFLAGS_APPLYDIRECTIVITY |
        IPL_DIRECTEFFECTFLAGS_APPLYOCCLUSION |
        IPL_DIRECTEFFECTFLAGS_APPLYTRANSMISSION);
    direct_params.transmissionType = IPL_TRANSMISSIONTYPE_FREQDEPENDENT;
    direct_params.distanceAttenuation = dsp->distance_attenuation;
    direct_params.airAbsorption[0] = 1.0f;
    direct_params.airAbsorption[1] = 1.0f;
    direct_params.airAbsorption[2] = 1.0f;
    direct_params.directivity = dsp->directivity;
    direct_params.occlusion = dsp->occlusion;
    direct_params.transmission[0] = dsp->transmission[0];
    direct_params.transmission[1] = dsp->transmission[1];
    direct_params.transmission[2] = dsp->transmission[2];

    iplDirectEffectApply(dsp->direct_effect, &direct_params, &dsp->in_audio_buf, &dsp->direct_audio_buf);

    /* 2. Apply Binaural HRTF Spatialization Effect */
    IPLBinauralEffectParams binaural_params{};
    binaural_params.direction = IPLVector3{dsp->rel_direction[0], dsp->rel_direction[1], dsp->rel_direction[2]};
    binaural_params.interpolation = IPL_HRTFINTERPOLATION_BILINEAR;
    binaural_params.spatialBlend = 1.0f;
    binaural_params.hrtf = hrtf;
    binaural_params.peakDelays = nullptr;

    iplBinauralEffectApply(dsp->binaural_effect, &binaural_params, &dsp->direct_audio_buf, &dsp->binaural_audio_buf);

    /* 3. Interleave processed 2-channel audio back into output buffer */
    float *chunk_out = out_interleaved + processed * 2;
    iplAudioBufferInterleave(context, &dsp->binaural_audio_buf, chunk_out);

    processed += chunk_size;
  }
}

PhononDSPProcessor *phonon_speaker_dsp_get_or_create(const void *speaker_key)
{
  std::lock_guard<std::mutex> lock(g_speaker_dsp_mutex);
  auto it = g_speaker_dsps.find(speaker_key);
  if (it != g_speaker_dsps.end()) {
    return it->second;
  }

  PhononDSPProcessor *dsp = phonon_dsp_create(48000, 512, 1);
  if (dsp) {
    g_speaker_dsps[speaker_key] = dsp;
  }
  return dsp;
}

void phonon_speaker_dsp_update(const void *speaker_key,
                               float occlusion,
                               const float transmission[3],
                               float distance_attenuation,
                               const float rel_direction[3])
{
  std::lock_guard<std::mutex> lock(g_speaker_dsp_mutex);
  auto it = g_speaker_dsps.find(speaker_key);
  if (it != g_speaker_dsps.end() && it->second) {
    phonon_dsp_set_parameters(it->second, 1.0f, distance_attenuation, occlusion, transmission, rel_direction);
  }
}

void phonon_speaker_dsp_destroy(const void *speaker_key)
{
  std::lock_guard<std::mutex> lock(g_speaker_dsp_mutex);
  auto it = g_speaker_dsps.find(speaker_key);
  if (it != g_speaker_dsps.end()) {
    if (it->second) {
      phonon_dsp_destroy(it->second);
    }
    g_speaker_dsps.erase(it);
  }
}

#endif /* WITH_STEAM_AUDIO */
