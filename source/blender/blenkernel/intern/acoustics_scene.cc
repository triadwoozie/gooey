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

#include <vector>

static bool object_use_steam_audio(const Object *ob)
{
  if (!ob) {
    return false;
  }
  if (ob->id.properties) {
    const IDProperty *prop = IDP_GetPropertyFromGroup(ob->id.properties, "use_steam_audio_mesh");
    if (prop) {
      if (prop->type == IDP_INT && IDP_Int(prop) != 0) {
        return true;
      }
      if (prop->type == IDP_BOOLEAN && IDP_Bool(prop)) {
        return true;
      }
    }
  }
  return false;
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

void BKE_acoustics_scene_sync(Depsgraph *depsgraph, Scene *scene)
{
  if (!depsgraph || !scene) {
    return;
  }

  BKE_acoustics_scene_init();
  phonon_scene_clear_meshes();

  DEGObjectIterSettings deg_iter_settings = {nullptr};
  deg_iter_settings.depsgraph = depsgraph;
  deg_iter_settings.flags = DEG_ITER_OBJECT_FLAG_LINKED_DIRECTLY |
                            DEG_ITER_OBJECT_FLAG_LINKED_INDIRECTLY |
                            DEG_ITER_OBJECT_FLAG_LINKED_VIA_SET;

  DEG_OBJECT_ITER_BEGIN (&deg_iter_settings, ob_eval) {
    if (ob_eval->type != OB_MESH) {
      continue;
    }

    /* Check if object is flagged for acoustics */
    const Object *ob_orig = DEG_get_original(ob_eval);
    if (!object_use_steam_audio(ob_orig) && !object_use_steam_audio(ob_eval)) {
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
}

#endif /* WITH_STEAM_AUDIO */
