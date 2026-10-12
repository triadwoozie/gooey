# SPDX-FileCopyrightText: 2026 Blender Authors
#
# SPDX-License-Identifier: GPL-2.0-or-later

import bpy

CAMERA_SHAKE_PROFILES = [
    ("0", "Investigation", "Handheld slow search, subtle breathing drifts"),
    ("1", "The Closeup", "Micro-tremor, subtle handheld tension"),
    ("2", "The Wedding", "Medium casual shoulder mount, organic sway"),
    ("3", "Walk To The Store", "Walking cadence with rhythmic vertical footstep dip"),
    ("4", "Handycam Run", "Urgent running cadence with heavy footfall recoil"),
    ("5", "Out Car Window", "Driving speed vibration, road bumps, high engine rev"),
    ("6", "Bike On Gravel", "High frequency gravel chatter and rapid handlebar rattle"),
    ("7", "Spaceship Shake", "Deep low frequency hull drone and structural groaning"),
    ("8", "The Zeek", "Erratic sudden impacts, weapon recoil, shockwave"),
]


def get_camera_shake_node(obj):
    """Find the GeometryNodeCameraShake node in the object's modifier stack"""
    if not obj or obj.type != 'MESH':
        return None
    for mod in obj.modifiers:
        if mod.type == 'NODES' and mod.node_group:
            for node in mod.node_group.nodes:
                if node.bl_idname == 'GeometryNodeCameraShake':
                    return node
    return None


def bind_camera_to_rig(rig_obj, camera):
    """Bind a camera to a regular mesh shaker rig using tested Camera Shakify constraints"""
    if not rig_obj or not camera or camera.type != 'CAMERA':
        return False

    # Ensure ShakePin vertex group on rig_obj
    if "ShakePin" not in rig_obj.vertex_groups:
        vg = rig_obj.vertex_groups.new(name="ShakePin")
        vg.add([0], 1.0, 'REPLACE')

    # Remove existing CameraShakify_GN constraints on this camera
    for c in list(camera.constraints):
        if c.name.startswith("CameraShakify_GN"):
            camera.constraints.remove(c)

    loc_c = camera.constraints.new(type='COPY_LOCATION')
    loc_c.name = "CameraShakify_GN_Loc"
    loc_c.target = rig_obj
    loc_c.subtarget = "ShakePin"
    loc_c.target_space = 'WORLD'
    loc_c.owner_space = 'LOCAL'
    loc_c.use_offset = True

    rot_c = camera.constraints.new(type='COPY_ROTATION')
    rot_c.name = "CameraShakify_GN_Rot"
    rot_c.target = rig_obj
    rot_c.subtarget = "ShakePin"
    rot_c.target_space = 'WORLD'
    rot_c.owner_space = 'LOCAL'
    rot_c.mix_mode = 'AFTER'

    return True


def unbind_camera_from_rig(camera):
    """Remove Camera Shakify Geometry Nodes constraints from camera"""
    if not camera:
        return
    for c in list(camera.constraints):
        if c.name.startswith("CameraShakify_GN"):
            camera.constraints.remove(c)


def on_shake_target_camera_update(self, context):
    """Callback when user selects a target camera from the shaker object dropdown"""
    if self.camera_shake_target_camera:
        bind_camera_to_rig(self, self.camera_shake_target_camera)


def on_shake_profile_update(self, context):
    """Callback when user changes the shake profile on the shaker object"""
    shake_node = get_camera_shake_node(self)
    if shake_node and 'Profile' in shake_node.inputs:
        shake_node.inputs['Profile'].default_value = int(self.camera_shake_profile)


