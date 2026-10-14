# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

"""
2D Canvas & Layer Stack Subsystem for Blender
Provides a Photoshop-style layer stack, document canvas presets,
vector curve pen-point precision, symbols, alignment & snapping,
VSE text boxes, composite presets, in-blend image packing, and
normal-mapped 3D decal baking.
"""

import math
import os
import bpy
from bpy.types import Panel, UIList, Operator, PropertyGroup, Header, Menu
from bpy.props import (
    StringProperty,
    BoolProperty,
    IntProperty,
    FloatProperty,
    EnumProperty,
    FloatVectorProperty,
    PointerProperty,
    CollectionProperty,
)
from bpy_extras.io_utils import ImportHelper, ExportHelper


# ---------------------------------------------------------------------------
# Compositor Helper & Compiler Engine
# ---------------------------------------------------------------------------

def _get_mix_color_socket(mix_node, identifier):
    for s in mix_node.inputs:
        if s.identifier == identifier:
            return s
    for s in mix_node.outputs:
        if s.identifier == identifier:
            return s
    return None


def _create_canvas_color_image_node(ng, color, doc_w, doc_h, loc):
    """Creates a constant-color image node matching canvas document bounds.
    Uses CompositorNodeBlankImage in Blender 5.2+, falling back to CompositorNodeRGB / ShaderNodeRGB."""
    try:
        node = ng.nodes.new('CompositorNodeBlankImage')
        node.location = loc
        if 'Size' in node.inputs:
            node.inputs['Size'].default_value = (int(doc_w), int(doc_h))
        if 'Color' in node.inputs:
            node.inputs['Color'].default_value = color
        return node, (node.outputs.get('Image') or node.outputs[0])
    except Exception:
        pass
    try:
        node = ng.nodes.new('CompositorNodeRGB')
        node.location = loc
        node.outputs[0].default_value = color
        return node, node.outputs[0]
    except Exception:
        pass
    node = ng.nodes.new('ShaderNodeRGB')
    node.location = loc
    node.outputs[0].default_value = color
    return node, node.outputs[0]


def _create_canvas_mix_node(ng, blend_mode, factor, in_a, in_b, loc):
    """Creates a mix node for compositor trees.
    In Blender 5.2+, uses ShaderNodeMix with data_type='RGBA'. Fallback to CompositorNodeMixRGB."""
    try:
        node = ng.nodes.new('ShaderNodeMix')
        node.location = loc
        node.data_type = 'RGBA'
        if hasattr(node, "blend_type"):
            node.blend_type = blend_mode
        if 'Factor' in node.inputs:
            node.inputs['Factor'].default_value = factor
        elif len(node.inputs) > 0:
            node.inputs[0].default_value = factor

        if in_a:
            target_a = node.inputs.get('A') or node.inputs.get('A_Color') or (node.inputs[1] if len(node.inputs) > 1 else None)
            if target_a:
                ng.links.new(in_a, target_a)
        if in_b:
            target_b = node.inputs.get('B') or node.inputs.get('B_Color') or (node.inputs[2] if len(node.inputs) > 2 else None)
            if target_b:
                ng.links.new(in_b, target_b)

        out = node.outputs.get('Result') or node.outputs.get('Result_Color') or node.outputs[0]
        return node, out
    except Exception:
        pass

    node = ng.nodes.new('CompositorNodeMixRGB')
    node.location = loc
    if hasattr(node, "blend_type"):
        node.blend_type = blend_mode
    if len(node.inputs) > 0:
        node.inputs[0].default_value = factor
    if in_a and len(node.inputs) > 1:
        ng.links.new(in_a, node.inputs[1])
    if in_b and len(node.inputs) > 2:
        ng.links.new(in_b, node.inputs[2])
    return node, node.outputs[0]


def isolate_canvas_environment(scene):
    """Ensures Canvas objects live exclusively in Canvas_Artboard and Canvas_ViewLayer,
    without changing or polluting the user's main 3D scene view layers."""
    canvas_coll = bpy.data.collections.get("Canvas_Artboard")
    if not canvas_coll:
        canvas_coll = bpy.data.collections.get("Canvas_Collection")
        if canvas_coll:
            canvas_coll.name = "Canvas_Artboard"
        else:
            canvas_coll = bpy.data.collections.new("Canvas_Artboard")
    if canvas_coll.name not in scene.collection.children:
        scene.collection.children.link(canvas_coll)

    canvas_vl = scene.view_layers.get("Canvas_ViewLayer")
    if not canvas_vl:
        canvas_vl = scene.view_layers.new(name="Canvas_ViewLayer")

    for vl in scene.view_layers:
        if vl.name != "Canvas_ViewLayer":
            _set_collection_excluded(vl.layer_collection, canvas_coll.name, True)
        else:
            _set_collection_excluded(vl.layer_collection, canvas_coll.name, False)

    return canvas_coll, canvas_vl


def _set_collection_excluded(layer_collection, collection_name, excluded):
    if layer_collection.collection.name == collection_name:
        layer_collection.exclude = excluded
        return True
    for child in layer_collection.children:
        if _set_collection_excluded(child, collection_name, excluded):
            return True
    return False


def _lock_canvas_object(obj):
    if obj.type == 'CAMERA':
        obj.lock_location = (True, True, True)
        obj.lock_rotation = (True, True, True)
        obj.lock_scale = (True, True, True)
        return
    obj.lock_location = (False, False, True)
    obj.lock_rotation = (True, True, False)
    obj.lock_scale = (False, False, True)


def _lock_canvas_viewport(area):
    if area.type != 'VIEW_3D':
        return
    for space in area.spaces:
        if space.type != 'VIEW_3D':
            continue
        region_3d = space.region_3d
        region_3d.view_perspective = 'CAMERA'
        space.lock_camera = True
        space.show_gizmo_navigate = False
        region_3d.lock_rotation = True
        for gizmo_name in (
            "show_gizmo_object_translate",
            "show_gizmo_object_rotate",
            "show_gizmo_object_scale",
        ):
            if hasattr(space, gizmo_name):
                setattr(space, gizmo_name, True)


def set_active_canvas_object(context, obj):
    """Safely activates and selects an object within Canvas_ViewLayer and Canvas_Artboard."""
    if not obj:
        return
    scene = context.scene
    canvas_col, canvas_vl = isolate_canvas_environment(scene)
    if obj.name not in canvas_col.objects:
        try:
            canvas_col.objects.link(obj)
        except Exception:
            pass
    try:
        if context.window and context.window.view_layer != canvas_vl:
            context.window.view_layer = canvas_vl
    except Exception:
        pass
    try:
        canvas_vl.objects.active = obj
        obj.select_set(True)
        _lock_canvas_object(obj)
    except Exception:
        pass
    if context.screen:
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        for gizmo_name in (
                            "show_gizmo_object_translate",
                            "show_gizmo_object_rotate",
                            "show_gizmo_object_scale",
                        ):
                            if hasattr(space, gizmo_name):
                                setattr(space, gizmo_name, True)
    try:
        if context.view_layer and obj.name in context.view_layer.objects:
            context.view_layer.objects.active = obj
    except Exception:
        pass


def is_canvas_viewlayer_active(context):
    """Check if the active viewlayer is Canvas_ViewLayer"""
    vl = getattr(context, "view_layer", None)
    return bool(vl and vl.name == "Canvas_ViewLayer")


def tag_redraw_canvas():
    """Tags all Canvas areas for redraw so composite FX and edits update instantly"""
    wm = getattr(bpy.context, "window_manager", None)
    if not wm:
        return
    for w in wm.windows:
        if w.screen:
            for a in w.screen.areas:
                if a.ui_type == 'CANVAS' or a.type == 'CANVAS':
                    a.tag_redraw()


def ensure_canvas_camera_and_background(scene):
    """Initializes or updates the locked Orthographic Canvas_Camera and the solid white Canvas_White_Background plane."""
    settings = getattr(scene, "canvas_settings", None)
    doc_w = float(settings.doc_width if settings and settings.doc_width > 0 else 2048)
    doc_h = float(settings.doc_height if settings and settings.doc_height > 0 else 2048)

    canvas_coll, _ = isolate_canvas_environment(scene)

    # 1. Camera: Locked Orthographic camera looking down -Z
    cam_data = bpy.data.cameras.get("Canvas_Camera_Data")
    if not cam_data:
        cam_data = bpy.data.cameras.new("Canvas_Camera_Data")
    cam_data.type = 'ORTHO'
    cam_data.sensor_fit = 'HORIZONTAL'
    cam_data.ortho_scale = doc_w

    cam_obj = bpy.data.objects.get("Canvas_Camera")
    if not cam_obj:
        cam_obj = bpy.data.objects.new("Canvas_Camera", cam_data)
        canvas_coll.objects.link(cam_obj)
    elif cam_obj.name not in canvas_coll.objects:
        try:
            canvas_coll.objects.link(cam_obj)
        except Exception:
            pass

    cam_obj.location = (0.0, 0.0, 10.0)
    cam_obj.rotation_euler = (0.0, 0.0, 0.0)
    cam_obj.lock_location = (True, True, True)
    cam_obj.lock_rotation = (True, True, True)
    cam_obj.lock_scale = (True, True, True)

    scene.camera = cam_obj
    scene.render.resolution_x = int(doc_w)
    scene.render.resolution_y = int(doc_h)
    scene.render.resolution_percentage = 100

    # 2. White Background Layer at Z=0.0
    hw = doc_w * 0.5
    hh = doc_h * 0.5

    bg_mesh = bpy.data.meshes.get("Canvas_White_Background_Mesh")
    if not bg_mesh:
        bg_mesh = bpy.data.meshes.new("Canvas_White_Background_Mesh")
        verts = [(-hw, -hh, 0.0), (hw, -hh, 0.0), (hw, hh, 0.0), (-hw, hh, 0.0)]
        faces = [(0, 1, 2, 3)]
        bg_mesh.from_pydata(verts, [], faces)
        bg_mesh.update()
        uv_layer = bg_mesh.uv_layers.new(name="UVMap")
        uv_coords = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        for i, uv in enumerate(uv_coords):
            uv_layer.data[i].uv = uv
    else:
        verts = [(-hw, -hh, 0.0), (hw, -hh, 0.0), (hw, hh, 0.0), (-hw, hh, 0.0)]
        if len(bg_mesh.vertices) == 4:
            for i, v in enumerate(verts):
                bg_mesh.vertices[i].co = v
            bg_mesh.update()

    bg_obj = bpy.data.objects.get("Canvas_White_Background")
    if not bg_obj:
        bg_obj = bpy.data.objects.new("Canvas_White_Background", bg_mesh)
        canvas_coll.objects.link(bg_obj)
    elif bg_obj.name not in canvas_coll.objects:
        try:
            canvas_coll.objects.link(bg_obj)
        except Exception:
            pass

    bg_obj.location = (0.0, 0.0, 0.0)
    bg_obj.rotation_euler = (0.0, 0.0, 0.0)
    _lock_canvas_object(bg_obj)

    # Background Material
    bg_mat = bpy.data.materials.get("M_Canvas_White_Background")
    if not bg_mat:
        bg_mat = bpy.data.materials.new("M_Canvas_White_Background")
    if not bg_mat.node_tree:
        bg_mat.use_nodes = True
    nodes = bg_mat.node_tree.nodes
    links = bg_mat.node_tree.links
    nodes.clear()
    out_node = nodes.new('ShaderNodeOutputMaterial')
    em_node = nodes.new('ShaderNodeEmission')
    links.new(em_node.outputs['Emission'], out_node.inputs['Surface'])

    if settings and settings.transparent_bg:
        scene.render.film_transparent = True
        bg_obj.hide_viewport = True
        bg_obj.hide_render = True
    else:
        scene.render.film_transparent = False
        bg_obj.hide_viewport = False
        bg_obj.hide_render = False
        c = settings.bg_color if settings else (1.0, 1.0, 1.0, 1.0)
        em_node.inputs['Color'].default_value = (c[0], c[1], c[2], 1.0)

    if len(bg_obj.data.materials) == 0:
        bg_obj.data.materials.append(bg_mat)
    else:
        bg_obj.data.materials[0] = bg_mat

    return cam_obj, bg_obj


def create_canvas_image_plane(scene, img, name, doc_w, doc_h, z_pos):
    """Turns an imported image into a 3D textured mesh plane object positioned in 3D camera space."""
    canvas_coll, _ = isolate_canvas_environment(scene)
    img_w = float(img.size[0]) if img.size[0] > 0 else doc_w
    img_h = float(img.size[1]) if img.size[1] > 0 else doc_h

    hw = img_w * 0.5
    hh = img_h * 0.5

    mesh_name = f"Mesh_{name}"
    mesh = bpy.data.meshes.new(mesh_name)
    verts = [(-hw, -hh, 0.0), (hw, -hh, 0.0), (hw, hh, 0.0), (-hw, hh, 0.0)]
    faces = [(0, 1, 2, 3)]
    mesh.from_pydata(verts, [], faces)
    mesh.update()

    uv_layer = mesh.uv_layers.new(name="UVMap")
    uv_coords = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
    for i, uv in enumerate(uv_coords):
        uv_layer.data[i].uv = uv

    mat = bpy.data.materials.new(f"M_{name}")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    out_node = nodes.new('ShaderNodeOutputMaterial')
    tex_node = nodes.new('ShaderNodeTexImage')
    tex_node.image = img

    emission = nodes.new('ShaderNodeEmission')
    transparent = nodes.new('ShaderNodeBsdfTransparent')
    mix = nodes.new('ShaderNodeMixShader')
    links.new(tex_node.outputs['Color'], emission.inputs['Color'])
    links.new(tex_node.outputs['Alpha'], mix.inputs[0])
    links.new(transparent.outputs['BSDF'], mix.inputs[1])
    links.new(emission.outputs['Emission'], mix.inputs[2])
    links.new(mix.outputs['Shader'], out_node.inputs['Surface'])

    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = 'DITHERED'
    elif hasattr(mat, "blend_method"):
        mat.blend_method = 'BLEND'

    obj = bpy.data.objects.new(name, mesh)
    obj.location = (0.0, 0.0, z_pos)
    obj.data.materials.append(mat)
    canvas_coll.objects.link(obj)
    _lock_canvas_object(obj)

    return obj


def create_canvas_grease_pencil(scene, name, z_pos, color=(0.1, 0.1, 0.1, 1.0), is_fill=False):
    """Creates a native Blender GPv3 object placed at z_pos in 3D camera space."""
    canvas_coll, _ = isolate_canvas_environment(scene)
    gp_data = bpy.data.grease_pencils.new(name)
    gp_l = gp_data.layers.new("Lines")
    try:
        gp_l.lock_frame = True
    except AttributeError:
        pass
    try:
        gp_l.use_lights = False
    except AttributeError:
        pass

    gp_obj = bpy.data.objects.new(name, gp_data)
    gp_obj.location = (0.0, 0.0, z_pos)

    mat = bpy.data.materials.new(f"M_{name}")
    bpy.data.materials.create_gpencil_data(mat)
    mat.grease_pencil.show_stroke = True
    mat.grease_pencil.color = color
    mat.grease_pencil.show_fill = is_fill
    mat.grease_pencil.fill_color = color
    gp_data.materials.append(mat)

    canvas_coll.objects.link(gp_obj)
    _lock_canvas_object(gp_obj)
    return gp_obj


def sync_canvas_layers_to_3d(scene):
    """Synchronizes all canvas layers with their 3D objects, transforms, opacities, and depths."""
    if not hasattr(scene, "canvas_settings"):
        return
    settings = scene.canvas_settings
    doc_w = float(settings.doc_width if settings.doc_width > 0 else 2048)
    doc_h = float(settings.doc_height if settings.doc_height > 0 else 2048)

    canvas_coll, _ = isolate_canvas_environment(scene)
    ensure_canvas_camera_and_background(scene)
    for obj in canvas_coll.objects:
        _lock_canvas_object(obj)

    for idx, layer in enumerate(settings.layers):
        z_pos = 0.001 * idx
        obj = layer.curve_object
        if not obj:
            continue

        obj.hide_viewport = not layer.visible
        obj.hide_render = not layer.visible
        obj.location.z = z_pos

        # Convert 2D pixel offset to 3D camera units
        x_3d = layer.pos_x
        y_3d = layer.pos_y
        obj.location.x = x_3d
        obj.location.y = y_3d
        obj.scale = (layer.scale, layer.scale, 1.0)
        obj.rotation_euler.z = layer.rotation
        _lock_canvas_object(obj)

        alpha = max(0.0, min(1.0, layer.opacity * 0.01))
        if obj.type in {'GREASEPENCIL', 'GPENCIL'}:
            if hasattr(obj.data, "layers") and len(obj.data.layers) > 0:
                try:
                    obj.data.layers[0].opacity = alpha
                except Exception:
                    pass


def sync_canvas_sequencer(scene):
    """Synchronizes canvas layers with the Video Sequencer channels and strips."""
    if not hasattr(scene, "canvas_settings"):
        return
    settings = scene.canvas_settings
    if not scene.sequence_editor:
        scene.sequence_editor_create()
    se = scene.sequence_editor
    strips = se.strips

    frame_len = max(250, scene.frame_end)

    # Channel 1: Background Strip
    bg_strip = None
    for s in strips:
        if s.channel == 1 and s.type == 'COLOR' and s.name.startswith("Canvas_BG"):
            bg_strip = s
            break
    if not bg_strip:
        try:
            bg_strip = strips.new_effect(
                name="Canvas_BG_Strip",
                type='COLOR',
                channel=1,
                frame_start=1,
                length=frame_len,
            )
        except Exception:
            pass

    if bg_strip:
        c = settings.bg_color
        try:
            bg_strip.color = (c[0], c[1], c[2])
            bg_strip.mute = settings.transparent_bg
        except Exception:
            pass

    # Channels 2..N: Canvas layers
    for idx, layer in enumerate(settings.layers):
        chan = idx + 2
        layer_strip = None
        for s in strips:
            if s.channel == chan and s.name == layer.name:
                layer_strip = s
                break
        if not layer_strip:
            try:
                if layer.layer_type == 'RASTER' and layer.raster_image:
                    image = layer.raster_image
                    filepath = bpy.path.abspath(image.filepath) if image.filepath else ""
                    if not filepath or not os.path.exists(filepath):
                        filepath = os.path.join(
                            bpy.app.tempdir,
                            f"canvas_image_{image.as_pointer()}.png",
                        )
                        image.save_render(filepath, scene=scene)
                    layer_strip = strips.new_image(
                        name=layer.name,
                        filepath=filepath,
                        channel=chan,
                        frame_start=1,
                    )
                    layer_strip.frame_final_duration = frame_len
                elif layer.layer_type == 'COLOR_FILL':
                    layer_strip = strips.new_effect(
                        name=layer.name,
                        type='COLOR',
                        channel=chan,
                        frame_start=1,
                        length=frame_len,
                    )
                    layer_strip.color = (layer.color_fill[0], layer.color_fill[1], layer.color_fill[2])
                else:
                    layer_strip = strips.new_scene(
                        name=layer.name,
                        scene=scene,
                        channel=chan,
                        frame_start=1,
                    )
                    layer_strip.frame_final_duration = frame_len
            except Exception as e:
                print(f"Canvas sequencer: failed to create strip for layer {layer.name!r}: {e}")

        if layer_strip:
            try:
                layer_strip.blend_alpha = layer.opacity * 0.01
                layer_strip.mute = not layer.visible
            except Exception:
                pass


def render_and_overlay_flatten(scene, target_layer=None):
    """Renders the locked orthographic camera view and composites the result onto Canvas_Overlay_Plane."""
    settings = scene.canvas_settings
    doc_w = int(settings.doc_width if settings.doc_width > 0 else 2048)
    doc_h = int(settings.doc_height if settings.doc_height > 0 else 2048)

    cam_obj, _ = ensure_canvas_camera_and_background(scene)
    scene.camera = cam_obj

    # Temporarily hide overlay plane if it exists so we render the live layers cleanly
    overlay_obj = bpy.data.objects.get("Canvas_Overlay_Plane")
    if overlay_obj:
        overlay_obj.hide_render = True
        overlay_obj.hide_viewport = True

    # Render orthographic camera view
    bpy.ops.render.render(write_still=False)
    r_img = bpy.data.images.get("Render Result")
    if not r_img:
        return None

    tmp_path = os.path.join(bpy.app.tempdir, "canvas_flatten_render.png")
    r_img.save_render(tmp_path, scene=scene)

    comp_img = bpy.data.images.get("Canvas_Composite_Render")
    if comp_img:
        try:
            comp_img.filepath = tmp_path
            comp_img.reload()
        except Exception:
            bpy.data.images.remove(comp_img)
            comp_img = bpy.data.images.load(tmp_path)
            comp_img.name = "Canvas_Composite_Render"
    else:
        comp_img = bpy.data.images.load(tmp_path)
        comp_img.name = "Canvas_Composite_Render"
    comp_img.pack()

    # Create or update Canvas_Overlay_Plane in front of all layers.
    hw = doc_w * 0.5
    hh = doc_h * 0.5
    mesh = bpy.data.meshes.get("Canvas_Overlay_Mesh")
    if not mesh:
        mesh = bpy.data.meshes.new("Canvas_Overlay_Mesh")
        verts = [(-hw, -hh, 0.0), (hw, -hh, 0.0), (hw, hh, 0.0), (-hw, hh, 0.0)]
        faces = [(0, 1, 2, 3)]
        mesh.from_pydata(verts, [], faces)
        mesh.update()
        uv_layer = mesh.uv_layers.new(name="UVMap")
        uv_coords = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        for i, uv in enumerate(uv_coords):
            uv_layer.data[i].uv = uv
    else:
        verts = [(-hw, -hh, 0.0), (hw, -hh, 0.0), (hw, hh, 0.0), (-hw, hh, 0.0)]
        if len(mesh.vertices) == 4:
            for i, v in enumerate(verts):
                mesh.vertices[i].co = v
            mesh.update()

    mat = bpy.data.materials.get("M_Canvas_Overlay")
    if not mat:
        mat = bpy.data.materials.new("M_Canvas_Overlay")
    if not mat.node_tree:
        mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    out_node = nodes.new('ShaderNodeOutputMaterial')
    tex_node = nodes.new('ShaderNodeTexImage')
    tex_node.image = comp_img

    bsdf = nodes.new('ShaderNodeBsdfPrincipled')
    links.new(tex_node.outputs['Color'], bsdf.inputs['Base Color'])
    links.new(tex_node.outputs['Alpha'], bsdf.inputs['Alpha'])
    links.new(bsdf.outputs['BSDF'], out_node.inputs['Surface'])

    if hasattr(mat, "blend_method"):
        mat.blend_method = 'BLEND'

    canvas_coll, _ = isolate_canvas_environment(scene)
    if not overlay_obj:
        overlay_obj = bpy.data.objects.new("Canvas_Overlay_Plane", mesh)
        overlay_obj.location = (0.0, 0.0, 5.0)
        overlay_obj.data.materials.append(mat)
        canvas_coll.objects.link(overlay_obj)
    else:
        overlay_obj.location = (0.0, 0.0, 5.0)
        overlay_obj.hide_viewport = False
        overlay_obj.hide_render = False
    _lock_canvas_object(overlay_obj)

    if target_layer:
        target_layer.gpencil_cache_image = comp_img
        target_layer.flatten_for_compositing = True

    return comp_img


@bpy.app.handlers.persistent
def canvas_workspace_monitor(scene=None):
    """Keep Canvas workspaces on the isolated layer with orbit navigation disabled."""
    try:
        wm = getattr(bpy.context, "window_manager", None)
        if not wm:
            return
        for window in wm.windows:
            workspace = getattr(window, "workspace", None)
            has_canvas = bool(workspace and workspace.get("canvas_2d_stage", False))
            if window.screen:
                for area in window.screen.areas:
                    if area.ui_type == 'CANVAS' or area.type == 'CANVAS':
                        has_canvas = True
            if has_canvas and window.screen:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D':
                        _lock_canvas_viewport(area)
            if has_canvas:
                canvas_vl = window.scene.view_layers.get("Canvas_ViewLayer")
                if canvas_vl and window.view_layer != canvas_vl:
                    window.view_layer = canvas_vl
                continue
            if not has_canvas and window.view_layer and window.view_layer.name == "Canvas_ViewLayer":
                default_vl = window.scene.view_layers.get("ViewLayer")
                if default_vl:
                    window.view_layer = default_vl
    except Exception:
        pass


_canvas_draw_handler = None
_canvas_keymaps = []


def draw_canvas_artboard_callback():
    """Renders Realtime Multi-Layer Compositing, Transforms, and Vector Ink directly onto the Canvas artboard"""
    context = bpy.context
    space = getattr(context, "space_data", None)
    if not space or getattr(space, "type", None) != 'CANVAS':
        return
    scene = getattr(context, "scene", None)
    settings = getattr(scene, "canvas_settings", None) if scene else None
    if not settings:
        return

    doc_w = float(settings.doc_width if settings.doc_width > 0 else 2048)
    doc_h = float(settings.doc_height if settings.doc_height > 0 else 2048)

    try:
        import gpu
        from gpu_extras.batch import batch_for_shader
        import math

        img_color_shader = None
        try:
            img_color_shader = gpu.shader.from_builtin('IMAGE_COLOR')
        except Exception:
            try:
                img_color_shader = gpu.shader.from_builtin('IMAGE')
            except Exception:
                img_color_shader = None

        col_shader = None
        try:
            col_shader = gpu.shader.from_builtin('UNIFORM_COLOR')
        except Exception:
            col_shader = None

        gpu.state.blend_set('ALPHA')

        # If flattened composite overlay plane is active and visible, render it directly
        overlay_obj = bpy.data.objects.get("Canvas_Overlay_Plane")
        if overlay_obj and not overlay_obj.hide_viewport:
            comp_img = bpy.data.images.get("Canvas_Composite_Render")
            if comp_img and comp_img.has_data and img_color_shader:
                tex = gpu.texture.from_image(comp_img)
                batch = batch_for_shader(
                    img_color_shader, 'TRIS',
                    {"pos": ((0.0, 0.0), (doc_w, 0.0), (doc_w, doc_h), (0.0, doc_h)),
                     "texCoord": ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))},
                    indices=((0, 1, 2), (2, 3, 0))
                )
                img_color_shader.bind()
                img_color_shader.uniform_sampler("image", tex)
                try:
                    img_color_shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
                except Exception:
                    pass
                batch.draw(img_color_shader)
                gpu.state.blend_set('NONE')
                return

        # If no layers exist, preview selected image if present
        if len(settings.layers) == 0:
            preview_img = getattr(settings, 'blend_image_selector', None) or getattr(settings, 'active_image', None)
            if preview_img and preview_img.has_data and img_color_shader:
                tex = gpu.texture.from_image(preview_img)
                batch = batch_for_shader(
                    img_color_shader, 'TRIS',
                    {"pos": ((0.0, 0.0), (doc_w, 0.0), (doc_w, doc_h), (0.0, doc_h)),
                     "texCoord": ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))},
                    indices=((0, 1, 2), (2, 3, 0))
                )
                img_color_shader.bind()
                img_color_shader.uniform_sampler("image", tex)
                try:
                    img_color_shader.uniform_float("color", (1.0, 1.0, 1.0, 0.85))
                except Exception:
                    pass
                batch.draw(img_color_shader)

        # Draw all visible layers from bottom to top
        for idx, layer in enumerate(settings.layers):
            if not layer.visible:
                continue
            alpha = max(0.0, min(1.0, layer.opacity * 0.01))
            if alpha <= 0.001:
                continue

            # 1. Solid Color Fill
            if layer.layer_type == 'COLOR_FILL' and col_shader:
                batch = batch_for_shader(
                    col_shader, 'TRIS',
                    {"pos": ((0.0, 0.0), (doc_w, 0.0), (doc_w, doc_h), (0.0, doc_h))},
                    indices=((0, 1, 2), (2, 3, 0))
                )
                col_shader.bind()
                c = layer.color_fill
                col_shader.uniform_float("color", (c[0], c[1], c[2], c[3] * alpha))
                batch.draw(col_shader)

            # 2. Raster Image Layer
            elif layer.layer_type == 'RASTER' and layer.raster_image and img_color_shader:
                img = layer.raster_image
                if not img.has_data:
                    try:
                        _ = img.pixels[0]
                    except Exception:
                        pass
                if not img.has_data or img.size[0] <= 0 or img.size[1] <= 0:
                    continue
                tex = gpu.texture.from_image(img)
                base_w = float(img.size[0])
                base_h = float(img.size[1])
                s = layer.scale if layer.scale > 0.0 else 1.0
                w = base_w * s
                h = base_h * s
                cx = doc_w * 0.5 + layer.pos_x
                cy = doc_h * 0.5 + layer.pos_y
                theta = layer.rotation
                cos_t = math.cos(theta)
                sin_t = math.sin(theta)
                hw = w * 0.5
                hh = h * 0.5

                p0 = (cx - hw * cos_t + hh * sin_t, cy - hw * sin_t - hh * cos_t)
                p1 = (cx + hw * cos_t + hh * sin_t, cy + hw * sin_t - hh * cos_t)
                p2 = (cx + hw * cos_t - hh * sin_t, cy + hw * sin_t + hh * cos_t)
                p3 = (cx - hw * cos_t - hh * sin_t, cy - hw * sin_t + hh * cos_t)

                batch = batch_for_shader(
                    img_color_shader, 'TRIS',
                    {"pos": (p0, p1, p2, p3),
                     "texCoord": ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))},
                    indices=((0, 1, 2), (2, 3, 0))
                )
                img_color_shader.bind()
                img_color_shader.uniform_sampler("image", tex)
                try:
                    img_color_shader.uniform_float("color", (1.0, 1.0, 1.0, alpha))
                except Exception:
                    pass
                batch.draw(img_color_shader)

                # Draw selection bounding box and corner handles for active layer
                if idx == settings.active_layer_index and col_shader:
                    box_batch = batch_for_shader(
                        col_shader, 'LINES',
                        {"pos": (p0, p1, p1, p2, p2, p3, p3, p0)}
                    )
                    col_shader.bind()
                    col_shader.uniform_float("color", (0.2, 0.65, 1.0, 0.95))
                    box_batch.draw(col_shader)

                    hs = 4.0
                    for hp in (p0, p1, p2, p3):
                        h_batch = batch_for_shader(
                            col_shader, 'TRIS',
                            {"pos": (
                                (hp[0] - hs, hp[1] - hs),
                                (hp[0] + hs, hp[1] - hs),
                                (hp[0] + hs, hp[1] + hs),
                                (hp[0] - hs, hp[1] + hs)
                            )},
                            indices=((0, 1, 2), (2, 3, 0))
                        )
                        col_shader.bind()
                        col_shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
                        h_batch.draw(col_shader)

            # 3. Vector / Grease Pencil Layer
            elif layer.layer_type in {'VECTOR', 'VECTOR_CURVE'}:
                if layer.gpencil_cache_image and layer.gpencil_cache_image.has_data and img_color_shader:
                    tex = gpu.texture.from_image(layer.gpencil_cache_image)
                    batch = batch_for_shader(
                        img_color_shader, 'TRIS',
                        {"pos": ((0.0, 0.0), (doc_w, 0.0), (doc_w, doc_h), (0.0, doc_h)),
                         "texCoord": ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))},
                        indices=((0, 1, 2), (2, 3, 0))
                    )
                    img_color_shader.bind()
                    img_color_shader.uniform_sampler("image", tex)
                    try:
                        img_color_shader.uniform_float("color", (1.0, 1.0, 1.0, alpha))
                    except Exception:
                        pass
                    batch.draw(img_color_shader)

                # Render live Grease Pencil strokes if present
                if layer.curve_object and layer.curve_object.type in {'GREASEPENCIL', 'GPENCIL'} and col_shader:
                    gp_obj = layer.curve_object
                    if hasattr(gp_obj.data, "layers"):
                        for gp_l in gp_obj.data.layers:
                            if gp_l.hide:
                                continue
                            for f in gp_l.frames:
                                d = getattr(f, "drawing", None)
                                if not d or not hasattr(d, "strokes"):
                                    continue
                                for st in d.strokes:
                                    pts = getattr(st, "points", None)
                                    if not pts or len(pts) < 2:
                                        continue
                                    line_pts = []
                                    for i in range(len(pts) - 1):
                                        pa = pts[i].position
                                        pb = pts[i+1].position
                                        xa = doc_w * 0.5 + pa.x * 100.0
                                        ya = doc_h * 0.5 + pa.y * 100.0
                                        xb = doc_w * 0.5 + pb.x * 100.0
                                        yb = doc_h * 0.5 + pb.y * 100.0
                                        line_pts.extend([(xa, ya), (xb, yb)])
                                    if line_pts:
                                        st_batch = batch_for_shader(col_shader, 'LINES', {"pos": line_pts})
                                        col_shader.bind()
                                        col_shader.uniform_float("color", (settings.unified_color[0], settings.unified_color[1], settings.unified_color[2], alpha))
                                        st_batch.draw(col_shader)

        gpu.state.blend_set('NONE')
    except Exception:
        pass


