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
 * Initialize or update simulation environment with the active IPLScene.
 */
bool phonon_simulator_init();

/**
 * Commit scene changes to the simulator.
 */
void phonon_simulator_commit_scene();

/**
 * Shutdown simulator and all associated sources.
 */
void phonon_simulator_shutdown();

/**
 * Update listener position and orientation in Steam Audio coordinates.
 */
void phonon_simulator_set_listener(const float position[3],
                                   const float ahead[3],
                                   const float up[3]);

/**
 * Create or retrieve a simulation source identified by speaker key.
 */
IPLSource phonon_simulator_source_get_or_create(const void *speaker_key);

/**
 * Destroy a simulation source identified by speaker key.
 */
void phonon_simulator_source_destroy(const void *speaker_key);

/**
 * Set position and orientation of a simulation source in Steam Audio coordinates.
 */
void phonon_simulator_source_set_pose(IPLSource source,
                                      const float position[3],
                                      const float ahead[3],
                                      const float up[3]);

/**
 * Run direct raycast simulation against evaluated IPLScene geometry.
 * Returns occlusion factor (0.0 to 1.0), 3-band transmission coefficients,
 * distance attenuation, and relative unit direction in listener coordinates.
 */
void phonon_simulator_source_simulate(IPLSource source,
                                      float *out_occlusion,
                                      float out_transmission[3],
                                      float *out_distance_attenuation,
                                      float out_rel_direction[3]);

#ifdef __cplusplus
}
#endif

#endif /* WITH_STEAM_AUDIO */