class CameraShakeAddGeoNodesRig(bpy.types.Operator):
    """Create a regular mesh shaker rig with Geometry Nodes Camera Shake controlling the target camera"""
    bl_idname = "object.camera_shake_add_geonodes_rig"
    bl_label = "Create Geometry Nodes Camera Rig"
    bl_options = {'REGISTER', 'UNDO'}

    target_camera: bpy.props.StringProperty(
        name="Target Camera",
        description="Name of camera to be controlled by the Geometry Nodes rig",
        default=""
    )
    profile: bpy.props.EnumProperty(
        name="Profile",
        description="Motion capture shake profile",
        items=CAMERA_SHAKE_PROFILES,
        default="0"
    )

    def draw(self, context):
        layout = self.layout
        layout.prop_search(self, "target_camera", context.scene, "objects", text="Target Camera")
        layout.prop(self, "profile")

    def invoke(self, context, event):
        if not self.target_camera:
            if context.active_object and context.active_object.type == 'CAMERA':
                self.target_camera = context.active_object.name
            elif context.scene.camera:
                self.target_camera = context.scene.camera.name
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        camera = None
        if self.target_camera and self.target_camera in bpy.data.objects:
            camera = bpy.data.objects[self.target_camera]
        elif context.active_object and context.active_object.type == 'CAMERA':
            camera = context.active_object
        elif context.scene.camera:
            camera = context.scene.camera

        if not camera or camera.type != 'CAMERA':
            self.report({'ERROR'}, "Please select a valid camera!")
            return {'CANCELLED'}

        # Create regular mesh object placed at origin (0,0,0) with 3-vertex polygon
        mesh_name = f"{camera.name}_ShakeRig_Mesh"
        mesh = bpy.data.meshes.new(mesh_name)
        verts = [(0.0, 0.0, 0.0), (0.05, 0.0, 0.0), (0.0, 0.05, 0.0)]
        faces = [(0, 1, 2)]
        mesh.from_pydata(verts, [], faces)
        mesh.update()

        rig_obj_name = f"{camera.name}_ShakeRig"
        rig_obj = bpy.data.objects.new(rig_obj_name, mesh)
        context.scene.collection.objects.link(rig_obj)
        rig_obj.location = (0.0, 0.0, 0.0)
        rig_obj.rotation_euler = (0.0, 0.0, 0.0)

        vg = rig_obj.vertex_groups.new(name="ShakePin")
        vg.add([0], 1.0, 'REPLACE')

        # Add Geometry Nodes modifier
        mod = rig_obj.modifiers.new(name="CameraShake_Nodes", type='NODES')
        tree_name = f"{camera.name}_ShakeTree"
        ntree = bpy.data.node_groups.new(name=tree_name, type='GeometryNodeTree')
        mod.node_group = ntree

        # Node Group Interface
        in_node = ntree.nodes.new(type='NodeGroupInput')
        in_node.location = (-300, 0)
        out_node = ntree.nodes.new(type='NodeGroupOutput')
        out_node.location = (300, 0)
        ntree.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
        ntree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')

        # Add native GeometryNodeCameraShake
        shake_node = ntree.nodes.new(type='GeometryNodeCameraShake')
        shake_node.location = (0, 0)
        shake_node.inputs['Profile'].default_value = int(self.profile)

        # Add Scene Time node to drive evaluation across frames
        time_node = ntree.nodes.new(type='GeometryNodeInputSceneTime')
        time_node.location = (-300, -120)

        # Connect geometry and time (Do NOT connect Camera socket to avoid depsgraph cycles)
        ntree.links.new(in_node.outputs['Geometry'], shake_node.inputs['Geometry'])
        ntree.links.new(shake_node.outputs['Geometry'], out_node.inputs['Geometry'])
        ntree.links.new(time_node.outputs['Seconds'], shake_node.inputs['Time'])

        # Store camera reference and profile on rig object, and bind constraints
        rig_obj.camera_shake_profile = self.profile
        rig_obj.camera_shake_target_camera = camera
        bind_camera_to_rig(rig_obj, camera)

        # Select the newly created rig object
        for o in context.selected_objects:
            o.select_set(False)
        rig_obj.select_set(True)
        context.view_layer.objects.active = rig_obj

        self.report({'INFO'}, f"Created Geometry Nodes shaker rig '{rig_obj.name}' controlling camera '{camera.name}'.")
        return {'FINISHED'}


class CameraShakeBindCamera(bpy.types.Operator):
    """Bind or re-bind the active regular shaker object to a camera using Camera Shakify constraints"""
    bl_idname = "object.camera_shake_bind_camera"
    bl_label = "Bind Camera to Rig"
    bl_options = {'REGISTER', 'UNDO'}

    target_camera: bpy.props.StringProperty(
        name="Target Camera",
        description="Name of camera to bind to this shaker object",
        default=""
    )

    def draw(self, context):
        layout = self.layout
        layout.prop_search(self, "target_camera", context.scene, "objects", text="Target Camera")

    def execute(self, context):
        rig_obj = context.active_object
        if not rig_obj or rig_obj.type != 'MESH':
            self.report({'ERROR'}, "Active object must be a mesh shaker rig!")
            return {'CANCELLED'}

        camera = None
        if self.target_camera and self.target_camera in bpy.data.objects:
            camera = bpy.data.objects[self.target_camera]
        elif rig_obj.camera_shake_target_camera:
            camera = rig_obj.camera_shake_target_camera
        elif context.scene.camera:
            camera = context.scene.camera
        else:
            self.report({'ERROR'}, "No camera selected or set in scene!")
            return {'CANCELLED'}

        rig_obj.camera_shake_target_camera = camera
        bind_camera_to_rig(rig_obj, camera)

        self.report({'INFO'}, f"Bound camera '{camera.name}' to '{rig_obj.name}'.")
        return {'FINISHED'}


