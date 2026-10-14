# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-License-Identifier: GPL-2.0-or-later

import math
import bpy
import bmesh
from bpy.types import Operator, Panel, PropertyGroup
from bpy.props import StringProperty, IntProperty, PointerProperty, BoolProperty


def isolate_canvas_collections(scene):
    """Ensure canvas collections and main collections are cleanly segregated across view layers."""
    # Find all canvas collections
    canvas_cols = set()
    for col in bpy.data.collections:
        if col.name.endswith("_Collection"):
            canvas_cols.add(col)

    def set_exclude_for_col(layer_col, target_col, exclude):
        if layer_col.collection == target_col:
            layer_col.exclude = exclude
            return
        for child in layer_col.children:
            set_exclude_for_col(child, target_col, exclude)

    for vl in scene.view_layers:
        is_canvas_vl = any(vl.name == c.name[:-11] for c in canvas_cols)
        matched_canvas_col = None
        for c in canvas_cols:
            if vl.name == c.name[:-11]:
                matched_canvas_col = c
                break

        if is_canvas_vl and matched_canvas_col:
            # Exclude all collections except the matched canvas collection
            for child in vl.layer_collection.children:
                if child.collection == matched_canvas_col:
                    child.exclude = False
                else:
                    child.exclude = True
        else:
            # Non-canvas view layer: exclude all canvas collections
            for c in canvas_cols:
                set_exclude_for_col(vl.layer_collection, c, True)


class CanvasSettings(PropertyGroup):
    name: StringProperty(
        name="Canvas Name",
        description="Name for new Canvas scene layer and target image",
        default="Canvas",
    )
    resolution_x: IntProperty(
        name="Width",
        description="Canvas target resolution width in pixels",
        default=1920,
        min=64,
        max=16384,
    )
    resolution_y: IntProperty(
        name="Height",
        description="Canvas target resolution height in pixels",
        default=1080,
        min=64,
        max=16384,
    )


