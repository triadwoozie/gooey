/* SPDX-FileCopyrightText: 2026 Blender Authors
 *
 * SPDX-License-Identifier: Apache-2.0 */

#ifdef WITH_STEAM_AUDIO

#include "BKE_acoustics_scene.hh"

#include "DNA_material_types.h"
#include "DNA_mesh_types.h"
#include "DNA_object_types.h"
#include "DNA_scene_types.h"

#include "BKE_idprop.hh"
#include "BKE_material.hh"
#include "BKE_mesh.hh"
#include "BKE_object.hh"

#include "BLI_math_matrix.hh"
#include "BLI_math_vector.hh"
#include "BLI_span.hh"

#include "DEG_depsgraph.hh"
#include "DEG_depsgraph_query.hh"

#include "plugins/phonon/phonon_device.h"
#include "plugins/phonon/phonon_scene.h"
#include "plugins/phonon/phonon_dsp.h"
#include "plugins/phonon/phonon_simulator.h"

#include <vector>

static bool object_use_steam_audio(const Object *ob, bool all_meshes_mode)
{
  if (!ob) {
    return false;
  }
  if (ob->id.properties) {
    const IDProperty *prop = IDP_GetPropertyFromGroup(ob->id.properties, "use_steam_audio_mesh");
    if (prop) {
      if (prop->type == IDP_INT) {
        return IDP_Int(prop) != 0;
      }
      if (prop->type == IDP_BOOLEAN) {
        return IDP_Bool(prop) != false;
      }
    }
  }
  return all_meshes_mode;
}

void BKE_acoustics_scene_init()
{
  if (!phonon_device_is_initialized()) {
    phonon_device_init(48000, 512);
  }
}

void BKE_acoustics_scene_free()
{
  phonon_scene_clear_meshes();
  phonon_device_shutdown();
}

static bool acoustics_geometry_is_dirty(const Depsgraph *depsgraph, const Scene *scene)
{
  /* If no meshes have been committed yet, initial synchronization is required */
  if (phonon_scene_get_mesh_count() == 0) {
    return true;
  }

  /* If depsgraph does not report changes to objects or mesh data, skip mesh rebuild */
  if (depsgraph) {
    if (!DEG_id_type_updated(depsgraph, ID_OB) && !DEG_id_type_updated(depsgraph, ID_ME)) {
      return false;
    }
  }

  const bool all_meshes_mode = (scene->flag_audio & SCENE_AUDIO_STEAM_ALL_MESHES) != 0;
  bool dirty = false;

  DEGObjectIterSettings deg_iter_settings = {nullptr};
  deg_iter_settings.depsgraph = const_cast<Depsgraph *>(depsgraph);
  deg_iter_settings.flags = DEG_ITER_OBJECT_FLAG_LINKED_DIRECTLY |
                            DEG_ITER_OBJECT_FLAG_LINKED_INDIRECTLY |
                            DEG_ITER_OBJECT_FLAG_LINKED_VIA_SET;

  DEG_OBJECT_ITER_BEGIN (&deg_iter_settings, ob_eval) {
    if (ob_eval->type != OB_MESH) {
      continue;
    }

    const Object *ob_orig = DEG_get_original(ob_eval);
    if (!object_use_steam_audio(ob_orig, all_meshes_mode) &&
        !object_use_steam_audio(ob_eval, all_meshes_mode)) {
      continue;
    }

    if ((ob_eval->id.recalc & (ID_RECALC_GEOMETRY | ID_RECALC_TRANSFORM)) ||
        (ob_orig && (ob_orig->id.recalc & (ID_RECALC_GEOMETRY | ID_RECALC_TRANSFORM)))) {
      dirty = true;
      break;
    }

    const Mesh *mesh_eval = BKE_object_get_evaluated_mesh(ob_eval);
    if (mesh_eval && (mesh_eval->id.recalc & (ID_RECALC_GEOMETRY | ID_RECALC_TRANSFORM))) {
      dirty = true;
      break;
    }
  }
  DEG_OBJECT_ITER_END;

  return dirty;
}

