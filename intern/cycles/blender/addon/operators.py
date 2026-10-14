# SPDX-FileCopyrightText: 2011-2022 Blender Foundation
#
# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import bpy
from bpy.types import Operator
from bpy.props import StringProperty

from bpy.app.translations import pgettext_tip as tip_


class CYCLES_OT_use_shading_nodes(Operator):
    """Enable nodes on a light"""
    bl_idname = "cycles.use_shading_nodes"
    bl_label = "Use Nodes"

    @classmethod
    def poll(cls, context):
        return getattr(context, "light", False)

    def execute(self, context):
        if context.light:
            context.light.use_nodes = True

        return {'FINISHED'}


class CYCLES_OT_denoise_animation(Operator):
    "Denoise rendered animation sequence using current scene and view " \
        "layer settings. Requires denoising data passes and output to " \
        "OpenEXR multilayer files"
    bl_idname = "cycles.denoise_animation"
    bl_label = "Denoise Animation"

    input_filepath: StringProperty(
        name='Input Filepath',
        description='File path for image to denoise. If not specified, uses the render file path and frame range from the scene',
        default='',
        subtype='FILE_PATH')

    output_filepath: StringProperty(
        name='Output Filepath',
        description='If not specified, renders will be denoised in-place',
        default='',
        subtype='FILE_PATH')

    def execute(self, context):
        import os

        preferences = context.preferences
        scene = context.scene
        view_layer = context.view_layer

        in_filepath = self.input_filepath
        out_filepath = self.output_filepath

        in_filepaths = []
        out_filepaths = []

        if in_filepath != '':
            # Denoise a single file
            if out_filepath == '':
                out_filepath = in_filepath

            in_filepaths.append(in_filepath)
            out_filepaths.append(out_filepath)
        else:
            # Denoise animation sequence with expanded frames matching
            # Blender render output file naming.
            in_filepath = scene.render.filepath
            if out_filepath == '':
                out_filepath = in_filepath

            # Backup since we will overwrite the scene path temporarily
            original_filepath = scene.render.filepath

            for frame in range(scene.frame_start, scene.frame_end + 1):
                scene.render.filepath = in_filepath
                filepath = scene.render.frame_path(frame=frame)
                in_filepaths.append(filepath)

                if not os.path.isfile(filepath):
                    scene.render.filepath = original_filepath
                    err_msg = tip_("Frame '%s' not found, animation must be complete") % filepath
                    self.report({'ERROR'}, err_msg)
                    return {'CANCELLED'}

                scene.render.filepath = out_filepath
                filepath = scene.render.frame_path(frame=frame)
                out_filepaths.append(filepath)

            scene.render.filepath = original_filepath

        # Run denoiser
        # TODO: support cancel and progress reports.
        import _cycles
        try:
            _cycles.denoise(preferences.as_pointer(),
                            scene.as_pointer(),
                            view_layer.as_pointer(),
                            input=in_filepaths,
                            output=out_filepaths)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'FINISHED'}

        self.report({'INFO'}, "Denoising completed")
        return {'FINISHED'}


class CYCLES_OT_merge_images(Operator):
    "Combine OpenEXR multi-layer images rendered with different sample " \
        "ranges into one image with reduced noise"
    bl_idname = "cycles.merge_images"
    bl_label = "Merge Images"

    input_filepath1: StringProperty(
        name='Input Filepath',
        description='File path for image to merge',
        default='',
        subtype='FILE_PATH')

    input_filepath2: StringProperty(
        name='Input Filepath',
        description='File path for image to merge',
        default='',
        subtype='FILE_PATH')

    output_filepath: StringProperty(
        name='Output Filepath',
        description='File path for merged image',
        default='',
        subtype='FILE_PATH')

    def execute(self, context):
        in_filepaths = [self.input_filepath1, self.input_filepath2]
        out_filepath = self.output_filepath

        import _cycles
        try:
            _cycles.merge(input=in_filepaths, output=out_filepath)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'FINISHED'}

        return {'FINISHED'}


class CYCLES_OT_enable_fsr1(Operator):
    """Enable AMD FSR 1 Upscaling for Cycles (inserts FSR1 in compositor, enables real-time viewport compositor, and sets preview pixel size to 2x)"""
    bl_idname = "cycles.enable_fsr1"
    bl_label = "Enable Cycles FSR1"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene

        # 1. Enable Compositor Nodes
        tree = getattr(scene, "node_tree", None)
        if not tree and hasattr(scene, "compositing_node_group"):
            if not scene.compositing_node_group:
                scene.compositing_node_group = bpy.data.node_groups.new("Compositor", "CompositorNodeTree")
            tree = scene.compositing_node_group

        if not tree:
            self.report({'ERROR'}, "Scene compositor node tree could not be accessed")
            return {'CANCELLED'}

        # 2. Check for existing FSR1 node or create one
        fsr_node = None
        for node in tree.nodes:
            if node.type == 'FSR1' or getattr(node, 'bl_idname', '') == 'CompositorNodeFSR1':
                fsr_node = node
                break

        if not fsr_node:
            try:
                fsr_node = tree.nodes.new(type='CompositorNodeFSR1')
            except Exception:
                fsr_node = None

        if fsr_node:
            fsr_node.location = (150, 200)
            if 'Scale' in fsr_node.inputs:
                fsr_node.inputs['Scale'].default_value = 2.0
            if 'Sharpness' in fsr_node.inputs:
                fsr_node.inputs['Sharpness'].default_value = 0.8

        # Find or create Render Layers node
        rl_node = None
        for node in tree.nodes:
            if node.type == 'R_LAYERS':
                rl_node = node
                break
        if not rl_node:
            rl_node = tree.nodes.new(type='CompositorNodeRLayers')
            rl_node.location = (-250, 200)

        # Find Composite and Viewer nodes
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
            comp_node.location = (450, 200)

        # Wire Render Layers -> FSR1 -> Composite / Viewer
        if fsr_node and rl_node and 'Image' in rl_node.outputs and 'Image' in fsr_node.inputs:
            tree.links.new(rl_node.outputs['Image'], fsr_node.inputs['Image'])

        if fsr_node and 'Image' in fsr_node.outputs:
            if comp_node:
                out_socket = comp_node.inputs.get("Image") or (comp_node.inputs[0] if comp_node.inputs else None)
                if out_socket:
                    tree.links.new(fsr_node.outputs['Image'], out_socket)
            if viewer_node and 'Image' in viewer_node.inputs:
                tree.links.new(fsr_node.outputs['Image'], viewer_node.inputs['Image'])

        # 3. Set Preview Pixel Size to 2x (half resolution rendering)
        scene.render.preview_pixel_size = '2'

        # 4. Set 3D Viewport shading compositor to ALWAYS
        for window in context.window_manager.windows:
            screen = window.screen
            if screen:
                for area in screen.areas:
                    if area.type == 'VIEW_3D':
                        for space in area.spaces:
                            if space.type == 'VIEW_3D' and hasattr(space, 'shading'):
                                space.shading.use_compositor = 'ALWAYS'

        self.report({'INFO'}, "Cycles FSR1 enabled: Compositor configured, Pixel Size set to 2x, Viewport Compositor set to Always.")
        return {'FINISHED'}


classes = (
    CYCLES_OT_use_shading_nodes,
    CYCLES_OT_denoise_animation,
    CYCLES_OT_merge_images,
    CYCLES_OT_enable_fsr1,
)


def register():
    from bpy.utils import register_class
    for cls in classes:
        register_class(cls)


def unregister():
    from bpy.utils import unregister_class
    for cls in classes:
        unregister_class(cls)
