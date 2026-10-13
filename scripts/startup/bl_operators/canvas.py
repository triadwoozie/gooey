# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-License-Identifier: GPL-2.0-or-later

import math
import bpy
from bpy.types import Operator, Header
from bpy.props import StringProperty, IntProperty


class CANVAS_OT_create(Operator):
    bl_idname = "canvas.create"
    bl_label = "Create Canvas"
    bl_description = "Create a new scene layer with an orthographic camera, dedicated render target image, and compositor pipeline"
    bl_options = {'REGISTER', 'UNDO'}

    canvas_name: StringProperty(
        name="Canvas Name",
        description="Custom name for the new Canvas scene layer and render target",
        default="Canvas",
    )
    resolution_x: IntProperty(
        name="Width",
        default=1920,
        min=64,
        max=16384,
    )
    resolution_y: IntProperty(
        name="Height",
        default=1080,
        min=64,
        max=16384,
    )

    def invoke(self, context, event):
        if context.scene:
            self.resolution_x = context.scene.render.resolution_x
            self.resolution_y = context.scene.render.resolution_y
            base_name = f"Canvas_{len(context.scene.view_layers)}"
            self.canvas_name = base_name
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
        name = self.canvas_name.strip() or "Canvas"

        # 1. Create a new Scene View Layer
        view_layer = scene.view_layers.get(name)
        if not view_layer:
            view_layer = scene.view_layers.new(name=name)
        context.window.view_layer = view_layer

        # 2. Setup an Orthographic Camera
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
            scene.collection.objects.link(cam_obj)

        scene.camera = cam_obj

        # 3. Create a Separate Render Target Image (customizable name every time)
        img_name = f"{name}_Target"
        target_img = bpy.data.images.get(img_name)
        if not target_img:
            target_img = bpy.data.images.new(
                name=img_name,
                width=self.resolution_x,
                height=self.resolution_y,
                alpha=True,
                float_buffer=False,
            )
            target_img.generated_color = (0.0, 0.0, 0.0, 0.0)

        # 4. Setup Dedicated Compositor Graph
        scene.use_nodes = True
        tree = scene.node_tree
        if tree:
            # Look for or add a Render Layers node configured for this view layer
            rl_node = None
            for node in tree.nodes:
                if node.type == 'R_LAYERS' and getattr(node, "layer", None) == view_layer.name:
                    rl_node = node
                    break
            if not rl_node:
                rl_node = tree.nodes.new(type='CompositorNodeRLayers')
                rl_node.layer = view_layer.name
                rl_node.location = (-300, 200)

            comp_node = None
            viewer_node = None
            for node in tree.nodes:
                if node.type == 'COMPOSITE':
                    comp_node = node
                elif node.type == 'VIEWER':
                    viewer_node = node

            if not comp_node:
                comp_node = tree.nodes.new(type='CompositorNodeComposite')
                comp_node.location = (200, 200)

            if not viewer_node:
                viewer_node = tree.nodes.new(type='CompositorNodeViewer')
                viewer_node.location = (200, -50)

            # Link Image output of Render Layers to Composite and Viewer
            if rl_node and "Image" in rl_node.outputs:
                img_out = rl_node.outputs["Image"]
                if comp_node and "Image" in comp_node.inputs:
                    tree.links.new(img_out, comp_node.inputs["Image"])
                if viewer_node and "Image" in viewer_node.inputs:
                    tree.links.new(img_out, viewer_node.inputs["Image"])

        self.report({'INFO'}, f"Canvas '{name}' created with Ortho Camera and target image '{img_name}'.")
        return {'FINISHED'}


def draw_canvas_header_button(self, context):
    layout = self.layout
    layout.separator(factor=0.5)
    layout.operator("canvas.create", text="Canvas", icon='IMAGE_RGB_ALPHA')


classes = (
    CANVAS_OT_create,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
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
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