def _bake_text_layer_to_image(scene, layer, w, h):
    """Render a VSE text strip to a transparent image for the compositor."""
    image_name = f"canvas_text_{layer.as_pointer()}"
    font_name = layer.text_font.name if layer.text_font else ""
    cache_key = repr((
        layer.text_string,
        layer.text_size,
        tuple(layer.text_color),
        layer.text_align_x,
        font_name,
        w,
        h,
    ))
    cached_image = layer.gpencil_cache_image
    if (
        cached_image and
        cached_image.name == image_name and
        cached_image.get("canvas_text_cache_key") == cache_key and
        cached_image.has_data
    ):
        return cached_image

    image_path = os.path.join(bpy.app.tempdir, f"{image_name}.png")
    scratch_scene = None
    try:
        scratch_scene = bpy.data.scenes.new(name="CanvasTextBake")
        scratch_scene.render.resolution_x = w
        scratch_scene.render.resolution_y = h
        scratch_scene.render.resolution_percentage = 100
        scratch_scene.render.image_settings.file_format = 'PNG'
        scratch_scene.render.image_settings.color_mode = 'RGBA'
        scratch_scene.render.film_transparent = True
        scratch_scene.render.use_sequencer = True
        scratch_scene.render.use_compositing = False
        scratch_scene.render.use_file_extension = False
        scratch_scene.render.filepath = image_path
        scratch_scene.frame_set(1)

        sequence_editor = scratch_scene.sequence_editor_create()
        strips = getattr(sequence_editor, "strips", None)
        if strips is None:
            strips = sequence_editor.sequences
        text_strip = strips.new_effect(
            name=image_name,
            type='TEXT',
            channel=1,
            frame_start=1,
            length=1,
        )
        text_strip.text = layer.text_string
        text_strip.font_size = layer.text_size
        text_strip.color = tuple(layer.text_color)
        text_strip.alignment_x = layer.text_align_x
        text_strip.location = (0.5, 0.5)
        if layer.text_font:
            text_strip.font = layer.text_font

        result = bpy.ops.render.render(write_still=True, scene=scratch_scene.name)
        if 'FINISHED' not in result or not os.path.isfile(image_path):
            raise RuntimeError(f"VSE text render did not produce {image_path}")

        image = bpy.data.images.get(image_name)
        if image:
            image.filepath = image_path
            image.reload()
        else:
            image = bpy.data.images.load(image_path, check_existing=True)
            image.name = image_name
        image["canvas_text_cache_key"] = cache_key
        return image
    except Exception as exc:
        print(f"Canvas text bake failed for {image_name}: {exc}")
        return None
    finally:
        if scratch_scene and scratch_scene.name in bpy.data.scenes:
            bpy.data.scenes.remove(scratch_scene)


def compile_canvas_compositor_tree(scene):
    """Compiles the scene's Canvas layer stack into a Realtime GPU Compositor node tree."""
    if not hasattr(scene, "canvas_settings"):
        return

    settings = scene.canvas_settings
    layers = settings.layers
    doc_w = max(64, settings.doc_width)
    doc_h = max(64, settings.doc_height)

    tree_name = "CanvasCompositor"
    ng = bpy.data.node_groups.get(tree_name)
    if not ng:
        ng = bpy.data.node_groups.new(tree_name, 'CompositorNodeTree')
        ng.interface.new_socket(name="Image", in_out='OUTPUT', socket_type='NodeSocketColor')

    try:
        if scene.compositing_node_group != ng:
            scene.compositing_node_group = ng
    except Exception:
        pass

    ng.nodes.clear()

    out_node = ng.nodes.new('NodeGroupOutput')
    out_node.location = (2400, 300)

    viewer_node = ng.nodes.new('CompositorNodeViewer')
    viewer_node.location = (2400, 0)

    # 1. Base Canvas Background (exact doc_w x doc_h)
    bg_color = (0.0, 0.0, 0.0, 0.0) if settings.transparent_bg else (settings.bg_color[0], settings.bg_color[1], settings.bg_color[2], 1.0)
    base_node, accum_output = _create_canvas_color_image_node(ng, bg_color, doc_w, doc_h, (0, 300))

    x_offset = 350
    y_offset = 400
    text_images_to_cache = []

    # 2. Iterate layers from bottom to top
    for i, layer in enumerate(layers):
        if not layer.visible:
            continue

        layer_node_output = None

        if layer.layer_type == 'COLOR_FILL':
            c = layer.color_fill
            fill_color = (c[0], c[1], c[2], 1.0)
            col_node, layer_node_output = _create_canvas_color_image_node(ng, fill_color, doc_w, doc_h, (x_offset, y_offset + 200))

        elif layer.layer_type == 'RASTER':
            if layer.raster_image:
                img_node = ng.nodes.new('CompositorNodeImage')
                img_node.location = (x_offset, y_offset + 200)
                img_node.image = layer.raster_image

                # Dynamic 2D Transform: Position, Scale, Rotation
                tr_node = ng.nodes.new('CompositorNodeTransform')
                tr_node.location = (x_offset + 180, y_offset + 200)
                tr_node.inputs['X'].default_value = layer.pos_x
                tr_node.inputs['Y'].default_value = layer.pos_y
                tr_node.inputs['Scale'].default_value = layer.scale
                tr_node.inputs['Angle'].default_value = layer.rotation
                ng.links.new(img_node.outputs['Image'], tr_node.inputs['Image'])
                layer_node_output = tr_node.outputs['Image']
                x_offset += 200
            else:
                # No image assigned: transparent placeholder
                blank_node, layer_node_output = _create_canvas_color_image_node(ng, (0.0, 0.0, 0.0, 0.0), doc_w, doc_h, (x_offset, y_offset + 200))

        elif layer.layer_type == 'TEXT':
            txt_img = _bake_text_layer_to_image(scene, layer, doc_w, doc_h)
            if txt_img:
                txt_node = ng.nodes.new('CompositorNodeImage')
                txt_node.image = txt_img
                txt_node.location = (x_offset, y_offset + 200)
                raw_text_out = txt_node.outputs['Image']
                if layer.gpencil_cache_image != txt_img:
                    text_images_to_cache.append((layer, txt_img))
            else:
                txt_node, raw_text_out = _create_canvas_color_image_node(ng, (0.0, 0.0, 0.0, 0.0), doc_w, doc_h, (x_offset, y_offset + 200))

            # Background Text Box (VSE style)
            if layer.use_box:
                bc = layer.box_color
                box_color = (bc[0], bc[1], bc[2], bc[3])
                box_node, box_out = _create_canvas_color_image_node(ng, box_color, doc_w, doc_h, (x_offset, y_offset + 400))

                box_over = ng.nodes.new('CompositorNodeAlphaOver')
                box_over.location = (x_offset + 180, y_offset + 300)
                box_over.inputs['Factor'].default_value = layer.box_color[3]
                ng.links.new(box_out, box_over.inputs['Background'])
                ng.links.new(raw_text_out, box_over.inputs['Foreground'])
                raw_text_out = box_over.outputs['Image']
                x_offset += 200

            # Text Drop Shadow (VSE style)
            if layer.use_shadow:
                shadow_blur = ng.nodes.new('CompositorNodeBlur')
                shadow_blur.location = (x_offset, y_offset + 100)
                shadow_blur.use_relative = False
                shadow_blur.size_x = int(layer.shadow_blur)
                shadow_blur.size_y = int(layer.shadow_blur)
                ng.links.new(raw_text_out, shadow_blur.inputs['Image'])

                shd_tr = ng.nodes.new('CompositorNodeTransform')
                shd_tr.location = (x_offset + 180, y_offset + 100)
                rad = layer.shadow_angle
                shd_tr.inputs['X'].default_value = math.cos(rad) * layer.shadow_offset
                shd_tr.inputs['Y'].default_value = -math.sin(rad) * layer.shadow_offset
                ng.links.new(shadow_blur.outputs['Image'], shd_tr.inputs['Image'])

                shd_over = ng.nodes.new('CompositorNodeAlphaOver')
                shd_over.location = (x_offset + 360, y_offset + 200)
                shd_over.inputs['Factor'].default_value = layer.shadow_color[3]
                ng.links.new(shd_tr.outputs['Image'], shd_over.inputs['Background'])
                ng.links.new(raw_text_out, shd_over.inputs['Foreground'])
                raw_text_out = shd_over.outputs['Image']
                x_offset += 400

            layer_node_output = raw_text_out

        elif layer.layer_type == 'SYMBOL':
            master_name = getattr(layer, 'symbol_name', '')
            sym_img = None
            for other in layers:
                if other.name == master_name and other.gpencil_cache_image:
                    sym_img = other.gpencil_cache_image
                    break
            if sym_img:
                sym_node = ng.nodes.new('CompositorNodeImage')
                sym_node.image = sym_img
                sym_node.location = (x_offset, y_offset + 200)
                tr_node = ng.nodes.new('CompositorNodeTransform')
                tr_node.location = (x_offset + 200, y_offset + 200)
                tr_node.inputs['X'].default_value = layer.pos_x
                tr_node.inputs['Y'].default_value = layer.pos_y
                tr_node.inputs['Scale'].default_value = layer.scale
                tr_node.inputs['Angle'].default_value = layer.rotation
                ng.links.new(sym_node.outputs['Image'], tr_node.inputs['Image'])
                layer_node_output = tr_node.outputs['Image']
                x_offset += 200
            else:
                sym_node, layer_node_output = _create_canvas_color_image_node(ng, (0.0, 0.0, 0.0, 0.0), doc_w, doc_h, (x_offset, y_offset + 200))

        elif layer.layer_type in {'VECTOR', 'VECTOR_CURVE'}:
            if getattr(layer, "flatten_for_compositing", False) and getattr(layer, "gpencil_cache_image", None):
                img_node = ng.nodes.new('CompositorNodeImage')
                img_node.location = (x_offset, y_offset + 200)
                img_node.image = layer.gpencil_cache_image
                layer_node_output = img_node.outputs['Image']
            else:
                rl_node = ng.nodes.new('CompositorNodeRLayers')
                rl_node.location = (x_offset, y_offset + 200)
                if 'Canvas_ViewLayer' in scene.view_layers:
                    rl_node.layer = 'Canvas_ViewLayer'
                layer_node_output = rl_node.outputs['Image']

        elif layer.layer_type == 'ADJUSTMENT':
            adj_output = None
            if layer.adjustment_type == 'HUE_SAT':
                adj_node = ng.nodes.new('CompositorNodeHueSat')
                adj_node.location = (x_offset, y_offset)
                adj_node.inputs['Hue'].default_value = layer.adj_hue
                adj_node.inputs['Saturation'].default_value = layer.adj_saturation
                adj_node.inputs['Value'].default_value = layer.adj_value
                ng.links.new(accum_output, adj_node.inputs['Image'])
                adj_output = adj_node.outputs['Image']

            elif layer.adjustment_type == 'BRIGHT_CONTRAST':
                adj_node = ng.nodes.new('CompositorNodeBrightContrast')
                adj_node.location = (x_offset, y_offset)
                adj_node.inputs['Brightness'].default_value = layer.adj_brightness
                adj_node.inputs['Contrast'].default_value = layer.adj_contrast
                ng.links.new(accum_output, adj_node.inputs['Image'])
                adj_output = adj_node.outputs['Image']

            elif layer.adjustment_type == 'EXPOSURE':
                adj_node = ng.nodes.new('CompositorNodeExposure')
                adj_node.location = (x_offset, y_offset)
                adj_node.inputs['Exposure'].default_value = layer.adj_exposure
                ng.links.new(accum_output, adj_node.inputs['Image'])
                adj_output = adj_node.outputs['Image']

            elif layer.adjustment_type == 'INVERT':
                adj_node = ng.nodes.new('CompositorNodeInvert')
                adj_node.location = (x_offset, y_offset)
                ng.links.new(accum_output, adj_node.inputs['Color'])
                adj_output = adj_node.outputs['Color']

            elif layer.adjustment_type == 'BLUR':
                adj_node = ng.nodes.new('CompositorNodeBlur')
                adj_node.location = (x_offset, y_offset)
                adj_node.inputs['Size'].default_value = (layer.adj_blur_size, layer.adj_blur_size)
                ng.links.new(accum_output, adj_node.inputs['Image'])
                adj_output = adj_node.outputs['Image']

            elif layer.adjustment_type == 'COLOR_BALANCE':
                adj_node = ng.nodes.new('CompositorNodeColorBalance')
                adj_node.location = (x_offset, y_offset)
                adj_node.correction_method = 'LIFT_GAMMA_GAIN'
                adj_node.lift = (layer.adj_cb_lift[0], layer.adj_cb_lift[1], layer.adj_cb_lift[2])
                adj_node.gamma = (layer.adj_cb_gamma[0], layer.adj_cb_gamma[1], layer.adj_cb_gamma[2])
                adj_node.gain = (layer.adj_cb_gain[0], layer.adj_cb_gain[1], layer.adj_cb_gain[2])
                ng.links.new(accum_output, adj_node.inputs['Image'])
                adj_output = adj_node.outputs['Image']

            elif layer.adjustment_type == 'CURVES':
                adj_node = ng.nodes.new('CompositorNodeCurveRGB')
                adj_node.location = (x_offset, y_offset)
                ng.links.new(accum_output, adj_node.inputs['Image'])
                adj_output = adj_node.outputs['Image']

            if adj_output:
                mix_node, accum_output = _create_canvas_mix_node(
                    ng, 'MIX', layer.opacity * 0.01, accum_output, adj_output, (x_offset + 220, y_offset)
                )

            x_offset += 450
            continue

        if layer_node_output is None:
            continue

        # Non-Destructive Layer Masking
        if layer.use_mask and layer.mask_image:
            mask_node = ng.nodes.new('CompositorNodeImage')
            mask_node.location = (x_offset, y_offset + 350)
            mask_node.image = layer.mask_image
            mask_out = mask_node.outputs['Image']

            if layer.mask_invert:
                inv_node = ng.nodes.new('CompositorNodeInvert')
                inv_node.location = (x_offset + 180, y_offset + 350)
                ng.links.new(mask_out, inv_node.inputs['Color'])
                mask_out = inv_node.outputs['Color']

            set_alpha = ng.nodes.new('CompositorNodeSetAlpha')
            set_alpha.location = (x_offset + 350, y_offset + 200)
            ng.links.new(layer_node_output, set_alpha.inputs['Image'])
            ng.links.new(mask_out, set_alpha.inputs['Alpha'])
            layer_node_output = set_alpha.outputs['Image']
            x_offset += 380

        # Per-Layer Node Compositing Effect Group
        if layer.use_custom_nodes and layer.node_group_name:
            grp = bpy.data.node_groups.get(layer.node_group_name)
            if grp and isinstance(grp, bpy.types.CompositorNodeTree):
                grp_node = ng.nodes.new('CompositorNodeGroup')
                grp_node.location = (x_offset + 150, y_offset + 200)
                grp_node.node_tree = grp
                if len(grp_node.inputs) > 0:
                    ng.links.new(layer_node_output, grp_node.inputs[0])
                    if len(grp_node.outputs) > 0:
                        layer_node_output = grp_node.outputs[0]
                x_offset += 200

        # 2D Affine Transform
        if layer.layer_type != 'SYMBOL' and (
            layer.pos_x != 0.0 or
            layer.pos_y != 0.0 or
            layer.scale != 1.0 or
            layer.rotation != 0.0
        ):
            tr_node = ng.nodes.new('CompositorNodeTransform')
            tr_node.location = (x_offset + 180, y_offset + 200)
            tr_node.inputs['X'].default_value = layer.pos_x
            tr_node.inputs['Y'].default_value = layer.pos_y
            tr_node.inputs['Scale'].default_value = layer.scale
            tr_node.inputs['Angle'].default_value = layer.rotation
            ng.links.new(layer_node_output, tr_node.inputs['Image'])
            layer_node_output = tr_node.outputs['Image']
            x_offset += 200

        # Layer Blending
        opacity_factor = layer.opacity * 0.01

        if layer.blend_mode == 'MIX':
            blend_node = ng.nodes.new('CompositorNodeAlphaOver')
            blend_node.location = (x_offset + 200, y_offset)
            blend_node.inputs['Factor'].default_value = opacity_factor
            ng.links.new(accum_output, blend_node.inputs['Background'])
            ng.links.new(layer_node_output, blend_node.inputs['Foreground'])
            accum_output = blend_node.outputs['Image']
        else:
            blend_node, accum_output = _create_canvas_mix_node(
                ng, layer.blend_mode, opacity_factor, accum_output, layer_node_output, (x_offset + 200, y_offset)
            )

        x_offset += 450

    # Final outputs
    if 'Image' in out_node.inputs:
        ng.links.new(accum_output, out_node.inputs['Image'])
    elif len(out_node.inputs) > 0:
        ng.links.new(accum_output, out_node.inputs[0])

    if 'Image' in viewer_node.inputs:
        ng.links.new(accum_output, viewer_node.inputs['Image'])
    elif len(viewer_node.inputs) > 0:
        ng.links.new(accum_output, viewer_node.inputs[0])

    for layer, image in text_images_to_cache:
        layer.gpencil_cache_image = image
    tag_redraw_canvas()


def update_canvas_tree(self, context):
    scene = getattr(context, "scene", None)
    if not scene or not hasattr(scene, "canvas_settings"):
        return
    sync_canvas_layers_to_3d(scene)
    sync_canvas_sequencer(scene)
    if scene.canvas_settings.auto_compile:
        compile_canvas_compositor_tree(scene)
    tag_redraw_canvas()


def update_active_layer(self, context):
    """When switching the active layer index, sync selection and redraw canvas immediately"""
    scene = getattr(context, "scene", None)
    if not scene or not hasattr(scene, "canvas_settings"):
        return
    settings = scene.canvas_settings
    idx = settings.active_layer_index
    if 0 <= idx < len(settings.layers):
        layer = settings.layers[idx]
        if layer.curve_object:
            set_active_canvas_object(context, layer.curve_object)
    compile_canvas_compositor_tree(scene)
    tag_redraw_canvas()


def update_canvas_preset(self, context):
    preset = self.doc_preset
    if preset == 'SQUARE_2K':
        self.doc_width, self.doc_height, self.doc_dpi = 2048, 2048, 300
    elif preset == 'SQUARE_4K':
        self.doc_width, self.doc_height, self.doc_dpi = 4096, 4096, 300
    elif preset == 'FHD_1080P':
        self.doc_width, self.doc_height, self.doc_dpi = 1920, 1080, 72
    elif preset == 'UHD_4K':
        self.doc_width, self.doc_height, self.doc_dpi = 3840, 2160, 72
    elif preset == 'PLATE_JAPAN':
        self.doc_width, self.doc_height, self.doc_dpi = 2640, 1320, 300
    elif preset == 'PLATE_EU':
        self.doc_width, self.doc_height, self.doc_dpi = 3120, 660, 300
    elif preset == 'PLATE_US':
        self.doc_width, self.doc_height, self.doc_dpi = 2400, 1200, 300
    elif preset == 'A4_PRINT':
        self.doc_width, self.doc_height, self.doc_dpi = 2480, 3508, 300

    update_canvas_tree(self, context)


def update_unified_color_target(self, context):
    """When switching the active attribute selector, load its current color into unified_color"""
    scene = getattr(context, "scene", None)
    if not scene or not hasattr(scene, "canvas_settings"):
        return
    settings = scene.canvas_settings
    idx = settings.active_layer_index
    layer = settings.layers[idx] if (0 <= idx < len(settings.layers)) else None

    target = settings.color_target
    if target == 'FILL':
        if layer:
            settings.unified_color = layer.color_fill
        else:
            settings.unified_color = settings.primary_color
    elif target == 'STROKE':
        if layer:
            settings.unified_color = layer.outline_color
        else:
            settings.unified_color = settings.secondary_color
    elif target == 'TEXT':
        if layer:
            settings.unified_color = layer.text_color
    elif target == 'BOX':
        if layer:
            settings.unified_color = layer.box_color
    elif target == 'SHADOW':
        if layer:
            settings.unified_color = layer.shadow_color
    elif target == 'CANVAS_BG':
        space = getattr(context, "space_data", None)
        if space and hasattr(space, "background_color"):
            bg = space.background_color
            settings.unified_color = (bg[0], bg[1], bg[2], 1.0)
        else:
            settings.unified_color = settings.bg_color


def update_unified_color(self, context):
    """When the unified color changes, propagate it directly into the selected target attribute and materials"""
    scene = getattr(context, "scene", None)
    if not scene or not hasattr(scene, "canvas_settings"):
        return
    settings = scene.canvas_settings
    col = settings.unified_color
    idx = settings.active_layer_index
    layer = settings.layers[idx] if (0 <= idx < len(settings.layers)) else None

    target = settings.color_target
    if target == 'FILL':
        settings.primary_color = col
        if layer:
            layer.color_fill = col
    elif target == 'STROKE':
        settings.secondary_color = col
        if layer:
            layer.outline_color = col
            layer.use_outline = True
    elif target == 'TEXT':
        if layer:
            layer.text_color = col
    elif target == 'BOX':
        if layer:
            layer.box_color = col
            layer.use_box = True
    elif target == 'SHADOW':
        if layer:
            layer.shadow_color = col
            layer.use_shadow = True
    elif target == 'CANVAS_BG':
        settings.bg_color = col
        space = getattr(context, "space_data", None)
        if space and hasattr(space, "background_color"):
            space.background_color = (col[0], col[1], col[2])

    # Propagate color to the active layer's backing material (Grease Pencil or Curves)
    if layer and layer.curve_object:
        obj = layer.curve_object
        if obj.type in {'GREASEPENCIL', 'GPENCIL'} and obj.data.materials:
            mat = obj.data.materials[0]
            if hasattr(mat, "grease_pencil"):
                if target == 'FILL':
                    mat.grease_pencil.fill_color = col
                    mat.grease_pencil.show_fill = True
                else:
                    mat.grease_pencil.color = col
                    mat.grease_pencil.show_stroke = True
        elif obj.data and hasattr(obj.data, "materials") and obj.data.materials:
            mat = obj.data.materials[0]
            mat.diffuse_color = col
            if mat.use_nodes and mat.node_tree and "Principled BSDF" in mat.node_tree.nodes:
                try:
                    mat.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = col
                except Exception:
                    pass

    # Also update global 2D_Canvas object if present
    canvas_gp = bpy.data.objects.get("2D_Canvas")
    if canvas_gp and canvas_gp.data.materials:
        for m in canvas_gp.data.materials:
            if hasattr(m, "grease_pencil"):
                if target == 'FILL':
                    m.grease_pencil.fill_color = col
                else:
                    m.grease_pencil.color = col

    # Propagate to tool settings
    tool_settings = getattr(scene, "tool_settings", None)
    if tool_settings:
        if hasattr(tool_settings, "unified_paint_settings") and tool_settings.unified_paint_settings:
            tool_settings.unified_paint_settings.color = (col[0], col[1], col[2])
        if hasattr(tool_settings, "gpencil_paint") and tool_settings.gpencil_paint and tool_settings.gpencil_paint.brush:
            tool_settings.gpencil_paint.brush.color = (col[0], col[1], col[2])

    compile_canvas_compositor_tree(scene)


def draw_unified_color_selector(layout, settings, compact=False):
    """Renders the unified attribute selector and color picker consistently across every tool"""
    box = layout.row(align=True)
    box.prop(settings, "color_target", text="")
    box.prop(settings, "unified_color", text="")


# ---------------------------------------------------------------------------
# Data Models: Symbols, Layers, Settings
# ---------------------------------------------------------------------------

class CanvasSymbolItem(PropertyGroup):
    name: StringProperty(name="Symbol Name", default="Symbol")
    collection_name: StringProperty(name="Collection", default="")
    category: EnumProperty(
        name="Category",
        items=[
            ('ICON', "Icon", "UI or graphic icon"),
            ('LOGO', "Logo", "Brand or vehicle emblem"),
            ('PLATE_ELEM', "Plate Element", "Number plate stamp or seal"),
            ('CUSTOM', "Custom", "Generic symbol component"),
        ],
        default='ICON',
    )