class CANVAS_OT_create(Operator):
    bl_idname = "canvas.create"
    bl_label = "Create Canvas"
    bl_description = "Create a new scene layer with an orthographic camera, dedicated render target image, and compositor pipeline"
    bl_options = {'REGISTER', 'UNDO'}

    canvas_name: StringProperty(
        name="Canvas Name",
        description="Custom name for the new Canvas scene layer and render target",
        default="",
    )
    resolution_x: IntProperty(
        name="Width",
        default=0,
        min=0,
        max=16384,
    )
    resolution_y: IntProperty(
        name="Height",
        default=0,
        min=0,
        max=16384,
    )

    def invoke(self, context, event):
        scene = context.scene
        settings = getattr(scene, "canvas_settings", None)
        if self.canvas_name == "":
            base_count = len(scene.view_layers)
            self.canvas_name = settings.name if (settings and settings.name != "Canvas") else f"Canvas_{base_count}"
        if self.resolution_x <= 0:
            self.resolution_x = settings.resolution_x if settings else scene.render.resolution_x
        if self.resolution_y <= 0:
            self.resolution_y = settings.resolution_y if settings else scene.render.resolution_y
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.prop(self, "canvas_name")
        row = col.row(align=True)
        row.prop(self, "resolution_x")
        row.prop(self, "resolution_y")

    def execute(self, context):
        scene = context.scene
        name = self.canvas_name.strip()
        if not name:
            name = f"Canvas_{len(scene.view_layers)}"

        res_x = self.resolution_x if self.resolution_x > 0 else scene.render.resolution_x
        res_y = self.resolution_y if self.resolution_y > 0 else scene.render.resolution_y

        settings = getattr(scene, "canvas_settings", None)
        if settings:
            settings.name = name
            settings.resolution_x = res_x
            settings.resolution_y = res_y

        # 1. Ensure root scene objects are encapsulated in a main collection so they can be isolated
        if scene.collection.objects:
            main_col = bpy.data.collections.get("Main_Collection") or bpy.data.collections.new("Main_Collection")
            if main_col.name not in scene.collection.children:
                scene.collection.children.link(main_col)
            for obj in list(scene.collection.objects):
                scene.collection.objects.unlink(obj)
                if obj.name not in main_col.objects:
                    main_col.objects.link(obj)

        # 2. Create a dedicated Collection for this Canvas
        canvas_col_name = f"{name}_Collection"
        canvas_col = bpy.data.collections.get(canvas_col_name) or bpy.data.collections.new(canvas_col_name)
        if canvas_col.name not in scene.collection.children:
            scene.collection.children.link(canvas_col)

        # 3. Create a new Scene View Layer
        view_layer = scene.view_layers.get(name)
        if not view_layer:
            view_layer = scene.view_layers.new(name=name)
        context.window.view_layer = view_layer

        # 4. Setup an Orthographic Camera inside the Canvas Collection
        cam_data_name = f"{name}_Camera"
        cam_data = bpy.data.cameras.get(cam_data_name) or bpy.data.cameras.new(name=cam_data_name)
        cam_data.type = 'ORTHO'
        cam_data.ortho_scale = 7.314  # standard viewport orthographic framing

        cam_obj_name = f"{name}_Camera"
        cam_obj = bpy.data.objects.get(cam_obj_name)
        if not cam_obj:
            cam_obj = bpy.data.objects.new(name=cam_obj_name, object_data=cam_data)
            cam_obj.location = (0.0, -10.0, 0.0)
            cam_obj.rotation_euler = (math.radians(90.0), 0.0, 0.0)
            canvas_col.objects.link(cam_obj)
        elif cam_obj.name not in canvas_col.objects:
            canvas_col.objects.link(cam_obj)

        scene.camera = cam_obj

        # 5. Setup White Background Plane in Canvas Collection
        # Placed behind origin along ortho camera view axis (+Y from camera at Y=-10)
        bg_obj_name = f"{name}_Background"
        bg_mesh_name = f"{name}_Background_Mesh"
        bg_mat_name = f"{name}_WhiteBackground"

        bg_mat = bpy.data.materials.get(bg_mat_name)
        if not bg_mat:
            bg_mat = bpy.data.materials.new(name=bg_mat_name)
            bg_mat.use_nodes = True
            mat_nodes = bg_mat.node_tree.nodes
            mat_nodes.clear()
            node_emit = mat_nodes.new(type='ShaderNodeEmission')
            node_emit.inputs['Color'].default_value = (1.0, 1.0, 1.0, 1.0)
            node_emit.inputs['Strength'].default_value = 1.0
            node_out = mat_nodes.new(type='ShaderNodeOutputMaterial')
            bg_mat.node_tree.links.new(node_emit.outputs['Emission'], node_out.inputs['Surface'])

        bg_obj = bpy.data.objects.get(bg_obj_name)
        if not bg_obj:
            bg_mesh = bpy.data.meshes.get(bg_mesh_name) or bpy.data.meshes.new(bg_mesh_name)
            bm = bmesh.new()
            bm.from_mesh(bg_mesh)
            bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=500.0)
            bm.to_mesh(bg_mesh)
            bm.free()

            bg_obj = bpy.data.objects.new(name=bg_obj_name, object_data=bg_mesh)
            bg_obj.location = (0.0, 50.0, 0.0)
            bg_obj.rotation_euler = (math.radians(90.0), 0.0, 0.0)
            canvas_col.objects.link(bg_obj)
        elif bg_obj.name not in canvas_col.objects:
            canvas_col.objects.link(bg_obj)

        if not bg_obj.data.materials:
            bg_obj.data.materials.append(bg_mat)
        else:
            bg_obj.data.materials[0] = bg_mat

        # 6. Isolate collections:
        def find_layer_col(layer_col, target_col):
            if layer_col.collection == target_col:
                return layer_col
            for child in layer_col.children:
                res = find_layer_col(child, target_col)
                if res:
                    return res
            return None

        isolate_canvas_collections(scene)

        # Make canvas collection the active collection in this view layer
        target_lc = find_layer_col(view_layer.layer_collection, canvas_col)
        if target_lc:
            view_layer.active_layer_collection = target_lc

        # 7. Create a Separate Render Target Image (customizable name every time) with White Background
        img_name = f"{name}_Target"
        target_img = bpy.data.images.get(img_name)
        if not target_img:
            target_img = bpy.data.images.new(
                name=img_name,
                width=res_x,
                height=res_y,
                alpha=True,
                float_buffer=False,
            )
            target_img.generated_color = (1.0, 1.0, 1.0, 1.0)
        else:
            if target_img.size[0] != res_x or target_img.size[1] != res_y:
                target_img.scale(res_x, res_y)

        # 8. Setup Dedicated Compositor Graph with White Background Alpha Over
        tree = getattr(scene, "node_tree", None)
        if not tree and hasattr(scene, "compositing_node_group"):
            if not scene.compositing_node_group:
                scene.compositing_node_group = bpy.data.node_groups.new("Compositor", "CompositorNodeTree")
            tree = scene.compositing_node_group

        if tree:
            rl_node = None
            for node in tree.nodes:
                if node.type == 'R_LAYERS' and getattr(node, "layer", None) == view_layer.name:
                    rl_node = node
                    break
            if not rl_node:
                rl_node = tree.nodes.new(type='CompositorNodeRLayers')
                rl_node.layer = view_layer.name
                rl_node.location = (-400, 200)

            comp_node = None
            viewer_node = None
            for node in tree.nodes:
                if node.type in {'COMPOSITE', 'GROUP_OUTPUT'}:
                    comp_node = node
                elif node.type == 'VIEWER':
                    viewer_node = node

            if not comp_node:
                try:
                    comp_node = tree.nodes.new(type='CompositorNodeComposite')
                except Exception:
                    if hasattr(tree, "interface") and not tree.interface.items_tree:
                        tree.interface.new_socket('Image', in_out='OUTPUT', socket_type='NodeSocketColor')
                    comp_node = tree.nodes.new(type='NodeGroupOutput')
                comp_node.location = (400, 200)

            if not viewer_node:
                try:
                    viewer_node = tree.nodes.new(type='CompositorNodeViewer')
                    viewer_node.location = (400, -50)
                except Exception:
                    pass

            # Alpha Over node with white background
            alpha_over_node = None
            for node in tree.nodes:
                if node.type == 'ALPHAOVER' and getattr(node, "name", "").startswith(f"{name}_AlphaOver"):
                    alpha_over_node = node
                    break
            if not alpha_over_node:
                alpha_over_node = tree.nodes.new(type='CompositorNodeAlphaOver')
                alpha_over_node.name = f"{name}_AlphaOver"
                alpha_over_node.label = "White Canvas Background"
                alpha_over_node.location = (0, 200)

            # Set background color to pure white (1.0, 1.0, 1.0, 1.0)
            if len(alpha_over_node.inputs) > 1:
                alpha_over_node.inputs[1].default_value = (1.0, 1.0, 1.0, 1.0)

            if rl_node and "Image" in rl_node.outputs and len(alpha_over_node.inputs) > 2:
                # Foreground image is socket index 2 (Image_001)
                tree.links.new(rl_node.outputs["Image"], alpha_over_node.inputs[2])

            if comp_node:
                out_socket = comp_node.inputs.get("Image") or (comp_node.inputs[0] if comp_node.inputs else None)
                if out_socket:
                    tree.links.new(alpha_over_node.outputs["Image"], out_socket)
            if viewer_node and "Image" in viewer_node.inputs:
                tree.links.new(alpha_over_node.outputs["Image"], viewer_node.inputs["Image"])

        # Also set 3D viewport background color to white if in Viewport mode
        for window in context.window_manager.windows:
            screen = window.screen
            if screen:
                for area in screen.areas:
                    if area.type == 'VIEW_3D':
                        for space in area.spaces:
                            if space.type == 'VIEW_3D' and hasattr(space, 'shading'):
                                space.shading.background_type = 'VIEWPORT'
                                space.shading.background_color = (1.0, 1.0, 1.0)

        self.report({'INFO'}, f"Canvas '{name}' created with white background, isolated collection, Ortho Camera and target image '{img_name}'.")
        return {'FINISHED'}