class CameraShakeLinkAudio(bpy.types.Operator):
    """Link Aubio Audio Analysis node to drive Camera Shake triggers and kicks"""
    bl_idname = "object.camera_shake_link_audio"
    bl_label = "Link Audio Source (Aubio)"
    bl_options = {'REGISTER', 'UNDO'}

    speaker: bpy.props.StringProperty(
        name="Speaker Object",
        description="Speaker containing audio source",
        default=""
    )

    def draw(self, context):
        layout = self.layout
        layout.prop_search(self, "speaker", context.scene, "objects", text="Speaker")

    def execute(self, context):
        rig_obj = context.active_object
        shake_node = get_camera_shake_node(rig_obj)
        if not shake_node or not rig_obj:
            self.report({'ERROR'}, "Active object is not a Camera Shake rig!")
            return {'CANCELLED'}

        mod = None
        for m in rig_obj.modifiers:
            if m.type == 'NODES' and m.node_group:
                if shake_node in m.node_group.nodes.values():
                    mod = m
                    break
        if not mod:
            self.report({'ERROR'}, "Modifier not found!")
            return {'CANCELLED'}

        ntree = mod.node_group
        # Find or create Audio Analysis node
        audio_node = None
        for n in ntree.nodes:
            if n.bl_idname == 'GeometryNodeAudioAnalysis':
                audio_node = n
                break
        if not audio_node:
            audio_node = ntree.nodes.new(type='GeometryNodeAudioAnalysis')
            audio_node.location = (shake_node.location.x - 300, shake_node.location.y - 150)

        # Wire time and onset/volume to Audio Trigger
        time_node = None
        for n in ntree.nodes:
            if n.bl_idname == 'GeometryNodeInputSceneTime':
                time_node = n
                break
        if time_node:
            ntree.links.new(time_node.outputs['Seconds'], audio_node.inputs['Time'])

        ntree.links.new(audio_node.outputs['Onset'], shake_node.inputs['Audio Trigger'])

        if self.speaker and self.speaker in bpy.data.objects:
            audio_node.inputs['Speaker'].default_value = bpy.data.objects[self.speaker]

        self.report({'INFO'}, "Successfully linked Aubio Audio Analysis to Camera Shake!")
        return {'FINISHED'}


class AudioMouthShapesAddModifier(bpy.types.Operator):
    """Add an Aubio-driven Mouth Shapes (Visemes) Geometry Nodes modifier to the active mesh"""
    bl_idname = "object.audio_mouth_shapes_add"
    bl_label = "Add Audio Mouth Shapes (Visemes)"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object to apply Mouth Shapes!")
            return {'CANCELLED'}

        mod = obj.modifiers.new(name="Audio_Mouth_Shapes", type='NODES')
        ntree = bpy.data.node_groups.new(name=f"{obj.name}_MouthShapes", type='GeometryNodeTree')
        mod.node_group = ntree

        in_node = ntree.nodes.new(type='NodeGroupInput')
        in_node.location = (-300, 0)
        out_node = ntree.nodes.new(type='NodeGroupOutput')
        out_node.location = (300, 0)
        ntree.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
        ntree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')

        mouth_node = ntree.nodes.new(type='GeometryNodeMouthShapes')
        mouth_node.location = (0, 0)

        time_node = ntree.nodes.new(type='GeometryNodeInputSceneTime')
        time_node.location = (-300, -120)

        ntree.links.new(in_node.outputs['Geometry'], mouth_node.inputs['Geometry'])
        ntree.links.new(mouth_node.outputs['Geometry'], out_node.inputs['Geometry'])
        ntree.links.new(time_node.outputs['Seconds'], mouth_node.inputs['Time'])

        self.report({'INFO'}, f"Added Audio Mouth Shapes modifier to '{obj.name}'.")
        return {'FINISHED'}


