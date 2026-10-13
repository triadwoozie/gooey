# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-License-Identifier: GPL-2.0-or-later

import os
import bpy
from bpy.types import Panel, Operator, PropertyGroup, UIList
from bpy.props import (
    StringProperty,
    IntProperty,
    EnumProperty,
    CollectionProperty,
    PointerProperty,
    BoolProperty,
)


class MediaBinItem(PropertyGroup):
    name: StringProperty(name="Media Name", default="New Media")
    filepath: StringProperty(name="Path", subtype='FILE_PATH', default="")
    media_type: EnumProperty(
        name="Type",
        items=[
            ('MOVIE', "Movie", "Video clip file", 'FILE_MOVIE', 0),
            ('IMAGE', "Image", "Still image or sequence", 'FILE_IMAGE', 1),
            ('SOUND', "Sound", "Audio file", 'FILE_SOUND', 2),
            ('SCENE', "Scene", "Blender Scene datablock", 'SCENE_DATA', 3),
        ],
        default='MOVIE',
    )
    in_frame: IntProperty(name="In Point", default=1, min=0)
    out_frame: IntProperty(name="Out Point", default=250, min=0)
    bin_name: StringProperty(name="Bin", default="Footage")


class MediaBinCategory(PropertyGroup):
    name: StringProperty(name="Bin Name", default="Footage")


class SequencerMediaManagement(PropertyGroup):
    active_bin_index: IntProperty(name="Active Bin", default=0)
    bins: CollectionProperty(type=MediaBinCategory)
    items: CollectionProperty(type=MediaBinItem)
    filter_type: EnumProperty(
        name="Filter",
        items=[
            ('ALL', "All Media", "Show all items"),
            ('MOVIE', "Movies", "Show only movie files"),
            ('IMAGE', "Images", "Show only image files"),
            ('SOUND', "Sounds", "Show only sound files"),
            ('SCENE', "Scenes", "Show only scenes"),
        ],
        default='ALL',
    )


def init_default_media_bins(mgmt):
    if len(mgmt.bins) == 0:
        for bname in ["Footage", "Audio", "VFX", "Graphics", "Scenes"]:
            b = mgmt.bins.add()
            b.name = bname


class SEQUENCER_OT_media_bin_add_bin(Operator):
    bl_idname = "sequencer.media_bin_add_bin"
    bl_label = "Add Media Bin"
    bl_description = "Create a new media bin folder"
    bl_options = {'REGISTER', 'UNDO'}

    bin_name: StringProperty(name="Name", default="New Bin")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        self.layout.prop(self, "bin_name")

    def execute(self, context):
        mgmt = context.scene.media_management
        b = mgmt.bins.add()
        b.name = self.bin_name
        self.report({'INFO'}, f"Created media bin '{self.bin_name}'")
        return {'FINISHED'}


class SEQUENCER_OT_media_strip_add(Operator):
    bl_idname = "sequencer.media_strip_add"
    bl_label = "Media..."
    bl_description = "Add media (video, audio, or image sequence) to the timeline and media bin"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: StringProperty(subtype='FILE_PATH')

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not self.filepath:
            return {'CANCELLED'}

        fp = bpy.path.abspath(self.filepath)
        ext = os.path.splitext(fp)[1].lower()
        frame = context.scene.frame_current

        seq_type = 'MOVIE'
        if ext in {'.mp3', '.wav', '.ogg', '.flac', '.aac', '.m4a'}:
            seq_type = 'SOUND'
            bpy.ops.sequencer.sound_strip_add(filepath=fp, frame_start=frame)
        elif ext in {'.png', '.jpg', '.jpeg', '.exr', '.tiff', '.bmp', '.tga'}:
            seq_type = 'IMAGE'
            bpy.ops.sequencer.image_strip_add(directory=os.path.dirname(fp), files=[{"name": os.path.basename(fp)}], frame_start=frame)
        else:
            seq_type = 'MOVIE'
            bpy.ops.sequencer.movie_strip_add(filepath=fp, frame_start=frame)

        # Register into active media bin
        mgmt = context.scene.media_management
        init_default_media_bins(mgmt)
        item = mgmt.items.add()
        item.name = os.path.basename(fp)
        item.filepath = fp
        item.media_type = seq_type
        if mgmt.bins and mgmt.active_bin_index < len(mgmt.bins):
            item.bin_name = mgmt.bins[mgmt.active_bin_index].name

        self.report({'INFO'}, f"Added media: {item.name}")
        return {'FINISHED'}


class SEQUENCER_UL_media_bins(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.prop(item, "name", text="", emboss=False, icon='FILE_FOLDER')


class SEQUENCER_UL_media_items(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        icon_map = {
            'MOVIE': 'FILE_MOVIE',
            'IMAGE': 'FILE_IMAGE',
            'SOUND': 'FILE_SOUND',
            'SCENE': 'SCENE_DATA',
        }
        ic = icon_map.get(item.media_type, 'FILE_BLANK')
        row = layout.row(align=True)
        row.label(text=item.name, icon=ic)
        row.label(text=f"[{item.in_frame}-{item.out_frame}]")


class SEQUENCER_PT_media_bin(Panel):
    bl_label = "Media Bin"
    bl_space_type = 'SEQUENCE_EDITOR'
    bl_region_type = 'UI'
    bl_category = "Media Bin"

    def draw(self, context):
        layout = self.layout
        mgmt = context.scene.media_management
        init_default_media_bins(mgmt)

        # Top Bar: Add Media Button
        row = layout.row(align=True)
        row.operator("sequencer.media_strip_add", text="Import Media...", icon='IMPORT')
        row.operator("sequencer.media_bin_add_bin", text="New Bin", icon='FOLDER_REDIRECT')

        layout.separator()

        # Bins Tree / List
        layout.label(text="Media Bins:", icon='LIBRARY_DATA_DIRECT')
        layout.template_list(
            "SEQUENCER_UL_media_bins", "",
            mgmt, "bins",
            mgmt, "active_bin_index",
            rows=3,
        )

        layout.separator()

        # Filter row
        layout.prop(mgmt, "filter_type", text="Filter")

        # Media Items List for selected bin
        active_bin_name = mgmt.bins[mgmt.active_bin_index].name if mgmt.bins else ""
        layout.label(text=f"Contents ({active_bin_name}):", icon='FILE_FOLDER')

        col = layout.column(align=True)
        for item in mgmt.items:
            if item.bin_name == active_bin_name or not active_bin_name:
                if mgmt.filter_type == 'ALL' or mgmt.filter_type == item.media_type:
                    box = col.box()
                    r = box.row(align=True)
                    r.label(text=item.name, icon='FILE_MOVIE' if item.media_type == 'MOVIE' else 'FILE_IMAGE')
                    r.prop(item, "in_frame", text="In")
                    r.prop(item, "out_frame", text="Out")


classes = (
    MediaBinItem,
    MediaBinCategory,
    SequencerMediaManagement,
    SEQUENCER_OT_media_bin_add_bin,
    SEQUENCER_OT_media_strip_add,
    SEQUENCER_UL_media_bins,
    SEQUENCER_UL_media_items,
    SEQUENCER_PT_media_bin,
)


def register_props():
    bpy.types.Scene.media_management = PointerProperty(type=SequencerMediaManagement)


def unregister_props():
    if hasattr(bpy.types.Scene, "media_management"):
        del bpy.types.Scene.media_management


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    register_props()


def unregister():
    unregister_props()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
