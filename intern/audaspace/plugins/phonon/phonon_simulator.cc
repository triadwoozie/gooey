/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#ifdef WITH_STEAM_AUDIO

#include "phonon_simulator.h"
#include "phonon_device.h"
#include "phonon_scene.h"

#include <cmath>
#include <cstdio>
#include <map>
#include <mutex>

static std::mutex g_simulator_mutex;
static IPLSimulator g_simulator = nullptr;
static IPLCoordinateSpace3 g_listener_space{};

struct SourceEntry {
  IPLSource source = nullptr;
  IPLCoordinateSpace3 pose{};
};

static std::map<const void *, SourceEntry> g_sources;

static IPLCoordinateSpace3 make_coordinate_space(const float position[3],
                                                 const float ahead[3],
                                                 const float up[3])
{
  IPLCoordinateSpace3 cs{};
  cs.origin = IPLVector3{position[0], position[1], position[2]};

  float a_len = std::sqrt(ahead[0] * ahead[0] + ahead[1] * ahead[1] + ahead[2] * ahead[2]);
  if (a_len > 1e-6f) {
    cs.ahead = IPLVector3{ahead[0] / a_len, ahead[1] / a_len, ahead[2] / a_len};
  }
  else {
    cs.ahead = IPLVector3{0.0f, 0.0f, -1.0f};
  }

  float u_len = std::sqrt(up[0] * up[0] + up[1] * up[1] + up[2] * up[2]);
  if (u_len > 1e-6f) {
    cs.up = IPLVector3{up[0] / u_len, up[1] / u_len, up[2] / u_len};
  }
  else {
    cs.up = IPLVector3{0.0f, 1.0f, 0.0f};
  }

  /* right = cross(ahead, up) in Steam Audio right-handed coordinates */
  cs.right = IPLVector3{
      cs.ahead.y * cs.up.z - cs.ahead.z * cs.up.y,
      cs.ahead.z * cs.up.x - cs.ahead.x * cs.up.z,
      cs.ahead.x * cs.up.y - cs.ahead.y * cs.up.x,
  };

  return cs;
}

bool phonon_simulator_init()
{
  std::lock_guard<std::mutex> lock(g_simulator_mutex);
  if (g_simulator) {
    return true;
  }

  if (!phonon_device_is_initialized()) {
    if (!phonon_device_init(48000, 512)) {
      return false;
    }
  }

  IPLContext context = phonon_device_get_context();
  IPLScene scene = phonon_device_get_scene();
  if (!context || !scene) {
    return false;
  }

  IPLAudioSettings audio_settings = phonon_device_get_audio_settings();

  IPLSimulationSettings sim_settings{};
  sim_settings.flags = IPL_SIMULATIONFLAGS_DIRECT;
  sim_settings.sceneType = IPL_SCENETYPE_DEFAULT;
  sim_settings.maxNumOcclusionSamples = 16;
  sim_settings.samplingRate = audio_settings.samplingRate;
  sim_settings.frameSize = audio_settings.frameSize;

  IPLerror err = iplSimulatorCreate(context, &sim_settings, &g_simulator);
  if (err != IPL_STATUS_SUCCESS || !g_simulator) {
    fprintf(stderr, "[Steam Audio Simulator] Failed to create simulator: %d\n", err);
    return false;
  }

  iplSimulatorSetScene(g_simulator, scene);
  iplSimulatorCommit(g_simulator);

  return true;
}

void phonon_simulator_commit_scene()
{
  std::lock_guard<std::mutex> lock(g_simulator_mutex);
  if (g_simulator) {
    IPLScene scene = phonon_device_get_scene();
    if (scene) {
      iplSimulatorSetScene(g_simulator, scene);
      iplSimulatorCommit(g_simulator);
    }
  }
}

void phonon_simulator_shutdown()
{
  std::lock_guard<std::mutex> lock(g_simulator_mutex);
  if (g_simulator) {
    for (auto &pair : g_sources) {
      if (pair.second.source) {
        iplSourceRemove(pair.second.source, g_simulator);
        iplSourceRelease(&pair.second.source);
      }
    }
    g_sources.clear();

    iplSimulatorRelease(&g_simulator);
    g_simulator = nullptr;
  }
}

void phonon_simulator_set_listener(const float position[3],
                                   const float ahead[3],
                                   const float up[3])
{
  std::lock_guard<std::mutex> lock(g_simulator_mutex);
  g_listener_space = make_coordinate_space(position, ahead, up);
}

IPLSource phonon_simulator_source_get_or_create(const void *speaker_key)
{
  std::lock_guard<std::mutex> lock(g_simulator_mutex);
  if (!g_simulator) {
    if (!phonon_simulator_init()) {
      return nullptr;
    }
  }

  auto it = g_sources.find(speaker_key);
  if (it != g_sources.end()) {
    return it->second.source;
  }

  IPLSourceSettings src_settings{};
  src_settings.flags = IPL_SIMULATIONFLAGS_DIRECT;

  IPLSource source = nullptr;
  IPLerror err = iplSourceCreate(g_simulator, &src_settings, &source);
  if (err != IPL_STATUS_SUCCESS || !source) {
    fprintf(stderr, "[Steam Audio Simulator] Failed to create source: %d\n", err);
    return nullptr;
  }

  iplSourceAdd(source, g_simulator);
  iplSimulatorCommit(g_simulator);

  SourceEntry entry{};
  entry.source = source;
  float pos[3] = {0.0f, 0.0f, 0.0f};
  float ahead[3] = {0.0f, 0.0f, -1.0f};
  float up[3] = {0.0f, 1.0f, 0.0f};
  entry.pose = make_coordinate_space(pos, ahead, up);
  g_sources[speaker_key] = entry;

  return source;
}

