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
 * Map standard Principled BSDF / physical material properties to a Steam Audio acoustic material.
 * \param metallic 0.0 to 1.0 (higher metallic reflects high frequencies, low transmission)
 * \param roughness 0.0 to 1.0 (affects diffuse acoustic scattering)
 * \param transmission 0.0 to 1.0 (sound transmission through wall / glass)
 * \param ior Index of refraction (acoustic impedance proxy)
 */
IPLMaterial phonon_material_from_bsdf(float metallic,
                                      float roughness,
                                      float transmission,
                                      float ior);

/**
 * Add a static triangle mesh to the Steam Audio scene.
 * \param vertices Array of vertex coordinates in Steam Audio space (X, Y, Z float triplets).
 * \param num_vertices Number of vertices (number of floats / 3).
 * \param triangles Array of triangle indices (int triplets).
 * \param num_triangles Number of triangles (number of ints / 3).
 * \param material Acoustic material applied to the mesh (or nullptr for fallback).
 * \return Handle to created static mesh, or NULL on failure.
 */
IPLStaticMesh phonon_scene_add_mesh(const float *vertices,
                                    int num_vertices,
                                    const int *triangles,
                                    int num_triangles,
                                    const IPLMaterial *material);

/**
 * Remove a static mesh from the scene and release it.
 */
void phonon_scene_remove_mesh(IPLStaticMesh mesh);

/**
 * Remove and release all active static meshes from the scene.
 */
void phonon_scene_clear_meshes();

/**
 * Commit all added / removed meshes to the Steam Audio BVH acceleration structure.
 */
void phonon_scene_commit();

/**
 * Returns the number of currently active meshes in the scene.
 */
int phonon_scene_get_mesh_count();

#ifdef __cplusplus
}
#endif

#endif /* WITH_STEAM_AUDIO */