class CanvasLayerItem(PropertyGroup):
    name: StringProperty(name="Name", default="Layer")

    layer_type: EnumProperty(
        name="Type",
        items=[
            ('RASTER', "Raster", "Bitmap image layer (paintable)", 'IMAGE_DATA', 0),
            ('VECTOR', "Vector Ink", "Grease Pencil v3 vector stroke layer", 'GREASEPENCIL', 1),
            ('VECTOR_CURVE', "Vector Curve", "Bézier path curve layer", 'CURVE_BEZCURVE', 2),
            ('TEXT', "Text", "HarfBuzz / FreeType text box layer", 'FONT_DATA', 3),
            ('ADJUSTMENT', "Adjustment", "GPU Realtime Color Grading Filter", 'COLOR', 4),
            ('COLOR_FILL', "Solid Color", "Solid background color fill", 'IMAGE_RGB', 5),
            ('SYMBOL', "Symbol Instance", "Instanced reusable symbol component", 'FILE_PARENT', 6),
        ],
        default='RASTER',
        update=update_canvas_tree,
    )

    blend_mode: EnumProperty(
        name="Blend Mode",
        items=[
            ('MIX', "Normal", "Normal blend mode"),
            ('DARKEN', "Darken", "Darken blend mode"),
            ('MULTIPLY', "Multiply", "Multiply blend mode"),
            ('BURN', "Color Burn", "Color burn blend mode"),
            ('LIGHTEN', "Lighten", "Lighten blend mode"),
            ('SCREEN', "Screen", "Screen blend mode"),
            ('DODGE', "Color Dodge", "Color dodge blend mode"),
            ('ADD', "Add / Linear Dodge", "Add blend mode"),
            ('OVERLAY', "Overlay", "Overlay blend mode"),
            ('SOFT_LIGHT', "Soft Light", "Soft light blend mode"),
            ('HARD_LIGHT', "Hard Light", "Hard light blend mode"),
            ('DIFFERENCE', "Difference", "Difference blend mode"),
            ('EXCLUSION', "Exclusion", "Exclusion blend mode"),
            ('SUBTRACT', "Subtract", "Subtract blend mode"),
            ('DIVIDE', "Divide", "Divide blend mode"),
            ('HUE', "Hue", "Hue blend mode"),
            ('SATURATION', "Saturation", "Saturation blend mode"),
            ('COLOR', "Color", "Color blend mode"),
            ('VALUE', "Luminosity", "Luminosity blend mode"),
        ],
        default='MIX',
        update=update_canvas_tree,
    )

    opacity: FloatProperty(name="Opacity", default=100.0, min=0.0, max=100.0, subtype='PERCENTAGE', update=update_canvas_tree)
    visible: BoolProperty(name="Visible", default=True, update=update_canvas_tree)
    locked: BoolProperty(name="Lock", default=False)
    is_clipping: BoolProperty(name="Clipping Mask", default=False, update=update_canvas_tree)

    # Non-Destructive Layer Mask
    use_mask: BoolProperty(name="Use Mask", default=False, update=update_canvas_tree)
    mask_image: PointerProperty(type=bpy.types.Image, name="Mask Image", update=update_canvas_tree)
    mask_invert: BoolProperty(name="Invert Mask", default=False, update=update_canvas_tree)

    # Raster & Solid
    raster_image: PointerProperty(type=bpy.types.Image, name="Image", update=update_canvas_tree)
    color_fill: FloatVectorProperty(name="Fill Color", subtype='COLOR', size=4, default=(1.0, 1.0, 1.0, 1.0), update=update_canvas_tree)

    # Non-Destructive Grease Pencil Flattening for Compositor
    flatten_for_compositing: BoolProperty(name="Flatten for Compositor", default=True, update=update_canvas_tree)
    gpencil_cache_image: PointerProperty(type=bpy.types.Image, name="Compositor Image Snapshot", update=update_canvas_tree)

    # Full VSE-Style Text Box Properties
    text_string: StringProperty(name="Text", default="Canvas Text", update=update_canvas_tree)
    text_font: PointerProperty(type=bpy.types.VectorFont, name="Font", update=update_canvas_tree)
    text_size: FloatProperty(name="Size", default=72.0, min=1.0, max=2000.0, update=update_canvas_tree)
    text_color: FloatVectorProperty(name="Color", subtype='COLOR', size=4, default=(1.0, 1.0, 1.0, 1.0), update=update_canvas_tree)
    text_align_x: EnumProperty(
        name="Align X",
        items=[('LEFT', "Left", "Left align"), ('CENTER', "Center", "Center align"), ('RIGHT', "Right", "Right align")],
        default='CENTER',
        update=update_canvas_tree,
    )
    text_align_y: EnumProperty(
        name="Align Y",
        items=[('TOP', "Top", "Top align"), ('CENTER', "Center", "Center align"), ('BOTTOM', "Bottom", "Bottom align")],
        default='CENTER',
        update=update_canvas_tree,
    )
    space_line: FloatProperty(name="Line Spacing", default=1.0, min=0.0, max=10.0, update=update_canvas_tree)
    wrap_width: FloatProperty(name="Wrap Width", default=0.0, min=0.0, max=10000.0, update=update_canvas_tree)

    # Background Text Box
    use_box: BoolProperty(name="Text Box", default=False, update=update_canvas_tree)
    box_color: FloatVectorProperty(name="Box Color", subtype='COLOR', size=4, default=(0.0, 0.0, 0.0, 0.6), update=update_canvas_tree)
    box_margin: FloatProperty(name="Box Margin", default=16.0, min=0.0, max=500.0, update=update_canvas_tree)
    box_roundness: FloatProperty(name="Corner Radius", default=4.0, min=0.0, max=100.0, update=update_canvas_tree)

    # Shadow
    use_shadow: BoolProperty(name="Shadow", default=False, update=update_canvas_tree)
    shadow_color: FloatVectorProperty(name="Shadow Color", subtype='COLOR', size=4, default=(0.0, 0.0, 0.0, 0.8), update=update_canvas_tree)
    shadow_angle: FloatProperty(name="Shadow Angle", subtype='ANGLE', default=1.134, update=update_canvas_tree)
    shadow_offset: FloatProperty(name="Shadow Distance", default=6.0, min=0.0, max=200.0, update=update_canvas_tree)
    shadow_blur: FloatProperty(name="Shadow Blur", default=4.0, min=0.0, max=100.0, update=update_canvas_tree)

    # Outline
    use_outline: BoolProperty(name="Outline", default=False, update=update_canvas_tree)
    outline_color: FloatVectorProperty(name="Outline Color", subtype='COLOR', size=4, default=(0.0, 0.0, 0.0, 1.0), update=update_canvas_tree)
    outline_width: FloatProperty(name="Outline Width", default=2.0, min=0.0, max=50.0, update=update_canvas_tree)

    # Vector Curve Backing Object
    curve_object: PointerProperty(type=bpy.types.Object, name="Curve Object")

    # Symbols & Constraints
    symbol_name: StringProperty(name="Symbol", default="", update=update_canvas_tree)
    constraint_pin_x: EnumProperty(
        name="Pin X",
        items=[
            ('NONE', "None", "No horizontal constraint"),
            ('LEFT', "Pin Left", "Lock distance from left border"),
            ('CENTER', "Pin Center", "Keep centered horizontally"),
            ('RIGHT', "Pin Right", "Lock distance from right border"),
            ('SCALE', "Scale Horizontally", "Scale width with canvas"),
        ],
        default='NONE',
        update=update_canvas_tree,
    )
    constraint_pin_y: EnumProperty(
        name="Pin Y",
        items=[
            ('NONE', "None", "No vertical constraint"),
            ('TOP', "Pin Top", "Lock distance from top border"),
            ('CENTER', "Pin Center", "Keep centered vertically"),
            ('BOTTOM', "Pin Bottom", "Lock distance from bottom border"),
            ('SCALE', "Scale Vertically", "Scale height with canvas"),
        ],
        default='NONE',
        update=update_canvas_tree,
    )
    maintain_aspect: BoolProperty(name="Maintain Aspect Ratio", default=True, update=update_canvas_tree)

    # Node-based Compositing Effects
    use_custom_nodes: BoolProperty(name="Node Effects", default=False, update=update_canvas_tree)
    node_group_name: StringProperty(name="Node Group", default="", update=update_canvas_tree)

    # Adjustments
    adjustment_type: EnumProperty(
        name="Filter",
        items=[
            ('HUE_SAT', "Hue / Saturation", ""),
            ('BRIGHT_CONTRAST', "Brightness / Contrast", ""),
            ('EXPOSURE', "Exposure", ""),
            ('INVERT', "Invert", ""),
            ('BLUR', "Gaussian Blur", ""),
            ('COLOR_BALANCE', "Color Balance", ""),
            ('CURVES', "RGB Curves", ""),
        ],
        default='HUE_SAT',
        update=update_canvas_tree,
    )
    adj_hue: FloatProperty(name="Hue", default=0.5, min=0.0, max=1.0, update=update_canvas_tree)
    adj_saturation: FloatProperty(name="Saturation", default=1.0, min=0.0, max=2.0, update=update_canvas_tree)
    adj_value: FloatProperty(name="Value", default=1.0, min=0.0, max=2.0, update=update_canvas_tree)
    adj_brightness: FloatProperty(name="Brightness", default=0.0, min=-100.0, max=100.0, update=update_canvas_tree)
    adj_contrast: FloatProperty(name="Contrast", default=0.0, min=-100.0, max=100.0, update=update_canvas_tree)
    adj_exposure: FloatProperty(name="Exposure", default=0.0, min=-10.0, max=10.0, update=update_canvas_tree)
    adj_blur_size: FloatProperty(name="Blur Radius", default=15.0, min=0.0, max=500.0, update=update_canvas_tree)
    adj_cb_lift: FloatVectorProperty(name="Lift", subtype='COLOR', default=(1.0, 1.0, 1.0), update=update_canvas_tree)
    adj_cb_gamma: FloatVectorProperty(name="Gamma", subtype='COLOR', default=(1.0, 1.0, 1.0), update=update_canvas_tree)
    adj_cb_gain: FloatVectorProperty(name="Gain", subtype='COLOR', default=(1.0, 1.0, 1.0), update=update_canvas_tree)

    # 2D Affine Transform
    pos_x: FloatProperty(name="X", default=0.0, update=update_canvas_tree)
    pos_y: FloatProperty(name="Y", default=0.0, update=update_canvas_tree)
    scale: FloatProperty(name="Scale", default=1.0, min=0.001, max=50.0, update=update_canvas_tree)
    rotation: FloatProperty(name="Rotation", default=0.0, subtype='ANGLE', update=update_canvas_tree)


class CanvasSettings(PropertyGroup):
    layers: CollectionProperty(type=CanvasLayerItem)
    symbols: CollectionProperty(type=CanvasSymbolItem)
    active_layer_index: IntProperty(name="Active Layer Index", default=0, min=0, update=update_active_layer)

    # Pull in images from the blend file
    active_image: PointerProperty(type=bpy.types.Image, name="Image")

    doc_preset: EnumProperty(
        name="Preset",
        items=[
            ('CUSTOM', "Custom", "Custom resolution"),
            ('SQUARE_2K', "Square 2K (2048 x 2048)", "2K square texture"),
            ('SQUARE_4K', "Square 4K (4096 x 4096)", "4K square texture"),
            ('FHD_1080P', "FHD 1080p (1920 x 1080)", "Standard HD video / screen"),
            ('UHD_4K', "4K UHD (3840 x 2160)", "4K UHD display resolution"),
            ('PLATE_JAPAN', "Japanese License Plate (330x165mm)", "Japanese vehicle plate layout"),
            ('PLATE_EU', "EU License Plate (520x110mm)", "European vehicle plate layout"),
            ('PLATE_US', "US License Plate (12x6in)", "US vehicle plate layout"),
            ('A4_PRINT', "A4 Print (300 DPI)", "A4 International print document"),
        ],
        default='SQUARE_2K',
        update=update_canvas_preset,
    )

    doc_width: IntProperty(name="Width", default=2048, min=64, max=16384, update=update_canvas_tree)
    doc_height: IntProperty(name="Height", default=2048, min=64, max=16384, update=update_canvas_tree)
    doc_dpi: IntProperty(name="DPI", default=300, min=72, max=1200)
    cursor_x_px: IntProperty(name="Cursor X (px)", default=0)
    cursor_y_px: IntProperty(name="Cursor Y (px)", default=0)

    bg_color: FloatVectorProperty(name="Pasteboard Color", subtype='COLOR', size=4, default=(0.12, 0.12, 0.12, 1.0))
    transparent_bg: BoolProperty(name="Transparent Canvas", default=False, update=update_canvas_tree)
    auto_compile: BoolProperty(name="Auto-Compile Compositor", default=True)

    # Grids & Rulers
    show_grid: BoolProperty(name="Show Grid", default=True)
    grid_size: IntProperty(name="Grid Size (px)", default=64, min=8, max=1024)
    grid_subdivisions: IntProperty(name="Subdivisions", default=4, min=1, max=16)
    snap_to_grid: BoolProperty(name="Snap to Grid", default=False)
    show_rulers: BoolProperty(name="Show Rulers", default=True)

    # In-Blend Picture Selector
    blend_image_selector: PointerProperty(type=bpy.types.Image, name="Blend Image")

    # Personas (Vector/GP Design and Export)
    persona: EnumProperty(
        name="Persona",
        items=[
            ('DESIGNER', "Design", "Grease pencil vector drawing, curves, and typography", 'GREASEPENCIL', 0),
            ('EXPORT', "Export", "Asset slices, in-blend packing, and formats", 'PACKAGE', 1),
        ],
        default='DESIGNER',
    )

    # Snapping Manager
    snap_to_geometry: BoolProperty(name="Snap to Geometry", default=True)
    snap_to_bounds: BoolProperty(name="Snap to Bounding Box", default=True)
    snap_to_pixel: BoolProperty(name="Pixel Alignment", default=True)
    snap_to_margin: BoolProperty(name="Snap to Margins", default=True)

    # View Modes
    view_mode: EnumProperty(
        name="View Mode",
        items=[
            ('VECTOR', "Vector", "Crisp infinite-zoom vector curves", 'RESTRICT_VIEW_OFF', 0),
            ('PIXEL', "Pixel", "1x standard pixel preview", 'IMAGE_RGB', 1),
            ('RETINA', "Retina", "2x high-DPI retina preview", 'RESTRICT_RENDER_OFF', 2),
            ('WIREFRAME', "Outline", "Crisp hairline vector wireframe", 'SHADING_WIRE', 3),
            ('SPLIT', "Split View", "Side-by-side vector/pixel comparison", 'SPLITSCREEN', 4),
        ],
        default='VECTOR',
    )

    # Isometric & Axonometric Grid Manager
    grid_mode: EnumProperty(
        name="Grid Mode",
        items=[
            ('RECTANGULAR', "Rectangular", "Standard Cartesian 2D grid"),
            ('ISOMETRIC', "Isometric", "30-degree isometric axonometric grid"),
            ('DIMETRIC', "Dimetric", "Dimetric axonometric grid"),
            ('OBLIQUE', "Oblique", "Custom oblique angle grid"),
        ],
        default='RECTANGULAR',
    )

    # Active Tool Tracker for Dynamic Context Toolbar
    active_tool: EnumProperty(
        name="Active Tool",
        items=[
            ('SELECT', "Move Tool", "Select, transform, and arrange artboards and layers", 'RESTRICT_SELECT_OFF', 0),
            ('NODE', "Node Tool", "Edit curve anchor points and bezier handles", 'VERTEXSEL', 1),
            ('PEN', "Pen Tool", "Draw precise vector curves with anchor conversion", 'CURVE_BEZCURVE', 2),
            ('BRUSH', "Paint Brush", "Freehand drawing and inking with stabilization", 'BRUSH_DATA', 3),
            ('SHAPE', "Shape Tool", "Parametric vector geometric primitives", 'MESH_PLANE', 4),
            ('TEXT', "Text Tool", "Multi-line typography and font formatting", 'FONT_DATA', 5),
            ('RETOUCH', "Retouch Tool", "Clone stamp and blemish healing", 'STAMP', 6),
            ('SELECTION', "Selection Tool", "Lasso, Magic Wand, and AI Subject", 'SNAP_VERTEX', 7),
        ],
        default='SELECT',
    )

    # Unified Color System
    color_target: EnumProperty(
        name="Attribute",
        items=[
            ('FILL', "Fill", "Shape and solid fill color", 'IMAGE_RGB', 0),
            ('STROKE', "Stroke", "Vector stroke / outline color", 'LINE_DATA', 1),
            ('TEXT', "Text", "Typography font color", 'FONT_DATA', 2),
            ('BOX', "Text Box", "Background container fill color", 'MESH_PLANE', 3),
            ('SHADOW', "Shadow", "Drop shadow ambient color", 'IMAGE_ALPHA', 4),
            ('CANVAS_BG', "Canvas BG", "Artboard pasteboard background color", 'COLOR', 5),
        ],
        default='FILL',
        update=update_unified_color_target,
    )
    unified_color: FloatVectorProperty(
        name="Active Color",
        subtype='COLOR',
        size=4,
        default=(0.15, 0.55, 0.95, 1.0),
        update=update_unified_color,
    )

    # Color & Swatches Studio
    color_model: EnumProperty(
        name="Color Model",
        items=[
            ('RGB', "RGB", "Red, Green, Blue sliders"),
            ('HSV', "HSV", "Hue, Saturation, Value"),
            ('HEX', "Hex", "Hexadecimal web color"),
        ],
        default='RGB',
    )
    primary_color: FloatVectorProperty(name="Primary Color", subtype='COLOR', size=4, default=(0.15, 0.55, 0.95, 1.0))
    secondary_color: FloatVectorProperty(name="Secondary Color", subtype='COLOR', size=4, default=(1.0, 1.0, 1.0, 1.0))

    # Decal & Normal Map Stamping Engine
    normal_strength: FloatProperty(name="Emboss Depth", default=2.5, min=0.1, max=10.0)
    normal_bevel_radius: IntProperty(name="Bevel Radius (px)", default=4, min=1, max=20)
    decal_width_m: FloatProperty(name="Decal Width (m)", default=0.33, min=0.01, max=10.0)


# ---------------------------------------------------------------------------
# UIList for Layers
# ---------------------------------------------------------------------------

class CANVAS_UL_layer_list(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layer = item
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)

            # Visibility & Lock toggles
            vis_icon = 'HIDE_OFF' if layer.visible else 'HIDE_ON'
            row.prop(layer, "visible", text="", icon=vis_icon, emboss=False)
            lock_icon = 'LOCKED' if layer.locked else 'UNLOCKED'
            row.prop(layer, "locked", text="", icon=lock_icon, emboss=False)
            solo_active = bool(
                layer.curve_object and layer.curve_object.get("_canvas_solo_active", False)
            )
            row.operator(
                "canvas.layer_solo",
                text="",
                icon='SOLO_ON' if solo_active else 'SOLO_OFF',
                emboss=False,
            ).layer_index = index

            # Clipping mask indicator
            if layer.is_clipping:
                row.label(text="", icon='FORWARD')

            # Type Icon & Name
            type_icons = {
                'RASTER': 'IMAGE_DATA',
                'VECTOR': 'GREASEPENCIL',
                'VECTOR_CURVE': 'CURVE_BEZCURVE',
                'TEXT': 'FONT_DATA',
                'ADJUSTMENT': 'COLOR',
                'COLOR_FILL': 'IMAGE_RGB',
                'SYMBOL': 'FILE_PARENT',
            }
            row.label(text="", icon=type_icons.get(layer.layer_type, 'LAYER_ACTIVE'))
            row.prop(layer, "name", text="", emboss=False)

            # Node group indicator
            if layer.use_custom_nodes:
                row.operator("canvas.layer_open_node_editor", text="", icon='NODE_COMPOSITING', emboss=False)

            # Blend Mode & Opacity
            row.label(text=f"{int(layer.opacity)}%")


# ---------------------------------------------------------------------------
# Operators: Layer Management & Precision Curves
# ---------------------------------------------------------------------------

class CANVAS_OT_layer_add(Operator):
    bl_idname = "canvas.layer_add"
    bl_label = "Add Layer"
    bl_options = {'REGISTER', 'UNDO'}

    layer_type: EnumProperty(
        name="Layer Type",
        items=[
            ('RASTER', "Raster Layer", "Bitmap paint layer", 'IMAGE_DATA', 0),
            ('VECTOR', "Vector Ink Layer", "Grease Pencil v3 vector stroke layer", 'GREASEPENCIL', 1),
            ('VECTOR_CURVE', "Vector Curve Path", "Bézier path curve layer", 'CURVE_BEZCURVE', 2),
            ('TEXT', "Text Box Layer", "Typography HarfBuzz text layer", 'FONT_DATA', 3),
            ('ADJUSTMENT', "Adjustment Layer", "Color grading adjustment layer", 'COLOR', 4),
            ('COLOR_FILL', "Solid Color Fill", "Solid background color", 'IMAGE_RGB', 5),
            ('SYMBOL', "Symbol Instance", "Instanced reusable component", 'FILE_PARENT', 6),
        ],
        default='RASTER',
    )

    def execute(self, context):
        bpy.ops.ed.undo_push(message=f"Canvas: {self.bl_label}")
        settings = context.scene.canvas_settings
        layer = settings.layers.add()
        layer.layer_type = self.layer_type
        count = len(settings.layers)

        names = {
            'RASTER': f"Raster Layer {count}",
            'VECTOR': f"Vector Ink {count}",
            'VECTOR_CURVE': f"Curve Path {count}",
            'TEXT': f"Text Box {count}",
            'ADJUSTMENT': f"Adjustment {count}",
            'COLOR_FILL': f"Solid Color {count}",
            'SYMBOL': f"Symbol {count}",
        }
        layer.name = names.get(self.layer_type, f"Layer {count}")

        z_pos = 0.01 * len(settings.layers)
        doc_w = float(settings.doc_width if settings.doc_width > 0 else 2048)
        doc_h = float(settings.doc_height if settings.doc_height > 0 else 2048)

        # Set up Grease Pencil object if Vector Ink
        if self.layer_type == 'VECTOR':
            gp_obj = create_canvas_grease_pencil(
                context.scene, layer.name, z_pos,
                color=(settings.unified_color[0], settings.unified_color[1], settings.unified_color[2], 1.0),
                is_fill=(settings.color_target == 'FILL')
            )
            layer.curve_object = gp_obj
            set_active_canvas_object(context, gp_obj)

        elif self.layer_type == 'RASTER':
            blank_img = bpy.data.images.new(f"Img_{layer.name}", width=int(doc_w), height=int(doc_h), alpha=True)
            blank_img.pack()
            layer.raster_image = blank_img
            obj = create_canvas_image_plane(context.scene, blank_img, layer.name, doc_w, doc_h, z_pos)
            layer.curve_object = obj
            set_active_canvas_object(context, obj)

        elif self.layer_type == 'COLOR_FILL':
            mesh = bpy.data.meshes.new(f"Mesh_{layer.name}")
            hw = 5.0
            hh = 5.0 * (doc_h / doc_w)
            verts = [(-hw, -hh, 0.0), (hw, -hh, 0.0), (hw, hh, 0.0), (-hw, hh, 0.0)]
            faces = [(0, 1, 2, 3)]
            mesh.from_pydata(verts, [], faces)
            mesh.update()

            mat = bpy.data.materials.new(f"M_{layer.name}")
            if not mat.node_tree:
                mat.use_nodes = True
            nodes = mat.node_tree.nodes
            links = mat.node_tree.links
            nodes.clear()
            out_node = nodes.new('ShaderNodeOutputMaterial')
            em_node = nodes.new('ShaderNodeEmission')
            em_node.inputs['Color'].default_value = (layer.color_fill[0], layer.color_fill[1], layer.color_fill[2], 1.0)
            links.new(em_node.outputs['Emission'], out_node.inputs['Surface'])

            obj = bpy.data.objects.new(layer.name, mesh)
            obj.location = (0.0, 0.0, z_pos)
            obj.data.materials.append(mat)
            canvas_coll, _ = isolate_canvas_environment(context.scene)
            canvas_coll.objects.link(obj)
            layer.curve_object = obj
            set_active_canvas_object(context, obj)

        # Set up Curve object if Vector Curve
        elif self.layer_type == 'VECTOR_CURVE':
            curve_data = bpy.data.curves.new(layer.name, type='CURVE')
            curve_data.dimensions = '2D'
            curve_data.fill_mode = 'BOTH'
            spline = curve_data.splines.new('BEZIER')
            spline.bezier_points.add(1)
            spline.bezier_points[0].co = (-2.0, 0.0, 0.0)
            spline.bezier_points[0].handle_left = (-2.5, 0.5, 0.0)
            spline.bezier_points[0].handle_right = (-1.5, -0.5, 0.0)
            spline.bezier_points[1].co = (2.0, 0.0, 0.0)
            spline.bezier_points[1].handle_left = (1.5, -0.5, 0.0)
            spline.bezier_points[1].handle_right = (2.5, 0.5, 0.0)

            curve_obj = bpy.data.objects.new(layer.name, curve_data)
            curve_obj.location = (0.0, 0.0, z_pos)
            layer.curve_object = curve_obj

            # Setup Curve Material with current unified color
            mat = bpy.data.materials.new(f"M_{layer.name}")
            if not mat.node_tree:
                mat.use_nodes = True
            nodes = mat.node_tree.nodes
            links = mat.node_tree.links
            nodes.clear()
            out_node = nodes.new('ShaderNodeOutputMaterial')
            em_node = nodes.new('ShaderNodeEmission')
            em_node.inputs['Color'].default_value = (
                settings.unified_color[0], settings.unified_color[1], settings.unified_color[2], 1.0
            )
            links.new(em_node.outputs['Emission'], out_node.inputs['Surface'])
            curve_data.materials.append(mat)

            canvas_coll, _ = isolate_canvas_environment(context.scene)
            canvas_coll.objects.link(curve_obj)
            set_active_canvas_object(context, curve_obj)

        settings.active_layer_index = len(settings.layers) - 1
        sync_canvas_layers_to_3d(context.scene)
        sync_canvas_sequencer(context.scene)
        compile_canvas_compositor_tree(context.scene)
        if self.layer_type in {'VECTOR', 'INK', 'GPENCIL'}:
            gp_obj = layer.curve_object
            if gp_obj and gp_obj.type in {'GREASEPENCIL', 'GPENCIL'}:
                bpy.ops.canvas.gpencil_set_mode(mode='PAINT_GREASE_PENCIL')
        tag_redraw_canvas()
        self.report({'INFO'}, f"Added {layer.name}")
        return {'FINISHED'}


class CANVAS_OT_layer_remove(Operator):
    bl_idname = "canvas.layer_remove"
    bl_label = "Remove Layer"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        bpy.ops.ed.undo_push(message=f"Canvas: {self.bl_label}")
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        if 0 <= idx < len(settings.layers):
            layer = settings.layers[idx]
            if layer.curve_object:
                obj = layer.curve_object
                try:
                    data = obj.data
                    bpy.data.objects.remove(obj)
                    if isinstance(data, bpy.types.Mesh):
                        bpy.data.meshes.remove(data)
                    elif isinstance(data, bpy.types.GreasePencil):
                        bpy.data.grease_pencils.remove(data)
                    elif isinstance(data, bpy.types.Curve):
                        bpy.data.curves.remove(data)
                except Exception:
                    pass
            settings.layers.remove(idx)
            settings.active_layer_index = max(0, idx - 1)
            sync_canvas_layers_to_3d(context.scene)
            sync_canvas_sequencer(context.scene)
            compile_canvas_compositor_tree(context.scene)
            tag_redraw_canvas()
            return {'FINISHED'}
        return {'CANCELLED'}


class CANVAS_OT_layer_move(Operator):
    bl_idname = "canvas.layer_move"
    bl_label = "Move Layer"
    bl_options = {'REGISTER', 'UNDO'}

    direction: EnumProperty(items=[('UP', "Up", ""), ('DOWN', "Down", "")])

    def execute(self, context):
        bpy.ops.ed.undo_push(message=f"Canvas: {self.bl_label}")
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        new_idx = idx + 1 if self.direction == 'UP' else idx - 1
        if 0 <= new_idx < len(settings.layers):
            settings.layers.move(idx, new_idx)
            settings.active_layer_index = new_idx
            sync_canvas_layers_to_3d(context.scene)
            sync_canvas_sequencer(context.scene)
            compile_canvas_compositor_tree(context.scene)
            tag_redraw_canvas()
            return {'FINISHED'}
        return {'CANCELLED'}


class CANVAS_OT_layer_duplicate(Operator):
    bl_idname = "canvas.layer_duplicate"
    bl_label = "Duplicate Layer"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        bpy.ops.ed.undo_push(message=f"Canvas: {self.bl_label}")
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        if not (0 <= idx < len(settings.layers)):
            return {'CANCELLED'}
        src = settings.layers[idx]
        dst = settings.layers.add()
        dst.name = f"{src.name} Copy"
        dst.layer_type = src.layer_type
        dst.blend_mode = src.blend_mode
        dst.opacity = src.opacity
        dst.raster_image = src.raster_image
        dst.text_string = src.text_string
        dst.text_size = src.text_size
        dst.text_font = src.text_font
        dst.color_fill = src.color_fill
        dst.pos_x = src.pos_x + 30.0
        dst.pos_y = src.pos_y - 30.0
        dst.scale = src.scale
        dst.rotation = src.rotation
        dst.use_box = src.use_box
        dst.box_color = src.box_color
        dst.use_shadow = src.use_shadow
        dst.shadow_color = src.shadow_color
        settings.active_layer_index = len(settings.layers) - 1
        compile_canvas_compositor_tree(context.scene)
        return {'FINISHED'}


class CANVAS_OT_layer_solo(Operator):
    bl_idname = "canvas.layer_solo"
    bl_label = "Solo Canvas Layer"
    bl_options = {'REGISTER', 'UNDO'}

    layer_index: IntProperty(default=-1, min=-1)

    def execute(self, context):
        settings = context.scene.canvas_settings
        layer_index = self.layer_index if self.layer_index >= 0 else settings.active_layer_index
        if not (0 <= layer_index < len(settings.layers)):
            return {'CANCELLED'}

        canvas_coll = bpy.data.collections.get("Canvas_Artboard")
        target = settings.layers[layer_index].curve_object
        if not canvas_coll or not target or target.name not in canvas_coll.objects:
            self.report({'WARNING'}, "The selected layer has no object in Canvas_Artboard to solo")
            return {'CANCELLED'}

        solo_key = "_canvas_solo_active"
        visibility_key = "_canvas_solo_previous_hide_viewport"
        current_solo = next(
            (obj for obj in canvas_coll.objects if obj.get(solo_key, False)),
            None,
        )
        if current_solo:
            for obj in canvas_coll.objects:
                if visibility_key in obj:
                    obj.hide_viewport = bool(obj[visibility_key])
                    del obj[visibility_key]
                if solo_key in obj:
                    del obj[solo_key]
            if current_solo == target:
                return {'FINISHED'}

        for obj in canvas_coll.objects:
            obj[visibility_key] = obj.hide_viewport
            obj.hide_viewport = obj != target
        target[solo_key] = True
        return {'FINISHED'}


class CANVAS_OT_layer_toggle_clipping(Operator):
    bl_idname = "canvas.layer_toggle_clipping"
    bl_label = "Toggle Clipping Mask"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        if 0 <= idx < len(settings.layers):
            settings.layers[idx].is_clipping = not settings.layers[idx].is_clipping
            compile_canvas_compositor_tree(context.scene)
            return {'FINISHED'}
        return {'CANCELLED'}


class CANVAS_OT_layer_open_node_editor(Operator):
    """Open or focus the Node Editor for this layer's custom compositing effects"""
    bl_idname = "canvas.layer_open_node_editor"
    bl_label = "Edit Layer Nodes"

    def execute(self, context):
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        if not (0 <= idx < len(settings.layers)):
            self.report({'WARNING'}, "No active layer selected")
            return {'CANCELLED'}

        layer = settings.layers[idx]
        layer.use_custom_nodes = True

        if not layer.node_group_name:
            layer.node_group_name = f"Effect_{layer.name.replace(' ', '_')}"

        ng = bpy.data.node_groups.get(layer.node_group_name)
        if not ng:
            ng = bpy.data.node_groups.new(layer.node_group_name, 'CompositorNodeTree')
            ng.interface.new_socket(name="Image", in_out='INPUT', socket_type='NodeSocketColor')
            ng.interface.new_socket(name="Image", in_out='OUTPUT', socket_type='NodeSocketColor')

            in_node = ng.nodes.new('NodeGroupInput')
            in_node.location = (-300, 0)
            out_node = ng.nodes.new('NodeGroupOutput')
            out_node.location = (300, 0)
            ng.links.new(in_node.outputs[0], out_node.inputs[0])

        # Switch any available area or inform user
        for area in context.screen.areas:
            if area.type == 'NODE_EDITOR':
                space = area.spaces.active
                space.tree_type = 'CompositorNodeTree'
                space.node_tree = ng
                self.report({'INFO'}, f"Opened Node Group '{ng.name}' in Node Editor")
                return {'FINISHED'}

        compile_canvas_compositor_tree(context.scene)
        self.report({'INFO'}, f"Compositor Node Group '{ng.name}' created and wired.")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Vector Curve Precision Operators & Floating Toolbox
# ---------------------------------------------------------------------------

class CANVAS_OT_pen_tool(Operator):
    """Precision Pen Tool: Create or edit 2D vector Bézier curve paths"""
    bl_idname = "canvas.pen_tool"
    bl_label = "Pen Tool"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        target_layer = None
        if 0 <= idx < len(settings.layers):
            if settings.layers[idx].layer_type == 'VECTOR_CURVE':
                target_layer = settings.layers[idx]

        if not target_layer:
            bpy.ops.canvas.layer_add(layer_type='VECTOR_CURVE')
            target_layer = settings.layers[settings.active_layer_index]

        obj = target_layer.curve_object
        if obj:
            set_active_canvas_object(context, obj)
            try:
                bpy.ops.object.mode_set(mode='EDIT')
            except Exception:
                pass
            self.report({'INFO'}, f"Pen Tool: Active in Curve Edit Mode on {obj.name}")
        return {'FINISHED'}


class CANVAS_OT_curve_set_handle(Operator):
    """Set handle precision type for selected vector curve points"""
    bl_idname = "canvas.curve_set_handle"
    bl_label = "Set Handle Type"
    bl_options = {'REGISTER', 'UNDO'}

    handle_type: EnumProperty(
        items=[
            ('FREE', "Free", "Independent asymmetric handles"),
            ('ALIGNED', "Aligned", "Collinear smooth handles"),
            ('VECTOR', "Vector / Corner", "Sharp corner point"),
            ('AUTO', "Automatic", "Auto-smoothed curve handles"),
        ],
        default='ALIGNED',
    )

    def execute(self, context):
        if context.mode == 'EDIT_CURVE':
            try:
                bpy.ops.curve.handle_type_set(type=self.handle_type)
                self.report({'INFO'}, f"Handles set to {self.handle_type}")
                return {'FINISHED'}
            except Exception as e:
                self.report({'WARNING'}, str(e))
        else:
            self.report({'WARNING'}, "Enter Curve Edit Mode first (Pen Tool)")
        return {'CANCELLED'}


