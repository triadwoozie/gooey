# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-License-Identifier: GPL-2.0-or-later

"""
Project Environment & Awareness (.blender_project)
Provides project-wide variables, settings, and path template resolution.
"""

import os
import re
import bpy

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore


class ProjectVariable:
    def __init__(self, name="", value="", description="", var_type="STRING", subtype="NONE"):
        self.name = name
        self.value = value
        self.description = description
        self.type = var_type
        self.subtype = subtype

    def __repr__(self):
        return f"<ProjectVariable '{self.name}': '{self.value}'>"


class BlenderProject:
    def __init__(self, root_dir=None):
        self.root = root_dir
        self.name = os.path.basename(root_dir) if root_dir else ""
        self.variables = []
        self.settings = {}
        self.is_valid = bool(root_dir and os.path.isdir(os.path.join(root_dir, ".blender_project")))
        if self.is_valid:
            self.reload()

    def reload(self):
        if not self.root:
            return
        proj_dir = os.path.join(self.root, ".blender_project")
        settings_file = os.path.join(proj_dir, "settings.toml")
        variables_file = os.path.join(proj_dir, "variables.toml")

        self.variables = []
        self.settings = {}

        if os.path.isfile(settings_file):
            try:
                with open(settings_file, "rb") as f:
                    self.settings = tomllib.load(f)
            except Exception as e:
                print(f"[Project] Error reading settings.toml: {e}")

        if os.path.isfile(variables_file):
            try:
                with open(variables_file, "rb") as f:
                    raw_vars = tomllib.load(f)
                    for k, v in raw_vars.items():
                        if isinstance(v, dict):
                            val = str(v.get("value", ""))
                            desc = str(v.get("description", ""))
                            v_type = str(v.get("type", "STRING"))
                            subtype = str(v.get("subtype", "NONE"))
                        else:
                            val = str(v)
                            desc = ""
                            v_type = "STRING"
                            subtype = "NONE"
                        self.variables.append(ProjectVariable(k, val, desc, v_type, subtype))
            except Exception as e:
                print(f"[Project] Error reading variables.toml: {e}")

    def get_var(self, name, default=""):
        for var in self.variables:
            if var.name == name:
                return var.value
        return default

    def __repr__(self):
        if self.is_valid:
            return f"<BlenderProject '{self.name}' at '{self.root}'>"
        return "<BlenderProject None>"


def find_project_root(start_path=None):
    if not start_path:
        start_path = bpy.data.filepath
    if not start_path:
        start_path = os.getcwd()
    else:
        start_path = os.path.abspath(start_path)
        if os.path.isfile(start_path):
            start_path = os.path.dirname(start_path)

    curr = start_path
    while curr:
        candidate = os.path.join(curr, ".blender_project")
        if os.path.isdir(candidate):
            return curr
        parent = os.path.dirname(curr)
        if parent == curr:
            break
        curr = parent
    return None


def get_current_project():
    root = find_project_root()
    return BlenderProject(root)


def resolve_path_templates(template_str, context=None):
    if not template_str or not isinstance(template_str, str):
        return template_str

    if context is None:
        context = bpy.context

    project = get_current_project()
    proj_root = project.root if project.is_valid else ""
    proj_name = project.name if project.is_valid else ""

    blend_filepath = bpy.data.filepath
    blend_name = os.path.splitext(os.path.basename(blend_filepath))[0] if blend_filepath else "untitled"

    scene = getattr(context, "scene", None)
    if scene and hasattr(scene, "render"):
        fps = str(scene.render.fps)
        res_x = str(scene.render.resolution_x)
        res_y = str(scene.render.resolution_y)
    else:
        fps = "24"
        res_x = "1920"
        res_y = "1080"

    mapping = {
        "project_root": proj_root,
        "project_name": proj_name,
        "blend_name": blend_name,
        "fps": fps,
        "resolution_x": res_x,
        "resolution_y": res_y,
    }

    if project.is_valid:
        for var in project.variables:
            mapping[var.name] = str(var.value)

    res = template_str
    for key, val in mapping.items():
        res = res.replace("{" + key + "}", val)

    return res


def init_context_project():
    try:
        if not hasattr(bpy.types.Context, "project"):
            bpy.types.Context.project = property(lambda self: get_current_project())
    except Exception as e:
        print(f"[Project] Context property registration: {e}")