class AcousticPropVibrationAddModifier(bpy.types.Operator):
    """Add a physically inferred Acoustic Prop Vibration Geometry Nodes modifier to the active object"""
    bl_idname = "object.acoustic_prop_vibration_add"
    bl_label = "Add Acoustic Prop Vibration"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj:
            self.report({'ERROR'}, "Select an object to apply Prop Vibration!")
            return {'CANCELLED'}

        mod = obj.modifiers.new(name="Acoustic_Prop_Vibration", type='NODES')
        ntree = bpy.data.node_groups.new(name=f"{obj.name}_PropVibration", type='GeometryNodeTree')
        mod.node_group = ntree

        in_node = ntree.nodes.new(type='NodeGroupInput')
        in_node.location = (-300, 0)
        out_node = ntree.nodes.new(type='NodeGroupOutput')
        out_node.location = (300, 0)
        ntree.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
        ntree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')

        vib_node = ntree.nodes.new(type='GeometryNodePropVibration')
        vib_node.location = (0, 0)
        vib_node.inputs['Object'].default_value = obj

        time_node = ntree.nodes.new(type='GeometryNodeInputSceneTime')
        time_node.location = (-300, -120)

        ntree.links.new(in_node.outputs['Geometry'], vib_node.inputs['Geometry'])
        ntree.links.new(vib_node.outputs['Geometry'], out_node.inputs['Geometry'])
        ntree.links.new(time_node.outputs['Seconds'], vib_node.inputs['Time'])

        self.report({'INFO'}, f"Added Acoustic Prop Vibration modifier to '{obj.name}'.")
        return {'FINISHED'}


def get_mouth_shapes_node(obj):
    if not obj or obj.type != 'MESH':
        return None
    for mod in obj.modifiers:
        if mod.type == 'NODES' and mod.node_group:
            for node in mod.node_group.nodes:
                if node.bl_idname == 'GeometryNodeMouthShapes':
                    return node
    return None


def get_prop_vibration_node(obj):
    if not obj:
        return None
    for mod in obj.modifiers:
        if mod.type == 'NODES' and mod.node_group:
            for node in mod.node_group.nodes:
                if node.bl_idname == 'GeometryNodePropVibration':
                    return node
    return None


class OBJECT_PT_mouth_shapes_geonodes(bpy.types.Panel):
    """Audio Mouth Shapes Panel in Object Properties"""
    bl_label = "Mouth Shapes (Audio Visemes)"
    bl_idname = "OBJECT_PT_mouth_shapes_geonodes"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "object"

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        mouth_node = get_mouth_shapes_node(obj)

        if mouth_node:
            box = layout.box()
            box.label(text="Active Aubio Lipsync Deformer", icon='CHECKMARK')
            box.prop(mouth_node.inputs['Speaker'], "default_value", text="Speaker Source")
            col = box.column(align=True)
            col.prop(mouth_node.inputs['Mouth Center'], "default_value", text="Mouth Center")
            col.prop(mouth_node.inputs['Influence Radius'], "default_value", text="Radius")
            col.prop(mouth_node.inputs['Jaw Open Scale'], "default_value", text="Jaw Open Scale ('A')")
            col.prop(mouth_node.inputs['Lip Wide Scale'], "default_value", text="Lip Wide Scale ('E')")
            col.prop(mouth_node.inputs['Lip Round Scale'], "default_value", text="Lip Round Scale ('O')")
            col.prop(mouth_node.inputs['Sensitivity'], "default_value", text="Sensitivity")
            col.prop(mouth_node.inputs['Smoothing'], "default_value", text="Smoothing")
        else:
            layout.operator("object.audio_mouth_shapes_add", text="Add Audio Mouth Shapes", icon='SPEAKER')


class OBJECT_PT_prop_vibration_geonodes(bpy.types.Panel):
    """Acoustic Prop Vibration Panel in Object Properties"""
    bl_label = "Prop Vibration (Physical Acoustics)"
    bl_idname = "OBJECT_PT_prop_vibration_geonodes"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "object"

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        vib_node = get_prop_vibration_node(obj)

        if vib_node:
            box = layout.box()
            box.label(text="Active Acoustic Prop Resonance", icon='CHECKMARK')
            box.prop(vib_node.inputs['Object'], "default_value", text="Sample Object")
            col = box.column(align=True)
            col.prop(vib_node.inputs['Trigger / Impulse'], "default_value", text="Impact Impulse / Trigger")
            col.prop(vib_node.inputs['Frequency'], "default_value", text="Resonant Freq f0 (-1 Auto)")
            col.prop(vib_node.inputs['Damping'], "default_value", text="Damping Decay (-1 Auto)")
            col.prop(vib_node.inputs['Amplitude Scale'], "default_value", text="Amplitude Scale")
            col.prop(vib_node.inputs['Vibration Axis'], "default_value", text="Vibration Axis")
        else:
            layout.operator("object.acoustic_prop_vibration_add", text="Add Acoustic Prop Vibration", icon='FORCE_VORTEX')


