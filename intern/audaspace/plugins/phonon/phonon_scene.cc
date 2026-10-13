/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#ifdef WITH_STEAM_AUDIO

#include "phonon_scene.h"
#include "phonon_device.h"

#include <algorithm>
#include <cstdio>
#include <mutex>
#include <vector>

static std::mutex g_scene_mutex;
static std::vector<IPLStaticMesh> g_scene_meshes;

IPLMaterial phonon_material_from_bsdf(float metallic,
                                      float roughness,
                                      float transmission,
                                      float /*ior*/)
{
  IPLMaterial mat{};
  metallic = std::clamp(metallic, 0.0f, 1.0f);
  roughness = std::clamp(roughness, 0.0f, 1.0f);
  transmission = std::clamp(transmission, 0.0f, 1.0f);

  /* Default acoustic absorption presets (low, mid, high frequencies) */
  /* Generic: {0.10f, 0.20f, 0.30f} */
  /* Metal:   {0.20f, 0.07f, 0.06f} */
  /* Glass:   {0.06f, 0.03f, 0.02f} */

  const float base_abs[3] = {0.10f, 0.20f, 0.30f};
  const float metal_abs[3] = {0.20f, 0.07f, 0.06f};
  const float glass_abs[3] = {0.06f, 0.03f, 0.02f};

  for (int b = 0; b < IPL_NUM_BANDS; b++) {
    float abs_val = base_abs[b];
    if (metallic > 0.0f) {
      abs_val = (1.0f - metallic) * abs_val + metallic * metal_abs[b];
    }
    if (transmission > 0.0f) {
      abs_val = (1.0f - transmission) * abs_val + transmission * glass_abs[b];
    }
    mat.absorption[b] = std::clamp(abs_val, 0.01f, 0.99f);
  }

  /* Scattering: rougher surfaces scatter diffuse sound, smooth surfaces reflect specular */
  mat.scattering = std::clamp(0.05f + 0.85f * (roughness * roughness), 0.01f, 0.99f);

  /* Transmission: acoustic pass-through */
  const float base_trans[3] = {0.05f, 0.03f, 0.01f};
  const float glass_trans[3] = {0.060f, 0.044f, 0.011f};
  for (int b = 0; b < IPL_NUM_BANDS; b++) {
    float t_val = base_trans[b];
    if (transmission > 0.0f) {
      t_val = (1.0f - transmission) * t_val + transmission * glass_trans[b];
    }
    if (metallic > 0.0f) {
      t_val *= (1.0f - 0.9f * metallic);
    }
    mat.transmission[b] = std::clamp(t_val, 0.001f, 0.99f);
  }

  return mat;
}

IPLStaticMesh phonon_scene_add_mesh(const float *vertices,
                                    int num_vertices,
                                    const int *triangles,
                                    int num_triangles,
                                    const IPLMaterial *material)
{
  std::lock_guard<std::mutex> lock(g_scene_mutex);
  if (!phonon_device_is_initialized()) {
    if (!phonon_device_init(48000, 512)) {
      return nullptr;
    }
  }

  IPLScene scene = phonon_device_get_scene();
  if (!scene || num_vertices <= 0 || num_triangles <= 0 || !vertices || !triangles) {
    return nullptr;
  }

  IPLStaticMeshSettings mesh_settings{};
  mesh_settings.numVertices = num_vertices;
  mesh_settings.numTriangles = num_triangles;
  mesh_settings.numMaterials = 1;
  mesh_settings.vertices = const_cast<IPLVector3 *>(reinterpret_cast<const IPLVector3 *>(vertices));
  mesh_settings.triangles = const_cast<IPLTriangle *>(reinterpret_cast<const IPLTriangle *>(triangles));

  std::vector<IPLint32> mat_indices(num_triangles, 0);
  mesh_settings.materialIndices = mat_indices.data();

  IPLMaterial fallback_mat;
  if (!material) {
    fallback_mat = phonon_material_from_bsdf(0.0f, 0.5f, 0.0f, 1.45f);
    mesh_settings.materials = &fallback_mat;
  }
  else {
    mesh_settings.materials = const_cast<IPLMaterial *>(material);
  }

  IPLStaticMesh static_mesh = nullptr;
  IPLerror err = iplStaticMeshCreate(scene, &mesh_settings, &static_mesh);
  if (err != IPL_STATUS_SUCCESS || !static_mesh) {
    fprintf(stderr, "[Steam Audio] Failed to create static mesh: %d\n", err);
    return nullptr;
  }

  iplStaticMeshAdd(static_mesh, scene);
  g_scene_meshes.push_back(static_mesh);

  return static_mesh;
}

void phonon_scene_remove_mesh(IPLStaticMesh mesh)
{
  std::lock_guard<std::mutex> lock(g_scene_mutex);
  IPLScene scene = phonon_device_get_scene();
  if (!scene || !mesh) {
    return;
  }

  auto it = std::find(g_scene_meshes.begin(), g_scene_meshes.end(), mesh);
  if (it != g_scene_meshes.end()) {
    iplStaticMeshRemove(mesh, scene);
    iplStaticMeshRelease(&mesh);
    g_scene_meshes.erase(it);
  }
}

void phonon_scene_clear_meshes()
{
  std::lock_guard<std::mutex> lock(g_scene_mutex);
  IPLScene scene = phonon_device_get_scene();
  if (!scene) {
    g_scene_meshes.clear();
    return;
  }

  for (IPLStaticMesh &mesh : g_scene_meshes) {
    if (mesh) {
      iplStaticMeshRemove(mesh, scene);
      iplStaticMeshRelease(&mesh);
    }
  }
  g_scene_meshes.clear();
}

#include "phonon_simulator.h"

void phonon_scene_commit()
{
  std::lock_guard<std::mutex> lock(g_scene_mutex);
  IPLScene scene = phonon_device_get_scene();
  if (scene) {
    iplSceneCommit(scene);
    phonon_simulator_commit_scene();
  }
}

int phonon_scene_get_mesh_count()
{
  std::lock_guard<std::mutex> lock(g_scene_mutex);
  return static_cast<int>(g_scene_meshes.size());
}

#endif /* WITH_STEAM_AUDIO */
