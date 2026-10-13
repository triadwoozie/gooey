# SPDX-FileCopyrightText: 2026 Gooey Authors
# SPDX-License-Identifier: GPL-2.0-or-later

"""Gooey Application Template Initialization

Sets up default workspace preferences, compositor node activation,
EEVEE-Next NPR settings, and canvas helpers upon loading factory/new files.
"""

import bpy
from bpy.app.handlers import persistent


def configure_gooey_scenes():
    for scene in bpy.data.scenes:
        # 1. Enable compositing nodes by default
        scene.use_nodes = True

        # 2. Ensure basic composite pipeline is connected if empty
        if scene.node_tree:
            nodes = scene.node_tree.nodes
            has_rl = any(n.type == 'R_LAYERS' for n in nodes)
            has_comp = any(n.type == 'COMPOSITE' for n in nodes)
            if not has_rl:
                rl = nodes.new(type='CompositorNodeRLayers')
                rl.location = (-300, 200)
            else:
                rl = next(n for n in nodes if n.type == 'R_LAYERS')
            if not has_comp:
                comp = nodes.new(type='CompositorNodeComposite')
                comp.location = (200, 200)
            else:
                comp = next(n for n in nodes if n.type == 'COMPOSITE')
            if not scene.node_tree.links and "Image" in rl.outputs and "Image" in comp.inputs:
                scene.node_tree.links.new(rl.outputs["Image"], comp.inputs["Image"])

        # 3. Default Render Engine to BLENDER_EEVEE_NEXT with AgX color management
        scene.render.engine = 'BLENDER_EEVEE_NEXT'
        scene.display_settings.display_device = 'sRGB'
        if hasattr(scene.view_settings, "view_transform"):
            scene.view_settings.view_transform = 'AgX'


def configure_gooey_workspaces():
    for ws in bpy.data.workspaces:
        for screen in ws.screens:
            for area in screen.areas:
                if area.type == 'NODE_EDITOR':
                    space = area.spaces.active
                    if hasattr(space, "tree_type") and space.tree_type == 'CompositorNodeTree':
                        if hasattr(space, "use_backdrop"):
                            space.use_backdrop = True


@persistent
def load_handler(_):
    configure_gooey_scenes()
    configure_gooey_workspaces()


def register():
    bpy.app.handlers.load_factory_startup_post.append(load_handler)


def unregister():
    if load_handler in bpy.app.handlers.load_factory_startup_post:
        bpy.app.handlers.load_factory_startup_post.remove(load_handler)