class CANVAS_OT_resize_target(Operator):
    bl_idname = "canvas.resize_target"
    bl_label = "Resize Target Image"
    bl_description = "Resize the active canvas target image to match current resolution settings"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        settings = getattr(scene, "canvas_settings", None)
        if not settings:
            self.report({'WARNING'}, "Canvas settings not found")
            return {'CANCELLED'}

        vl = context.window.view_layer
        img_name = f"{vl.name}_Target"
        target_img = bpy.data.images.get(img_name)
        if not target_img:
            # Fallback to any image ending in _Target
            for img in bpy.data.images:
                if img.name.endswith("_Target"):
                    target_img = img
                    break

        if not target_img:
            self.report({'WARNING'}, f"No target image '{img_name}' found to resize")
            return {'CANCELLED'}

        target_img.scale(settings.resolution_x, settings.resolution_y)
        self.report({'INFO'}, f"Resized '{target_img.name}' to {settings.resolution_x}x{settings.resolution_y}")
        return {'FINISHED'}


class CANVAS_OT_set_scene_resolution(Operator):
    bl_idname = "canvas.set_scene_resolution"
    bl_label = "Apply to Scene Render"
    bl_description = "Update the active scene render resolution to match canvas resolution"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        settings = getattr(scene, "canvas_settings", None)
        if not settings:
            return {'CANCELLED'}
        scene.render.resolution_x = settings.resolution_x
        scene.render.resolution_y = settings.resolution_y
        self.report({'INFO'}, f"Scene render resolution set to {settings.resolution_x}x{settings.resolution_y}")
        return {'FINISHED'}


