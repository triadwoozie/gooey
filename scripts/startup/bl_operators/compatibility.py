# SPDX-FileCopyrightText: 2026 Gooey Authors
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import annotations

import os
import struct
import bpy
from bpy.types import Operator
from bpy.props import StringProperty, BoolProperty


def decode_blend_file_header(filepath: str) -> dict | None:
    """Reads and decodes the .blend file header for both legacy (v0) and modern (v1) formats."""
    if not os.path.isfile(filepath):
        return None

    try:
        with open(filepath, "rb") as f:
            magic = f.read(4)
            if magic == b"\x28\xb5\x2f\xfd":
                try:
                    import zstandard as zstd
                    f.seek(0)
                    dctx = zstd.ZstdDecompressor()
                    with dctx.stream_reader(f) as reader:
                        header_bytes = reader.read(64)
                except Exception:
                    return None
            else:
                f.seek(0)
                header_bytes = f.read(64)

            if len(header_bytes) < 12 or not header_bytes.startswith(b"BLENDER"):
                return None

            # Legacy header: BLENDER-v450 or BLENDER_v450
            if header_bytes[7:8] in (b"_", b"-"):
                ptr_size = 8 if header_bytes[7:8] == b"-" else 4
                endian = "<" if header_bytes[8:9] == b"v" else ">"
                v_str = header_bytes[9:12].decode("ascii", errors="replace")
                try:
                    v_int = int(v_str)
                except ValueError:
                    v_int = 0
                return {
                    "header_format": 0,
                    "pointer_size": ptr_size,
                    "endian": endian,
                    "version_int": v_int,
                    "version_major": v_int // 100,
                    "version_minor": v_int % 100,
                    "version_string": f"{v_int // 100}.{v_int % 100}",
                    "is_newer": v_int > 450,
                }

            # Modern header (Blender 5.0+): BLENDER17-01v0503
            if header_bytes[7:9].isdigit():
                header_size = int(header_bytes[7:9])
                ptr_size = 8 if header_bytes[9:10] == b"-" else 4
                format_ver = int(header_bytes[10:12]) if header_bytes[10:12].isdigit() else 1
                endian = "<" if header_bytes[12:13] == b"v" else ">"
                v_str = header_bytes[13:17].decode("ascii", errors="replace")
                try:
                    v_int = int(v_str)
                except ValueError:
                    v_int = 500
                return {
                    "header_format": format_ver,
                    "pointer_size": ptr_size,
                    "endian": endian,
                    "version_int": v_int,
                    "version_major": v_int // 100,
                    "version_minor": v_int % 100,
                    "version_string": f"{v_int // 100}.{v_int % 100}",
                    "is_newer": v_int > 450,
                }
    except Exception as ex:
        print(f"[Compatibility Audit] Error reading {filepath}: {ex}")
        return None

    return None