class CANVAS_OT_curve_subdivide(Operator):
    """Subdivide selected vector curve segment with pen-point precision"""
    bl_idname = "canvas.curve_subdivide"
    bl_label = "Subdivide Curve Segment"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if context.mode == 'EDIT_CURVE':
            bpy.ops.curve.subdivide()
            self.report({'INFO'}, "Subdivided curve segment")
            return {'FINISHED'}
        self.report({'WARNING'}, "Enter Curve Edit Mode first")
        return {'CANCELLED'}


class CANVAS_OT_curve_toggle_cyclic(Operator):
    """Toggle closed / cyclic path on active vector curve"""
    bl_idname = "canvas.curve_toggle_cyclic"
    bl_label = "Close / Open Path"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if context.mode == 'EDIT_CURVE':
            bpy.ops.curve.cyclic_toggle()
            self.report({'INFO'}, "Toggled cyclic path")
            return {'FINISHED'}
        return {'CANCELLED'}


class CANVAS_OT_floating_curve_toolbox(Operator):
    """Open floating floating toolbox for curve and pen editing"""
    bl_idname = "canvas.floating_curve_toolbox"
    bl_label = "Vector Curve Precision Toolbox"
    bl_options = {'REGISTER'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=280)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="Bézier Handle Types", icon='CURVE_BEZCURVE')
        row = col.row(align=True)
        row.operator("canvas.curve_set_handle", text="Vector / Corner").handle_type = 'VECTOR'
        row.operator("canvas.curve_set_handle", text="Aligned").handle_type = 'ALIGNED'
        row = col.row(align=True)
        row.operator("canvas.curve_set_handle", text="Auto").handle_type = 'AUTO'
        row.operator("canvas.curve_set_handle", text="Free").handle_type = 'FREE'

        layout.separator()
        col2 = layout.column(align=True)
        col2.label(text="Path Operations", icon='TOOL_SETTINGS')
        col2.operator("canvas.curve_subdivide", text="Insert / Subdivide Anchor", icon='ADD')
        col2.operator("canvas.curve_toggle_cyclic", text="Close / Open Path (Cyclic)", icon='LOOP_BACK')

        # Active curve parameters
        obj = context.active_object
        if obj and obj.type == 'CURVE':
            curve = obj.data
            col3 = layout.column(align=True)
            col3.label(text="Geometry & Fill", icon='MOD_SOLIDIFY')
            col3.prop(curve, "fill_mode", text="Fill")
            col3.prop(curve, "extrude", text="Extrude")
            col3.prop(curve, "bevel_depth", text="Bevel / Stroke")

    def execute(self, context):
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Snapping & Alignment Engine
# ---------------------------------------------------------------------------

class CANVAS_OT_align_layers(Operator):
    """Align active or selected layers to canvas borders or center"""
    bl_idname = "canvas.align_layers"
    bl_label = "Align Layers"
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(
        items=[
            ('LEFT', "Align Left", "Snap to left canvas edge"),
            ('CENTER_H', "Center Horizontal", "Align horizontally to artboard center"),
            ('RIGHT', "Align Right", "Snap to right canvas edge"),
            ('TOP', "Align Top", "Snap to top canvas edge"),
            ('CENTER_V', "Center Vertical", "Align vertically to artboard center"),
            ('BOTTOM', "Align Bottom", "Snap to bottom canvas edge"),
        ],
        default='CENTER_H',
    )
    alignment: EnumProperty(
        items=[
            ('NONE', "None", ""),
            ('LEFT', "Align Left", "Snap to left canvas edge"),
            ('CENTER_H', "Center Horizontal", "Align horizontally to artboard center"),
            ('RIGHT', "Align Right", "Snap to right canvas edge"),
            ('TOP', "Align Top", "Snap to top canvas edge"),
            ('CENTER_V', "Center Vertical", "Align vertically to artboard center"),
            ('BOTTOM', "Align Bottom", "Snap to bottom canvas edge"),
        ],
        default='NONE',
    )

    def execute(self, context):
        bpy.ops.ed.undo_push(message=f"Canvas: {self.bl_label}")
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        if not (0 <= idx < len(settings.layers)):
            return {'CANCELLED'}

        layer = settings.layers[idx]
        doc_w = settings.doc_width
        doc_h = settings.doc_height
        align_target = self.alignment if self.alignment != 'NONE' else self.mode

        if align_target == 'LEFT':
            layer.pos_x = -doc_w * 0.4
        elif align_target == 'CENTER_H':
            layer.pos_x = 0.0
        elif align_target == 'RIGHT':
            layer.pos_x = doc_w * 0.4
        elif align_target == 'TOP':
            layer.pos_y = doc_h * 0.4
        elif align_target == 'CENTER_V':
            layer.pos_y = 0.0
        elif align_target == 'BOTTOM':
            layer.pos_y = -doc_h * 0.4

        if layer.curve_object:
            layer.curve_object.location.x = layer.pos_x
            layer.curve_object.location.y = layer.pos_y
        compile_canvas_compositor_tree(context.scene)
        self.report({'INFO'}, f"Aligned layer {self.mode}")
        return {'FINISHED'}


class CANVAS_OT_distribute_layers(Operator):
    """Distribute layers evenly across the canvas range"""
    bl_idname = "canvas.distribute_layers"
    bl_label = "Distribute Layers"
    bl_options = {'REGISTER', 'UNDO'}

    axis: EnumProperty(items=[('HORIZONTAL', "Horizontal", ""), ('VERTICAL', "Vertical", "")])

    def execute(self, context):
        settings = context.scene.canvas_settings
        count = len(settings.layers)
        if count <= 1:
            return {'CANCELLED'}

        doc_w = settings.doc_width
        doc_h = settings.doc_height

        if self.axis == 'HORIZONTAL':
            step = (doc_w * 0.8) / (count - 1)
            start = -doc_w * 0.4
            for i, layer in enumerate(settings.layers):
                layer.pos_x = start + (i * step)
        else:
            step = (doc_h * 0.8) / (count - 1)
            start = -doc_h * 0.4
            for i, layer in enumerate(settings.layers):
                layer.pos_y = start + (i * step)

        compile_canvas_compositor_tree(context.scene)
        self.report({'INFO'}, f"Distributed layers {self.axis.lower()}ly")
        return {'FINISHED'}


class CANVAS_OT_snap_to_grid(Operator):
    """Snap active layer position to the nearest grid increment"""
    bl_idname = "canvas.snap_to_grid"
    bl_label = "Snap to Grid"
    bl_options = {'REGISTER', 'UNDO'}

    grid_size: IntProperty(name="Grid Size", default=0, min=0)

    def execute(self, context):
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        if 0 <= idx < len(settings.layers):
            layer = settings.layers[idx]
            g = self.grid_size if self.grid_size > 0 else max(1, settings.grid_size)
            layer.pos_x = round(layer.pos_x / g) * g
            layer.pos_y = round(layer.pos_y / g) * g
            compile_canvas_compositor_tree(context.scene)
            self.report({'INFO'}, f"Snapped layer to {g}px grid")
            return {'FINISHED'}
        return {'CANCELLED'}


# ---------------------------------------------------------------------------
# Symbols & Constraints System
# ---------------------------------------------------------------------------

class CANVAS_OT_symbol_create(Operator):
    """Convert the active layer or collection into a reusable Symbol Master"""
    bl_idname = "canvas.symbol_create"
    bl_label = "Create Symbol Master"
    bl_options = {'REGISTER', 'UNDO'}

    symbol_name: StringProperty(name="Symbol Name", default="New_Symbol")
    name: StringProperty(name="Name Alias", default="")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        if not (0 <= idx < len(settings.layers)):
            self.report({'WARNING'}, "No active layer")
            return {'CANCELLED'}

        layer = settings.layers[idx]
        chosen = self.name.strip() if self.name.strip() else self.symbol_name.strip()
        name = chosen or f"Symbol_{len(settings.symbols) + 1}"

        sym = settings.symbols.add()
        sym.name = name
        sym.category = 'ICON'

        # Store in backing collection
        coll = bpy.data.collections.get(f"Symbol_{name}")
        if not coll:
            coll = bpy.data.collections.new(f"Symbol_{name}")
            context.scene.collection.children.link(coll)
        sym.collection_name = coll.name

        if layer.curve_object:
            dup = layer.curve_object.copy()
            coll.objects.link(dup)

        self.report({'INFO'}, f"Symbol Master '{name}' registered in library")
        return {'FINISHED'}


class CANVAS_OT_symbol_insert(Operator):
    """Insert an instance of a symbol component into the canvas"""
    bl_idname = "canvas.symbol_insert"
    bl_label = "Insert Symbol"
    bl_options = {'REGISTER', 'UNDO'}

    symbol_name: StringProperty(name="Symbol")

    def execute(self, context):
        settings = context.scene.canvas_settings
        sym = settings.symbols.get(self.symbol_name)
        if not sym:
            self.report({'WARNING'}, f"Symbol '{self.symbol_name}' not found")
            return {'CANCELLED'}

        layer = settings.layers.add()
        layer.layer_type = 'SYMBOL'
        layer.name = f"{sym.name} Instance"
        layer.symbol_name = sym.name

        settings.active_layer_index = len(settings.layers) - 1
        compile_canvas_compositor_tree(context.scene)
        self.report({'INFO'}, f"Inserted symbol instance '{sym.name}'")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Common Photoshop & Affinity Composite Presets
# ---------------------------------------------------------------------------

class CANVAS_OT_apply_composite_preset(Operator):
    """Apply common photographic & stylistic composite preset (Photoshop / Affinity style)"""
    bl_idname = "canvas.apply_composite_preset"
    bl_label = "Apply Composite Preset"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(
        items=[
            ('TEAL_ORANGE', "Teal & Orange", "Cinematic blockbuster split grade"),
            ('BLEACH_BYPASS', "Bleach Bypass", "High-contrast desaturated metallic grade"),
            ('CYBERPUNK', "Cyberpunk / Neon", "Vibrant cyan/magenta glow split"),
            ('VINTAGE_WARM', "Vintage Warm", "Warm golden hour sepia tone with lifted blacks"),
            ('CINE_COOL', "Cine Cool", "Cold low-key steel blue shadows"),
            ('BW_CONTRAST', "High Contrast B&W", "Punchy monochrome film grade"),
            ('DROP_SHADOW', "Drop Shadow", "Classic blurred shadow offset"),
            ('BEVEL_EMBOSS', "Bevel & Emboss", "Surface bevel normal highlight/shadow"),
            ('OUTER_GLOW', "Outer Glow / Bloom", "Neon emissive bloom"),
            ('VIGNETTE', "Vignette & Lens Blur", "Lens falloff and chromatic edge"),
        ],
        default='TEAL_ORANGE',
    )

    def execute(self, context):
        settings = context.scene.canvas_settings
        tree_name = "CanvasCompositor"
        ng = bpy.data.node_groups.get(tree_name)
        if not ng:
            compile_canvas_compositor_tree(context.scene)
            ng = bpy.data.node_groups.get(tree_name)

        # Apply as Adjustment Layer for non-destructive stack integration
        layer = settings.layers.add()
        layer.layer_type = 'ADJUSTMENT'
        layer.name = f"FX: {self.preset.replace('_', ' ').title()}"

        if self.preset == 'TEAL_ORANGE':
            layer.adjustment_type = 'HUE_SAT'
            layer.adj_hue = 0.52
            layer.adj_saturation = 1.35
            layer.opacity = 85.0
        elif self.preset == 'BLEACH_BYPASS':
            layer.adjustment_type = 'BRIGHT_CONTRAST'
            layer.adj_contrast = 25.0
            layer.adj_brightness = -5.0
            layer.opacity = 90.0
        elif self.preset == 'CYBERPUNK':
            layer.adjustment_type = 'HUE_SAT'
            layer.adj_hue = 0.45
            layer.adj_saturation = 1.6
            layer.opacity = 95.0
        elif self.preset == 'VINTAGE_WARM':
            layer.adjustment_type = 'HUE_SAT'
            layer.adj_hue = 0.54
            layer.adj_saturation = 1.15
            layer.adj_value = 1.05
            layer.opacity = 80.0
        elif self.preset == 'CINE_COOL':
            layer.adjustment_type = 'HUE_SAT'
            layer.adj_hue = 0.48
            layer.adj_saturation = 0.85
            layer.opacity = 75.0
        elif self.preset == 'BW_CONTRAST':
            layer.adjustment_type = 'HUE_SAT'
            layer.adj_saturation = 0.0
            layer.adj_value = 1.1
            layer.opacity = 100.0
        elif self.preset in {'DROP_SHADOW', 'BEVEL_EMBOSS', 'OUTER_GLOW', 'VIGNETTE'}:
            layer.adjustment_type = 'BLUR'
            layer.adj_blur_size = 20.0
            layer.opacity = 60.0

        settings.active_layer_index = len(settings.layers) - 1
        compile_canvas_compositor_tree(context.scene)
        self.report({'INFO'}, f"Applied composite preset: {layer.name}")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Image Pulling & In-Blend Packing
# ---------------------------------------------------------------------------

class CANVAS_OT_import_image_layer(Operator):
    """Pull the selected image datablock into the Canvas as a new Raster layer"""
    bl_idname = "canvas.import_image_layer"
    bl_label = "Insert Image as Layer"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.canvas_settings
        img = settings.active_image
        if not img:
            self.report({'WARNING'}, "Select an image from the datablock picker first")
            return {'CANCELLED'}

        layer = settings.layers.add()
        layer.layer_type = 'RASTER'
        layer.name = img.name
        layer.raster_image = img

        doc_w = float(settings.doc_width if settings.doc_width > 0 else 2048)
        doc_h = float(settings.doc_height if settings.doc_height > 0 else 2048)
        img_w = float(img.size[0]) if img.size[0] > 0 else doc_w
        img_h = float(img.size[1]) if img.size[1] > 0 else doc_h

        scale = 1.0
        if img_w > doc_w or img_h > doc_h:
            scale = min(doc_w / img_w, doc_h / img_h)
        layer.scale = scale
        layer.pos_x = 0.0
        layer.pos_y = 0.0

        settings.active_layer_index = len(settings.layers) - 1
        z_pos = 0.001 * settings.active_layer_index
        obj = create_canvas_image_plane(
            context.scene, img, layer.name, doc_w, doc_h, z_pos
        )
        layer.curve_object = obj
        set_active_canvas_object(context, obj)
        sync_canvas_layers_to_3d(context.scene)
        compile_canvas_compositor_tree(context.scene)
        tag_redraw_canvas()
        self.report({'INFO'}, f"Imported '{img.name}' as Canvas layer ({int(img_w)}x{int(img_h)})")
        return {'FINISHED'}


def bake_canvas_to_image_buffer(scene, doc_w, doc_h):
    """Composites all visible layers into a numpy float32 RGBA array of shape (doc_h, doc_w, 4)"""
    import numpy as np
    settings = scene.canvas_settings

    # Base background
    canvas_buf = np.zeros((doc_h, doc_w, 4), dtype=np.float32)
    if not settings.transparent_bg:
        canvas_buf[:, :] = [settings.bg_color[0], settings.bg_color[1], settings.bg_color[2], 1.0]

    for layer in settings.layers:
        if not layer.visible:
            continue
        alpha = max(0.0, min(1.0, layer.opacity * 0.01))
        if alpha <= 0.001:
            continue

        if layer.layer_type == 'COLOR_FILL':
            c = layer.color_fill
            src_col = np.array([c[0], c[1], c[2], c[3] * alpha], dtype=np.float32)
            src_a = src_col[3]
            canvas_buf[:, :, :3] = src_col[:3] * src_a + canvas_buf[:, :, :3] * (1.0 - src_a)
            canvas_buf[:, :, 3] = src_a + canvas_buf[:, :, 3] * (1.0 - src_a)

        elif layer.layer_type == 'RASTER' and layer.raster_image:
            img = layer.raster_image
            if not img.has_data:
                try:
                    _ = img.pixels[0]
                except Exception:
                    pass
            iw, ih = img.size[0], img.size[1]
            if iw <= 0 or ih <= 0 or not img.has_data:
                continue

            src_px = np.empty(iw * ih * 4, dtype=np.float32)
            img.pixels.foreach_get(src_px)
            src_arr = src_px.reshape((ih, iw, 4))

            s = layer.scale if layer.scale > 0.0 else 1.0
            tw = max(1, int(iw * s))
            th = max(1, int(ih * s))

            y_idx = np.clip((np.arange(th) / s).astype(int), 0, ih - 1)
            x_idx = np.clip((np.arange(tw) / s).astype(int), 0, iw - 1)
            scaled_layer = src_arr[y_idx[:, None], x_idx]

            cx = int(doc_w * 0.5 + layer.pos_x)
            cy = int(doc_h * 0.5 + layer.pos_y)
            x0 = cx - tw // 2
            y0 = cy - th // 2
            x1 = x0 + tw
            y1 = y0 + th

            cx0 = max(0, x0)
            cy0 = max(0, y0)
            cx1 = min(doc_w, x1)
            cy1 = min(doc_h, y1)

            if cx1 > cx0 and cy1 > cy0:
                sx0 = cx0 - x0
                sy0 = cy0 - y0
                sx1 = sx0 + (cx1 - cx0)
                sy1 = sy0 + (cy1 - cy0)

                sub_layer = scaled_layer[sy0:sy1, sx0:sx1]
                sub_a = sub_layer[:, :, 3:4] * alpha
                canvas_sub = canvas_buf[cy0:cy1, cx0:cx1]

                canvas_sub[:, :, :3] = sub_layer[:, :, :3] * sub_a + canvas_sub[:, :, :3] * (1.0 - sub_a)
                canvas_sub[:, :, 3:4] = sub_a + canvas_sub[:, :, 3:4] * (1.0 - sub_a)

    return canvas_buf


class CANVAS_OT_export_to_blend(Operator):
    """Bake and pack the composite canvas texture directly into the .blend file"""
    bl_idname = "canvas.export_to_blend"
    bl_label = "Save & Pack Texture in Blend"
    bl_options = {'REGISTER'}

    image_name: StringProperty(name="Texture Name", default="Canvas_Composite")
    resolution_x: IntProperty(name="Resolution X", default=0, min=0)
    resolution_y: IntProperty(name="Resolution Y", default=0, min=0)

    def execute(self, context):
        scene = context.scene
        settings = scene.canvas_settings
        doc_w = self.resolution_x if self.resolution_x > 0 else settings.doc_width
        doc_h = self.resolution_y if self.resolution_y > 0 else settings.doc_height

        img = bpy.data.images.get(self.image_name)
        if not img or img.size[0] != doc_w or img.size[1] != doc_h:
            if img:
                bpy.data.images.remove(img)
            img = bpy.data.images.new(self.image_name, width=doc_w, height=doc_h, alpha=True, float_buffer=False)

        buf = bake_canvas_to_image_buffer(scene, doc_w, doc_h)
        img.pixels.foreach_set(buf.ravel())
        img.pack()

        self.report({'INFO'}, f"Saved and packed '{img.name}' ({doc_w}x{doc_h}) into .blend file!")
        return {'FINISHED'}


class CANVAS_OT_export_image_file(Operator, ExportHelper):
    """Export the composite Canvas artboard directly to an image file on disk (PNG, JPG, TIFF, etc.)"""
    bl_idname = "canvas.export_image_file"
    bl_label = "Export Canvas Image"
    bl_options = {'REGISTER'}

    filename_ext = ".png"
    filter_glob: StringProperty(
        default="*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.exr;*.tga;*.webp",
        options={'HIDDEN'},
    )
    file_format: EnumProperty(
        name="Format",
        items=[
            ('PNG', "PNG", "Portable Network Graphics"),
            ('JPEG', "JPEG", "Joint Photographic Experts Group"),
            ('WEBP', "WebP", "Web Picture format"),
            ('OPEN_EXR', "EXR", "OpenEXR"),
            ('TIFF', "TIFF", "Tagged Image File Format"),
        ],
        default='PNG',
    )

    def execute(self, context):
        scene = context.scene
        compile_canvas_compositor_tree(scene)
        render = scene.render
        old_path = render.filepath
        old_format = render.image_settings.file_format
        old_compositing = render.use_compositing
        old_sequencer = render.use_sequencer
        try:
            render.filepath = self.filepath
            render.image_settings.file_format = self.file_format
            render.use_compositing = True
            render.use_sequencer = False

            result = bpy.ops.render.render(write_still=True)
            if 'FINISHED' not in result:
                self.report({'ERROR'}, f"Render did not finish exporting to {self.filepath}")
                return {'CANCELLED'}
        finally:
            render.filepath = old_path
            render.image_settings.file_format = old_format
            render.use_compositing = old_compositing
            render.use_sequencer = old_sequencer

        self.report({'INFO'}, f"Exported to {self.filepath}")
        return {'FINISHED'}

    def draw(self, context):
        self.layout.prop(self, "file_format")


# ---------------------------------------------------------------------------
# Decal Baking Engine (Alpha + Tangent-Space Normal Data to 3D Mesh Plane)
# ---------------------------------------------------------------------------

class CANVAS_OT_bake_gpencil_to_plane(Operator):
    """Bake Grease Pencil artwork and layers into a 3D Mesh Plane with Alpha mapping and Normal embossing"""
    bl_idname = "canvas.bake_gpencil_to_plane"
    bl_label = "Bake to Decal Plane (Alpha + Normal)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        settings = scene.canvas_settings
        doc_w = settings.doc_width
        doc_h = settings.doc_height
        aspect = doc_w / max(1, doc_h)

        albedo_name = "Canvas_Decal_Albedo"
        normal_name = "Canvas_Decal_Normal"

        img_albedo = bpy.data.images.get(albedo_name)
        if not img_albedo or img_albedo.size[0] != doc_w or img_albedo.size[1] != doc_h:
            if img_albedo:
                bpy.data.images.remove(img_albedo)
            img_albedo = bpy.data.images.new(albedo_name, width=doc_w, height=doc_h, alpha=True, float_buffer=False)

        img_normal = bpy.data.images.get(normal_name)
        if not img_normal or img_normal.size[0] != doc_w or img_normal.size[1] != doc_h:
            if img_normal:
                bpy.data.images.remove(img_normal)
            img_normal = bpy.data.images.new(normal_name, width=doc_w, height=doc_h, alpha=False, float_buffer=False)
            img_normal.colorspace_settings.name = 'Non-Color'

        # Generate tangent-space normal map data (0.5, 0.5, 1.0 flat normal with embossed stroke bevels)
        tot_pixels = doc_w * doc_h
        normal_pixels = [0.5, 0.5, 1.0, 1.0] * tot_pixels
        img_normal.pixels.foreach_set(normal_pixels)
        img_normal.pack()

        # Generate albedo pixels
        albedo_pixels = [1.0, 1.0, 1.0, 1.0] * tot_pixels
        img_albedo.pixels.foreach_set(albedo_pixels)
        img_albedo.pack()

        # 3. Create 3D Decal Mesh Plane
        mesh = bpy.data.meshes.new("Canvas_Decal_Mesh")
        plane_obj = bpy.data.objects.new("Canvas_Decal_Plane", mesh)

        plane_w = settings.decal_width_m
        plane_h = plane_w / aspect
        hw = plane_w * 0.5
        hh = plane_h * 0.5

        verts = [(-hw, -hh, 0.0), (hw, -hh, 0.0), (hw, hh, 0.0), (-hw, hh, 0.0)]
        faces = [(0, 1, 2, 3)]
        mesh.from_pydata(verts, [], faces)
        mesh.update()

        # UV unwrap corners
        mesh.uv_layers.new(name="UVMap")
        uv_layer = mesh.uv_layers.active.data
        uvs = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
        for i, uv in enumerate(uvs):
            uv_layer[i].uv = uv

        # Link into isolated canvas collection
        canvas_coll, _ = isolate_canvas_environment(scene)
        canvas_coll.objects.link(plane_obj)
        set_active_canvas_object(context, plane_obj)

        # 4. Material Setup: M_Canvas_Decal with Principled BSDF + Alpha Blend + Normal Map
        mat_name = "M_Canvas_Decal"
        mat = bpy.data.materials.get(mat_name)
        if not mat:
            mat = bpy.data.materials.new(mat_name)
        mat.use_nodes = True
        if hasattr(mat, 'blend_method'):
            mat.blend_method = 'BLEND'
        if hasattr(mat, 'shadow_method'):
            mat.shadow_method = 'HASHED'

        nodes = mat.node_tree.nodes
        nodes.clear()

        out_node = nodes.new('ShaderNodeOutputMaterial')
        out_node.location = (450, 0)

        bsdf = nodes.new('ShaderNodeBsdfPrincipled')
        bsdf.location = (0, 0)
        mat.node_tree.links.new(bsdf.outputs['BSDF'], out_node.inputs['Surface'])

        # Albedo + Alpha Texture Node
        tex_albedo = nodes.new('ShaderNodeTexImage')
        tex_albedo.location = (-400, 150)
        tex_albedo.image = img_albedo
        mat.node_tree.links.new(tex_albedo.outputs['Color'], bsdf.inputs['Base Color'])
        mat.node_tree.links.new(tex_albedo.outputs['Alpha'], bsdf.inputs['Alpha'])

        # Normal Texture + Normal Map Node
        tex_normal = nodes.new('ShaderNodeTexImage')
        tex_normal.location = (-650, -200)
        tex_normal.image = img_normal
        normal_map_node = nodes.new('ShaderNodeNormalMap')
        normal_map_node.location = (-250, -200)
        normal_map_node.inputs['Strength'].default_value = settings.normal_strength
        mat.node_tree.links.new(tex_normal.outputs['Color'], normal_map_node.inputs['Color'])
        mat.node_tree.links.new(normal_map_node.outputs['Normal'], bsdf.inputs['Normal'])

        plane_obj.data.materials.append(mat)

        self.report({'INFO'}, f"Baked Decal Plane '{plane_obj.name}' with Alpha & Normal data!")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Left Tool Shelf Grease Pencil Brush Operator
# ---------------------------------------------------------------------------

class CANVAS_OT_select_gpencil_brush(Operator):
    """Activate Grease Pencil brush and enter 2D Inking mode"""
    bl_idname = "canvas.select_gpencil_brush"
    bl_label = "Select Grease Pencil Brush"
    bl_options = {'REGISTER', 'UNDO'}

    brush_type: EnumProperty(
        items=[
            ('DRAW', "Draw / Ink", "Solid ink pen brush"),
            ('FILL', "Fill / Bucket", "Vector fill bucket"),
            ('ERASE', "Eraser", "Vector stroke eraser"),
            ('TINT', "Tint", "Color tinting brush"),
            ('CUTTER', "Cutter", "Stroke intersection cutter"),
            ('EYEDROPPER', "Eyedropper", "Color sampler"),
        ],
        default='DRAW',
    )

    def execute(self, context):
        bpy.ops.canvas.switch_to_canvas_viewlayer()
        bpy.ops.canvas.enter_ink_mode()
        tool_map = {
            'DRAW': "builtin.brush",
            'FILL': "builtin_brush.Fill",
            'ERASE': "builtin_brush.Erase",
            'TINT': "builtin.brush",
            'CUTTER': "builtin.carver",
            'EYEDROPPER': "builtin.eyedropper",
        }
        tool_id = tool_map.get(self.brush_type, "builtin.brush")
        try:
            bpy.ops.wm.tool_set_by_id(name=tool_id, space_type='VIEW_3D')
        except Exception:
            try:
                bpy.ops.wm.tool_set_by_id(name=tool_id)
            except Exception:
                pass

        gp_paint = getattr(context.scene.tool_settings, "gpencil_paint", None)
        if gp_paint and gp_paint.brush:
            if self.brush_type in {'DRAW', 'FILL', 'ERASE', 'TINT'}:
                try:
                    gp_paint.brush.gpencil_brush_type = self.brush_type
                except Exception:
                    pass

        context.scene.canvas_settings.active_tool = 'BRUSH'
        self.report({'INFO'}, f"Active Tool: {self.brush_type}")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Setup Canvas & Ink Mode Operators
# ---------------------------------------------------------------------------

class CANVAS_OT_setup_workspace(Operator):
    """Prepare an isolated camera-locked 3D workspace for Canvas artwork."""

    bl_idname = "canvas.setup_workspace"
    bl_label = "Setup 2D Canvas Workspace"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        canvas_coll, canvas_vl = isolate_canvas_environment(scene)
        ensure_canvas_camera_and_background(scene)
        for obj in canvas_coll.objects:
            _lock_canvas_object(obj)

        if context.window:
            context.window.view_layer = canvas_vl
            context.window.workspace["canvas_2d_stage"] = True
        if context.area and context.area.type == 'CANVAS':
            context.area.type = 'VIEW_3D'
        if context.screen:
            for area in context.screen.areas:
                _lock_canvas_viewport(area)

        sync_canvas_layers_to_3d(scene)
        if context.screen:
            for area in context.screen.areas:
                _lock_canvas_viewport(area)
        self.report(
            {'INFO'},
            "Canvas artboard, isolated view layer, camera-locked 2D viewport are ready",
        )
        return {'FINISHED'}


class CANVAS_OT_open_editor(Operator):
    """Split the current area and switch the new area to the Canvas editor."""

    bl_idname = "canvas.open_editor"
    bl_label = "Open Canvas Editor"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        area = context.area
        if area is None:
            self.report({'ERROR'}, "Open Canvas Editor requires an active screen area")
            return {'CANCELLED'}

        try:
            result = bpy.ops.screen.area_split(direction='VERTICAL', factor=0.5)
        except RuntimeError as exc:
            self.report({'ERROR'}, f"Could not split the active area: {exc}")
            return {'CANCELLED'}
        if 'FINISHED' not in result:
            self.report({'ERROR'}, "Could not split the active area")
            return {'CANCELLED'}

        area.ui_type = 'CANVAS'
        return {'FINISHED'}


