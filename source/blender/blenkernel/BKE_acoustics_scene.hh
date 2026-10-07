/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#pragma once

#ifdef WITH_STEAM_AUDIO

#include "BLI_sys_types.h"

struct Depsgraph;
struct Scene;

#ifdef __cplusplus
extern "C" {
#endif

/**
 * Initialize acoustics subsystem if needed.
 */
void BKE_acoustics_scene_init();

/**
 * Free acoustics subsystem resources.
 */
void BKE_acoustics_scene_free();

/**
 * Synchronize Blender evaluated scene geometry with Steam Audio.
 * Traverses evaluated objects in Depsgraph, extracts meshes tagged with
 * 'use_steam_audio_mesh', transforms coordinates from Blender (X, Y, Z) to Steam Audio (X, Z, -Y),
 * maps Principled BSDF / materials to IPLMaterial acoustic properties, and builds the acoustic scene.
 */
void BKE_acoustics_scene_sync(struct Depsgraph *depsgraph, struct Scene *scene);

/**
 * Dynamically synchronize listener transform (camera/viewport) and active speaker objects,
 * run ray-traced direct sound simulation (occlusion, transmission EQ, distance attenuation),
 * and update Steam Audio DSP parameters.
 */
void BKE_acoustics_scene_update_audio(struct Depsgraph *depsgraph, struct Scene *scene);

#ifdef __cplusplus
}
#endif

#endif /* WITH_STEAM_AUDIO */