class CANVAS_OT_isolate_collections(Operator):
    bl_idname = "canvas.isolate_collections"
    bl_label = "Re-Isolate Collections"
    bl_description = "Ensure canvas collections and main collections are strictly separated across all view layers"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        isolate_canvas_collections(context.scene)
        self.report({'INFO'}, "Canvas collections isolated across view layers")
        return {'FINISHED'}


class CANVAS_OT_view_camera(Operator):
    bl_idname = "canvas.view_camera"
    bl_label = "View Canvas Camera"
    bl_description = "Switch active camera to canvas camera and align viewport"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        vl = context.window.view_layer
        cam_obj = bpy.data.objects.get(f"{vl.name}_Camera") or scene.camera
        if cam_obj:
            scene.camera = cam_obj
            for area in context.screen.areas:
                if area.type == 'VIEW_3D':
                    for space in area.spaces:
                        if space.type == 'VIEW_3D':
                            space.region_3d.view_perspective = 'CAMERA'
            self.report({'INFO'}, f"Active camera set to '{cam_obj.name}'")
            return {'FINISHED'}
        self.report({'WARNING'}, "No camera found for canvas")
        return {'CANCELLED'}


def draw_canvas_panel_ui(self, context):
    layout = self.layout
    scene = context.scene
    settings = getattr(scene, "canvas_settings", None)
    vl = context.window.view_layer

    # 1. Quick Create Canvas
    box = layout.box()
    box.label(text="Create New Canvas", icon='NEWFOLDER')
    if settings:
        box.prop(settings, "name", text="Name")
        col_res = box.column(align=True)
        col_res.prop(settings, "resolution_x", text="Width")
        col_res.prop(settings, "resolution_y", text="Height")
    
    op = box.operator("canvas.create", text="Create Canvas Layer", icon='ADD')
    if settings:
        op.canvas_name = settings.name
        op.resolution_x = settings.resolution_x
        op.resolution_y = settings.resolution_y

    # 2. Active Layer Information & Tools
    box = layout.box()
    box.label(text=f"Active Layer: {vl.name}", icon='RENDERLAYERS')

    target_img_name = f"{vl.name}_Target"
    target_img = bpy.data.images.get(target_img_name)
    cam_obj = bpy.data.objects.get(f"{vl.name}_Camera")

    # Resolution controls for active canvas
    col = box.column(align=True)
    col.label(text="Resolution Tools:", icon='SETTINGS')
    if settings:
        row = col.row(align=True)
        row.prop(settings, "resolution_x", text="X")
        row.prop(settings, "resolution_y", text="Y")
    
    row_ops = col.row(align=True)
    row_ops.operator("canvas.resize_target", text="Resize Target", icon='IMAGE_DATA')
    row_ops.operator("canvas.set_scene_resolution", text="Set Scene Res", icon='SCENE_DATA')

    if target_img:
        col.label(text=f"Target: {target_img.name} ({target_img.size[0]}x{target_img.size[1]})", icon='IMAGE_RGB_ALPHA')

    # Camera controls
    box_cam = box.column(align=True)
    box_cam.label(text="Camera & Framing:", icon='CAMERA_DATA')
    if cam_obj and cam_obj.type == 'CAMERA':
        box_cam.prop(cam_obj.data, "ortho_scale", text="Ortho Scale")
        row_clip = box_cam.row(align=True)
        row_clip.prop(cam_obj.data, "clip_start", text="Clip Start")
        row_clip.prop(cam_obj.data, "clip_end", text="End")
    box_cam.operator("canvas.view_camera", text="Align View to Camera", icon='VIEW_CAMERA')

    # Isolation & Pipeline
    box_iso = box.column(align=True)
    box_iso.label(text="Isolation & Passes:", icon='OUTLINER_COLLECTION')
    box_iso.operator("canvas.isolate_collections", text="Isolate Collections", icon='FILE_REFRESH')
    box_iso.prop(vl, "use_sky", text="Render Sky / Background")

    box_fsr = box.column(align=True)
    box_fsr.label(text="Compositor Upscaling:", icon='MOD_THICKNESS')
    box_fsr.operator("cycles.enable_fsr1", text="Enable Cycles FSR1", icon='MOD_THICKNESS')