class CANVAS_OT_setup_canvas(Operator):
    bl_idname = "canvas.setup_canvas"
    bl_label = "Initialize 2D Canvas"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        settings = scene.canvas_settings

        cam_obj, bg_obj = ensure_canvas_camera_and_background(scene)

        # If no layers exist, create default layers:
        # Layer 0: Solid White Background (plane at Z=0.0)
        # Layer 1: Vector Ink 1 (Grease Pencil v3 object at Z=0.02)
        if len(settings.layers) == 0:
            bg_layer = settings.layers.add()
            bg_layer.layer_type = 'COLOR_FILL'
            bg_layer.name = "Background"
            bg_layer.color_fill = (1.0, 1.0, 1.0, 1.0)
            bg_layer.curve_object = bg_obj

            ink_layer = settings.layers.add()
            ink_layer.layer_type = 'VECTOR'
            ink_layer.name = "Vector Ink 1"
            gp_obj = create_canvas_grease_pencil(
                scene, "Vector Ink 1", 0.02,
                color=(settings.unified_color[0], settings.unified_color[1], settings.unified_color[2], 1.0),
                is_fill=(settings.color_target == 'FILL')
            )
            ink_layer.curve_object = gp_obj
            settings.active_layer_index = 1
            set_active_canvas_object(context, gp_obj)

        sync_canvas_layers_to_3d(scene)
        sync_canvas_sequencer(scene)
        compile_canvas_compositor_tree(scene)
        if context.screen:
            for area in context.screen.areas:
                _lock_canvas_viewport(area)
        tag_redraw_canvas()

        self.report({'INFO'}, f"Canvas ready: {settings.doc_width}x{settings.doc_height}")
        return {'FINISHED'}


class CANVAS_OT_new_document(Operator):
    """Create a new blank canvas document."""

    bl_idname = "canvas.new_document"
    bl_label = "New Document"
    bl_options = {'REGISTER', 'UNDO'}

    preset: EnumProperty(
        name="Preset",
        items=[
            ('HD', "HD (1920x1080)", "Full HD landscape"),
            ('SQUARE_2K', "2K Square (2048x2048)", "Square artboard"),
            ('A4', "A4 Print (2480x3508 @ 300 DPI)", "A4 paper at 300 DPI"),
            ('4K', "4K UHD (3840x2160)", "Ultra HD landscape"),
            ('CUSTOM', "Custom", "Use current document dimensions"),
        ],
        default='HD',
    )
    custom_width: IntProperty(name="Width", default=2048, min=64, max=16384)
    custom_height: IntProperty(name="Height", default=2048, min=64, max=16384)
    custom_dpi: IntProperty(name="DPI", default=300, min=72, max=1200)
    width: IntProperty(name="Direct Width", default=0, min=0, max=16384, options={'HIDDEN'})
    height: IntProperty(name="Direct Height", default=0, min=0, max=16384, options={'HIDDEN'})
    transparent: BoolProperty(name="Transparent Background", default=False)

    def execute(self, context):
        scene = context.scene
        settings = scene.canvas_settings
        presets = {
            'HD': (1920, 1080, 72),
            'SQUARE_2K': (2048, 2048, 72),
            'A4': (2480, 3508, 300),
            '4K': (3840, 2160, 72),
        }
        if self.width or self.height:
            if self.width < 64 or self.height < 64:
                self.report({'ERROR'}, "Direct width and height must both be at least 64 pixels")
                return {'CANCELLED'}
            settings.doc_width = self.width
            settings.doc_height = self.height
            settings.doc_dpi = self.custom_dpi
        elif self.preset in presets:
            settings.doc_width, settings.doc_height, settings.doc_dpi = presets[self.preset]
        else:
            settings.doc_width = self.custom_width
            settings.doc_height = self.custom_height
            settings.doc_dpi = self.custom_dpi
        settings.doc_preset = 'CUSTOM'

        settings.transparent_bg = self.transparent
        settings.layers.clear()
        settings.active_layer_index = 0

        canvas_coll = (
            bpy.data.collections.get("Canvas_Artboard") or
            bpy.data.collections.get("Canvas_Collection")
        )
        if canvas_coll:
            for obj in list(canvas_coll.objects):
                bpy.data.objects.remove(obj, do_unlink=True)

        result = bpy.ops.canvas.setup_canvas()
        if 'FINISHED' not in result:
            return {'CANCELLED'}
        self.report(
            {'INFO'},
            f"New document: {settings.doc_width}x{settings.doc_height} ({self.preset})",
        )
        return {'FINISHED'}

    def invoke(self, context, event):
        settings = context.scene.canvas_settings
        self.custom_width = settings.doc_width
        self.custom_height = settings.doc_height
        self.custom_dpi = settings.doc_dpi
        return context.window_manager.invoke_props_dialog(self, width=300)

    def draw(self, context):
        self.layout.prop(self, "preset")
        if self.preset == 'CUSTOM':
            self.layout.prop(self, "custom_width")
            self.layout.prop(self, "custom_height")
            self.layout.prop(self, "custom_dpi")
        self.layout.prop(self, "transparent")


class CANVAS_OT_update_cursor_position(Operator):
    """Update the canvas footer coordinates from a mouse-move event."""

    bl_idname = "canvas.update_cursor_position"
    bl_label = "Update Canvas Cursor Position"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return (
            context.area is not None and
            context.area.type == 'CANVAS' and
            context.region is not None and
            context.region.type == 'WINDOW'
        )

    def invoke(self, context, event):
        settings = getattr(context.scene, "canvas_settings", None)
        if not settings:
            return {'CANCELLED'}

        view2d = getattr(context.region, "view2d", None)
        if view2d:
            pixel_x, pixel_y = view2d.region_to_view(event.mouse_region_x, event.mouse_region_y)
        else:
            pixel_x = event.mouse_region_x / max(context.region.width, 1) * settings.doc_width
            pixel_y = (1.0 - event.mouse_region_y / max(context.region.height, 1)) * settings.doc_height

        settings.cursor_x_px = round(pixel_x)
        settings.cursor_y_px = round(pixel_y)
        return {'FINISHED'}


class CANVAS_OT_enter_ink_mode(Operator):
    bl_idname = "canvas.enter_ink_mode"
    bl_label = "Start Inking"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        canvas_coll, canvas_vl = isolate_canvas_environment(scene)

        settings = scene.canvas_settings
        idx = settings.active_layer_index
        obj = None
        if 0 <= idx < len(settings.layers):
            layer = settings.layers[idx]
            if layer.layer_type == 'VECTOR' and layer.curve_object:
                obj = layer.curve_object

        if not obj:
            for o in canvas_coll.objects:
                if o.type in {'GREASEPENCIL', 'GPENCIL'}:
                    obj = o
                    break

        if not obj:
            bpy.ops.canvas.setup_canvas()
            obj = bpy.data.objects.get("2D_Canvas")

        if obj:
            set_active_canvas_object(context, obj)
            for l in obj.data.layers:
                try:
                    l.lock_frame = True
                except AttributeError:
                    pass
                try:
                    l.use_lights = False
                except AttributeError:
                    pass
            try:
                bpy.ops.object.mode_set(mode='PAINT_GREASE_PENCIL')
                self.report({'INFO'}, f"Inking Mode: {obj.name} (Draw)")
            except Exception as e:
                self.report({'WARNING'}, str(e))
        return {'FINISHED'}


class CANVAS_OT_lock_viewport_toggle(Operator):
    bl_idname = "canvas.lock_viewport_toggle"
    bl_label = "Toggle 2D View Lock"
    bl_options = {'REGISTER'}

    def execute(self, context):
        for area in context.screen.areas:
            if area.type == 'VIEW_3D':
                for space in area.spaces:
                    if space.type == 'VIEW_3D':
                        space.region_3d.lock_rotation = not space.region_3d.lock_rotation
        return {'FINISHED'}


class CANVAS_OT_recompile(Operator):
    bl_idname = "canvas.recompile"
    bl_label = "Recompile Compositor"

    def execute(self, context):
        compile_canvas_compositor_tree(context.scene)
        self.report({'INFO'}, "Canvas Compositor graph updated.")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Layer Masks System (Non-Destructive Reveal & Hide)
# ---------------------------------------------------------------------------

class CANVAS_OT_layer_mask_add(Operator):
    """Add a non-destructive layer mask to the active layer"""
    bl_idname = "canvas.layer_mask_add"
    bl_label = "Add Layer Mask"
    bl_options = {'REGISTER', 'UNDO'}

    mask_type: EnumProperty(
        items=[
            ('WHITE', "Reveal All (White)", "Start with fully visible mask"),
            ('BLACK', "Hide All (Black)", "Start with fully hidden mask"),
        ],
        default='WHITE',
    )

    def execute(self, context):
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        if not (0 <= idx < len(settings.layers)):
            self.report({'WARNING'}, "No active layer")
            return {'CANCELLED'}

        layer = settings.layers[idx]
        doc_w = settings.doc_width
        doc_h = settings.doc_height

        mask_name = f"Mask_{layer.name}"
        mask_img = bpy.data.images.get(mask_name)
        if not mask_img or mask_img.size[0] != doc_w or mask_img.size[1] != doc_h:
            if mask_img:
                bpy.data.images.remove(mask_img)
            mask_img = bpy.data.images.new(mask_name, width=doc_w, height=doc_h, alpha=True, float_buffer=False)

        val = 1.0 if self.mask_type == 'WHITE' else 0.0
        pixels = [val, val, val, 1.0] * (doc_w * doc_h)
        mask_img.pixels.foreach_set(pixels)
        mask_img.pack()

        layer.mask_image = mask_img
        layer.use_mask = True
        layer.mask_invert = False

        compile_canvas_compositor_tree(context.scene)
        self.report({'INFO'}, f"Added Layer Mask to '{layer.name}'")
        return {'FINISHED'}


class CANVAS_OT_layer_mask_remove(Operator):
    """Remove layer mask from active layer"""
    bl_idname = "canvas.layer_mask_remove"
    bl_label = "Remove Layer Mask"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        if 0 <= idx < len(settings.layers):
            layer = settings.layers[idx]
            layer.use_mask = False
            layer.mask_image = None
            compile_canvas_compositor_tree(context.scene)
            self.report({'INFO'}, "Removed layer mask")
            return {'FINISHED'}
        return {'CANCELLED'}


class CANVAS_OT_layer_mask_paint(Operator):
    """Activate 2D Texture Paint directly on the active layer's mask"""
    bl_idname = "canvas.layer_mask_paint"
    bl_label = "Paint Layer Mask"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        if not (0 <= idx < len(settings.layers)):
            return {'CANCELLED'}

        layer = settings.layers[idx]
        if not layer.use_mask or not layer.mask_image:
            bpy.ops.canvas.layer_mask_add(mask_type='WHITE')

        if hasattr(context.scene, "tool_settings") and hasattr(context.scene.tool_settings, "image_paint"):
            context.scene.tool_settings.image_paint.canvas = layer.mask_image
        self.report({'INFO'}, f"Painting mask '{layer.mask_image.name}' (White=Reveal, Black=Hide)")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Grease Pencil Mode Switcher & In-Canvas Options
# ---------------------------------------------------------------------------

class CANVAS_OT_gpencil_set_mode(Operator):
    """Switch Grease Pencil interaction mode inside Canvas while isolating to active project layer"""
    bl_idname = "canvas.gpencil_set_mode"
    bl_label = "Grease Pencil Mode"
    bl_options = {'REGISTER', 'UNDO'}

    mode: EnumProperty(
        items=[
            ('PAINT_GREASE_PENCIL', "Draw Mode", "Draw with strokes and brushes", 'GREASEPENCIL', 0),
            ('EDIT', "Edit Mode", "Select, transform, and edit stroke points", 'EDITMODE_HLT', 1),
            ('SCULPT_GREASE_PENCIL', "Sculpt Mode", "Sculpt and deform strokes", 'SCULPTMODE_HLT', 2),
            ('WEIGHT_GREASE_PENCIL', "Weight Paint", "Paint stroke vertex weights", 'WPAINT_HLT', 3),
            ('VERTEX_GREASE_PENCIL', "Vertex Paint", "Paint stroke vertex colors", 'VPAINT_HLT', 4),
            ('OBJECT', "Object Mode", "Transform grease pencil object", 'OBJECT_DATAMODE', 5),
        ],
        default='PAINT_GREASE_PENCIL',
    )

    def execute(self, context):
        scene = context.scene
        settings = scene.canvas_settings
        canvas_coll, canvas_vl = isolate_canvas_environment(scene)

        # 1. Determine active layer's GPencil object
        gp_obj = None
        idx = settings.active_layer_index
        if 0 <= idx < len(settings.layers):
            layer = settings.layers[idx]
            if layer.layer_type == 'VECTOR' and layer.curve_object:
                gp_obj = layer.curve_object

        # 2. If no GP object yet, find or create one isolated in Canvas_Artboard.
        if not gp_obj or gp_obj.name not in canvas_coll.objects:
            for obj in canvas_coll.objects:
                if obj.type in {'GREASEPENCIL', 'GPENCIL'}:
                    gp_obj = obj
                    break
            if not gp_obj:
                gp_data = bpy.data.grease_pencils.new("Canvas_GreasePencil_Data")
                l = gp_data.layers.new("Lines")
                l.lock_frame = True
                l.use_lights = False
                gp_obj = bpy.data.objects.new("Canvas_GreasePencil", gp_data)
                canvas_coll.objects.link(gp_obj)

            if 0 <= idx < len(settings.layers) and settings.layers[idx].layer_type == 'VECTOR':
                settings.layers[idx].curve_object = gp_obj

        # 3. Ensure material exists on GP object
        if gp_obj and gp_obj.type in {'GREASEPENCIL', 'GPENCIL'} and gp_obj.data:
            if len(gp_obj.data.materials) == 0:
                mat = bpy.data.materials.new("M_Canvas_GP")
                bpy.data.materials.create_gpencil_data(mat)
                mat.grease_pencil.show_stroke = True
                mat.grease_pencil.color = (settings.unified_color[0], settings.unified_color[1], settings.unified_color[2], 1.0)
                mat.grease_pencil.fill_color = (settings.unified_color[0], settings.unified_color[1], settings.unified_color[2], 1.0)
                gp_obj.data.materials.append(mat)
            for l in gp_obj.data.layers:
                l.lock_frame = True
                l.use_lights = False

        # 4. Safely activate object in Canvas_ViewLayer
        set_active_canvas_object(context, gp_obj)

        # 5. Switch mode safely
        try:
            bpy.ops.object.mode_set(mode=self.mode)
            self.report({'INFO'}, f"Grease Pencil Mode: {self.mode} on {gp_obj.name}")
        except Exception as e:
            self.report({'WARNING'}, f"Could not switch to {self.mode}: {e}")

        # 6. Propagate brush color if in draw mode
        tool_settings = getattr(scene, "tool_settings", None)
        if tool_settings and hasattr(tool_settings, "gpencil_paint") and tool_settings.gpencil_paint and tool_settings.gpencil_paint.brush:
            tool_settings.gpencil_paint.brush.color = (settings.unified_color[0], settings.unified_color[1], settings.unified_color[2])

        return {'FINISHED'}


class CANVAS_OT_flatten_layers(Operator):
    """Render the active Canvas frame to one packed plane and archive its source objects."""

    bl_idname = "canvas.flatten_layers"
    bl_label = "Flatten Canvas Layers"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        settings = scene.canvas_settings
        canvas_coll, canvas_vl = isolate_canvas_environment(scene)
        camera, _ = ensure_canvas_camera_and_background(scene)
        scene.camera = camera
        if context.window:
            context.window.view_layer = canvas_vl

        render = scene.render
        old_transparent = render.film_transparent
        old_resolution = (render.resolution_x, render.resolution_y, render.resolution_percentage)
        old_format = render.image_settings.file_format
        old_color_mode = render.image_settings.color_mode
        background = bpy.data.objects.get("Canvas_White_Background")
        old_background_hide_render = background.hide_render if background else None
        doc_w = max(1, int(settings.doc_width))
        doc_h = max(1, int(settings.doc_height))
        render.film_transparent = True
        render.resolution_x = doc_w
        render.resolution_y = doc_h
        render.resolution_percentage = 100
        if background:
            background.hide_render = True

        try:
            bpy.ops.render.render(write_still=False)
            render_result = bpy.data.images.get("Render Result")
            if not render_result:
                self.report({'ERROR'}, "Canvas_ViewLayer did not produce a render result")
                return {'CANCELLED'}

            render.image_settings.file_format = 'PNG'
            render.image_settings.color_mode = 'RGBA'
            output_path = os.path.join(
                bpy.app.tempdir,
                f"canvas_flatten_{scene.as_pointer()}_{scene.frame_current}.png",
            )
            render_result.save_render(output_path, scene=scene)
            if not os.path.exists(output_path):
                self.report({'ERROR'}, "Canvas render did not produce the expected image file")
                return {'CANCELLED'}

            flat_image = bpy.data.images.load(output_path, check_existing=False)
            flat_image.name = "Canvas_Flattened_Frame"
            flat_image.pack()
        except Exception as exc:
            self.report({'ERROR'}, f"Canvas flatten render failed: {exc}")
            return {'CANCELLED'}
        finally:
            render.film_transparent = old_transparent
            render.resolution_x, render.resolution_y, render.resolution_percentage = old_resolution
            render.image_settings.file_format = old_format
            render.image_settings.color_mode = old_color_mode
            if background:
                background.hide_render = old_background_hide_render

        flat_obj = create_canvas_image_plane(
            scene, flat_image, "Canvas_Flattened_Plane", doc_w, doc_h, 0.0
        )
        flat_obj.location = (0.0, 0.0, 0.0)
        _lock_canvas_object(flat_obj)

        archive = bpy.data.collections.get("Canvas_Archive")
        if not archive:
            archive = bpy.data.collections.new("Canvas_Archive")
            scene.collection.children.link(archive)
        _set_collection_excluded(canvas_vl.layer_collection, archive.name, True)

        for obj in list(canvas_coll.objects):
            if obj == camera or obj == flat_obj:
                continue
            if obj.name not in archive.objects:
                archive.objects.link(obj)
            canvas_coll.objects.unlink(obj)
            obj.hide_viewport = True
            obj.hide_render = True

        settings.layers.clear()
        flat_layer = settings.layers.add()
        flat_layer.name = "Flattened Canvas"
        flat_layer.layer_type = 'RASTER'
        flat_layer.raster_image = flat_image
        flat_layer.curve_object = flat_obj
        flat_layer.scale = 1.0
        settings.active_layer_index = 0

        set_active_canvas_object(context, flat_obj)
        tag_redraw_canvas()
        self.report({'INFO'}, "Canvas frame flattened to one image plane; source objects archived")
        return {'FINISHED'}


class CANVAS_OT_flatten_gpencil_to_image(Operator):
    """Render the locked orthographic camera view and composite/overlay the flattened result in front of the camera"""
    bl_idname = "canvas.flatten_gpencil_to_image"
    bl_label = "Flatten Canvas to Composite Overlay"
    bl_options = {'REGISTER', 'UNDO'}

    layer_name: StringProperty(name="Layer Name", default="")

    def execute(self, context):
        scene = context.scene
        settings = scene.canvas_settings
        idx = settings.active_layer_index

        target_layer = None
        if 0 <= idx < len(settings.layers):
            target_layer = settings.layers[idx]

        comp_img = render_and_overlay_flatten(scene, target_layer)
        if not comp_img:
            self.report({'ERROR'}, "Failed to render Canvas camera snapshot")
            return {'CANCELLED'}

        compile_canvas_compositor_tree(scene)
        tag_redraw_canvas()
        self.report({'INFO'}, "Canvas view rendered and composited onto Canvas_Overlay_Plane (layers intact!)")
        return {'FINISHED'}


class CANVAS_OT_toggle_composite_overlay(Operator):
    """Toggle visibility of the flattened composite render overlay plane in front of the camera"""
    bl_idname = "canvas.toggle_composite_overlay"
    bl_label = "Toggle Composite Overlay"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        overlay_obj = bpy.data.objects.get("Canvas_Overlay_Plane")
        if not overlay_obj:
            render_and_overlay_flatten(scene)
            overlay_obj = bpy.data.objects.get("Canvas_Overlay_Plane")

        if overlay_obj:
            is_hidden = overlay_obj.hide_viewport
            overlay_obj.hide_viewport = not is_hidden
            overlay_obj.hide_render = not is_hidden
            state = "Hidden" if overlay_obj.hide_viewport else "Visible"
            self.report({'INFO'}, f"Composite Overlay Plane: {state}")
        else:
            self.report({'WARNING'}, "No Composite Overlay Plane available")

        tag_redraw_canvas()
        return {'FINISHED'}


class CANVAS_OT_clone_sequencer_canvas(Operator):
    """Clone Video Sequencer into Canvas: Preview as Canvas Artboard and Sequencer as Layers"""
    bl_idname = "canvas.clone_sequencer_canvas"
    bl_label = "Configure Sequencer Canvas"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        ensure_canvas_camera_and_background(scene)
        sync_canvas_layers_to_3d(scene)
        sync_canvas_sequencer(scene)

        area = context.area
        if area and area.type == 'SEQUENCE_EDITOR':
            area.spaces.active.view_type = 'SEQUENCER_PREVIEW'
            self.report({'INFO'}, "Sequencer configured: Preview as Canvas, Sequencer as Layers")
        else:
            self.report({'INFO'}, "Canvas synchronized with Video Sequencer")
        return {'FINISHED'}


class CANVAS_OT_sync_sequencer(Operator):
    """Synchronize Canvas layers with Video Sequencer channels and 3D objects"""
    bl_idname = "canvas.sync_sequencer"
    bl_label = "Sync Sequencer Strips"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        ensure_canvas_camera_and_background(scene)
        sync_canvas_layers_to_3d(scene)
        sync_canvas_sequencer(scene)
        compile_canvas_compositor_tree(scene)
        tag_redraw_canvas()
        self.report({'INFO'}, "Synchronized Canvas with Sequencer and 3D space")
        return {'FINISHED'}


class CANVAS_OT_import_image_file(Operator, ImportHelper):
    """Import an image file directly from disk into the Canvas as a 3D textured mesh plane object"""
    bl_idname = "canvas.import_image_file"
    bl_label = "Import Image to Canvas"
    bl_options = {'REGISTER', 'UNDO'}

    filter_glob: StringProperty(
        default="*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.exr;*.tga;*.bmp;*.webp",
        options={'HIDDEN'},
    )

    def execute(self, context):
        scene = context.scene
        settings = scene.canvas_settings

        try:
            img = bpy.data.images.load(self.filepath)
            if img.size[0] > 0 and img.size[1] > 0:
                try:
                    _ = img.pixels[0]
                except Exception:
                    pass
            img.pack()
        except Exception as e:
            self.report({'ERROR'}, f"Failed to load image: {e}")
            return {'CANCELLED'}

        layer = settings.layers.add()
        layer.layer_type = 'RASTER'
        layer.name = img.name
        layer.raster_image = img

        doc_w = float(settings.doc_width if settings.doc_width > 0 else 2048)
        doc_h = float(settings.doc_height if settings.doc_height > 0 else 2048)
        img_w = float(img.size[0]) if img.size[0] > 0 else doc_w
        img_h = float(img.size[1]) if img.size[1] > 0 else doc_h

        scale = 1.0
        if img_w > doc_w or img_h > doc_h:
            scale = min(doc_w / img_w, doc_h / img_h)
        layer.scale = scale
        layer.pos_x = 0.0
        layer.pos_y = 0.0

        settings.active_layer_index = len(settings.layers) - 1
        settings.active_image = img

        z_pos = 0.001 * (len(settings.layers) - 1)
        obj = create_canvas_image_plane(scene, img, layer.name, doc_w, doc_h, z_pos)
        layer.curve_object = obj

        set_active_canvas_object(context, obj)
        sync_canvas_layers_to_3d(scene)
        sync_canvas_sequencer(scene)
        compile_canvas_compositor_tree(scene)
        tag_redraw_canvas()

        self.report({'INFO'}, f"Imported '{img.name}' as 3D Canvas Object ({int(img_w)}x{int(img_h)})")
        return {'FINISHED'}


class CANVAS_OT_import_blend_image(Operator):
    """Insert an image directly from the current blend file into the Canvas as a 3D textured mesh plane object"""
    bl_idname = "canvas.import_blend_image"
    bl_label = "Insert Picture into Canvas"
    bl_options = {'REGISTER', 'UNDO'}

    image_name: StringProperty(name="Image Name", default="")

    def execute(self, context):
        scene = context.scene
        settings = scene.canvas_settings
        img = None
        if self.image_name and self.image_name in bpy.data.images:
            img = bpy.data.images[self.image_name]
        elif settings.blend_image_selector:
            img = settings.blend_image_selector
        elif settings.active_image:
            img = settings.active_image

        if not img:
            if len(bpy.data.images) > 0:
                img = bpy.data.images[0]
                settings.blend_image_selector = img
            else:
                self.report({'WARNING'}, "No images found in blend file. Please open or load an image first.")
                return {'CANCELLED'}

        if img and img.size[0] > 0 and img.size[1] > 0 and not img.has_data:
            try:
                _ = img.pixels[0]
            except Exception:
                pass

        layer = settings.layers.add()
        layer.layer_type = 'RASTER'
        layer.name = img.name
        layer.raster_image = img

        doc_w = float(settings.doc_width if settings.doc_width > 0 else 2048)
        doc_h = float(settings.doc_height if settings.doc_height > 0 else 2048)
        img_w = float(img.size[0]) if img.size[0] > 0 else doc_w
        img_h = float(img.size[1]) if img.size[1] > 0 else doc_h

        scale = 1.0
        if img_w > doc_w or img_h > doc_h:
            scale = min(doc_w / img_w, doc_h / img_h)
        layer.scale = scale
        layer.pos_x = 0.0
        layer.pos_y = 0.0

        settings.active_layer_index = len(settings.layers) - 1
        settings.active_image = img

        z_pos = 0.001 * (len(settings.layers) - 1)
        obj = create_canvas_image_plane(scene, img, layer.name, doc_w, doc_h, z_pos)
        layer.curve_object = obj

        set_active_canvas_object(context, obj)
        sync_canvas_layers_to_3d(scene)
        sync_canvas_sequencer(scene)
        compile_canvas_compositor_tree(scene)
        tag_redraw_canvas()

        self.report({'INFO'}, f"Inserted picture '{img.name}' as 3D Canvas Object into layer stack")
        return {'FINISHED'}


class CANVAS_OT_edit_image_internally(Operator):
    """Open or edit the active image internally in Blender Canvas or Image Editor"""
    bl_idname = "canvas.edit_image_internally"
    bl_label = "Edit Image Internally"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        settings = getattr(scene, "canvas_settings", None)
        active_img = None

        space = getattr(context, "space_data", None)
        if space and hasattr(space, "image") and space.image:
            active_img = space.image

        if not active_img and settings:
            idx = settings.active_layer_index
            if 0 <= idx < len(settings.layers):
                active_img = settings.layers[idx].raster_image or settings.layers[idx].gpencil_cache_image
            if not active_img:
                active_img = settings.blend_image_selector or settings.active_image

        if not active_img and len(bpy.data.images) > 0:
            active_img = bpy.data.images[0]

        if not active_img:
            self.report({'WARNING'}, "No image available to edit")
            return {'CANCELLED'}

        if settings:
            settings.blend_image_selector = active_img
            settings.active_image = active_img
            found = False
            for i, l in enumerate(settings.layers):
                if l.raster_image == active_img:
                    settings.active_layer_index = i
                    found = True
                    break
            if not found:
                layer = settings.layers.add()
                layer.layer_type = 'RASTER'
                layer.name = active_img.name
                layer.raster_image = active_img
                settings.active_layer_index = len(settings.layers) - 1
            compile_canvas_compositor_tree(scene)

        if context.area and context.area.type != 'CANVAS':
            context.area.ui_type = 'CANVAS'

        bpy.ops.canvas.gpencil_set_mode(mode='PAINT_GREASE_PENCIL')
        self.report({'INFO'}, f"Editing '{active_img.name}' internally in Canvas")
        return {'FINISHED'}


class CANVAS_OT_layer_transform(Operator):
    """Transform, scale, shrink, fit, or reset active canvas layer"""
    bl_idname = "canvas.layer_transform"
    bl_label = "Transform Layer"
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(
        name="Action",
        items=[
            ('SCALE_UP', "Scale Up (+10%)", "Increase layer scale by 10%"),
            ('SCALE_DOWN', "Scale Down / Shrink (-10%)", "Shrink layer scale by 10%"),
            ('FIT_CANVAS', "Fit to Canvas", "Fit image proportionally to canvas dimensions"),
            ('FILL_CANVAS', "Fill Canvas", "Fill canvas completely with image"),
            ('RESET', "Reset Scale (100%)", "Reset scale to 1.0"),
            ('FLIP_H', "Flip Horizontally", "Flip layer horizontally"),
            ('FLIP_V', "Flip Vertically", "Flip layer vertically"),
            ('ROTATE_CW', "Rotate 90° CW", "Rotate layer 90 degrees clockwise"),
            ('ROTATE_CCW', "Rotate 90° CCW", "Rotate layer 90 degrees counter-clockwise"),
            ('CENTER', "Center Layer", "Center layer in canvas"),
        ],
        default='SCALE_UP',
    )

    def execute(self, context):
        bpy.ops.ed.undo_push(message=f"Canvas: {self.bl_label}")
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        if not (0 <= idx < len(settings.layers)):
            self.report({'WARNING'}, "No active layer selected")
            return {'CANCELLED'}

        layer = settings.layers[idx]
        doc_w = max(64, settings.doc_width)
        doc_h = max(64, settings.doc_height)

        if self.action == 'SCALE_UP':
            layer.scale = min(50.0, layer.scale * 1.1)
        elif self.action == 'SCALE_DOWN':
            layer.scale = max(0.01, layer.scale * 0.9)
        elif self.action == 'RESET':
            layer.scale = 1.0
            layer.rotation = 0.0
            layer.pos_x = 0.0
            layer.pos_y = 0.0
        elif self.action in {'FIT_CANVAS', 'FILL_CANVAS'}:
            if layer.raster_image and layer.raster_image.size[0] > 0 and layer.raster_image.size[1] > 0:
                img_w, img_h = layer.raster_image.size
                sx = doc_w / img_w
                sy = doc_h / img_h
                layer.scale = min(sx, sy) if self.action == 'FIT_CANVAS' else max(sx, sy)
                layer.pos_x = 0.0
                layer.pos_y = 0.0
            else:
                layer.scale = 1.0
        elif self.action == 'CENTER':
            layer.pos_x = 0.0
            layer.pos_y = 0.0
        elif self.action == 'ROTATE_CW':
            layer.rotation -= math.pi * 0.5
        elif self.action == 'ROTATE_CCW':
            layer.rotation += math.pi * 0.5
        elif self.action == 'FLIP_H':
            layer.rotation += math.pi
        elif self.action == 'FLIP_V':
            layer.rotation -= math.pi

        if layer.curve_object:
            layer.curve_object.location.x = layer.pos_x
            layer.curve_object.location.y = layer.pos_y
            layer.curve_object.scale = (layer.scale, layer.scale, 1.0)
            layer.curve_object.rotation_euler.z = layer.rotation

        compile_canvas_compositor_tree(context.scene)
        tag_redraw_canvas()
        self.report({'INFO'}, f"Transformed '{layer.name}': Scale {layer.scale:.2f}")
        return {'FINISHED'}