def run_full_compatibility_scan(filepath: str = "") -> dict:
    """Scans the current blend data or given file and builds an exhaustive compatibility report."""
    active_path = filepath if filepath else bpy.data.filepath
    header_info = decode_blend_file_header(active_path) if (active_path and os.path.isfile(active_path)) else None

    # Determine originating version
    if header_info:
        v_major = header_info["version_major"]
        v_minor = header_info["version_minor"]
        v_str = header_info["version_string"]
        is_newer = header_info["is_newer"]
    elif not active_path or not bpy.data.is_saved:
        # A new / unsaved project created in Gooey
        app_ver = getattr(bpy.app, "version", (4, 5, 14))
        v_major = app_ver[0]
        v_minor = app_ver[1]
        v_str = f"{v_major}.{v_minor}"
        is_newer = False
    else:
        # Fallback to bpy.data.version
        data_ver = getattr(bpy.data, "version", (4, 5, 0))
        v_major = data_ver[0]
        v_minor = data_ver[1] if len(data_ver) > 1 else 0
        v_str = f"{v_major}.{v_minor}"
        is_newer = (v_major > 4) or (v_major == 4 and v_minor > 5)

    # 1. Scan for NodeUndefined (nodes from 5.x that cannot load in 4.5)
    undefined_nodes = []
    trees_to_scan = list(bpy.data.node_groups)
    for mat in bpy.data.materials:
        if mat.use_nodes and mat.node_tree:
            trees_to_scan.append(mat.node_tree)
    for world in bpy.data.worlds:
        if world.use_nodes and world.node_tree:
            trees_to_scan.append(world.node_tree)
    for scene in bpy.data.scenes:
        if scene.use_nodes and scene.node_tree:
            trees_to_scan.append(scene.node_tree)

    for tree in trees_to_scan:
        for node in tree.nodes:
            if node.bl_idname == "NodeUndefined" or getattr(node, "type", "") == "UNDEFINED":
                undefined_nodes.append({
                    "tree": tree.name,
                    "node": node.name,
                    "label": getattr(node, "label", ""),
                })

    # 2. Scan for 3D Gaussian Splats / PointCloud attributes
    gaussian_splat_pcs = []
    for pc in bpy.data.pointclouds:
        has_splat = False
        splat_attrs = []
        for attr in pc.attributes:
            if attr.name in {"scale", "rotation", "radiance:base"} or attr.name.startswith("radiance:sh_"):
                has_splat = True
                splat_attrs.append(attr.name)
        if has_splat:
            gaussian_splat_pcs.append({"name": pc.name, "attributes": splat_attrs})

    # 3. Known features lost in Upstream 5.0 - 5.3 when opened & saved in Gooey (4.5)
    upstream_lost_features = [
        {
            "category": "Render Engines (EEVEE-Next & Cycles)",
            "impact": "CRITICAL" if is_newer else "INFORMATIONAL",
            "items": [
                "EEVEE-Next Material Lighting Nodes (Light Accumulation, Light Info, Light Evaluation, Shadow Raycast)",
                "Cycles Transmission Dispersion & Transmission Roughness Anisotropy (Glass/Principled BSDF)",
                "Panoramic Camera modes (Equirectangular, Cubemap, Fisheye Polynomial, Cylindrical)",
                "Sky Texture Sun Disc direct lighting contribution",
                "NVIDIA DLSS Viewport Denoiser configuration (falls back to OpenImageDenoise)",
            ],
        },
        {
            "category": "Compositor",
            "impact": "HIGH" if is_newer else "INFORMATIONAL",
            "items": [
                "Layered Effects Stack architecture (reverts to flat node group evaluation)",
                "Interactive compositor playback caching metadata",
                "Bundle nodes (Get/Store Bundle Item, Combine/Separate Bundle)",
                "Compositor Math & Utility nodes (Matrix SVD, Float to Int, Bone Info, Import Text)",
            ],
        },
        {
            "category": "Geometry Nodes & 3D Gaussian Splats",
            "impact": "HIGH" if (is_newer or gaussian_splat_pcs) else "INFORMATIONAL",
            "items": [
                "3D Gaussian Splats: Native ellipsoid radiance & spherical harmonics attributes (scale, rotation, radiance:sh_*)",
                "Volume Grid nodes (Rasterize Points, Poisson Solver, Grid Topology Boolean, Deactivate Voxels)",
                "Curve nodes (Miter Scale on Curve to Mesh, Construct Curves topology node)",
                "Built-in NURBS attribute nodes (Order, Weight)",
                "List utility nodes (Combine List, Get Vector Component)",
            ],
        },
        {
            "category": "Sequencer (VSE)",
            "impact": "MEDIUM" if is_newer else "INFORMATIONAL",
            "items": [
                "Ripple Editing framework modes (Shuffle, Ripple, Overwrite)",
                "OpenTimelineIO (OTIO) metadata tracks and import/export configurations",
                "Unclamped handles and custom slipping states (revert to legacy clamp)",
                "Timeline strip middle thumbnails (Text, Mask, MovieClip)",
            ],
        },
        {
            "category": "Grease Pencil (GPv3)",
            "impact": "MEDIUM" if is_newer else "INFORMATIONAL",
            "items": [
                "Carver Tool boolean subtraction cuts and multi-region fills",
                "Generic gradient system for GP materials and cyclic stroke format",
                "Camera view roll and flip metadata",
            ],
        },
        {
            "category": "Sculpt & Paint",
            "impact": "LOW-MEDIUM" if is_newer else "INFORMATIONAL",
            "items": [
                "Automasking settings in Vertex Paint & Weight Paint (Cavity, Topology, View Normal, Area Normal)",
                "Custom defined curves for brush Hardness, Auto-Smooth, and Spacing",
                "Multires Generic Attributes display in Sculpt Mode",
            ],
        },
        {
            "category": "Core DNA & Project Serialization",
            "impact": "CRITICAL" if is_newer else "SAFE",
            "items": [
                ".blender_project configuration and per-project OCIO ACES 2.0 blocks",
                "5.x DNA 64-bit aligned structures stripped; file rewritten in 4.5 DNA format (format version 0)",
                "Bitwise .blend file diff preservation disabled",
            ],
        },
    ]

    risk_level = "CRITICAL" if (is_newer and (undefined_nodes or gaussian_splat_pcs)) else (
        "HIGH" if is_newer else "SAFE"
    )

    return {
        "filepath": active_path or "<Unsaved File>",
        "originating_version": f"Blender {v_str}",
        "is_newer": is_newer,
        "risk_level": risk_level,
        "undefined_nodes": undefined_nodes,
        "gaussian_splats": gaussian_splat_pcs,
        "loss_categories": upstream_lost_features,
    }