class CANVAS_PT_tools_view3d(Panel):
    bl_label = "Canvas Tools"
    bl_idname = "CANVAS_PT_tools_view3d"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Canvas"

    def draw(self, context):
        draw_canvas_panel_ui(self, context)


class CANVAS_PT_tools_node(Panel):
    bl_label = "Canvas Tools"
    bl_idname = "CANVAS_PT_tools_node"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Canvas"

    def draw(self, context):
        draw_canvas_panel_ui(self, context)


def draw_canvas_header_button(self, context):
    layout = self.layout
    layout.separator(factor=0.5)
    layout.operator("canvas.create", text="Canvas", icon='IMAGE_RGB_ALPHA')


classes = (
    CanvasSettings,
    CANVAS_OT_create,
    CANVAS_OT_resize_target,
    CANVAS_OT_set_scene_resolution,
    CANVAS_OT_isolate_collections,
    CANVAS_OT_view_camera,
    CANVAS_PT_tools_view3d,
    CANVAS_PT_tools_node,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.canvas_settings = PointerProperty(type=CanvasSettings)
    if hasattr(bpy.types, "VIEW3D_HT_header"):
        bpy.types.VIEW3D_HT_header.append(draw_canvas_header_button)
    if hasattr(bpy.types, "NODE_HT_header"):
        bpy.types.NODE_HT_header.append(draw_canvas_header_button)
    if hasattr(bpy.types, "IMAGE_HT_header"):
        bpy.types.IMAGE_HT_header.append(draw_canvas_header_button)


def unregister():
    if hasattr(bpy.types, "IMAGE_HT_header"):
        bpy.types.IMAGE_HT_header.remove(draw_canvas_header_button)
    if hasattr(bpy.types, "NODE_HT_header"):
        bpy.types.NODE_HT_header.remove(draw_canvas_header_button)
    if hasattr(bpy.types, "VIEW3D_HT_header"):
        bpy.types.VIEW3D_HT_header.remove(draw_canvas_header_button)
    if hasattr(bpy.types.Scene, "canvas_settings"):
        del bpy.types.Scene.canvas_settings
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