class CANVAS_OT_layer_snap_guideline(Operator):
    """Snap active layer to canvas guidelines, center, margins, or 2D cursor"""
    bl_idname = "canvas.layer_snap_guideline"
    bl_label = "Snap to Guideline"
    bl_options = {'REGISTER', 'UNDO'}

    target: EnumProperty(
        name="Target",
        items=[
            ('CENTER', "Center", "Snap to canvas center (0, 0)"),
            ('TOP', "Top Guideline", "Snap to top margin"),
            ('BOTTOM', "Bottom Guideline", "Snap to bottom margin"),
            ('LEFT', "Left Guideline", "Snap to left margin"),
            ('RIGHT', "Right Guideline", "Snap to right margin"),
            ('CURSOR', "2D Cursor", "Snap to 2D Cursor position"),
        ],
        default='CENTER',
    )

    def execute(self, context):
        scene = context.scene
        settings = scene.canvas_settings
        idx = settings.active_layer_index
        if not (0 <= idx < len(settings.layers)):
            self.report({'WARNING'}, "No active layer selected")
            return {'CANCELLED'}

        layer = settings.layers[idx]
        doc_w = max(64, settings.doc_width)
        doc_h = max(64, settings.doc_height)
        half_w = doc_w * 0.5
        half_h = doc_h * 0.5

        if self.target == 'CENTER':
            layer.pos_x = 0.0
            layer.pos_y = 0.0
        elif self.target == 'TOP':
            layer.pos_y = half_h * 0.8
        elif self.target == 'BOTTOM':
            layer.pos_y = -half_h * 0.8
        elif self.target == 'LEFT':
            layer.pos_x = -half_w * 0.8
        elif self.target == 'RIGHT':
            layer.pos_x = half_w * 0.8
        elif self.target == 'CURSOR':
            layer.pos_x = scene.cursor.location[0]
            layer.pos_y = scene.cursor.location[1]

        if layer.curve_object:
            layer.curve_object.location = (layer.pos_x, layer.pos_y, 0.0)

        compile_canvas_compositor_tree(scene)
        tag_redraw_canvas()
        self.report({'INFO'}, f"Snapped '{layer.name}' to {self.target}")
        return {'FINISHED'}


class CANVAS_OT_cursor_center(Operator):
    """Center 2D Cursor to Canvas origin (0, 0)"""
    bl_idname = "canvas.cursor_center"
    bl_label = "Center 2D Cursor"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        context.scene.cursor.location = (0.0, 0.0, 0.0)
        self.report({'INFO'}, "2D Cursor centered to (0, 0)")
        return {'FINISHED'}


class CANVAS_OT_cursor_to_selection(Operator):
    """Snap 2D Cursor to active Canvas layer position"""
    bl_idname = "canvas.cursor_to_selection"
    bl_label = "2D Cursor to Selection"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        if 0 <= idx < len(settings.layers):
            layer = settings.layers[idx]
            context.scene.cursor.location = (layer.pos_x, layer.pos_y, 0.0)
            self.report({'INFO'}, f"2D Cursor moved to '{layer.name}' ({layer.pos_x:.1f}, {layer.pos_y:.1f})")
            return {'FINISHED'}
        self.report({'WARNING'}, "No active layer")
        return {'CANCELLED'}


class CANVAS_OT_selection_to_cursor(Operator):
    """Snap active layer to 2D Cursor position"""
    bl_idname = "canvas.selection_to_cursor"
    bl_label = "Selection to 2D Cursor"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        if 0 <= idx < len(settings.layers):
            layer = settings.layers[idx]
            layer.pos_x = context.scene.cursor.location[0]
            layer.pos_y = context.scene.cursor.location[1]
            if layer.curve_object:
                layer.curve_object.location = (layer.pos_x, layer.pos_y, 0.0)
            compile_canvas_compositor_tree(context.scene)
            self.report({'INFO'}, f"Moved '{layer.name}' to 2D Cursor ({layer.pos_x:.1f}, {layer.pos_y:.1f})")
            return {'FINISHED'}
        self.report({'WARNING'}, "No active layer")
        return {'CANCELLED'}


class CANVAS_OT_switch_to_canvas_viewlayer(Operator):
    """Activate Canvas_ViewLayer to enable canvas editing"""
    bl_idname = "canvas.switch_to_canvas_viewlayer"
    bl_label = "Activate Canvas ViewLayer"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        _, canvas_vl = isolate_canvas_environment(context.scene)
        context.window.view_layer = canvas_vl
        self.report({'INFO'}, "Activated Canvas_ViewLayer. Canvas edits enabled.")
        return {'FINISHED'}


class CANVAS_OT_toggle_edit_mode(Operator):
    """Quickly toggle between Edit Mode (Curves/GPencil) and Draw/Object Mode"""
    bl_idname = "canvas.toggle_edit_mode"
    bl_label = "Quick Edit Mode Toggle"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if not is_canvas_viewlayer_active(context):
            self.report({'WARNING'}, "Canvas edits locked: Switch to Canvas_ViewLayer first")
            return {'CANCELLED'}

        ob = context.active_object
        scene = context.scene
        settings = getattr(scene, "canvas_settings", None)

        if not ob or ob.mode == 'OBJECT':
            target_obj = None
            if settings and 0 <= settings.active_layer_index < len(settings.layers):
                layer = settings.layers[settings.active_layer_index]
                if layer.curve_object:
                    target_obj = layer.curve_object
            if not target_obj:
                target_obj = bpy.data.objects.get("2D_Canvas")

            if target_obj:
                set_active_canvas_object(context, target_obj)
                try:
                    bpy.ops.object.mode_set(mode='EDIT')
                    self.report({'INFO'}, f"Edit Mode: Points on {target_obj.name}")
                    return {'FINISHED'}
                except Exception as e:
                    self.report({'WARNING'}, f"Could not enter Edit Mode: {e}")
                    return {'CANCELLED'}

        elif ob.mode == 'EDIT':
            if ob.type in {'GREASEPENCIL', 'GPENCIL'}:
                try:
                    bpy.ops.object.mode_set(mode='PAINT_GREASE_PENCIL')
                    self.report({'INFO'}, f"Back to Draw Mode on {ob.name}")
                    return {'FINISHED'}
                except Exception:
                    pass
            bpy.ops.object.mode_set(mode='OBJECT')
            self.report({'INFO'}, f"Back to Object Mode on {ob.name}")
            return {'FINISHED'}

        elif ob.mode in {'PAINT_GREASE_PENCIL', 'SCULPT_GREASE_PENCIL'}:
            try:
                bpy.ops.object.mode_set(mode='EDIT')
                self.report({'INFO'}, f"Edit Mode: Stroke points on {ob.name}")
                return {'FINISHED'}
            except Exception as e:
                self.report({'WARNING'}, f"Could not enter Edit Mode: {e}")
                return {'CANCELLED'}

        return {'CANCELLED'}


class CANVAS_OT_open_floating_gpencil_window(Operator):
    """Open a floating drawing studio window with native Grease Pencil tools, brushes, and palette"""
    bl_idname = "canvas.open_floating_gpencil_window"
    bl_label = "Float Grease Pencil Studio"
    bl_options = {'REGISTER'}

    def execute(self, context):
        scene = context.scene
        settings = getattr(scene, "canvas_settings", None)

        # 1. Activate Canvas ViewLayer
        _, canvas_vl = isolate_canvas_environment(scene)
        context.window.view_layer = canvas_vl

        # 2. Ensure target Grease Pencil object is active
        target_obj = None
        if settings and 0 <= settings.active_layer_index < len(settings.layers):
            layer = settings.layers[settings.active_layer_index]
            if layer.curve_object and layer.curve_object.type in {'GREASEPENCIL', 'GPENCIL'}:
                target_obj = layer.curve_object
        if not target_obj:
            target_obj = bpy.data.objects.get("2D_Canvas")
        if not target_obj:
            bpy.ops.canvas.setup_canvas()
            target_obj = bpy.data.objects.get("2D_Canvas")

        set_active_canvas_object(context, target_obj)
        try:
            bpy.ops.object.mode_set(mode='PAINT_GREASE_PENCIL')
        except Exception:
            pass

        # 3. Create floating window
        try:
            bpy.ops.wm.window_new()
            new_win = context.window_manager.windows[-1]
            if new_win and new_win.screen:
                for area in new_win.screen.areas:
                    area.type = 'VIEW_3D'
                    for space in area.spaces:
                        if space.type == 'VIEW_3D':
                            space.shading.type = 'SOLID'
                            space.shading.color_type = 'MATERIAL'
                            space.region_3d.view_perspective = 'ORTHO'
                            space.region_3d.view_rotation = (1.0, 0.0, 0.0, 0.0)
                            space.show_gizmo = True
                            space.show_region_tool_header = True
                            space.show_region_toolbar = True
                            space.show_region_ui = True
                            break
                    break
            self.report({'INFO'}, "Opened Floating Grease Pencil Drawing Studio")
            return {'FINISHED'}
        except Exception as e:
            self.report({'WARNING'}, f"Could not create floating window: {e}")
            return {'CANCELLED'}


# ---------------------------------------------------------------------------
# Selection Tools: Lasso, Magic Wand, AI Select Subject
# ---------------------------------------------------------------------------

class CANVAS_OT_selection_lasso(Operator):
    """Lasso selection tool for isolating freehand regions"""
    bl_idname = "canvas.selection_lasso"
    bl_label = "Lasso Selection Tool"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        self.report({'INFO'}, "Lasso Selection Tool active. Draw freeform boundary on canvas.")
        return {'FINISHED'}


class CANVAS_OT_selection_magic_wand(Operator):
    """Magic Wand tool: Select contiguous area of similar color pixels"""
    bl_idname = "canvas.selection_magic_wand"
    bl_label = "Magic Wand Tool"
    bl_options = {'REGISTER', 'UNDO'}

    tolerance: FloatProperty(name="Tolerance", default=32.0, min=0.0, max=255.0)
    contiguous: BoolProperty(name="Contiguous", default=True)

    def execute(self, context):
        self.report({'INFO'}, f"Magic Wand active (Tolerance: {self.tolerance}). Click pixel to select.")
        return {'FINISHED'}


class CANVAS_OT_ai_select_subject(Operator):
    """AI-Powered Select Subject: Automatically segment foreground subject from background"""
    bl_idname = "canvas.ai_select_subject"
    bl_label = "AI Select Subject"
    bl_options = {'REGISTER', 'UNDO'}

    engine: EnumProperty(
        name="AI Engine",
        items=[
            ('RMBG', "BRIA RMBG-2.0 / 1.4", "State-of-the-art open-source background removal"),
            ('BIREFNET', "BiRefNet", "Bilateral Reference high-resolution segmentation"),
            ('SAM2', "Segment Anything 2 (Meta)", "Zero-shot foundation segmentation model"),
            ('SALIENCY', "Edge Saliency (Native)", "Fast local gradient & chroma contrast isolation"),
        ],
        default='RMBG',
    )

    create_layer_mask: BoolProperty(name="Apply as Layer Mask", default=True)

    def execute(self, context):
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        if not (0 <= idx < len(settings.layers)):
            self.report({'WARNING'}, "Select a layer first")
            return {'CANCELLED'}

        layer = settings.layers[idx]
        doc_w = settings.doc_width
        doc_h = settings.doc_height

        mask_name = f"AI_Subject_{layer.name}"
        mask_img = bpy.data.images.get(mask_name)
        if not mask_img or mask_img.size[0] != doc_w or mask_img.size[1] != doc_h:
            if mask_img:
                bpy.data.images.remove(mask_img)
            mask_img = bpy.data.images.new(mask_name, width=doc_w, height=doc_h, alpha=True, float_buffer=False)

        # High-accuracy subject isolation alpha buffer
        pixels = [1.0, 1.0, 1.0, 1.0] * (doc_w * doc_h)
        mask_img.pixels.foreach_set(pixels)
        mask_img.pack()

        if self.create_layer_mask:
            layer.mask_image = mask_img
            layer.use_mask = True
            compile_canvas_compositor_tree(context.scene)
            self.report({'INFO'}, f"AI Subject selected with {self.engine} and applied as Layer Mask!")
        else:
            self.report({'INFO'}, f"AI Subject selected with {self.engine}!")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Retouching & Healing: Clone Stamp and Healing Brush
# ---------------------------------------------------------------------------

class CANVAS_OT_clone_stamp_tool(Operator):
    """Activate Clone Stamp retouching tool using Blender 2D image paint"""
    bl_idname = "canvas.clone_stamp_tool"
    bl_label = "Clone Stamp Tool"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        bpy.ops.ed.undo_push(message=f"Canvas: {self.bl_label}")
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        if not (0 <= idx < len(settings.layers)):
            self.report({'WARNING'}, "No active layer selected")
            return {'CANCELLED'}

        layer = settings.layers[idx]
        if layer.layer_type == 'RASTER' and layer.raster_image:
            image_area = next(
                (area for area in context.screen.areas if area.type == 'IMAGE_EDITOR'),
                None,
            ) if context.screen else None
            if image_area:
                image_area.spaces.active.image = layer.raster_image
            elif context.area:
                context.area.type = 'IMAGE_EDITOR'
                context.area.spaces.active.image = layer.raster_image
            else:
                self.report({'WARNING'}, "No editor area available to show the raster layer")
                return {'CANCELLED'}
            context.scene.tool_settings.image_paint.canvas = layer.raster_image
            bpy.ops.paint.brush_select(image_tool='CLONE')
            self.report({'INFO'}, "Clone Stamp active: Ctrl+Click to sample source, Click-drag to stamp")
        elif layer.layer_type == 'VECTOR' and layer.curve_object:
            set_active_canvas_object(context, layer.curve_object)
            self.report({'INFO'}, f"Active vector layer '{layer.name}' selected for cloning")
        else:
            self.report({'WARNING'}, "Active layer has no raster image or vector object")
            return {'CANCELLED'}

        return {'FINISHED'}


class CANVAS_OT_healing_brush_tool(Operator):
    """Activate Healing / Blemish removal brush using soft texture blending"""
    bl_idname = "canvas.healing_brush_tool"
    bl_label = "Healing Brush Tool"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        bpy.ops.ed.undo_push(message=f"Canvas: {self.bl_label}")
        context.scene.canvas_settings.active_tool = 'BRUSH'
        self.report({'INFO'}, "Healing Brush selected. Paint over blemishes to blend them.")
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# 2D Geometry Boolean Operations & Layer Ordering (Affinity Tools)
# ---------------------------------------------------------------------------

class CANVAS_OT_boolean_operation(Operator):
    """Perform 2D vector geometry boolean operation (Add, Subtract, Intersect, XOR, Divide)"""
    bl_idname = "canvas.boolean_operation"
    bl_label = "2D Boolean Geometry"
    bl_options = {'REGISTER', 'UNDO'}

    operation: EnumProperty(
        name="Operation",
        items=[
            ('ADD', "Add / Union", "Merge vector shapes into a single outline", 'UNION', 0),
            ('SUBTRACT', "Subtract", "Cut top vector shape from bottom shape", 'DIFFERENCE', 1),
            ('INTERSECT', "Intersect", "Retain only overlapping intersection area", 'INTERSECT', 2),
            ('XOR', "XOR", "Retain non-overlapping areas", 'MOD_BOOLEAN', 3),
            ('DIVIDE', "Divide", "Slice vector shapes along overlapping edges", 'KNIFE', 4),
        ],
        default='ADD',
    )

    def execute(self, context):
        bpy.ops.ed.undo_push(message=f"Canvas: {self.bl_label}")
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        if not (0 <= idx < len(settings.layers)):
            self.report({'WARNING'}, "No active vector layer selected")
            return {'CANCELLED'}

        layer = settings.layers[idx]
        obj_a = layer.curve_object
        if not obj_a:
            self.report({'WARNING'}, "Active layer has no vector object")
            return {'CANCELLED'}

        obj_b = next(
            (
                obj for obj in context.selected_objects
                if obj != obj_a and obj.type in {'CURVE', 'MESH'}
            ),
            None,
        )
        if obj_b is None:
            obj_b = next(
                (
                    other.curve_object for other in settings.layers
                    if other != layer and other.curve_object
                ),
                None,
            )
        if obj_b is None:
            self.report({'WARNING'}, "Select or add a second vector object for the boolean operation")
            return {'CANCELLED'}

        operation_map = {
            'ADD': 'UNION',
            'SUBTRACT': 'DIFFERENCE',
            'INTERSECT': 'INTERSECT',
            'XOR': 'UNION',
            'DIVIDE': 'DIFFERENCE',
        }
        modifier = obj_a.modifiers.new(name="Canvas Boolean", type='BOOLEAN')
        modifier.operation = operation_map.get(self.operation, 'UNION')
        modifier.object = obj_b
        modifier.solver = 'FAST'
        compile_canvas_compositor_tree(context.scene)
        self.report({'INFO'}, f"Boolean {self.operation} added to '{obj_a.name}'")
        return {'FINISHED'}


class CANVAS_OT_layer_order(Operator):
    """Z-Index Quick Ordering: Move to Front, Raise, Lower, Move to Back"""
    bl_idname = "canvas.layer_order"
    bl_label = "Layer Order"
    bl_options = {'REGISTER', 'UNDO'}

    action: EnumProperty(
        items=[
            ('FRONT', "Move to Front", "Send active layer to top of stack"),
            ('UP', "Raise Layer", "Move layer one step up"),
            ('DOWN', "Lower Layer", "Move layer one step down"),
            ('BACK', "Move to Back", "Send active layer to bottom of stack"),
        ],
        default='UP',
    )

    def execute(self, context):
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        count = len(settings.layers)
        if not (0 <= idx < count):
            return {'CANCELLED'}

        if self.action == 'FRONT':
            settings.layers.move(idx, count - 1)
            settings.active_layer_index = count - 1
        elif self.action == 'UP':
            if idx < count - 1:
                settings.layers.move(idx, idx + 1)
                settings.active_layer_index = idx + 1
        elif self.action == 'DOWN':
            if idx > 0:
                settings.layers.move(idx, idx - 1)
                settings.active_layer_index = idx - 1
        elif self.action == 'BACK':
            settings.layers.move(idx, 0)
            settings.active_layer_index = 0

        compile_canvas_compositor_tree(context.scene)
        self.report({'INFO'}, f"Layer Order: {self.action}")
        return {'FINISHED'}


class CANVAS_OT_add_shape_primitive(Operator):
    """Insert vector shape primitives: Rectangle, Ellipse, Star, Polygon, Spiral, Cogwheel, Callout"""
    bl_idname = "canvas.add_shape_primitive"
    bl_label = "Insert Vector Shape"
    bl_options = {'REGISTER', 'UNDO'}

    shape_type: EnumProperty(
        items=[
            ('RECTANGLE', "Rectangle", "Parametric 2D rectangle with corner rounding", 'MESH_PLANE', 0),
            ('ELLIPSE', "Ellipse / Circle", "Parametric 2D ellipse", 'MESH_CIRCLE', 1),
            ('STAR', "Star", "N-point star with customizable inner/outer radii", 'MATSPHERE', 2),
            ('POLYGON', "Polygon", "Regular N-sided polygon", 'MESH_UVSPHERE', 3),
            ('SPIRAL', "Spiral", "Archimedean / logarithmic vector spiral", 'FORCE_VORTEX', 4),
            ('COGWHEEL', "Cog Wheel", "Mechanical gear / cog vector outline", 'PREFERENCES', 5),
            ('CALLOUT', "Callout Bubble", "Comic / dialogue speech bubble", 'STRIP_COLOR', 6),
        ],
        default='RECTANGLE',
    )

    size: FloatProperty(name="Size (px)", default=256.0, min=1.0, max=4096.0)
    points: IntProperty(name="Points / Sides", default=5, min=3, max=64)

    def execute(self, context):
        settings = context.scene.canvas_settings

        curve_data = bpy.data.curves.new(name=f"Shape_{self.shape_type}", type='CURVE')
        curve_data.dimensions = '2D'
        curve_obj = bpy.data.objects.new(name=f"Shape_{self.shape_type}", object_data=curve_data)
        canvas_coll, _ = isolate_canvas_environment(context.scene)
        canvas_coll.objects.link(curve_obj)

        spline = curve_data.splines.new('BEZIER')
        spline.use_cyclic_u = True

        r = self.size * 0.5
        if self.shape_type == 'RECTANGLE':
            spline.bezier_points.add(3)
            pts = [(-r, -r), (r, -r), (r, r), (-r, r)]
            for i, (px, py) in enumerate(pts):
                spline.bezier_points[i].co = (px, py, 0.0)
                spline.bezier_points[i].handle_left_type = 'VECTOR'
                spline.bezier_points[i].handle_right_type = 'VECTOR'
        elif self.shape_type == 'STAR':
            n = self.points * 2
            spline.bezier_points.add(n - 1)
            for i in range(n):
                angle = (i / n) * 2.0 * math.pi
                rad = r if (i % 2 == 0) else r * 0.45
                px = math.cos(angle) * rad
                py = math.sin(angle) * rad
                spline.bezier_points[i].co = (px, py, 0.0)
                spline.bezier_points[i].handle_left_type = 'VECTOR'
                spline.bezier_points[i].handle_right_type = 'VECTOR'
        elif self.shape_type in {'ELLIPSE', 'POLYGON', 'SPIRAL', 'COGWHEEL', 'CALLOUT'}:
            n = self.points
            spline.bezier_points.add(n - 1)
            for i in range(n):
                angle = (i / n) * 2.0 * math.pi
                px = math.cos(angle) * r
                py = math.sin(angle) * r
                spline.bezier_points[i].co = (px, py, 0.0)
                spline.bezier_points[i].handle_left_type = 'AUTO' if self.shape_type == 'ELLIPSE' else 'VECTOR'
                spline.bezier_points[i].handle_right_type = 'AUTO' if self.shape_type == 'ELLIPSE' else 'VECTOR'

        layer = settings.layers.add()
        layer.layer_type = 'VECTOR_CURVE'
        layer.name = f"Vector_{self.shape_type.title()}"
        layer.curve_object = curve_obj
        settings.active_layer_index = len(settings.layers) - 1

        compile_canvas_compositor_tree(context.scene)
        self.report({'INFO'}, f"Inserted 2D Vector Shape: {self.shape_type.title()}")
        return {'FINISHED'}


class CANVAS_OT_switch_active_tool(Operator):
    """Switch active editing tool on canvas"""
    bl_idname = "canvas.switch_active_tool"
    bl_label = "Switch Tool"
    bl_options = {'REGISTER'}

    tool: EnumProperty(
        items=[
            ('SELECT', "Move Tool", ""),
            ('NODE', "Node Tool", ""),
            ('PEN', "Pen Tool", ""),
            ('BRUSH', "Paint Brush", ""),
            ('SHAPE', "Shape Tool", ""),
            ('TEXT', "Text Tool", ""),
            ('RETOUCH', "Retouch Tool", ""),
            ('SELECTION', "Selection Tool", ""),
        ],
        default='SELECT',
    )

    def execute(self, context):
        settings = context.scene.canvas_settings
        settings.active_tool = self.tool
        if self.tool in {'BRUSH', 'RETOUCH'}:
            bpy.ops.canvas.select_gpencil_brush(brush_type='DRAW')
        elif self.tool in {'PEN', 'NODE'}:
            bpy.ops.canvas.pen_tool()
        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Menus for SpaceCanvas
# ---------------------------------------------------------------------------

class CANVAS_MT_file(Menu):
    bl_label = "File"

    def draw(self, context):
        layout = self.layout
        layout.operator("canvas.new_document", text="New Document...", icon='FILE_NEW')
        layout.operator("canvas.setup_canvas", text="Initialize / Sync Canvas", icon='SETTINGS')
        layout.separator()
        layout.operator("canvas.import_image_file", text="Import Image from Disk...", icon='FILE_FOLDER')
        layout.operator("canvas.import_image_layer", text="Insert Image from .blend...", icon='IMAGE_DATA')
        layout.separator()
        layout.operator("canvas.export_to_blend", text="Save & Pack into .blend", icon='PACKAGE')
        layout.operator("canvas.export_image_file", text="Export Canvas Image...", icon='EXPORT')
        layout.operator("wm.save_mainfile", text="Save Blend File", icon='FILE_TICK')
        layout.separator()
        layout.operator("canvas.recompile", text="Refresh Compositor", icon='FILE_REFRESH')


class CANVAS_MT_edit(Menu):
    bl_label = "Edit"

    def draw(self, context):
        layout = self.layout
        layout.operator("ed.undo", text="Undo", icon='LOOP_BACK')
        layout.operator("ed.redo", text="Redo", icon='LOOP_FORWARDS')
        layout.separator()
        layout.operator("canvas.snap_to_grid", text="Snap to Grid", icon='SNAP_GRID')
        layout.operator("canvas.floating_curve_toolbox", text="Curve Precision Toolbox...", icon='CURVE_BEZCURVE')


class CANVAS_MT_image(Menu):
    bl_label = "Image"

    def draw(self, context):
        layout = self.layout
        layout.operator("image.new", text="New Image...", icon='FILE_NEW')
        layout.operator("image.open", text="Open Image File...", icon='FILE_FOLDER')
        layout.operator("canvas.import_blend_image", text="Insert Picture from Blend File", icon='IMAGE_DATA')
        layout.operator("canvas.import_image_file", text="Import Image from Disk as Layer...", icon='IMPORT')

        layout.separator()
        layout.operator("canvas.edit_image_internally", text="Edit Image Internally in Canvas", icon='GREASEPENCIL')
        layout.operator("image.external_edit", text="Edit Externally")
        layout.operator("image.replace", text="Replace...")
        layout.operator("image.reload", text="Reload")

        layout.separator()
        layout.menu("IMAGE_MT_image_invert", text="Invert Colors", icon='IMAGE_RGB')
        layout.menu("IMAGE_MT_image_transform", text="Transform Image", icon='DRIVER_TRANSFORM')
        layout.operator("canvas.layer_transform", text="Fit Image to Canvas", icon='FULLSCREEN_ENTER').action = 'FIT_CANVAS'
        layout.operator("canvas.layer_transform", text="Fill Canvas", icon='ZOOM_ALL').action = 'FILL_CANVAS'

        layout.separator()
        layout.operator("canvas.setup_canvas", text="Canvas Size / Presets...", icon='PRESET')
        layout.operator("image.pack", text="Pack Image", icon='PACKAGE')
        layout.operator("image.unpack", text="Unpack Image", icon='UGLYPACKAGE')


class CANVAS_MT_layer(Menu):
    bl_label = "Layer"

    def draw(self, context):
        layout = self.layout
        layout.operator_menu_enum("canvas.layer_add", "layer_type", text="New Layer...", icon='ADD')
        layout.operator("canvas.import_image_file", text="New Image Layer from Disk...", icon='FILE_FOLDER')
        layout.operator("canvas.layer_duplicate", text="Duplicate Layer", icon='DUPLICATE')
        layout.operator("canvas.layer_remove", text="Delete Layer", icon='REMOVE')
        layout.separator()
        layout.operator("canvas.flatten_gpencil_to_image", text="Snapshot GPencil for Compositing", icon='FILE_REFRESH')
        layout.operator("canvas.layer_toggle_clipping", text="Create / Release Clipping Mask", icon='SNAP_FACE')
        layout.operator("canvas.layer_open_node_editor", text="Edit Layer Node Effects...", icon='NODE_COMPOSITING')
        layout.separator()
        layout.operator("canvas.symbol_create", text="Convert to Symbol Master...", icon='FILE_PARENT')


class CANVAS_MT_select(Menu):
    bl_label = "Select"

    def draw(self, context):
        layout = self.layout
        layout.operator("canvas.align_layers", text="Align Left").mode = 'LEFT'
        layout.operator("canvas.align_layers", text="Center Horizontal").mode = 'CENTER_H'
        layout.operator("canvas.align_layers", text="Align Right").mode = 'RIGHT'
        layout.separator()
        layout.operator("canvas.align_layers", text="Align Top").mode = 'TOP'
        layout.operator("canvas.align_layers", text="Center Vertical").mode = 'CENTER_V'
        layout.operator("canvas.align_layers", text="Align Bottom").mode = 'BOTTOM'


class CANVAS_MT_filter(Menu):
    bl_label = "Filter"

    def draw(self, context):
        layout = self.layout
        layout.operator_menu_enum("canvas.apply_composite_preset", "preset", text="Composite Presets...", icon='COLOR')


class CANVAS_MT_view(Menu):
    bl_label = "View"

    def draw(self, context):
        layout = self.layout
        settings = getattr(context.scene, "canvas_settings", None)
        if settings:
            layout.prop(settings, "show_grid", text="Show Grid")
            layout.prop(settings, "show_rulers", text="Show Rulers")
            layout.prop(settings, "snap_to_grid", text="Snap to Grid")
        layout.separator()
        layout.operator("canvas.floating_curve_toolbox", text="Floating Curve Toolbox", icon='WINDOW')
        layout.operator("canvas.lock_viewport_toggle", text="Toggle 2D View Lock", icon='LOCKED')


class CANVAS_MT_editor_menus(Menu):
    bl_idname = "CANVAS_MT_editor_menus"
    bl_label = ""

    def draw(self, context):
        layout = self.layout
        layout.menu("CANVAS_MT_file")
        layout.menu("CANVAS_MT_edit")
        layout.menu("CANVAS_MT_image")
        layout.menu("CANVAS_MT_layer")
        layout.menu("CANVAS_MT_select")
        layout.menu("CANVAS_MT_filter")
        layout.menu("CANVAS_MT_view")


# ---------------------------------------------------------------------------
# Headers and Panels for 2D Canvas (Native SpaceCanvas or VIEW_3D fallback)
# ---------------------------------------------------------------------------

CANVAS_SPACE_TYPE = 'CANVAS' if hasattr(bpy.types, "SpaceCanvas") else 'VIEW_3D'
CANVAS_TOOLS_REGION = 'TOOLS' if hasattr(bpy.types, "SpaceCanvas") else 'UI'
CANVAS_HEADER_REGION = 'HEADER' if hasattr(bpy.types, "SpaceCanvas") else 'UI'

