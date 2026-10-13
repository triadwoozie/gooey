/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#ifdef WITH_STEAM_AUDIO

#include "phonon_device.h"
#include <cstdio>
#include <mutex>

static std::mutex g_phonon_mutex;
static bool g_initialized = false;
static IPLContext g_context = nullptr;
static IPLHRTF g_hrtf = nullptr;
static IPLScene g_scene = nullptr;
static IPLAudioSettings g_audio_settings{};

static void IPLCALL phonon_log_callback(IPLLogLevel level, const char *message)
{
  switch (level) {
    case IPL_LOGLEVEL_INFO:
      printf("[Steam Audio] %s\n", message);
      break;
    case IPL_LOGLEVEL_WARNING:
      fprintf(stderr, "[Steam Audio Warning] %s\n", message);
      break;
    case IPL_LOGLEVEL_ERROR:
      fprintf(stderr, "[Steam Audio ERROR] %s\n", message);
      break;
    default:
      break;
  }
}

bool phonon_device_init(int sampling_rate, int frame_size)
{
  std::lock_guard<std::mutex> lock(g_phonon_mutex);
  if (g_initialized) {
    return true;
  }

  IPLContextSettings ctx_settings{};
  ctx_settings.version = STEAMAUDIO_VERSION;
  ctx_settings.logCallback = phonon_log_callback;
  ctx_settings.allocateCallback = nullptr;
  ctx_settings.freeCallback = nullptr;
  ctx_settings.simdLevel = IPL_SIMDLEVEL_AVX2;
  ctx_settings.flags = static_cast<IPLContextFlags>(0);

  IPLerror err = iplContextCreate(&ctx_settings, &g_context);
  if (err != IPL_STATUS_SUCCESS) {
    fprintf(stderr, "[Steam Audio] Failed to create context: error %d\n", err);
    return false;
  }

  /* Audio settings */
  g_audio_settings.samplingRate = sampling_rate > 0 ? sampling_rate : 48000;
  g_audio_settings.frameSize = frame_size > 0 ? frame_size : 512;

  /* HRTF */
  IPLHRTFSettings hrtf_settings{};
  hrtf_settings.type = IPL_HRTFTYPE_DEFAULT;
  hrtf_settings.volume = 1.0f;
  hrtf_settings.normType = IPL_HRTFNORMTYPE_NONE;

  err = iplHRTFCreate(g_context, &g_audio_settings, &hrtf_settings, &g_hrtf);
  if (err != IPL_STATUS_SUCCESS) {
    fprintf(stderr, "[Steam Audio] Failed to create HRTF: error %d\n", err);
    iplContextRelease(&g_context);
    g_context = nullptr;
    return false;
  }

  /* Scene */
  IPLSceneSettings scene_settings{};
  scene_settings.type = IPL_SCENETYPE_DEFAULT;

  err = iplSceneCreate(g_context, &scene_settings, &g_scene);
  if (err != IPL_STATUS_SUCCESS) {
    fprintf(stderr, "[Steam Audio] Failed to create scene: error %d\n", err);
    iplHRTFRelease(&g_hrtf);
    g_hrtf = nullptr;
    iplContextRelease(&g_context);
    g_context = nullptr;
    return false;
  }

  g_initialized = true;
  printf("[Steam Audio] Initialized successfully (Rate: %d Hz, Frame: %d)\n",
         g_audio_settings.samplingRate, g_audio_settings.frameSize);
  return true;
}

void phonon_device_shutdown()
{
  std::lock_guard<std::mutex> lock(g_phonon_mutex);
  if (!g_initialized) {
    return;
  }

  if (g_scene) {
    iplSceneRelease(&g_scene);
    g_scene = nullptr;
  }

  if (g_hrtf) {
    iplHRTFRelease(&g_hrtf);
    g_hrtf = nullptr;
  }

  if (g_context) {
    iplContextRelease(&g_context);
    g_context = nullptr;
  }

  g_initialized = false;
  printf("[Steam Audio] Shutdown complete\n");
}

bool phonon_device_is_initialized()
{
  std::lock_guard<std::mutex> lock(g_phonon_mutex);
  return g_initialized;
}

IPLContext phonon_device_get_context()
{
  std::lock_guard<std::mutex> lock(g_phonon_mutex);
  return g_context;
}

IPLHRTF phonon_device_get_hrtf()
{
  std::lock_guard<std::mutex> lock(g_phonon_mutex);
  return g_hrtf;
}

IPLScene phonon_device_get_scene()
{
  std::lock_guard<std::mutex> lock(g_phonon_mutex);
  return g_scene;
}

IPLAudioSettings phonon_device_get_audio_settings()
{
  std::lock_guard<std::mutex> lock(g_phonon_mutex);
  return g_audio_settings;
}

#endif /* WITH_STEAM_AUDIO */
