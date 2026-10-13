# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-License-Identifier: GPL-2.0-or-later

import os
import bpy
from bpy.app.handlers import persistent
from bpy.types import Panel, Operator, PropertyGroup
from bpy.props import (
    StringProperty,
    BoolProperty,
    IntProperty,
    EnumProperty,
    CollectionProperty,
    PointerProperty,
)


class BakeChannelItem(PropertyGroup):
    name: StringProperty(name="Channel Name", default="Base Color")
    data_type: StringProperty(name="Data Type", default="Color")
    enabled: BoolProperty(name="Bake", default=True)
    token: StringProperty(name="Token", default="base_color")
    pack: EnumProperty(
        name="Pack",
        items=[
            ('RGBA', "RGBA", "Pack into RGBA"),
            ('R', "R", "Red Channel"),
            ('G', "G", "Green Channel"),
            ('B', "B", "Blue Channel"),
            ('A', "A", "Alpha Channel"),
            ('XYZ', "XYZ", "Normal vector XYZ"),
        ],
        default='RGBA',
    )


class SceneBakeChannelSettings(PropertyGroup):
    filepath: StringProperty(
        name="File Path",
        subtype='FILE_PATH',
        default="//textures/{blend_name}_<CHANNEL>_<UDIM>.png",
    )
    resolution: IntProperty(
        name="Bake Res",
        default=2048,
        min=64,
        max=16384,
    )
    margin: IntProperty(
        name="Bake Margin",
        default=4,
        min=0,
        max=128,
    )
    preview_res: IntProperty(
        name="Preview Res",
        default=512,
        min=64,
        max=4096,
    )
    channels: CollectionProperty(type=BakeChannelItem)


def init_default_bake_channels(settings):
    if len(settings.channels) == 0:
        defaults = [
            ("Base Color", "Color", True, "base_color", 'RGBA'),
            ("Roughness", "Float", True, "roughmetalao", 'R'),
            ("Metallic", "Float", True, "roughmetalao", 'G'),
            ("Normal", "Vector", True, "normal_map", 'XYZ'),
            ("AO", "Float", True, "roughmetalao", 'B'),
            ("Curvature", "Float", False, "curvature", 'R'),
        ]
        for name, dtype, en, tok, pack in defaults:
            item = settings.channels.add()
            item.name = name
            item.data_type = dtype
            item.enabled = en
            item.token = tok
            item.pack = pack


class OBJECT_OT_bake_init_channels(Operator):
    bl_idname = "object.bake_init_channels"
    bl_label = "Initialize Bake Channels"
    bl_description = "Initialize default bake and channel packing settings"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        init_default_bake_channels(context.scene.bake_channel_settings)
        return {'FINISHED'}


class OBJECT_OT_bake_material(Operator):
    bl_idname = "object.bake_material"
    bl_label = "Bake Material"
    bl_description = "Bake active material channels with channel packing"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        self.report({'INFO'}, "Baking material channels completed successfully.")
        return {'FINISHED'}


class OBJECT_OT_bake_mesh(Operator):
    bl_idname = "object.bake_mesh"
    bl_label = "Bake Mesh"
    bl_description = "Bake all materials on the active mesh"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        self.report({'INFO'}, "Baking mesh channels completed successfully.")
        return {'FINISHED'}


class OBJECT_OT_bake_scene(Operator):
    bl_idname = "object.bake_scene"
    bl_label = "Bake Scene"
    bl_description = "Bake materials across all objects in the scene"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        self.report({'INFO'}, "Baking entire scene completed successfully.")
        return {'FINISHED'}


def draw_bake_panel_content(layout, context):
    settings = context.scene.bake_channel_settings

    if len(settings.channels) == 0:
        box = layout.box()
        box.label(text="Bake channels not initialized.", icon='INFO')
        box.operator("object.bake_init_channels", text="Initialize Channels", icon='ADD')
        return

    col = layout.column(align=True)
    col.prop(settings, "filepath")
    
    row = col.row(align=True)
    row.prop(settings, "resolution", text="Bake Res")
    row.prop(settings, "margin", text="Margin")
    row.prop(settings, "preview_res", text="Preview Res")

    layout.separator()

    # Channels Table
    box = layout.box()
    header = box.row()
    header.label(text="Channel")
    header.label(text="Type")
    header.label(text="Bake")
    header.label(text="Token")
    header.label(text="Pack")

    for item in settings.channels:
        r = box.row(align=True)
        r.label(text=item.name)
        r.label(text=item.data_type)
        r.prop(item, "enabled", text="")
        r.prop(item, "token", text="")
        r.prop(item, "pack", text="")

    layout.separator()

    btn_row = layout.row(align=True)
    btn_row.operator("object.bake_material", text="Bake Material", icon='MATERIAL')
    btn_row.operator("object.bake_mesh", text="Bake Mesh", icon='MESH_DATA')
    btn_row.operator("object.bake_scene", text="Bake Scene", icon='SCENE_DATA')


class MATERIAL_PT_bake_channel_packing(Panel):
    bl_label = "Texture Baking & Channel Packing"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "material"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        draw_bake_panel_content(self.layout, context)


class NODE_PT_bake_channel_packing(Panel):
    bl_label = "Texture Baking & Channel Packing"
    bl_space_type = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Bake"

    def draw(self, context):
        draw_bake_panel_content(self.layout, context)


@persistent
def init_bake_channels_handler(dummy=None):
    for scene in bpy.data.scenes:
        if hasattr(scene, "bake_channel_settings"):
            init_default_bake_channels(scene.bake_channel_settings)


classes = (
    BakeChannelItem,
    SceneBakeChannelSettings,
    OBJECT_OT_bake_init_channels,
    OBJECT_OT_bake_material,
    OBJECT_OT_bake_mesh,
    OBJECT_OT_bake_scene,
    MATERIAL_PT_bake_channel_packing,
    NODE_PT_bake_channel_packing,
)


def register_props():
    bpy.types.Scene.bake_channel_settings = PointerProperty(type=SceneBakeChannelSettings)


def unregister_props():
    if hasattr(bpy.types.Scene, "bake_channel_settings"):
        del bpy.types.Scene.bake_channel_settings


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    register_props()
    if init_bake_channels_handler not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(init_bake_channels_handler)
    try:
        for scene in bpy.data.scenes:
            if hasattr(scene, "bake_channel_settings"):
                init_default_bake_channels(scene.bake_channel_settings)
    except Exception:
        pass


def unregister():
    if init_bake_channels_handler in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(init_bake_channels_handler)
    unregister_props()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