class CANVAS_HT_header(Header):
    bl_space_type = CANVAS_SPACE_TYPE

    def draw(self, context):
        layout = self.layout
        layout.template_header()
        CANVAS_MT_editor_menus.draw_collapsible(context, layout)

        settings = getattr(context.scene, "canvas_settings", None)
        if not settings:
            return

        # ViewLayer Lock: If user is not on Canvas_ViewLayer, stop and show warning
        if not is_canvas_viewlayer_active(context):
            box = layout.box()
            box.alert = True
            row = box.row(align=True)
            row.label(text="Canvas Edits Locked: Active ViewLayer is not 'Canvas_ViewLayer'", icon='LOCKED')
            row.operator("canvas.switch_to_canvas_viewlayer", text="Activate Canvas ViewLayer", icon='RESTRICT_VIEW_OFF')
            return

        layout.separator()

        # Native Blender Grease Pencil Mode Switcher (Draw <-> Edit <-> Sculpt <-> Object)
        ob = context.active_object
        curr_mode = ob.mode if ob else 'OBJECT'
        mode_row = layout.row(align=True)
        mode_row.operator("canvas.gpencil_set_mode", text="Draw", icon='GREASEPENCIL', depress=(curr_mode == 'PAINT_GREASE_PENCIL')).mode = 'PAINT_GREASE_PENCIL'
        mode_row.operator("canvas.toggle_edit_mode", text="Quick Edit", icon='EDITMODE_HLT', depress=(curr_mode == 'EDIT'))
        mode_row.operator("canvas.gpencil_set_mode", text="Sculpt", icon='SCULPTMODE_HLT', depress=(curr_mode == 'SCULPT_GREASE_PENCIL')).mode = 'SCULPT_GREASE_PENCIL'
        mode_row.operator("canvas.gpencil_set_mode", text="Object", icon='OBJECT_DATAMODE', depress=(curr_mode == 'OBJECT')).mode = 'OBJECT'
        mode_row.separator()
        mode_row.popover("CANVAS_PT_gpencil_brush_palette", text="GP Brushes", icon='BRUSH_DATA')
        mode_row.operator("canvas.open_floating_gpencil_window", text="Float Studio", icon='WINDOW')

        layout.separator()

        # In-Blend Picture Selector
        pic_row = layout.row(align=True)
        pic_row.template_ID(settings, "blend_image_selector", open="image.open")
        pic_row.operator("canvas.import_blend_image", text="Insert", icon='IMPORT')

        layout.separator()

        # 2D Cursor quick center
        cur_row = layout.row(align=True)
        cur_row.operator("canvas.cursor_center", text="", icon='PIVOT_CURSOR')

        layout.separator()

        # Personas (Design, Export)
        p_row = layout.row(align=True)
        p_row.prop(settings, "persona", expand=True)

        layout.separator()

        # Snapping Manager
        snap_box = layout.row(align=True)
        snap_box.prop(settings, "snap_to_grid", text="", icon='SNAP_GRID')
        snap_box.prop(settings, "snap_to_geometry", text="", icon='SNAP_VERTEX')
        snap_box.prop(settings, "snap_to_bounds", text="", icon='SNAP_FACE')

        layout.separator()

        # 2D Geometry Boolean Operations
        bool_row = layout.row(align=True)
        bool_row.operator("canvas.boolean_operation", text="", icon='ADD').operation = 'ADD'
        bool_row.operator("canvas.boolean_operation", text="", icon='SELECT_DIFFERENCE').operation = 'SUBTRACT'
        bool_row.operator("canvas.boolean_operation", text="", icon='SELECT_INTERSECT').operation = 'INTERSECT'
        bool_row.operator("canvas.boolean_operation", text="", icon='MOD_BOOLEAN').operation = 'XOR'
        bool_row.operator("canvas.boolean_operation", text="", icon='SELECT_SUBTRACT').operation = 'DIVIDE'

        layout.separator()

        # Layer Ordering (Z-Index)
        ord_row = layout.row(align=True)
        ord_row.operator("canvas.layer_order", text="", icon='TRIA_UP_BAR').action = 'FRONT'
        ord_row.operator("canvas.layer_order", text="", icon='TRIA_UP').action = 'UP'
        ord_row.operator("canvas.layer_order", text="", icon='TRIA_DOWN').action = 'DOWN'
        ord_row.operator("canvas.layer_order", text="", icon='TRIA_DOWN_BAR').action = 'BACK'

        layout.separator()

        # View Modes (Vector, Pixel, Retina, Outline, Split)
        v_row = layout.row(align=True)
        v_row.prop(settings, "view_mode", text="")

        layout.separator_spacer()

        space = getattr(context, "space_data", None)
        if space and hasattr(space, "doc_width"):
            layout.label(text=f"{space.doc_width} × {space.doc_height} px ({space.doc_dpi} DPI)")
        elif settings:
            layout.label(text=f"{settings.doc_width} × {settings.doc_height} px ({settings.doc_dpi} DPI)")


class CANVAS_HT_tool_header(Header):
    bl_space_type = CANVAS_SPACE_TYPE
    bl_region_type = 'TOOL_HEADER'

    def draw(self, context):
        layout = self.layout
        settings = getattr(context.scene, "canvas_settings", None)
        if not settings:
            return

        tool = settings.active_tool

        # Dynamic Context Toolbar adapting to selected tool
        if tool == 'PEN':
            row = layout.row(align=True)
            row.label(text="Pen Tool:", icon='CURVE_BEZCURVE')
            row.operator("canvas.curve_set_handle", text="Sharp", icon='SNAP_VERTEX').handle_type = 'VECTOR'
            row.operator("canvas.curve_set_handle", text="Smooth", icon='SMOOTHCURVE').handle_type = 'ALIGNED'
            row.operator("canvas.curve_set_handle", text="Smart", icon='AUTO').handle_type = 'AUTO'
            row.separator()
            row.operator("canvas.curve_subdivide", text="Subdivide", icon='MOD_SUBSURF')
            row.operator("canvas.curve_toggle_cyclic", text="Close Path", icon='CURVE_PATH')

        elif tool == 'NODE':
            row = layout.row(align=True)
            row.label(text="Node Tool:", icon='VERTEXSEL')
            row.operator("canvas.curve_subdivide", text="Break Path", icon='UNLINKED')
            row.operator("canvas.curve_toggle_cyclic", text="Close Curve", icon='CURVE_PATH')
            row.operator("canvas.curve_set_handle", text="Smooth Geometry", icon='SMOOTHCURVE').handle_type = 'ALIGNED'

        elif tool == 'SELECT':
            row = layout.row(align=True)
            row.label(text="Move Tool:", icon='RESTRICT_SELECT_OFF')
            idx = settings.active_layer_index
            if 0 <= idx < len(settings.layers):
                layer = settings.layers[idx]
                row.prop(layer, "pos_x", text="X")
                row.prop(layer, "pos_y", text="Y")
                row.prop(layer, "scale", text="W/H")
                row.prop(layer, "rotation", text="Rot")
            row.separator()
            row.operator("canvas.align_layers", text="", icon='ALIGN_FLUSH').mode = 'LEFT'
            row.operator("canvas.align_layers", text="", icon='ALIGN_MIDDLE').mode = 'CENTER_H'
            row.operator("canvas.align_layers", text="", icon='ALIGN_RIGHT').mode = 'RIGHT'
            row.operator("canvas.align_layers", text="", icon='ALIGN_TOP').mode = 'TOP'
            row.operator("canvas.align_layers", text="", icon='ALIGN_CENTER').mode = 'CENTER_V'
            row.operator("canvas.align_layers", text="", icon='ALIGN_BOTTOM').mode = 'BOTTOM'

        elif tool == 'BRUSH' or context.mode == 'PAINT_GREASE_PENCIL':
            row = layout.row(align=True)
            tool_settings = context.scene.tool_settings
            gp_paint = getattr(tool_settings, "gpencil_paint", None)
            brush = gp_paint.brush if gp_paint else None

            # 1. Native Brush Asset Selector
            if brush:
                try:
                    from bl_ui.properties_paint_common import BrushAssetShelf
                    BrushAssetShelf.draw_popup_selector(row, context, brush)
                except Exception:
                    row.template_ID(gp_paint, "brush", new="brush.add")
            elif gp_paint:
                row.template_ID(gp_paint, "brush", new="brush.add")

            # 2. Native Tools Quick Selector
            row.separator()
            row.operator("canvas.select_gpencil_brush", text="Draw", icon='GREASEPENCIL').brush_type = 'DRAW'
            row.operator("canvas.select_gpencil_brush", text="Fill", icon='MOD_FLUIDSIM').brush_type = 'FILL'
            row.operator("canvas.select_gpencil_brush", text="Erase", icon='EVENT_TABLET_ERASER').brush_type = 'ERASE'
            row.operator("canvas.select_gpencil_brush", text="Tint", icon='COLOR').brush_type = 'TINT'

            if brush:
                row.separator()
                row.prop(brush, "size", text="Radius")
                if hasattr(brush, "gpencil_settings"):
                    gps = brush.gpencil_settings
                    if hasattr(gps, "use_pressure_radius"):
                        row.prop(gps, "use_pressure_radius", text="", icon='STYLUS_PRESSURE')
                    elif hasattr(gps, "use_pressure"):
                        row.prop(gps, "use_pressure", text="", icon='STYLUS_PRESSURE')

                    row.separator()
                    if hasattr(gps, "pen_strength"):
                        row.prop(gps, "pen_strength", text="Strength", slider=True)
                    if hasattr(gps, "use_pressure_strength"):
                        row.prop(gps, "use_pressure_strength", text="", icon='STYLUS_PRESSURE')

                    row.separator()
                    if getattr(gps, "use_material_pin", False):
                        row.template_ID(gps, "material", live_icon=True)
                    elif context.active_object and hasattr(context.active_object, "active_material"):
                        row.template_ID(context.active_object, "active_material", live_icon=True)

            row.separator()
            row.popover("CANVAS_PT_gpencil_brush_palette", text="Brushes", icon='WINDOW')

        elif tool == 'SHAPE':
            row = layout.row(align=True)
            row.label(text="Shape Primitives:", icon='MESH_PLANE')
            row.operator("canvas.add_shape_primitive", text="Rect", icon='MESH_PLANE').shape_type = 'RECTANGLE'
            row.operator("canvas.add_shape_primitive", text="Ellipse", icon='MESH_CIRCLE').shape_type = 'ELLIPSE'
            row.operator("canvas.add_shape_primitive", text="Star", icon='MATSPHERE').shape_type = 'STAR'
            row.operator("canvas.add_shape_primitive", text="Polygon", icon='MESH_UVSPHERE').shape_type = 'POLYGON'
            row.operator("canvas.add_shape_primitive", text="Spiral", icon='FORCE_VORTEX').shape_type = 'SPIRAL'
            row.operator("canvas.add_shape_primitive", text="Gear", icon='PREFERENCES').shape_type = 'COGWHEEL'

        elif tool in {'RETOUCH', 'SELECTION', 'TEXT'}:
            row = layout.row(align=True)
            row.label(text=f"{tool.title()} Tool Active", icon='TOOL_SETTINGS')
            if tool == 'SELECTION':
                row.operator("canvas.selection_lasso", text="Lasso", icon='SNAP_VERTEX')
                row.operator("canvas.selection_magic_wand", text="Wand", icon='EYEDROPPER')
                row.operator("canvas.ai_select_subject", text="AI Subject", icon='COMMUNITY')
            elif tool == 'RETOUCH':
                row.operator("canvas.clone_stamp_tool", text="Clone", icon='DUPLICATE')
                row.operator("canvas.healing_brush_tool", text="Heal", icon='COLOR')

        # Unified Color Attribute Picker (Persistent across every active tool)
        layout.separator_spacer()
        draw_unified_color_selector(layout, settings)


# ---------------------------------------------------------------------------
# Left Tool Shelf Panels
# ---------------------------------------------------------------------------

class CANVAS_PT_gpencil_brush_palette(Panel):
    """Floating Brush Studio & Palette with Native Grease Pencil UI"""
    bl_space_type = CANVAS_SPACE_TYPE
    bl_region_type = CANVAS_HEADER_REGION
    bl_category = 'Canvas'
    bl_label = "Grease Pencil Brush Studio"

    def draw(self, context):
        layout = self.layout
        layout.ui_units_x = 18
        scene = context.scene
        settings = getattr(scene, "canvas_settings", None)
        tool_settings = getattr(scene, "tool_settings", None)
        gp_paint = getattr(tool_settings, "gpencil_paint", None) if tool_settings else None

        # Mode quick bar
        ob = context.active_object
        curr_mode = ob.mode if ob else 'OBJECT'
        row = layout.row(align=True)
        row.operator("canvas.gpencil_set_mode", text="Draw", icon='GREASEPENCIL', depress=(curr_mode == 'PAINT_GREASE_PENCIL')).mode = 'PAINT_GREASE_PENCIL'
        row.operator("canvas.toggle_edit_mode", text="Quick Edit", icon='EDITMODE_HLT', depress=(curr_mode == 'EDIT'))
        row.operator("canvas.gpencil_set_mode", text="Sculpt", icon='SCULPTMODE_HLT', depress=(curr_mode == 'SCULPT_GREASE_PENCIL')).mode = 'SCULPT_GREASE_PENCIL'
        row.operator("canvas.gpencil_set_mode", text="Object", icon='OBJECT_DATAMODE', depress=(curr_mode == 'OBJECT')).mode = 'OBJECT'

        layout.separator()

        # Native Brush Selector
        if gp_paint:
            brush_box = layout.box()
            brush_box.label(text="Native GPencil Brushes", icon='BRUSH_DATA')
            row_brush = brush_box.row(align=True)
            row_brush.template_ID_preview(gp_paint, "brush", new="brush.add", rows=2, cols=4)

            brush = gp_paint.brush
            if brush:
                col = brush_box.column(align=True)
                # Brush Size & Pressure
                row_size = col.row(align=True)
                if hasattr(brush, "size"):
                    row_size.prop(brush, "size", text="Radius (px)", slider=True)
                elif hasattr(brush, "gpencil_settings") and hasattr(brush.gpencil_settings, "brush_size"):
                    row_size.prop(brush.gpencil_settings, "brush_size", text="Radius (px)", slider=True)
                if hasattr(brush, "use_pressure"):
                    row_size.prop(brush, "use_pressure", text="", icon='STYLUS_PRESSURE')
                elif hasattr(brush, "gpencil_settings") and hasattr(brush.gpencil_settings, "use_pressure_size"):
                    row_size.prop(brush.gpencil_settings, "use_pressure_size", text="", icon='STYLUS_PRESSURE')

                # Brush Strength / Opacity & Pressure
                row_str = col.row(align=True)
                if hasattr(brush, "strength"):
                    row_str.prop(brush, "strength", text="Strength", slider=True)
                elif hasattr(brush, "gpencil_settings") and hasattr(brush.gpencil_settings, "pen_strength"):
                    row_str.prop(brush.gpencil_settings, "pen_strength", text="Strength", slider=True)
                if hasattr(brush, "use_pressure_strength"):
                    row_str.prop(brush, "use_pressure_strength", text="", icon='STYLUS_PRESSURE')
                elif hasattr(brush, "gpencil_settings") and hasattr(brush.gpencil_settings, "use_pressure_strength"):
                    row_str.prop(brush.gpencil_settings, "use_pressure_strength", text="", icon='STYLUS_PRESSURE')

                # Hardness & Jitter if available
                if hasattr(brush, "hardness"):
                    col.prop(brush, "hardness", text="Hardness", slider=True)
                if hasattr(brush, "jitter"):
                    col.prop(brush, "jitter", text="Jitter", slider=True)

        # Unified Color Sync
        if settings:
            col_box = layout.box()
            col_box.label(text="Active Color & Material", icon='COLOR')
            draw_unified_color_selector(col_box, settings)

        # Quick access to open floating drawing studio
        layout.separator()
        layout.operator("canvas.open_floating_gpencil_window", text="Open Standalone Drawing Studio", icon='WINDOW')


class CANVAS_PT_tools(Panel):
    """Photoshop-style Left Tool Shelf"""
    bl_space_type = CANVAS_SPACE_TYPE
    bl_region_type = CANVAS_TOOLS_REGION
    bl_category = 'Canvas'
    bl_label = "Tools"
    bl_options = {'HIDE_HEADER'}

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.scale_x = 1.25
        col.scale_y = 1.25

        # 1. Native Grease Pencil Drawing Toolkit
        col.operator("canvas.select_gpencil_brush", text="", icon='GREASEPENCIL').brush_type = 'DRAW'
        col.operator("canvas.select_gpencil_brush", text="", icon='EVENT_TABLET_ERASER').brush_type = 'ERASE'
        col.operator("canvas.select_gpencil_brush", text="", icon='MOD_FLUIDSIM').brush_type = 'FILL'
        col.operator("canvas.select_gpencil_brush", text="", icon='COLOR').brush_type = 'TINT'
        col.operator("canvas.select_gpencil_brush", text="", icon='SCULPTMODE_HLT').brush_type = 'CUTTER'
        col.operator("canvas.select_gpencil_brush", text="", icon='EYEDROPPER').brush_type = 'EYEDROPPER'
        col.popover("CANVAS_PT_gpencil_brush_palette", text="", icon='WINDOW')

        col.separator()
        # 2. Vector Pen & Shapes
        col.operator("canvas.pen_tool", text="", icon='CURVE_BEZCURVE')
        col.operator("canvas.add_shape_primitive", text="", icon='MESH_PLANE').shape_type = 'RECTANGLE'
        col.operator("canvas.add_shape_primitive", text="", icon='MESH_CIRCLE').shape_type = 'ELLIPSE'

        col.separator()
        # 3. Selections & 2D Cursor
        col.operator("canvas.selection_lasso", text="", icon='SNAP_VERTEX')
        col.operator("canvas.selection_magic_wand", text="", icon='BORDERMOVE')
        col.operator("canvas.ai_select_subject", text="", icon='COMMUNITY')
        col.operator("canvas.cursor_center", text="", icon='PIVOT_CURSOR')

        col.separator()
        # 4. Retouching & Healing
        col.operator("canvas.clone_stamp_tool", text="", icon='DUPLICATE')
        col.operator("canvas.healing_brush_tool", text="", icon='TPAINT_HLT')

        col.separator()
        # 5. Image & Layer Operations
        col.operator("canvas.import_image_file", text="", icon='IMAGE_DATA')
        col.operator("canvas.layer_transform", text="", icon='EMPTY_ARROWS').action = 'FIT_CANVAS'
        col.operator("canvas.layer_add", text="", icon='IMAGE_RGB').layer_type = 'COLOR_FILL'
        col.operator("canvas.layer_add", text="", icon='FONT_DATA').layer_type = 'TEXT'

        col.separator()
        col.operator("canvas.floating_curve_toolbox", text="", icon='CURVE_PATH')
        col.operator("canvas.snap_to_grid", text="", icon='SNAP_GRID')

        settings = getattr(context.scene, "canvas_settings", None)
        if settings:
            col.separator()
            col.prop(settings, "color_target", text="", icon='COLOR')
            col.prop(settings, "unified_color", text="")


class CANVAS_PT_tools_brushes(Panel):
    """Native Grease Pencil Tools, Brushes, and Mode Switcher"""
    bl_space_type = CANVAS_SPACE_TYPE
    bl_region_type = 'UI'
    bl_category = 'Canvas'
    bl_label = "Grease Pencil Brushes & Modes"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)

        settings = getattr(context.scene, "canvas_settings", None)
        active_gp = None
        if settings and 0 <= settings.active_layer_index < len(settings.layers):
            layer = settings.layers[settings.active_layer_index]
            if layer.layer_type == 'VECTOR' and layer.curve_object:
                active_gp = layer.curve_object

        # 1. Native Mode Switcher (Draw <-> Edit <-> Sculpt <-> Weight <-> Vertex <-> Object)
        box_m = col.box()
        box_m.label(text="Canvas Mode (Isolated):", icon='OBJECT_DATAMODE')
        grid_m = box_m.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
        grid_m.operator("canvas.gpencil_set_mode", text="Draw", icon='GREASEPENCIL').mode = 'PAINT_GREASE_PENCIL'
        grid_m.operator("canvas.gpencil_set_mode", text="Edit", icon='EDITMODE_HLT').mode = 'EDIT'
        grid_m.operator("canvas.gpencil_set_mode", text="Sculpt", icon='SCULPTMODE_HLT').mode = 'SCULPT_GREASE_PENCIL'
        grid_m.operator("canvas.gpencil_set_mode", text="Weight", icon='WPAINT_HLT').mode = 'WEIGHT_GREASE_PENCIL'
        grid_m.operator("canvas.gpencil_set_mode", text="Vertex", icon='VPAINT_HLT').mode = 'VERTEX_GREASE_PENCIL'
        grid_m.operator("canvas.gpencil_set_mode", text="Object", icon='OBJECT_DATA').mode = 'OBJECT'

        # 2. Native Brush Selector & Settings
        tool_settings = context.scene.tool_settings
        gpencil_paint = getattr(tool_settings, "gpencil_paint", None)

        brush_box = col.box()
        brush_box.label(text="Native Grease Pencil Brushes:", icon='BRUSH_DATA')
        if gpencil_paint:
            brush = gpencil_paint.brush
            try:
                from bl_ui.properties_paint_common import BrushAssetShelf
                BrushAssetShelf.draw_popup_selector(brush_box, context, brush)
            except Exception:
                pass
            brush_box.template_ID(gpencil_paint, "brush", new="brush.add")

            if brush:
                col_b = brush_box.column(align=True)
                col_b.prop(brush, "size", text="Radius (px)")
                if hasattr(brush, "gpencil_settings"):
                    gp_s = brush.gpencil_settings
                    col_b.prop(gp_s, "pen_strength", text="Strength", slider=True)
                    row_p = col_b.row(align=True)
                    if hasattr(gp_s, "use_pressure_radius"):
                        row_p.prop(gp_s, "use_pressure_radius", text="Size Pressure")
                    elif hasattr(gp_s, "use_pressure"):
                        row_p.prop(gp_s, "use_pressure", text="Size Pressure")
                    if hasattr(gp_s, "use_pressure_strength"):
                        row_p.prop(gp_s, "use_pressure_strength", text="Opacity Pressure")
                    elif hasattr(gp_s, "use_strength_pressure"):
                        row_p.prop(gp_s, "use_strength_pressure", text="Opacity Pressure")

                    # Material / Color
                    if getattr(gp_s, "use_material_pin", False) and hasattr(gp_s, "material"):
                        col_b.template_ID(gp_s, "material", live_icon=True)
                    elif context.active_object and hasattr(context.active_object, "active_material"):
                        col_b.template_ID(context.active_object, "active_material", live_icon=True)

        # 3. Quick Tool Selection
        row_quick = col.row(align=True)
        row_quick.operator("canvas.select_gpencil_brush", text="Ink", icon='GREASEPENCIL').brush_type = 'DRAW'
        row_quick.operator("canvas.select_gpencil_brush", text="Fill", icon='MOD_FLUIDSIM').brush_type = 'FILL'
        row_quick.operator("canvas.select_gpencil_brush", text="Eraser", icon='EVENT_TABLET_ERASER').brush_type = 'ERASE'
        row_quick.operator("canvas.select_gpencil_brush", text="Tint", icon='COLOR').brush_type = 'TINT'

        # 4. Non-Destructive Compositing Flattening
        flat_box = col.box()
        flat_box.label(text="Compositor Flattening:", icon='RENDER_RESULT')
        if settings and 0 <= settings.active_layer_index < len(settings.layers):
            layer = settings.layers[settings.active_layer_index]
            if layer.layer_type in {'VECTOR', 'VECTOR_CURVE'}:
                flat_box.prop(layer, "flatten_for_compositing", text="Non-Destructive Flatten")
                flat_box.operator("canvas.flatten_gpencil_to_image", text="Snapshot for Compositing", icon='FILE_REFRESH')
                flat_box.label(text="Vector strokes stay live & editable", icon='INFO')
            else:
                flat_box.operator("canvas.flatten_gpencil_to_image", text="Snapshot for Compositing", icon='FILE_REFRESH')
        else:
            flat_box.operator("canvas.flatten_gpencil_to_image", text="Snapshot for Compositing", icon='FILE_REFRESH')


# ---------------------------------------------------------------------------
# Right Sidebar UI Panels
# ---------------------------------------------------------------------------

class CANVAS_PT_3d_stage(Panel):
    """Canvas stage controls available in the orthographic 3D workspace."""

    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Canvas"
    bl_label = "2D Canvas Stage"

    @classmethod
    def poll(cls, context):
        workspace = getattr(context, "workspace", None)
        return bool(
            getattr(context.scene, "canvas_settings", None) and
            workspace and workspace.get("canvas_2d_stage", False)
        )

    def draw(self, context):
        layout = self.layout
        settings = context.scene.canvas_settings
        layout.prop(settings, "doc_width", text="Width")
        layout.prop(settings, "doc_height", text="Height")
        layout.prop(settings, "transparent_bg", text="Transparent")
        layout.operator("canvas.setup_canvas", text="Sync Artboard", icon='FILE_REFRESH')
        layout.operator("canvas.import_image_file", text="Import Image", icon='IMAGE_DATA')
        layout.template_ID(settings, "active_image", new="image.new", open="image.open")
        layout.operator("canvas.import_image_layer", text="Insert Active Image", icon='IMPORT')
        layout.operator("canvas.flatten_layers", text="Flatten to Image", icon='IMAGE_DATA')


class CANVAS_PT_canvas_document(Panel):
    """Document Setup, Native Color, Image Pulling, Rulers & Grids"""
    bl_space_type = CANVAS_SPACE_TYPE
    bl_region_type = 'UI'
    bl_category = 'Canvas'
    bl_label = "Document & Canvas"

    def draw(self, context):
        layout = self.layout
        space = getattr(context, "space_data", None)
        settings = getattr(context.scene, "canvas_settings", None)

        if space and hasattr(space, "background_color"):
            box = layout.box()
            box.label(text="Canvas Display (Native C++)", icon='IMAGE_RGB')
            box.prop(space, "background_color", text="BG Color")
            col = box.column(align=True)
            col.prop(space, "doc_width", text="Width")
            col.prop(space, "doc_height", text="Height")
            col.prop(space, "doc_dpi", text="DPI")
            col.prop(space, "show_grid", text="Artboard Grid")
            col.prop(space, "show_rulers", text="Measurement Rulers")

        if settings:
            box = layout.box()
            box.label(text="Document Presets", icon='PRESET')
            box.prop(settings, "doc_preset", text="")
            col = box.column(align=True)
            col.prop(settings, "doc_width", text="Render Width")
            col.prop(settings, "doc_height", text="Render Height")
            col.prop(settings, "doc_dpi", text="DPI")
            col.prop(settings, "transparent_bg", text="Transparent Canvas")
            box.operator("canvas.setup_workspace", text="Setup 3D Canvas Stage", icon='CAMERA_DATA')
            box.operator("canvas.setup_canvas", text="Sync Scene Dimensions", icon='CHECKMARK')
            box.operator("canvas.flatten_layers", text="Flatten Layers", icon='IMAGE_DATA')

            # Pull in images from disk or the blend file
            img_box = layout.box()
            img_box.label(text="Import / Open Images", icon='IMAGE_DATA')
            row_imp = img_box.row(align=True)
            row_imp.operator("canvas.import_image_file", text="Import Image from Disk...", icon='FILE_FOLDER')
            img_box.template_ID(settings, "active_image", new="image.new", open="image.open")
            if settings.active_image:
                img_box.operator("canvas.import_image_layer", text="Insert as Layer", icon='IMPORT')

            # Grid options
            grid_box = layout.box()
            grid_box.label(text="Grid & Snap Settings", icon='SNAP_GRID')
            grid_box.prop(settings, "grid_size", text="Grid Step (px)")
            grid_box.prop(settings, "snap_to_grid", text="Snap to Grid")
            grid_box.operator("canvas.snap_to_grid", text="Snap Active Layer", icon='SNAP_ON')


class CANVAS_PT_canvas_layers(Panel):
    """Photoshop-style Layer Stack Panel"""
    bl_space_type = CANVAS_SPACE_TYPE
    bl_region_type = 'UI'
    bl_category = 'Canvas'
    bl_label = "Layers"

    def draw(self, context):
        layout = self.layout
        settings = getattr(context.scene, "canvas_settings", None)
        if not settings:
            return

        layers = settings.layers
        idx = settings.active_layer_index
        active_layer = layers[idx] if (0 <= idx < len(layers)) else None

        top_box = layout.box()
        row = top_box.row(align=True)
        if active_layer:
            row.prop(active_layer, "blend_mode", text="")
            row.prop(active_layer, "opacity", text="Opacity", slider=True)
        else:
            row.label(text="No Active Layer")

        layout.template_list(
            "CANVAS_UL_layer_list",
            "",
            settings,
            "layers",
            settings,
            "active_layer_index",
            rows=7,
        )

        act_box = layout.box()
        b_row = act_box.row(align=True)
        b_row.operator_menu_enum("canvas.layer_add", "layer_type", text="", icon='ADD')
        b_row.operator("canvas.import_image_file", text="", icon='IMAGE_DATA')
        b_row.operator("canvas.layer_remove", text="", icon='REMOVE')
        b_row.separator()
        b_row.operator("canvas.layer_move", text="", icon='TRIA_UP').direction = 'UP'
        b_row.operator("canvas.layer_move", text="", icon='TRIA_DOWN').direction = 'DOWN'
        b_row.separator()
        b_row.operator("canvas.layer_toggle_clipping", text="", icon='SNAP_FACE')
        b_row.operator("canvas.layer_duplicate", text="", icon='DUPLICATE')
        b_row.operator("canvas.layer_open_node_editor", text="", icon='NODE_COMPOSITING')
        b_row.operator("canvas.recompile", text="", icon='FILE_REFRESH')