class WM_OT_compatibility_audit(Operator):
    """Inspect and report what data or upstream features would be lost if opened and saved in Gooey"""
    bl_idname = "wm.compatibility_audit"
    bl_label = "Check Upstream Compatibility"
    bl_options = {'REGISTER'}

    filepath: StringProperty(
        name="File Path",
        description="Path to .blend file to audit (leave empty to audit active project)",
        default="",
        subtype='FILE_PATH',
    )
    save_log: BoolProperty(
        name="Save Report to File",
        description="Write the detailed diagnostic report to a text file next to the project",
        default=False,
    )

    def execute(self, context):
        report_data = run_full_compatibility_scan(self.filepath)

        # Print detailed report to console
        print("\n" + "=" * 70)
        print(" GOOEY UPSTREAM BLENDER COMPATIBILITY AUDIT REPORT")
        print("=" * 70)
        print(f" Target File:         {report_data['filepath']}")
        print(f" Originating Version: {report_data['originating_version']}")
        print(f" Current Environment: Gooey 4.5.14 LTS (Blender 4.5.93 DNA)")
        print(f" Overall Risk Level:  {report_data['risk_level']}")
        print("-" * 70)

        if report_data["undefined_nodes"]:
            print(f" [!] UNDEFINED / UNRECOGNIZED NODES DETECTED ({len(report_data['undefined_nodes'])}):")
            for item in report_data["undefined_nodes"]:
                print(f"     - Tree: '{item['tree']}' -> Node: '{item['node']}' ({item['label']})")
        else:
            print(" [i] No undefined nodes currently loaded in active nodetrees.")

        if report_data["gaussian_splats"]:
            print(f" [!] 3D GAUSSIAN SPLATS DETECTED ({len(report_data['gaussian_splats'])}):")
            for item in report_data["gaussian_splats"]:
                print(f"     - PointCloud: '{item['name']}' (Attributes: {', '.join(item['attributes'])})")
        else:
            print(" [i] No 3D Gaussian Splats detected in pointclouds.")

        print("-" * 70)
        print(" UPSTREAM FEATURES THAT WILL NOT BE PRESERVED UPON SAVING:")
        for cat in report_data["loss_categories"]:
            print(f"\n * {cat['category']} [{cat['impact']}]:")
            for feat in cat["items"]:
                print(f"     - {feat}")

        print("=" * 70 + "\n")

        # Save to file if requested
        if self.save_log or not bpy.app.background:
            target_dir = os.path.dirname(report_data["filepath"]) if os.path.isfile(report_data["filepath"]) else os.path.expanduser("~")
            log_path = os.path.join(target_dir, "gooey_compatibility_report.txt")
            try:
                with open(log_path, "w", encoding="utf-8") as f:
                    f.write("GOOEY UPSTREAM BLENDER COMPATIBILITY AUDIT REPORT\n")
                    f.write("=" * 70 + "\n")
                    f.write(f"Target File:         {report_data['filepath']}\n")
                    f.write(f"Originating Version: {report_data['originating_version']}\n")
                    f.write(f"Current Environment: Gooey 4.5.14 LTS\n")
                    f.write(f"Overall Risk Level:  {report_data['risk_level']}\n")
                    f.write("-" * 70 + "\n")
                    if report_data["undefined_nodes"]:
                        f.write(f"UNDEFINED NODES ({len(report_data['undefined_nodes'])}):\n")
                        for item in report_data["undefined_nodes"]:
                            f.write(f"  - Tree: '{item['tree']}' | Node: '{item['node']}' ({item['label']})\n")
                    if report_data["gaussian_splats"]:
                        f.write(f"3D GAUSSIAN SPLATS ({len(report_data['gaussian_splats'])}):\n")
                        for item in report_data["gaussian_splats"]:
                            f.write(f"  - PointCloud: '{item['name']}' | Attrs: {', '.join(item['attributes'])}\n")
                    f.write("\nUPSTREAM FEATURES NOT PRESERVED:\n")
                    for cat in report_data["loss_categories"]:
                        f.write(f"\n[{cat['category']} - {cat['impact']}]\n")
                        for feat in cat["items"]:
                            f.write(f"  - {feat}\n")
                self.report({'INFO'}, f"Compatibility report written to {log_path}")
            except Exception as e:
                print(f"[Compatibility Audit] Could not save log file: {e}")

        status_msg = f"{report_data['originating_version']}: Risk {report_data['risk_level']}. Check system console for full audit."
        self.report({'WARNING' if report_data['is_newer'] else 'INFO'}, status_msg)
        return {'FINISHED'}

    def invoke(self, context, _event):
        return context.window_manager.invoke_props_dialog(self, width=540)

    def draw(self, context):
        layout = self.layout
        report_data = run_full_compatibility_scan(self.filepath)

        col = layout.column(align=False)
        box = col.box()
        b_col = box.column(align=True)
        b_col.label(text=f"Target File: {os.path.basename(report_data['filepath'])}", icon='FILE_BLEND')
        b_col.label(text=f"Originating Version: {report_data['originating_version']}", icon='INFO')
        b_col.label(text=f"Gooey Environment: 4.5.14 LTS (Blender 4.5.93 DNA)", icon='BLENDER')

        risk = report_data["risk_level"]
        icon = 'CANCEL' if risk == 'CRITICAL' else ('ERROR' if risk == 'HIGH' else 'CHECKMARK')
        b_col.separator()
        b_col.label(text=f"Overall Degradation Risk: {risk}", icon=icon)

        if report_data["undefined_nodes"]:
            layout.separator()
            u_box = layout.box()
            u_box.label(text=f"Undefined / Missing Nodes Detected ({len(report_data['undefined_nodes'])}):", icon='ERROR')
            for item in report_data["undefined_nodes"][:5]:
                u_box.label(text=f"  • {item['tree']} -> {item['node']}")
            if len(report_data["undefined_nodes"]) > 5:
                u_box.label(text=f"  ... and {len(report_data['undefined_nodes']) - 5} more in console")

        if report_data["gaussian_splats"]:
            layout.separator()
            g_box = layout.box()
            g_box.label(text=f"3D Gaussian Splats Detected ({len(report_data['gaussian_splats'])}):", icon='POINTCLOUD_DATA')
            for item in report_data["gaussian_splats"]:
                g_box.label(text=f"  • {item['name']} (Radiance SH attributes)")

        layout.separator()
        layout.label(text="Upstream Features Subject to Degradation Upon Save:", icon='WARN')

        for cat in report_data["loss_categories"]:
            cat_box = layout.box()
            cat_box.label(text=f"{cat['category']} ({cat['impact']})", icon='DOT')
            for feat in cat["items"]:
                cat_box.label(text=f"  • {feat}")

        layout.separator()
        layout.prop(self, "save_log", text="Save Detailed Report to Disk")


classes = (
    WM_OT_compatibility_audit,
)
