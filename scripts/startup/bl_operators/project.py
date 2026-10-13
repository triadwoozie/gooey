# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-License-Identifier: GPL-2.0-or-later

import os
import bpy
from bpy.types import Operator
from bpy.props import StringProperty
from bpy_extras.io_utils import ImportHelper

import blender_project


class PROJECT_OT_setup(Operator):
    bl_idname = "project.setup"
    bl_label = "Setup Project"
    bl_description = "Setup a new .blender_project environment in a directory"
    bl_options = {'REGISTER', 'UNDO'}

    directory: StringProperty(
        name="Project Directory",
        subtype='DIR_PATH',
        default="//",
    )

    def invoke(self, context, event):
        if bpy.data.filepath:
            self.directory = os.path.dirname(os.path.abspath(bpy.data.filepath))
        else:
            self.directory = os.getcwd()
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "directory")

    def execute(self, context):
        proj_dir = os.path.abspath(bpy.path.abspath(self.directory))
        os.makedirs(proj_dir, exist_ok=True)
        dot_proj = os.path.join(proj_dir, ".blender_project")
        os.makedirs(dot_proj, exist_ok=True)

        settings_path = os.path.join(dot_proj, "settings.toml")
        variables_path = os.path.join(dot_proj, "variables.toml")

        if not os.path.exists(settings_path):
            with open(settings_path, "w", encoding="utf-8") as f:
                f.write(
                    "# Blender Project Settings\n"
                    f'name = "{os.path.basename(proj_dir)}"\n'
                    'version = "1.0"\n'
                    'fps = 24\n'
                )

        if not os.path.exists(variables_path):
            with open(variables_path, "w", encoding="utf-8") as f:
                f.write(
                    "# Blender Project Variables\n"
                    '[render_output]\n'
                    'value = "//render/{blend_name}/"\n'
                    'description = "Default render output directory"\n'
                    'type = "STRING"\n'
                    'subtype = "DIR_PATH"\n\n'
                    '[shot]\n'
                    'value = "010"\n'
                    'description = "Current shot identifier"\n'
                )

        self.report({'INFO'}, f"Project initialized at '{proj_dir}'")
        return {'FINISHED'}


class PROJECT_OT_resolve_render_path(Operator):
    bl_idname = "project.resolve_render_path"
    bl_label = "Resolve Render Path Variables"
    bl_description = "Evaluate {project_root}, {blend_name}, {fps}, etc. in scene render filepath"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        old_path = context.scene.render.filepath
        new_path = blender_project.resolve_path_templates(old_path, context)
        context.scene.render.filepath = new_path
        self.report({'INFO'}, f"Evaluated path: {new_path}")
        return {'FINISHED'}


class PROJECT_OT_open_settings(Operator):
    bl_idname = "project.open_settings"
    bl_label = "Open Project Settings"
    bl_description = "Open project configuration files in Blender Text Editor"

    def execute(self, context):
        proj = blender_project.get_current_project()
        if not proj.is_valid:
            self.report({'WARNING'}, "No active project found in parent directories")
            return {'CANCELLED'}

        dot_proj = os.path.join(proj.root, ".blender_project")
        for fn in ["settings.toml", "variables.toml"]:
            fp = os.path.join(dot_proj, fn)
            if os.path.exists(fp):
                name = f"project_{fn}"
                txt = bpy.data.texts.get(name) or bpy.data.texts.new(name)
                with open(fp, "r", encoding="utf-8") as f:
                    txt.from_string(f.read())

        self.report({'INFO'}, f"Opened settings for '{proj.name}'")
        return {'FINISHED'}


classes = (
    PROJECT_OT_setup,
    PROJECT_OT_resolve_render_path,
    PROJECT_OT_open_settings,
)


def register():
    blender_project.init_context_project()
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