void phonon_simulator_source_destroy(const void *speaker_key)
{
  std::lock_guard<std::mutex> lock(g_simulator_mutex);
  if (!g_simulator) {
    return;
  }

  auto it = g_sources.find(speaker_key);
  if (it != g_sources.end()) {
    if (it->second.source) {
      iplSourceRemove(it->second.source, g_simulator);
      iplSourceRelease(&it->second.source);
    }
    g_sources.erase(it);
    iplSimulatorCommit(g_simulator);
  }
}

void phonon_simulator_source_set_pose(IPLSource source,
                                      const float position[3],
                                      const float ahead[3],
                                      const float up[3])
{
  std::lock_guard<std::mutex> lock(g_simulator_mutex);
  for (auto &pair : g_sources) {
    if (pair.second.source == source) {
      pair.second.pose = make_coordinate_space(position, ahead, up);
      break;
    }
  }
}

void phonon_simulator_source_simulate(IPLSource source,
                                      float *out_occlusion,
                                      float out_transmission[3],
                                      float *out_distance_attenuation,
                                      float out_rel_direction[3])
{
  std::lock_guard<std::mutex> lock(g_simulator_mutex);
  if (!g_simulator || !source) {
    if (out_occlusion) *out_occlusion = 0.0f;
    if (out_transmission) {
      out_transmission[0] = 1.0f;
      out_transmission[1] = 1.0f;
      out_transmission[2] = 1.0f;
    }
    if (out_distance_attenuation) *out_distance_attenuation = 1.0f;
    if (out_rel_direction) {
      out_rel_direction[0] = 0.0f;
      out_rel_direction[1] = 0.0f;
      out_rel_direction[2] = -1.0f;
    }
    return;
  }

  IPLCoordinateSpace3 source_pose{};
  bool found = false;
  for (auto &pair : g_sources) {
    if (pair.second.source == source) {
      source_pose = pair.second.pose;
      found = true;
      break;
    }
  }

  if (!found) {
    float pos[3] = {0.0f, 0.0f, 0.0f};
    float ahead[3] = {0.0f, 0.0f, -1.0f};
    float up[3] = {0.0f, 1.0f, 0.0f};
    source_pose = make_coordinate_space(pos, ahead, up);
  }

  /* Set shared listener inputs */
  IPLSimulationSharedInputs shared_inputs{};
  shared_inputs.listener = g_listener_space;
  iplSimulatorSetSharedInputs(g_simulator, IPL_SIMULATIONFLAGS_DIRECT, &shared_inputs);

  /* Set source inputs */
  IPLSimulationInputs src_inputs{};
  src_inputs.flags = IPL_SIMULATIONFLAGS_DIRECT;
  src_inputs.directFlags = static_cast<IPLDirectSimulationFlags>(
      IPL_DIRECTSIMULATIONFLAGS_DISTANCEATTENUATION |
      IPL_DIRECTSIMULATIONFLAGS_AIRABSORPTION |
      IPL_DIRECTSIMULATIONFLAGS_OCCLUSION |
      IPL_DIRECTSIMULATIONFLAGS_TRANSMISSION);
  src_inputs.source = source_pose;
  src_inputs.distanceAttenuationModel.type = IPL_DISTANCEATTENUATIONTYPE_DEFAULT;
  src_inputs.airAbsorptionModel.type = IPL_AIRABSORPTIONTYPE_DEFAULT;
  src_inputs.occlusionType = IPL_OCCLUSIONTYPE_RAYCAST;
  src_inputs.numTransmissionRays = 4;
  iplSourceSetInputs(source, IPL_SIMULATIONFLAGS_DIRECT, &src_inputs);

  /* Run ray-traced direct simulation */
  iplSimulatorRunDirect(g_simulator);

  /* Retrieve results */
  IPLSimulationOutputs outputs{};
  iplSourceGetOutputs(source, IPL_SIMULATIONFLAGS_DIRECT, &outputs);

  if (out_occlusion) {
    *out_occlusion = outputs.direct.occlusion;
  }
  if (out_transmission) {
    out_transmission[0] = outputs.direct.transmission[0];
    out_transmission[1] = outputs.direct.transmission[1];
    out_transmission[2] = outputs.direct.transmission[2];
  }
  if (out_distance_attenuation) {
    *out_distance_attenuation = outputs.direct.distanceAttenuation;
  }

  /* Calculate relative direction in listener space */
  IPLContext context = phonon_device_get_context();
  if (context && out_rel_direction) {
    IPLVector3 rel = iplCalculateRelativeDirection(
        context,
        source_pose.origin,
        g_listener_space.origin,
        g_listener_space.ahead,
        g_listener_space.up);
    out_rel_direction[0] = rel.x;
    out_rel_direction[1] = rel.y;
    out_rel_direction[2] = rel.z;
  }
}

#endif /* WITH_STEAM_AUDIO */