class CameraShakeObjectPanel(bpy.types.Panel):
    """Camera Shake Geometry Nodes panel for regular objects in Object Properties"""
    bl_label = "Camera Shake (Geometry Nodes)"
    bl_idname = "OBJECT_PT_camera_shake_geonodes"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "object"

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        shake_node = get_camera_shake_node(obj)

        if shake_node:
            box = layout.box()
            box.label(text="Active Camera Shake Rig", icon='CHECKMARK')

            # Dropdown to choose which camera to control
            box.prop(obj, "camera_shake_target_camera", text="Target Camera")

            col = box.column(align=True)
            col.prop(obj, "camera_shake_profile", text="Profile")
            col.prop(shake_node.inputs['Frequency'], "default_value", text="Frequency / Speed")
            col.prop(shake_node.inputs['Intensity'], "default_value", text="Intensity")
            col.prop(shake_node.inputs['Audio Trigger'], "default_value", text="Audio Trigger (Aubio)")
            col.prop(shake_node.inputs['Seed'], "default_value", text="Random Seed")

            row = box.row()
            row.operator("object.camera_shake_link_audio", text="Link Audio (Aubio)", icon='SPEAKER')
            row.operator("object.camera_shake_bind_camera", text="Re-Bind Target Camera", icon='CONSTRAINT')
        else:
            layout.operator("object.camera_shake_add_geonodes_rig", text="Setup as Camera Shake Rig", icon='NODETREE')


class CAMERA_PT_camera_shake_rig(bpy.types.Panel):
    """Camera Shake panel inside Camera Data Properties"""
    bl_label = "Camera Shake (Geometry Nodes)"
    bl_idname = "DATA_PT_camera_shake_geonodes"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        return context.camera is not None

    def draw(self, context):
        layout = self.layout
        cam_obj = context.active_object

        # Check if camera has constraints targeting a rig
        has_shake_constraint = False
        target_rig = None
        if cam_obj:
            for c in cam_obj.constraints:
                if c.name.startswith("CameraShakify_GN") and getattr(c, "target", None):
                    has_shake_constraint = True
                    target_rig = c.target
                    break

        if has_shake_constraint and target_rig:
            box = layout.box()
            box.label(text=f"Controlled by Rig: {target_rig.name}", icon='CHECKMARK')
            shake_node = get_camera_shake_node(target_rig)
            if shake_node:
                col = box.column(align=True)
                col.prop(target_rig, "camera_shake_profile", text="Profile")
                col.prop(shake_node.inputs['Frequency'], "default_value", text="Frequency / Speed")
                col.prop(shake_node.inputs['Intensity'], "default_value", text="Intensity")
                col.prop(shake_node.inputs['Audio Trigger'], "default_value", text="Audio Trigger")
                col.prop(shake_node.inputs['Seed'], "default_value", text="Random Seed")
                row = box.row()
                row.operator("object.camera_shake_link_audio", text="Link Audio (Aubio)", icon='SPEAKER')
        else:
            box = layout.box()
            box.label(text="Procedural Camera Shake", icon='NODETREE')
            op = box.operator("object.camera_shake_add_geonodes_rig", text="Add Camera Shake Rig", icon='ADD')
            if cam_obj:
                op.target_camera = cam_obj.name


classes = (
    CameraShakeAddGeoNodesRig,
    CameraShakeBindCamera,
    CameraShakeLinkAudio,
    AudioMouthShapesAddModifier,
    AcousticPropVibrationAddModifier,
    CameraShakeObjectPanel,
    OBJECT_PT_mouth_shapes_geonodes,
    OBJECT_PT_prop_vibration_geonodes,
    CAMERA_PT_camera_shake_rig,
)


def register_props():
    if not hasattr(bpy.types.Object, "camera_shake_target_camera"):
        bpy.types.Object.camera_shake_target_camera = bpy.props.PointerProperty(
            name="Target Camera",
            description="Camera controlled by this Geometry Nodes shaker rig",
            type=bpy.types.Object,
            poll=lambda self, obj: obj.type == 'CAMERA',
            update=on_shake_target_camera_update,
        )
    if not hasattr(bpy.types.Object, "camera_shake_profile"):
        bpy.types.Object.camera_shake_profile = bpy.props.EnumProperty(
            name="Profile",
            description="Active Camera Shakify motion capture preset",
            items=CAMERA_SHAKE_PROFILES,
            default="0",
            update=on_shake_profile_update,
        )


def unregister_props():
    if hasattr(bpy.types.Object, "camera_shake_profile"):
        del bpy.types.Object.camera_shake_profile
    if hasattr(bpy.types.Object, "camera_shake_target_camera"):
        del bpy.types.Object.camera_shake_target_camera


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    register_props()


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    unregister_props()


try:
    register_props()
except Exception:
    pass