void BKE_acoustics_scene_sync(Depsgraph *depsgraph, Scene *scene)
{
  if (!depsgraph || !scene) {
    return;
  }

  /* Guard: Only rebuild or triangulate static meshes if geometry or transforms actually changed */
  if (!acoustics_geometry_is_dirty(depsgraph, scene)) {
    return;
  }

  BKE_acoustics_scene_init();
  phonon_scene_clear_meshes();

  const bool all_meshes_mode = (scene->flag_audio & SCENE_AUDIO_STEAM_ALL_MESHES) != 0;

  DEGObjectIterSettings deg_iter_settings = {nullptr};
  deg_iter_settings.depsgraph = depsgraph;
  deg_iter_settings.flags = DEG_ITER_OBJECT_FLAG_LINKED_DIRECTLY |
                            DEG_ITER_OBJECT_FLAG_LINKED_INDIRECTLY |
                            DEG_ITER_OBJECT_FLAG_LINKED_VIA_SET;

  DEG_OBJECT_ITER_BEGIN (&deg_iter_settings, ob_eval) {
    if (ob_eval->type != OB_MESH) {
      continue;
    }

    /* Check if object is flagged for acoustics or all meshes mode is active */
    const Object *ob_orig = DEG_get_original(ob_eval);
    if (!object_use_steam_audio(ob_orig, all_meshes_mode) &&
        !object_use_steam_audio(ob_eval, all_meshes_mode)) {
      continue;
    }

    const Mesh *mesh_eval = BKE_object_get_evaluated_mesh(ob_eval);
    if (!mesh_eval || mesh_eval->verts_num == 0) {
      continue;
    }

    const blender::Span<blender::float3> positions = mesh_eval->vert_positions();
    const blender::Span<blender::int3> corner_tris = mesh_eval->corner_tris();
    const blender::Span<int> corner_verts = mesh_eval->corner_verts();

    if (positions.is_empty() || corner_tris.is_empty() || corner_verts.is_empty()) {
      continue;
    }

    /* Convert vertices to world coordinates and apply coordinate system transform:
     * Blender (X, Y, Z) -> Steam Audio (X, Z, -Y) */
    const blender::float4x4 &obmat = ob_eval->object_to_world();
    std::vector<float> sa_vertices;
    sa_vertices.resize(positions.size() * 3);

    for (int i = 0; i < positions.size(); i++) {
      const blender::float3 world_co = blender::math::transform_point(obmat, positions[i]);
      sa_vertices[i * 3 + 0] = world_co.x;       /* Right: Blender X */
      sa_vertices[i * 3 + 1] = world_co.z;       /* Up: Blender Z */
      sa_vertices[i * 3 + 2] = -world_co.y;      /* Ahead: Blender -Y */
    }

    /* Triangulate mesh */
    std::vector<int> sa_triangles;
    sa_triangles.resize(corner_tris.size() * 3);

    for (int i = 0; i < corner_tris.size(); i++) {
      const blender::int3 &tri = corner_tris[i];
      sa_triangles[i * 3 + 0] = corner_verts[tri[0]];
      sa_triangles[i * 3 + 1] = corner_verts[tri[1]];
      sa_triangles[i * 3 + 2] = corner_verts[tri[2]];
    }

    /* Material BSDF mapping */
    float metallic = 0.0f;
    float roughness = 0.5f;
    float transmission = 0.0f;
    float ior = 1.45f;

    Material *mat = BKE_object_material_get(const_cast<Object *>(ob_eval), 1);
    if (mat) {
      metallic = mat->metallic;
      roughness = mat->roughness;
    }

    IPLMaterial sa_mat = phonon_material_from_bsdf(metallic, roughness, transmission, ior);

    phonon_scene_add_mesh(sa_vertices.data(),
                          static_cast<int>(positions.size()),
                          sa_triangles.data(),
                          static_cast<int>(corner_tris.size()),
                          &sa_mat);
  }
  DEG_OBJECT_ITER_END;

  phonon_scene_commit();
  printf("[Steam Audio] Scene geometry synchronized: %d meshes committed to simulation BVH\n",
         phonon_scene_get_mesh_count());
}