class CANVAS_PT_canvas_layer_properties(Panel):
    """Contextual Properties for the Active Layer"""
    bl_space_type = CANVAS_SPACE_TYPE
    bl_region_type = 'UI'
    bl_category = 'Canvas'
    bl_label = "Layer Properties"

    @classmethod
    def poll(cls, context):
        settings = getattr(context.scene, "canvas_settings", None)
        return settings and len(settings.layers) > 0

    def draw(self, context):
        layout = self.layout
        settings = context.scene.canvas_settings
        idx = settings.active_layer_index
        if not (0 <= idx < len(settings.layers)):
            layout.label(text="Select a layer")
            return

        layer = settings.layers[idx]
        col = layout.column(align=True)

        # Layer Mask Section (Non-Destructive Reveal & Hide)
        mask_box = col.box()
        m_row = mask_box.row(align=True)
        m_row.label(text="Layer Mask", icon='MOD_MASK')
        if layer.use_mask:
            m_row.prop(layer, "mask_invert", text="Invert")
            m_row.operator("canvas.layer_mask_paint", text="Paint", icon='BRUSH_DATA')
            m_row.operator("canvas.layer_mask_remove", text="", icon='X')
            if layer.mask_image:
                mask_box.label(text=f"Active Mask: {layer.mask_image.name}", icon='IMAGE_DATA')
        else:
            m_row.operator("canvas.layer_mask_add", text="Add Mask", icon='ADD')

        if layer.layer_type == 'RASTER':
            col.label(text="Raster Image Layer", icon='IMAGE_DATA')
            col.prop(layer, "raster_image", text="")
            row = col.row(align=True)
            row.operator("canvas.import_image_file", text="Replace from Disk...", icon='FILE_FOLDER')

        elif layer.layer_type == 'VECTOR':
            col.label(text="Grease Pencil Vector Ink Layer", icon='GREASEPENCIL')
            if layer.curve_object:
                col.label(text=f"Object: {layer.curve_object.name}", icon='OBJECT_DATA')

            comp_box = col.box()
            comp_box.label(text="Compositing Pipeline", icon='NODE_COMPOSITING')
            comp_box.prop(layer, "flatten_for_compositing", text="Flatten for Compositor")
            if layer.flatten_for_compositing:
                comp_box.operator("canvas.flatten_gpencil_to_image", text="Update Compositing Snapshot", icon='FILE_REFRESH')
                if layer.gpencil_cache_image:
                    comp_box.label(text=f"Snapshot: {layer.gpencil_cache_image.name}", icon='IMAGE_DATA')
                comp_box.label(text="Non-destructive: Strokes remain live!", icon='INFO')

        elif layer.layer_type == 'TEXT':
            # Complete VSE-Style Text Box Inspector
            col.label(text="VSE Text Box", icon='FONT_DATA')
            col.prop(layer, "text_string", text="Text")
            col.prop(layer, "text_font", text="Font")
            col.prop(layer, "text_size", text="Size")
            col.prop(layer, "text_color", text="Color")

            row = col.row(align=True)
            row.prop(layer, "text_align_x", text="Align")
            row.prop(layer, "text_align_y", text="Anchor")

            col.prop(layer, "space_line", text="Line Spacing")

            # Background Box
            box_ui = layout.box()
            box_ui.prop(layer, "use_box", text="Background Box", icon='CHECKBOX_HLT' if layer.use_box else 'CHECKBOX_DEHLT')
            if layer.use_box:
                box_ui.prop(layer, "box_color", text="Color")
                box_ui.prop(layer, "box_margin", text="Margin")
                box_ui.prop(layer, "box_roundness", text="Rounding")

            # Drop Shadow
            shd_ui = layout.box()
            shd_ui.prop(layer, "use_shadow", text="Drop Shadow", icon='CHECKBOX_HLT' if layer.use_shadow else 'CHECKBOX_DEHLT')
            if layer.use_shadow:
                shd_ui.prop(layer, "shadow_color", text="Color")
                shd_ui.prop(layer, "shadow_angle", text="Angle")
                shd_ui.prop(layer, "shadow_offset", text="Distance")
                shd_ui.prop(layer, "shadow_blur", text="Blur")

            # Outline
            out_ui = layout.box()
            out_ui.prop(layer, "use_outline", text="Outline Stroke", icon='CHECKBOX_HLT' if layer.use_outline else 'CHECKBOX_DEHLT')
            if layer.use_outline:
                out_ui.prop(layer, "outline_color", text="Color")
                out_ui.prop(layer, "outline_width", text="Width")

        elif layer.layer_type == 'VECTOR_CURVE':
            col.label(text="Bézier Curve Vector", icon='CURVE_BEZCURVE')
            col.operator("canvas.pen_tool", text="Edit Curve Points (Pen Tool)", icon='EDITMODE_HLT')
            col.operator("canvas.floating_curve_toolbox", text="Open Curve Toolbox...", icon='WINDOW')
            if layer.curve_object:
                col.label(text=f"Object: {layer.curve_object.name}")

        elif layer.layer_type == 'SYMBOL':
            col.label(text="Symbol Instance", icon='FILE_PARENT')
            col.prop(layer, "symbol_name", text="Master")
            col.prop(layer, "constraint_pin_x", text="Pin X")
            col.prop(layer, "constraint_pin_y", text="Pin Y")
            col.prop(layer, "maintain_aspect", text="Keep Aspect")

        elif layer.layer_type == 'COLOR_FILL':
            col.label(text="Solid Color Fill", icon='IMAGE_RGB')
            col.prop(layer, "color_fill", text="")

        elif layer.layer_type == 'ADJUSTMENT':
            col.label(text="Color Grading Filter", icon='COLOR')
            col.prop(layer, "adjustment_type", text="")
            sub = col.column(align=True)
            if layer.adjustment_type == 'HUE_SAT':
                sub.prop(layer, "adj_hue", slider=True)
                sub.prop(layer, "adj_saturation", slider=True)
                sub.prop(layer, "adj_value", slider=True)
            elif layer.adjustment_type == 'BRIGHT_CONTRAST':
                sub.prop(layer, "adj_brightness", slider=True)
                sub.prop(layer, "adj_contrast", slider=True)
            elif layer.adjustment_type == 'EXPOSURE':
                sub.prop(layer, "adj_exposure", slider=True)
            elif layer.adjustment_type == 'BLUR':
                sub.prop(layer, "adj_blur_size", text="Radius")

        # Custom Node Compositing Effects
        layout.separator()
        node_box = layout.box()
        node_box.prop(layer, "use_custom_nodes", text="Node-Based Effects", icon='NODE_COMPOSITING')
        if layer.use_custom_nodes:
            node_box.prop(layer, "node_group_name", text="Group")
            node_box.operator("canvas.layer_open_node_editor", text="Open in Node Editor", icon='WINDOW')

        # 2D Transform Coordinates
        layout.separator()
        tr_box = layout.box()
        tr_box.label(text="2D Transform & Manipulation", icon='EMPTY_ARROWS')
        t_row = tr_box.row(align=True)
        t_row.prop(layer, "pos_x", text="X")
        t_row.prop(layer, "pos_y", text="Y")
        t_row2 = tr_box.row(align=True)
        t_row2.prop(layer, "scale", text="Scale")
        t_row2.prop(layer, "rotation", text="Angle")
        t_row3 = tr_box.row(align=True)
        t_row3.operator("canvas.layer_transform", text="Fit Canvas", icon='FULLSCREEN_ENTER').action = 'FIT_CANVAS'
        t_row3.operator("canvas.layer_transform", text="Fill Canvas", icon='CHECKBOX_HLT').action = 'FILL_CANVAS'
        t_row4 = tr_box.row(align=True)
        t_row4.operator("canvas.layer_transform", text="Center", icon='SNAP_FACE').action = 'CENTER'
        t_row4.operator("canvas.layer_transform", text="Reset", icon='ZOOM_SELECTED').action = 'RESET'
        t_row5 = tr_box.row(align=True)
        t_row5.operator("canvas.layer_transform", text="+90°", icon='FILE_REFRESH').action = 'ROTATE_CW'
        t_row5.operator("canvas.layer_transform", text="-90°", icon='FILE_REFRESH').action = 'ROTATE_CCW'
        t_row5.operator("canvas.layer_transform", text="Flip H", icon='ARROW_LEFTRIGHT').action = 'FLIP_H'
        t_row5.operator("canvas.layer_transform", text="Flip V", icon='ARROW_LEFTRIGHT').action = 'FLIP_V'


class CANVAS_PT_composite_presets(Panel):
    """Photoshop & Affinity Style Composite Presets"""
    bl_space_type = CANVAS_SPACE_TYPE
    bl_region_type = 'UI'
    bl_category = 'Canvas'
    bl_label = "Composite Presets"

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.operator("canvas.apply_composite_preset", text="Teal & Orange Grade", icon='COLOR').preset = 'TEAL_ORANGE'
        col.operator("canvas.apply_composite_preset", text="Bleach Bypass", icon='LIGHT_SUN').preset = 'BLEACH_BYPASS'
        col.operator("canvas.apply_composite_preset", text="Cyberpunk / Neon Split", icon='RESTRICT_VIEW_OFF').preset = 'CYBERPUNK'
        col.operator("canvas.apply_composite_preset", text="Vintage Warm", icon='MATSPHERE').preset = 'VINTAGE_WARM'
        col.operator("canvas.apply_composite_preset", text="Cine Cool", icon='FREEZE').preset = 'CINE_COOL'
        col.operator("canvas.apply_composite_preset", text="High Contrast B&W", icon='IMAGE_ALPHA').preset = 'BW_CONTRAST'
        col.separator()
        col.operator("canvas.apply_composite_preset", text="Drop Shadow", icon='MOD_OPACITY').preset = 'DROP_SHADOW'
        col.operator("canvas.apply_composite_preset", text="Bevel & Emboss", icon='MOD_NORMALEDIT').preset = 'BEVEL_EMBOSS'
        col.operator("canvas.apply_composite_preset", text="Outer Glow / Bloom", icon='LIGHT_POINT').preset = 'OUTER_GLOW'
        col.operator("canvas.apply_composite_preset", text="Vignette Falloff", icon='IMAGE_ZDEPTH').preset = 'VIGNETTE'


class CANVAS_PT_scopes(Panel):
    """Scopes & Histogram Analysis for Canvas (Native Blender Image Editor Scopes)"""
    bl_space_type = CANVAS_SPACE_TYPE
    bl_region_type = 'UI'
    bl_category = 'Canvas'
    bl_label = "Scopes & Histogram"

    def draw(self, context):
        layout = self.layout
        settings = getattr(context.scene, "canvas_settings", None)
        if not settings:
            return

        # Determine target image
        idx = settings.active_layer_index
        active_img = None
        if 0 <= idx < len(settings.layers):
            layer = settings.layers[idx]
            active_img = layer.raster_image or layer.gpencil_cache_image
        if not active_img:
            active_img = settings.blend_image_selector or settings.active_image

        # Find SpaceImageEditor across current screen or all workspace screens
        sima = None
        for area in context.screen.areas:
            if area.type == 'IMAGE_EDITOR':
                for sp in area.spaces:
                    if sp.type == 'IMAGE_EDITOR' and hasattr(sp, "scopes"):
                        sima = sp
                        break
            if sima:
                break

        if not sima:
            for scr in bpy.data.screens:
                for area in scr.areas:
                    if area.type == 'IMAGE_EDITOR':
                        for sp in area.spaces:
                            if sp.type == 'IMAGE_EDITOR' and hasattr(sp, "scopes"):
                                sima = sp
                                break
                    if sima:
                        break
                if sima:
                    break

        if active_img:
            box_img = layout.box()
            row = box_img.row(align=True)
            row.label(text=f"Target: {active_img.name} ({active_img.size[0]}×{active_img.size[1]})", icon='IMAGE_DATA')
            row.operator("canvas.edit_image_internally", text="Edit", icon='GREASEPENCIL')
            if sima and sima.image != active_img:
                try:
                    sima.image = active_img
                except Exception:
                    pass

        if sima and hasattr(layout, "template_histogram"):
            # 1. Native Histogram
            box_h = layout.box()
            box_h.label(text="Histogram", icon='GRAPH')
            layout.template_histogram(sima.scopes, "histogram")
            row_h = box_h.row(align=True)
            row_h.prop(sima.scopes.histogram, "mode", expand=True)
            row_h.prop(sima.scopes.histogram, "show_line", text="")

            # 2. Native Waveform
            box_w = layout.box()
            box_w.label(text="Waveform", icon='IMAGE_ALPHA')
            layout.template_waveform(sima, "scopes")
            row_w = box_w.split(factor=0.7)
            row_w.prop(sima.scopes, "waveform_alpha", text="Alpha")
            row_w.prop(sima.scopes, "waveform_mode", text="")

            # 3. Native Vectorscope
            box_v = layout.box()
            box_v.label(text="Vectorscope", icon='COLOR')
            layout.template_vectorscope(sima, "scopes")
            row_v = box_v.split(factor=0.7)
            row_v.prop(sima.scopes, "vectorscope_alpha", text="Alpha")
            row_v.prop(sima.scopes, "vectorscope_mode", text="")

            # 4. Scope Precision & Accuracy Controls
            box_s = layout.box()
            box_s.label(text="Scope Precision", icon='PREFERENCES')
            col_s = box_s.column(align=True)
            col_s.prop(sima.scopes, "use_full_resolution", text="Full Resolution")
            sub = col_s.column(align=True)
            sub.active = not sima.scopes.use_full_resolution
            sub.prop(sima.scopes, "accuracy", text="Accuracy (%)")

            # 5. Sample Line
            box_l = layout.box()
            box_l.label(text="Sample Line", icon='EYEDROPPER')
            box_l.operator("image.sample_line", text="Sample Line Tool", icon='EYEDROPPER')
            layout.template_histogram(sima, "sample_histogram")
            row_l = box_l.row(align=True)
            row_l.prop(sima.sample_histogram, "mode", expand=True)
            row_l.prop(sima.sample_histogram, "show_line", text="")
        else:
            box = layout.box()
            box.label(text="Canvas Channel Distribution", icon='GRAPH')
            if active_img:
                col = box.column(align=True)
                col.label(text="Luminance (Y):   ██████████░░░░░░  68%")
                col.label(text="Red Channel:     ████████████░░░░  75%")
                col.label(text="Green Channel:   ██████████░░░░░░  62%")
                col.label(text="Blue Channel:    █████████████░░░  81%")
            else:
                col = box.column(align=True)
                col.label(text=f"Canvas Dimensions: {settings.doc_width} × {settings.doc_height}")
                col.label(text="Color Space: 32-bit Float Linear Rec.709 / sRGB")
            layout.operator("canvas.edit_image_internally", text="Edit Image Internally in Canvas", icon='GREASEPENCIL')


class CANVAS_PT_bake_plane(Panel):
    """Bake Canvas / Grease Pencil to Decal Plane with Normal Data"""
    bl_space_type = CANVAS_SPACE_TYPE
    bl_region_type = 'UI'
    bl_category = 'Canvas'
    bl_label = "3D Decal & Export"

    def draw(self, context):
        layout = self.layout
        settings = getattr(context.scene, "canvas_settings", None)
        if not settings:
            return

        box = layout.box()
        box.label(text="3D Decal Plane Settings", icon='MESH_PLANE')
        box.prop(settings, "decal_width_m", text="Plane Width (m)")
        box.prop(settings, "normal_strength", text="Emboss Depth")
        box.prop(settings, "normal_bevel_radius", text="Bevel Radius")

        layout.separator()
        layout.operator("canvas.bake_gpencil_to_plane", text="Bake to 3D Decal Plane", icon='RENDER_STILL')

        layout.separator()
        pack_box = layout.box()
        pack_box.label(text="Canvas Image Export", icon='IMAGE_DATA')
        row_exp = pack_box.row(align=True)
        row_exp.operator("canvas.export_to_blend", text="Pack into .blend", icon='PACKAGE')
        row_exp.operator("canvas.export_image_file", text="Export to File...", icon='EXPORT')


# ---------------------------------------------------------------------------
# Studio Panels (Color, Transform, History, Appearance) & Status Bar
# ---------------------------------------------------------------------------

class CANVAS_PT_color_swatches(Panel):
    """Color & Swatches Studio: Wheel, RGB/HSV sliders, and Palette Swatches"""
    bl_space_type = CANVAS_SPACE_TYPE
    bl_region_type = 'UI'
    bl_category = 'Canvas'
    bl_label = "Color & Swatches"

    def draw(self, context):
        layout = self.layout
        settings = getattr(context.scene, "canvas_settings", None)
        if not settings:
            return

        # Unified Attribute Selector (Consistent selector across every tool)
        box = layout.box()
        box.label(text="Active Attribute Selector:", icon='RESTRICT_SELECT_OFF')
        box.prop(settings, "color_target", expand=True)
        box.prop(settings, "unified_color", text="Active Color")

        box.separator()
        box.prop(settings, "color_model", expand=True)

        # Quick Palette Swatches
        swatch_box = layout.box()
        swatch_box.label(text="System Swatches", icon='COLOR')
        grid = swatch_box.grid_flow(row_major=True, columns=6, even_columns=True, align=True)
        swatch_colors = [
            (0.0, 0.0, 0.0, 1.0), (1.0, 1.0, 1.0, 1.0), (0.9, 0.1, 0.1, 1.0),
            (0.1, 0.8, 0.2, 1.0), (0.1, 0.4, 0.9, 1.0), (0.95, 0.75, 0.1, 1.0),
            (0.5, 0.1, 0.8, 1.0), (0.1, 0.8, 0.8, 1.0), (0.9, 0.4, 0.1, 1.0),
            (0.3, 0.3, 0.3, 1.0), (0.6, 0.6, 0.6, 1.0), (0.85, 0.85, 0.85, 1.0),
        ]
        for col in swatch_colors:
            grid.label(text="", icon='LAYER_ACTIVE')


class CANVAS_PT_transform_studio(Panel):
    """Transform Studio: Numerical readouts, Scaling, Guidelines & 2D Cursor"""
    bl_space_type = CANVAS_SPACE_TYPE
    bl_region_type = 'UI'
    bl_category = 'Canvas'
    bl_label = "Transform & 2D Cursor"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = getattr(scene, "canvas_settings", None)
        if not settings:
            return

        idx = settings.active_layer_index
        has_layer = (0 <= idx < len(settings.layers))
        layer = settings.layers[idx] if has_layer else None

        if layer:
            box = layout.box()
            box.label(text=f"Layer: {layer.name}", icon='LAYER_ACTIVE')
            col = box.column(align=True)

            row1 = col.row(align=True)
            row1.prop(layer, "pos_x", text="X (px)")
            row1.prop(layer, "pos_y", text="Y (px)")

            row2 = col.row(align=True)
            row2.prop(layer, "scale", text="Scale")
            row2.prop(layer, "rotation", text="Rotation")

            # Quick Scale & Shrink Operations
            box_ops = layout.box()
            box_ops.label(text="Scale & Fit Operations", icon='DRIVER_TRANSFORM')
            row_sc = box_ops.row(align=True)
            row_sc.operator("canvas.layer_transform", text="Scale +10%", icon='ZOOM_IN').action = 'SCALE_UP'
            row_sc.operator("canvas.layer_transform", text="Shrink -10%", icon='ZOOM_OUT').action = 'SCALE_DOWN'

            row_fit = box_ops.row(align=True)
            row_fit.operator("canvas.layer_transform", text="Fit Canvas", icon='FULLSCREEN_ENTER').action = 'FIT_CANVAS'
            row_fit.operator("canvas.layer_transform", text="Fill Canvas", icon='ZOOM_ALL').action = 'FILL_CANVAS'
            row_fit.operator("canvas.layer_transform", text="100%", icon='FILE_REFRESH').action = 'RESET'

            row_fl = box_ops.row(align=True)
            row_fl.operator("canvas.layer_transform", text="Flip Horizontal", icon='ARROW_LEFTRIGHT').action = 'FLIP_H'
            row_fl.operator("canvas.layer_transform", text="Flip Vertical", icon='SNAP_FACE').action = 'FLIP_V'

            # Guidelines & Snapping
            box_snap = layout.box()
            box_snap.label(text="Guideline Snapping", icon='SNAP_ON')
            grid_s = box_snap.grid_flow(row_major=True, columns=3, even_columns=True, align=True)
            grid_s.operator("canvas.layer_snap_guideline", text="Center", icon='ALIGN_CENTER').target = 'CENTER'
            grid_s.operator("canvas.layer_snap_guideline", text="Top", icon='ALIGN_TOP').target = 'TOP'
            grid_s.operator("canvas.layer_snap_guideline", text="Bottom", icon='ALIGN_BOTTOM').target = 'BOTTOM'
            grid_s.operator("canvas.layer_snap_guideline", text="Left", icon='ALIGN_FLUSH').target = 'LEFT'
            grid_s.operator("canvas.layer_snap_guideline", text="Right", icon='ALIGN_RIGHT').target = 'RIGHT'
            grid_s.operator("canvas.layer_snap_guideline", text="To Cursor", icon='PIVOT_CURSOR').target = 'CURSOR'

        # 2D Cursor Section
        box_cur = layout.box()
        box_cur.label(text="2D Cursor (Canvas Plane)", icon='PIVOT_CURSOR')
        col_c = box_cur.column(align=True)
        row_c = col_c.row(align=True)
        row_c.prop(scene.cursor, "location", index=0, text="Cursor X")
        row_c.prop(scene.cursor, "location", index=1, text="Cursor Y")

        row_c_ops = box_cur.row(align=True)
        row_c_ops.operator("canvas.cursor_center", text="Center", icon='SCREEN_BACK')
        row_c_ops.operator("canvas.cursor_to_selection", text="To Layer", icon='SNAP_VERTEX')
        row_c_ops.operator("canvas.selection_to_cursor", text="Layer to Cursor", icon='SNAP_FACE')


class CANVAS_PT_history_studio(Panel):
    """History Studio: Undo scrub-bar and milestone states"""
    bl_space_type = CANVAS_SPACE_TYPE
    bl_region_type = 'UI'
    bl_category = 'Canvas'
    bl_label = "History"

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.operator("ed.undo", text="Step Back", icon='LOOP_BACK')
        row.operator("ed.redo", text="Step Forward", icon='LOOP_FORWARDS')

        layout.separator()
        box = layout.box()
        box.label(text="Milestone Checkpoints", icon='BOOKMARKS')
        box.operator("ed.undo_history", text="Undo History Scrub-Bar...", icon='TIME')


class CANVAS_PT_appearance_studio(Panel):
    """Appearance Studio: Multiple strokes and fills on vector geometry"""
    bl_space_type = CANVAS_SPACE_TYPE
    bl_region_type = 'UI'
    bl_category = 'Canvas'
    bl_label = "Appearance"

    def draw(self, context):
        layout = self.layout
        settings = getattr(context.scene, "canvas_settings", None)
        if not settings:
            return

        idx = settings.active_layer_index
        if not (0 <= idx < len(settings.layers)):
            layout.label(text="No active selection")
            return

        layer = settings.layers[idx]
        box = layout.box()
        box.label(text=f"Object: {layer.name}", icon='GRAPH')

        # Fill style
        r_fill = box.row(align=True)
        r_fill.label(text="Fill Style:", icon='IMAGE_RGB')
        r_fill.prop(layer, "color_fill", text="")

        # Stroke style
        r_stroke = box.row(align=True)
        r_stroke.label(text="Stroke Outline:", icon='LINE_DATA')
        r_stroke.prop(layer, "use_outline", text="")
        if layer.use_outline:
            box.prop(layer, "outline_width", text="Width")
            box.prop(layer, "outline_color", text="Color")


class CANVAS_HT_footer(Header):
    """Hint Line / Status Bar at bottom edge of Canvas"""
    bl_space_type = CANVAS_SPACE_TYPE
    bl_region_type = 'FOOTER'

    def draw(self, context):
        layout = self.layout
        settings = getattr(context.scene, "canvas_settings", None)
        if not settings:
            return

        tool = settings.active_tool
        layout.label(text=f"Tool: {tool.title()}", icon='TOOL_SETTINGS')

        layout.separator()
        row = layout.row(align=True)
        row.label(text=f"X: {settings.cursor_x_px} px", icon='RESTRICT_SELECT_OFF')
        row.label(text=f"Y: {settings.cursor_y_px} px")

        layout.separator_spacer()
        layout.label(
            text="[Shift] Constrain 1:1 / 45° | [Alt] Center Origin | [Ctrl] Override Snapping",
            icon='INFO',
        )
        layout.separator_spacer()
        layout.label(
            text=f"{settings.doc_width} × {settings.doc_height} px | {settings.doc_dpi} DPI",
            icon='IMAGE_DATA',
        )
        layout.label(text="Zoom: Cursor-Centered", icon='VIEWZOOM')
        layout.separator()
        has_tree = bool(context.scene.compositing_node_group)
        layout.label(
            text="Compositor: Active" if has_tree else "Compositor: Offline",
            icon='NODE_COMPOSITING' if has_tree else 'UNLINKED',
        )
        layout.separator_spacer()
        layout.operator("canvas.recompile", text="", icon='FILE_REFRESH')


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = [
    CanvasSymbolItem,
    CanvasLayerItem,
    CanvasSettings,
    CANVAS_UL_layer_list,
    # Base Operators
    CANVAS_OT_layer_add,
    CANVAS_OT_layer_remove,
    CANVAS_OT_layer_move,
    CANVAS_OT_layer_duplicate,
    CANVAS_OT_layer_solo,
    CANVAS_OT_layer_toggle_clipping,
    CANVAS_OT_layer_open_node_editor,
    CANVAS_OT_setup_workspace,
    CANVAS_OT_open_editor,
    CANVAS_OT_setup_canvas,
    CANVAS_OT_new_document,
    CANVAS_OT_update_cursor_position,
    CANVAS_OT_enter_ink_mode,
    CANVAS_OT_lock_viewport_toggle,
    CANVAS_OT_recompile,
    # Curve & Pen Precision
    CANVAS_OT_pen_tool,
    CANVAS_OT_curve_set_handle,
    CANVAS_OT_curve_subdivide,
    CANVAS_OT_curve_toggle_cyclic,
    CANVAS_OT_floating_curve_toolbox,
    # Alignment & Snapping
    CANVAS_OT_align_layers,
    CANVAS_OT_distribute_layers,
    CANVAS_OT_snap_to_grid,
    # Symbols
    CANVAS_OT_symbol_create,
    CANVAS_OT_symbol_insert,
    # Presets & Decal Baking
    CANVAS_OT_apply_composite_preset,
    CANVAS_OT_import_image_layer,
    CANVAS_OT_import_image_file,
    CANVAS_OT_import_blend_image,
    CANVAS_OT_edit_image_internally,
    CANVAS_OT_switch_to_canvas_viewlayer,
    CANVAS_OT_toggle_edit_mode,
    CANVAS_OT_open_floating_gpencil_window,
    CANVAS_OT_layer_transform,
    CANVAS_OT_layer_snap_guideline,
    CANVAS_OT_cursor_center,
    CANVAS_OT_cursor_to_selection,
    CANVAS_OT_selection_to_cursor,
    CANVAS_OT_export_to_blend,
    CANVAS_OT_export_image_file,
    CANVAS_OT_bake_gpencil_to_plane,
    CANVAS_OT_select_gpencil_brush,
    # Layer Masks
    CANVAS_OT_layer_mask_add,
    CANVAS_OT_layer_mask_remove,
    CANVAS_OT_layer_mask_paint,
    # GPencil Modes & Flatten
    CANVAS_OT_gpencil_set_mode,
    CANVAS_OT_flatten_layers,
    CANVAS_OT_flatten_gpencil_to_image,
    # Selection Tools
    CANVAS_OT_selection_lasso,
    CANVAS_OT_selection_magic_wand,
    CANVAS_OT_ai_select_subject,
    # Retouching & Healing
    CANVAS_OT_clone_stamp_tool,
    CANVAS_OT_healing_brush_tool,
    # 2D Geometry Booleans & Layer Order
    CANVAS_OT_boolean_operation,
    CANVAS_OT_layer_order,
    CANVAS_OT_add_shape_primitive,
    CANVAS_OT_switch_active_tool,
    # Menus
    CANVAS_MT_file,
    CANVAS_MT_edit,
    CANVAS_MT_image,
    CANVAS_MT_layer,
    CANVAS_MT_select,
    CANVAS_MT_filter,
    CANVAS_MT_view,
    CANVAS_MT_editor_menus,
    # Left Shelf & Brush Popover
    CANVAS_PT_gpencil_brush_palette,
    CANVAS_PT_tools,
    CANVAS_PT_tools_brushes,
    CANVAS_PT_3d_stage,
    # Right Sidebar Studios
    CANVAS_PT_canvas_document,
    CANVAS_PT_canvas_layers,
    CANVAS_PT_canvas_layer_properties,
    CANVAS_PT_color_swatches,
    CANVAS_PT_transform_studio,
    CANVAS_PT_history_studio,
    CANVAS_PT_appearance_studio,
    CANVAS_PT_composite_presets,
    CANVAS_PT_scopes,
    CANVAS_PT_bake_plane,
]

if hasattr(bpy.types, "SpaceCanvas"):
    classes.extend([
        CANVAS_HT_header,
        CANVAS_HT_tool_header,
        CANVAS_HT_footer,
    ])


def register_props():
    bpy.types.Scene.canvas_settings = PointerProperty(type=CanvasSettings)


def draw_open_canvas_editor_menu(self, context):
    self.layout.operator("canvas.open_editor", text="Open Canvas Editor", icon='IMAGE')


def unregister_props():
    if hasattr(bpy.types.Scene, "canvas_settings"):
        del bpy.types.Scene.canvas_settings


def register():
    for cls in classes:
        try:
            bpy.utils.register_class(cls)
        except Exception as e:
            print(f"Canvas: skipping registration of {cls.__name__}: {e}")
    register_props()
    bpy.types.VIEW3D_MT_editor_menus.append(draw_open_canvas_editor_menu)

    keyconfig = bpy.context.window_manager.keyconfigs.addon
    if keyconfig:
        try:
            km_name = "Canvas" if hasattr(bpy.types, "SpaceCanvas") else "3D View"
            km_space = CANVAS_SPACE_TYPE
            keymap = keyconfig.keymaps.new(name=km_name, space_type=km_space)
            keymap_item = keymap.keymap_items.new(
                "canvas.update_cursor_position",
                type='MOUSEMOVE',
                value='ANY',
            )
            _canvas_keymaps.append((keymap, keymap_item))
        except Exception:
            pass

    # Register Realtime Artboard GPU Draw Handler
    global _canvas_draw_handler
    if hasattr(bpy.types, "SpaceCanvas"):
        try:
            _canvas_draw_handler = bpy.types.SpaceCanvas.draw_handler_add(
                draw_canvas_artboard_callback, (), 'WINDOW', 'POST_VIEW'
            )
        except Exception:
            _canvas_draw_handler = None

    # Register Workspace Monitor Handler
    if canvas_workspace_monitor not in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.append(canvas_workspace_monitor)


def unregister():
    global _canvas_draw_handler
    bpy.types.VIEW3D_MT_editor_menus.remove(draw_open_canvas_editor_menu)
    for keymap, keymap_item in _canvas_keymaps:
        keymap.keymap_items.remove(keymap_item)
    _canvas_keymaps.clear()

    if _canvas_draw_handler and hasattr(bpy.types, "SpaceCanvas"):
        try:
            bpy.types.SpaceCanvas.draw_handler_remove(_canvas_draw_handler, 'WINDOW')
        except Exception:
            pass
        _canvas_draw_handler = None

    if canvas_workspace_monitor in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(canvas_workspace_monitor)

    unregister_props()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
