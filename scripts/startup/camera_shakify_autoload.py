# SPDX-FileCopyrightText: 2026 Blender Authors
# SPDX-License-Identifier: GPL-2.0-or-later

import bpy
import addon_utils
from bpy.app.handlers import persistent

def _enable_shakify():
    try:
        loaded, enabled = addon_utils.check("camera_shakify")
        if not enabled:
            addon_utils.enable("camera_shakify", default_set=False)
    except Exception as e:
        print(f"[Camera Shakify Autoload] Error: {e}")

_enable_shakify()

@persistent
def _ensure_shakify_on_load(scene):
    _enable_shakify()

if _ensure_shakify_on_load not in bpy.app.handlers.load_post:
    bpy.app.handlers.load_post.append(_ensure_shakify_on_load)