void BKE_acoustics_scene_update_audio(Depsgraph *depsgraph, Scene *scene)
{
  if (!depsgraph || !scene) {
    return;
  }

  if (!(scene->flag_audio & SCENE_AUDIO_USE_STEAM_AUDIO)) {
    /* Steam Audio disabled: bypass simulation and return */
    return;
  }

  BKE_acoustics_scene_init();
  BKE_acoustics_scene_sync(depsgraph, scene);
  phonon_simulator_init();

  /* 1. Update Listener Transform from Active Camera */
  if (scene->camera) {
    const blender::float4x4 &obmat = scene->camera->object_to_world();
    blender::float3 loc = obmat.location();
    /* Blender camera points down -Z, with +Y as up */
    blender::float3 fwd = -blender::math::normalize(blender::float3(obmat[2].x, obmat[2].y, obmat[2].z));
    blender::float3 up = blender::math::normalize(blender::float3(obmat[1].x, obmat[1].y, obmat[1].z));

    /* Coordinate conversion Blender (X, Y, Z) -> Steam Audio (X, Z, -Y) */
    float sa_pos[3] = {loc.x, loc.z, -loc.y};
    float sa_ahead[3] = {fwd.x, fwd.z, -fwd.y};
    float sa_up[3] = {up.x, up.z, -up.y};

    phonon_simulator_set_listener(sa_pos, sa_ahead, sa_up);

    static int last_mesh_count = -1;
    int current_meshes = phonon_scene_get_mesh_count();
    if (current_meshes != last_mesh_count) {
      last_mesh_count = current_meshes;
      printf("[Steam Audio] Active: Listener at (%.2f, %.2f, %.2f) | %d tagged meshes\n",
             sa_pos[0], sa_pos[1], sa_pos[2], current_meshes);
    }
  }

  /* 2. Update Active Speakers and run Ray-Traced Direct Simulation */
  DEGObjectIterSettings deg_iter_settings = {nullptr};
  deg_iter_settings.depsgraph = depsgraph;
  deg_iter_settings.flags = DEG_ITER_OBJECT_FLAG_LINKED_DIRECTLY |
                            DEG_ITER_OBJECT_FLAG_LINKED_INDIRECTLY |
                            DEG_ITER_OBJECT_FLAG_LINKED_VIA_SET;

  DEG_OBJECT_ITER_BEGIN (&deg_iter_settings, ob_eval) {
    if (ob_eval->type != OB_SPEAKER) {
      continue;
    }

    const blender::float4x4 &obmat = ob_eval->object_to_world();
    blender::float3 loc = obmat.location();
    blender::float3 fwd = blender::math::normalize(blender::float3(obmat[1].x, obmat[1].y, obmat[1].z));
    blender::float3 up = blender::math::normalize(blender::float3(obmat[2].x, obmat[2].y, obmat[2].z));

    float sa_pos[3] = {loc.x, loc.z, -loc.y};
    float sa_ahead[3] = {fwd.x, fwd.z, -fwd.y};
    float sa_up[3] = {up.x, up.z, -up.y};

    /* Get or create simulation source for speaker */
    IPLSource source = phonon_simulator_source_get_or_create(ob_eval);
    if (!source) {
      continue;
    }

    phonon_simulator_source_set_pose(source, sa_pos, sa_ahead, sa_up);

    /* Run raycast simulation against evaluated IPLScene */
    float occlusion = 0.0f;
    float transmission[3] = {1.0f, 1.0f, 1.0f};
    float distance_atten = 1.0f;
    float rel_direction[3] = {0.0f, 0.0f, -1.0f};
    phonon_simulator_source_simulate(source, &occlusion, transmission, &distance_atten, rel_direction);

    /* Update DSP effect processor parameters for this speaker */
    phonon_speaker_dsp_update(ob_eval, occlusion, transmission, distance_atten, rel_direction);
  }
  DEG_OBJECT_ITER_END;
}

#endif /* WITH_STEAM_AUDIO */
