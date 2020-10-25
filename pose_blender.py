from bpy.app.handlers import persistent
from collections import namedtuple
from mathutils import Vector, Euler, Quaternion
from numbers import Number
import bpy
import itertools
import json
import re

bl_info = {
    "name": "Pose Blender",
    "author": "greisane",
    "description": "Allows blending between poses similarly to the UE4 AnimGraph node",
    "version": (0, 1),
    "blender": (2, 80, 0),
    "location": "View 3D > Sidebar > Tool Tab",
    "category": "Animation"
}

ZERO_ANIMWEIGHT_THRESH = 0.00001
DELTA = 0.00001
lerp = lambda a, b, t: t * b + (1.0 - t) * a
zero_vector = Vector((0.0, 0.0, 0.0))
one_vector = Vector((1.0, 1.0, 1.0))

def get_flipped_name(name):
    """Returns the given name with flipped L/R affixes, or None if not applicable"""

    def flip_LR(s):
        if "L" in s.upper():
            return s.replace("l", "r").replace("L", "R")
        else:
            return s.replace("r", "l").replace("R", "L")

    match = re.match(r"(.+)([_\.][LlRr])$", name) # Suffix
    if match:
        return match[1] + flip_LR(match[2])

    match = re.match(r"^([LlRr][_\.])(.+)", name) # Prefix
    if match:
        return flip_LR(match[1]) + match[2]

    return None

class Transform:
    __slots__ = ['location', 'rotation', 'scale']

    def __init__(self, location=None, rotation=None, scale=None):
        self.location = location or Vector()
        self.rotation = rotation or Quaternion()
        self.scale = scale or Vector((1.0, 1.0, 1.0))

    def copy(self):
        return Transform(
            self.location.copy(),
            self.rotation.copy(),
            self.scale.copy())

    def equals(self, other, tolerance=0.00001):
        return (abs(self.location.x - other.location.x) <= tolerance
            and abs(self.location.y - other.location.y) <= tolerance
            and abs(self.location.z - other.location.z) <= tolerance
            and abs(self.rotation.w - other.rotation.w) <= tolerance
            and abs(self.rotation.x - other.rotation.x) <= tolerance
            and abs(self.rotation.y - other.rotation.y) <= tolerance
            and abs(self.rotation.z - other.rotation.z) <= tolerance
            and abs(self.scale.x - other.scale.x) <= tolerance
            and abs(self.scale.y - other.scale.y) <= tolerance
            and abs(self.scale.z - other.scale.z) <= tolerance)

    def accumulate_with_shortest_rotation(self, delta_atom, blend_weight=1.0):
        """Accumulates another transform with this one, with an optional blending weight.
Rotation is accumulated additively, in the shortest direction."""

        atom = delta_atom * blend_weight

        # To ensure the shortest route, make sure the dot product between the rotations is positive
        if self.rotation.dot(atom.rotation) < 0.0:
            self.rotation -= atom.rotation
        else:
            self.rotation += atom.rotation

        self.location += atom.location
        self.scale += atom.scale

        # Return self for convenience
        return self

    @staticmethod
    def blend_from_identity_and_accumulate(final_atom, source_atom, blend_weight=1.0):
        """Blends the identity transform with a weighted source transform \
and accumulates that into a destination transform."""

        delta_location = source_atom.location
        delta_rotation = source_atom.rotation
        delta_scale = source_atom.scale

        # Scale delta by weight
        if blend_weight < 1.0 - ZERO_ANIMWEIGHT_THRESH:
            delta_location = source_atom.location * blend_weight
            delta_scale = zero_vector.lerp(source_atom.scale, blend_weight)
            delta_rotation = source_atom.rotation * blend_weight
            delta_rotation.w = lerp(1.0, source_atom.rotation.w, blend_weight)

        # Add ref pose relative animation to base animation, only if rotation is significant
        if delta_rotation.w * delta_rotation.w < 1.0 - DELTA * DELTA:
            # final_atom.rotation = delta_rotation * final_atom.rotation
            final_atom.rotation.rotate(delta_rotation)

        final_atom.location += delta_location
        final_atom.scale.x *= 1.0 + delta_scale.x
        final_atom.scale.y *= 1.0 + delta_scale.y
        final_atom.scale.z *= 1.0 + delta_scale.z

    def get_safe_scale_reciprocal(self, tolerance=0.00001):
        return Vector((
            0.0 if abs(self.scale.x) <= tolerance else 1.0 / self.scale.x,
            0.0 if abs(self.scale.y) <= tolerance else 1.0 / self.scale.y,
            0.0 if abs(self.scale.z) <= tolerance else 1.0 / self.scale.z))

    def make_additive(self, base_transform):
        self.location -= base_transform.location
        self.rotation.rotate(base_transform.rotation.inverted())
        self.rotation.normalize()
        base_scale = base_transform.get_safe_scale_reciprocal()
        self.scale.x = self.scale.x * base_scale.x - 1.0
        self.scale.y = self.scale.y * base_scale.y - 1.0
        self.scale.z = self.scale.z * base_scale.z - 1.0

    def __eq__(self, other):
        if isinstance(other, Transform):
            return (self.location == other.location
                and self.rotation == other.rotation
                and self.scale == other.scale)
        else:
            return NotImplemented

    def __ne__(self, other):
        if isinstance(other, Transform):
            return (self.location != other.location
                or self.rotation != other.rotation
                or self.scale != other.scale)
        else:
            return NotImplemented

    def __add__(self, other):
        if isinstance(other, Transform):
            return self.copy().accumulate_with_shortest_rotation(other)
        else:
            return NotImplemented

    def __sub__(self, other):
        if isinstance(other, Transform):
            return self.copy().accumulate_with_shortest_rotation(-other)
        else:
            return NotImplemented

    def __mul__(self, other):
        if isinstance(other, Number):
            return Transform(
                self.location * other,
                self.rotation * other,
                self.scale * other)
        else:
            return NotImplemented

    def __iadd__(self, other):
        if isinstance(other, Transform):
            return self.accumulate_with_shortest_rotation(other)
        else:
            return NotImplemented

    def __isub__(self, other):
        if isinstance(other, Transform):
            return self.accumulate_with_shortest_rotation(-other)
        else:
            return NotImplemented

    def __imul__(self, other):
        if isinstance(other, Number):
            self.location *= other
            self.rotation *= other
            self.scale *= other
        else:
            return NotImplemented

    def __neg__(self):
        return Transform(
            -self.location
            -self.rotation
            -self.scale)

    def __pos__(self):
        return Transform(
            +self.location
            +self.rotation
            +self.scale)

class Pose:
    __slots__ = ['owner', 'name', 'transforms']

    def get_weight(self):
        return self.owner.armature[self.name]

    def set_weight(self, value):
        if not isinstance(value, Number):
            return
        value = min(1.0, max(0.0, float(value)))
        self.owner.armature[self.name] = value

    weight = property(get_weight, set_weight, None, "Pose weight")

    def __init__(self, owner, name, transforms):
        self.owner = owner
        self.name = name
        self.transforms = transforms

class PoseBlender:
    additive = False
    depsgraph_update_pre_handler = None
    frame_change_post_handler = None
    undo_post_handler = None

    def __init__(self, armature):
        self.armature = armature
        self.armature_name = armature.name
        self.pose_lib = getattr(armature, 'pose_library')
        self.poses = []
        self.pose_rows = []
        self.pose_names = {}  # Pose name to pose map
        self.additive = True  # Need to expose this

        if self.pose_lib:
            self.sort_poses()
            self.cache_poses()
            self.key_pose_lib()
            self.ensure_properties_exist()

    def ensure_properties_exist(self):
        """Adds the custom properties that will drive the poses"""

        if not self.pose_lib or not self.pose_lib.pose_markers:
            return

        if '_RNA_UI' not in self.armature:
            self.armature['_RNA_UI'] = {}

        for marker in self.pose_lib.pose_markers:
            pose_name = marker.name
            if pose_name not in self.armature:
                self.armature[pose_name] = 0.0
            if pose_name not in self.armature['_RNA_UI']:
                self.armature['_RNA_UI'][pose_name] = {'min': 0.0, 'max': 1.0, 'default': 0.0,
                    'soft_min': 0.0, 'soft_max': 1.0, 'description': "Pose weight"}

    def key_pose_lib(self):
        """Keys the pose library for later exporting"""

        if not self.pose_lib or not self.pose_lib.pose_markers:
            return

        # Temporary datapath->fcurve map for fast searching
        fcurves_map = {fc.data_path:fc for fc in self.pose_lib.fcurves}
        start_frame, last_frame = self.pose_lib.frame_range

        # base_pose_name = self.pose_lib.pose_markers[0].name # TODO let user choose

        for marker in self.pose_lib.pose_markers:
            # if marker.name == base_pose_name:
            #     continue

            data_path = '["%s"]' % marker.name
            fcurve = fcurves_map.get(data_path)
            if fcurve:
                self.pose_lib.fcurves.remove(fcurve)
            fcurves_map[data_path] = fcurve = self.pose_lib.fcurves.new(data_path)

            if marker.frame > start_frame:
                fcurve.keyframe_points.insert(marker.frame - 1, 0.0).interpolation = 'LINEAR'
            fcurve.keyframe_points.insert(marker.frame, 1.0).interpolation = 'LINEAR'
            if marker.frame < last_frame:
                fcurve.keyframe_points.insert(marker.frame + 1, 0.0).interpolation = 'LINEAR'

    def ensure_armature_valid(self):
        if self.armature and not self.is_armature_valid():
            # Armature reference breaks after undo, try finding it by name
            # This may be incorrect if it was renamed, however not handling undo is more inconvenient
            self.armature = bpy.context.scene.objects.get(self.armature_name)
            if not self.armature:
                # Couldn't recover, clean up invalid pose blender
                self.armature = None
                self.unregister()
                del PB_PT_pose_blender.pose_blenders[self.armature_name]
        return self.armature is not None

    def is_armature_valid(self):
        try:
            self.armature.pose_library
        except (ReferenceError, KeyError):
            return False
        return True

    def sort_poses(self):
        """Ensures pose frames match the order that is displayed in the Pose Library panel"""
        fixed_pose_names = []
        for marker_idx, marker in enumerate(self.pose_lib.pose_markers):
            if marker.frame != marker_idx:
                marker.frame = marker_idx
                fixed_pose_names.append(marker.name)
        if fixed_pose_names:
            print(f"Fixed frame index for poses {', '.join(fixed_pose_names)}")

    def cache_poses(self):
        default_transform = Transform()  # Ref pose used to determine if a transform is significant
        pose_transforms = []  # List of bonename->transform maps

        for marker in self.pose_lib.pose_markers:
            transforms = {}
            curves = {}
            euler_rotations = {}
            pose_transforms.append(transforms)

            for fcurve in self.pose_lib.fcurves:
                match_pb = re.match(r'^pose\.bones\["(.+)"\]\.(\w+)$', fcurve.data_path)
                if match_pb:
                    # Handle bone curves
                    bone_name = match_pb[1]
                    data_elem = match_pb[2]
                    data_idx = fcurve.array_index
                    data_value = fcurve.evaluate(marker.frame)

                    transform = transforms.get(bone_name)
                    if not transform:
                        transforms[bone_name] = transform = default_transform.copy()

                    if data_elem == 'location':
                        transform.location[data_idx] = data_value
                    elif data_elem == 'rotation_euler':
                        # Need the whole rotation to convert, so save it for later
                        euler_rotations[bone_name] = euler_rotations.get(bone_name, Euler())
                        euler_rotations[bone_name][data_idx] = data_value
                    elif data_elem == 'rotation_axis_angle':
                        pass  # Not implemented
                    elif data_elem == 'rotation_quaternion':
                        transform.rotation[data_idx] = data_value
                    elif data_elem == 'scale':
                        transform.scale[data_idx] = data_value
                else:
                    pass
                    # TODO
                    # print(fcurve.data_path)
                    # curves[fcurve.data_path] = fcurve.evaluate(marker.frame)

            # Convert eulers to quaternions
            for bone_name, euler in euler_rotations.items():
                transforms[bone_name].rotation = euler.to_quaternion()

            # Remove bones that don't contribute to the pose
            for bone_name in list(transforms.keys()):
                if transforms[bone_name].equals(default_transform):
                    del transforms[bone_name]

        # Collect the names of the bones used in the poses
        self.relevant_bones = sorted(set(itertools.chain.from_iterable(transforms.keys()
            for transforms in pose_transforms)))

        # Finalize poses, changing dicts to lists for performance. The indices correspond
        # to relevant_bones, relevant_curves etc. and have None where the pose isn't affected
        self.poses.clear()
        for marker, transforms in zip(self.pose_lib.pose_markers, pose_transforms):
            if marker.name == "bind_pose":
                continue
            transforms = [transforms.get(bone_name) for bone_name in self.relevant_bones]
            pose = Pose(self, marker.name, transforms)
            self.poses.append(pose)

        # Make additive relative to the chosen base pose
        self.base_pose = None
        if self.additive and self.poses:
            try:
                self.base_pose = next(pose for pose in self.poses if pose.name == "base_pose")
            except StopIteration:
                # TODO allow user to choose
                self.base_pose = self.poses[0]

            self.poses.remove(self.base_pose)

            for pose in self.poses:
                for transform, base_transform in zip(pose.transforms, self.base_pose.transforms):
                    if transform:
                        transform.make_additive(base_transform or default_transform)
                # for curve, base_curve in zip(pose.curves, base_pose.curves):
                #     curve.value -= base_curve.value

        self.pose_names = {pose.name: pose for pose in self.poses}

        # Put poses in pairs where there are symmetric poses
        self.pose_rows.clear()
        pose_names = [pose.name for pose in self.poses]
        pose_names.reverse()
        while pose_names:
            pose_name = pose_names.pop()
            flipped_name = get_flipped_name(pose_name)

            if flipped_name and flipped_name in pose_names:
                pose_names.remove(flipped_name)
                # R/L is more intuitive since you usually pose the character in front view
                self.pose_rows.append((flipped_name, pose_name))
            else:
                self.pose_rows.append((pose_name,))

    def get_pose(self, out_pose):
        # Mirrors UE4 implementation, see Runtime/Engine/Private/Animation/PoseAsset.cpp
        total_weight = sum(pose.weight for pose in self.poses)

        for bone_idx, bone_name in enumerate(self.relevant_bones):
            blending = []
            total_local_weight = 0.0

            for pose in self.poses:
                transform = pose.transforms[bone_idx]
                pose_weight = pose.weight

                if transform and pose_weight > 0.0:
                    if total_weight > 1.0:
                        pose_weight /= total_weight
                    blending.append((transform, pose_weight))
                    total_local_weight += pose_weight

            blend_idx = 0 if total_local_weight < 1.0 else 1

            if not blending:
                blended = out_pose[bone_idx]
            elif blend_idx == 0:
                blended = out_pose[bone_idx] * (1.0 - total_local_weight)
            else:
                blended = blending[0][0] * blending[0][1]

            for blend_idx in range(blend_idx, len(blending)):
                transform, weight = blending[blend_idx]
                blended.accumulate_with_shortest_rotation(transform, weight)
            blended.rotation.normalize()

            out_pose[bone_idx] = blended

    def get_pose_additive(self, out_pose):
        for bone_idx, bone_name in enumerate(self.relevant_bones):
            blended = out_pose[bone_idx]

            for pose in self.poses:
                transform = pose.transforms[bone_idx]
                pose_weight = pose.weight

                if transform and pose_weight > 0.0:
                    blended.rotation.normalize()
                    Transform.blend_from_identity_and_accumulate(blended, transform, pose_weight)
            blended.rotation.normalize()

            out_pose[bone_idx] = blended

    def clear_pose_weights(self):
        if self.is_armature_valid():
            for pose in self.poses:
                self.armature[pose.name] = 0.0
            self.update_armature()

    def flip_pose_weights(self):
        if self.is_armature_valid():
            self.ensure_properties_exist()
            for pose_row in self.pose_rows:
                if len(pose_row) == 2:
                    a, b = pose_row
                    self.armature[a], self.armature[b] = self.armature[b], self.armature[a]
            self.update_armature()

    def key_pose_weights(self):
        if self.is_armature_valid():
            frame = bpy.context.scene.frame_current
            self.ensure_properties_exist()
            for pose in self.poses:
                self.armature.keyframe_insert('["%s"]' % pose.name, frame=frame)

    def update_armature(self):
        if not self.is_armature_valid():
            return

        if self.additive:
            if self.base_pose:
                current_pose = [transform.copy() if transform else Transform()
                    for transform in self.base_pose.transforms]
            else:
                current_pose = [Transform() for _ in range(len(self.relevant_bones))]
            self.get_pose_additive(current_pose)
        else:
            current_pose = [Transform() for _ in range(len(self.relevant_bones))]
            self.get_pose(current_pose)

        for bone_name, transform in zip(self.relevant_bones, current_pose):
            pose_bone = self.armature.pose.bones.get(bone_name)
            if pose_bone:
                if pose_bone.rotation_mode == 'QUATERNION':
                    pose_bone.rotation_quaternion = transform.rotation
                elif pose_bone.rotation_mode == 'AXIS_ANGLE':
                    axis, angle = transform.rotation.to_axis_angle()
                    pose_bone.rotation_axis_angle[0] = angle
                    pose_bone.rotation_axis_angle[1] = axis[0]
                    pose_bone.rotation_axis_angle[2] = axis[1]
                    pose_bone.rotation_axis_angle[3] = axis[2]
                else:
                    pose_bone.rotation_euler = transform.rotation.to_euler(pose_bone.rotation_mode)
                pose_bone.location = transform.location
                pose_bone.scale = transform.scale

    def on_update(self, *args):
        if self.ensure_armature_valid():
            self.update_armature()
            self.armature_name = self.armature.name

    def on_undo(self, *args):
        if self.ensure_armature_valid():
            self.ensure_properties_exist()
            self.update_armature()

    def register(self):
        if not self.depsgraph_update_pre_handler:
            self.depsgraph_update_pre_handler = self.on_update
            bpy.app.handlers.depsgraph_update_pre.append(self.depsgraph_update_pre_handler)

        if not self.frame_change_post_handler:
            self.frame_change_post_handler = self.on_update
            bpy.app.handlers.frame_change_post.append(self.frame_change_post_handler)

        if not self.undo_post_handler:
            self.undo_post_handler = self.on_undo
            bpy.app.handlers.undo_post.append(self.undo_post_handler)

    def unregister(self):
        if self.depsgraph_update_pre_handler:
            if self.depsgraph_update_pre_handler in bpy.app.handlers.depsgraph_update_pre:
                bpy.app.handlers.depsgraph_update_pre.remove(self.depsgraph_update_pre_handler)
            self.depsgraph_update_pre_handler = None

        if self.frame_change_post_handler:
            if self.frame_change_post_handler in bpy.app.handlers.frame_change_post:
                bpy.app.handlers.frame_change_post.remove(self.frame_change_post_handler)
            self.frame_change_post_handler = None

        if self.undo_post_handler:
            if self.undo_post_handler in bpy.app.handlers.undo_post:
                bpy.app.handlers.undo_post.remove(self.undo_post_handler)
            self.undo_post_handler = None

class PB_OT_add(bpy.types.Operator):
    #tooltip
    """Adds pose blending to the active object"""

    bl_idname = 'pose_blender.add'
    bl_label = "Add Pose Blender"

    @classmethod
    def poll(cls, context):
        return (context.object and context.object.type == 'ARMATURE'
            and context.object.pose_library
            and context.object.name not in PB_PT_pose_blender.pose_blenders)

    def execute(self, context):
        cls = PB_PT_pose_blender
        obj = context.object

        pose_blender = cls.pose_blenders.get(obj.name)
        if not pose_blender:
            cls.pose_blenders[obj.name] = pose_blender = PoseBlender(obj)
            pose_blender.register()
        else:
            pose_blender.cache_poses()
        pose_blender.update_armature()

        return {'FINISHED'}

class PB_OT_remove(bpy.types.Operator):
    #tooltip
    """Removes pose blending from the active object"""

    bl_idname = 'pose_blender.remove'
    bl_label = "Remove Pose Blender"

    @classmethod
    def poll(cls, context):
        return context.object and context.object.name in PB_PT_pose_blender.pose_blenders

    def execute(self, context):
        cls = PB_PT_pose_blender
        obj = context.object

        pose_blender = cls.pose_blenders.get(obj.name)
        if pose_blender:
            pose_blender.unregister()
            del cls.pose_blenders[obj.name]

        return {'FINISHED'}

class PB_OT_clear(bpy.types.Operator):
    #tooltip
    """Clear weights for all poses"""

    bl_idname = 'pose_blender.clear'
    bl_label = "Clear Poses"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.name in PB_PT_pose_blender.pose_blenders

    def execute(self, context):
        cls = PB_PT_pose_blender
        obj = context.object

        pose_blender = cls.pose_blenders.get(obj.name)
        if pose_blender:
            pose_blender.clear_pose_weights()

        return {'FINISHED'}

class PB_OT_flip(bpy.types.Operator):
    #tooltip
    """Swaps weights for symmetric poses"""

    bl_idname = "pose_blender.flip"
    bl_label = "Flip Poses"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.name in PB_PT_pose_blender.pose_blenders

    def execute(self, context):
        cls = PB_PT_pose_blender
        obj = context.object

        pose_blender = cls.pose_blenders.get(obj.name)
        if pose_blender:
            pose_blender.flip_pose_weights()

        return {'FINISHED'}

class PB_OT_copy(bpy.types.Operator):
    #tooltip
    """Copies pose weights to clipboard"""

    bl_idname = 'pose_blender.copy'
    bl_label = "Copy Poses"

    @classmethod
    def poll(cls, context):
        return context.object and context.object.name in PB_PT_pose_blender.pose_blenders

    def execute(self, context):
        cls = PB_PT_pose_blender
        obj = context.object

        pose_blender = cls.pose_blenders.get(obj.name)
        if pose_blender:
            pose_weights = {pose.name: pose.weight for pose in pose_blender.poses}
            pose_weights_json = json.dumps(pose_weights)
            context.window_manager.clipboard = pose_weights_json
            self.report({'INFO'}, "Copied pose weights to clipboard.")

        return {'FINISHED'}

class PB_OT_paste(bpy.types.Operator):
    #tooltip
    """Pastes pose weights from clipboard"""

    bl_idname = 'pose_blender.paste'
    bl_label = "Paste Poses"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.name in PB_PT_pose_blender.pose_blenders

    def execute(self, context):
        cls = PB_PT_pose_blender
        obj = context.object

        pose_blender = cls.pose_blenders.get(obj.name)
        if not pose_blender:
            return {'CANCELLED'}

        try:
            pose_weights = json.loads(context.window_manager.clipboard)
        except:
            return {'CANCELLED'}

        try:
            for pose_name, weight in pose_weights.items():
                pose = pose_blender.pose_names.get(pose_name)
                if pose:
                    pose.weight = weight
            pose_blender.update_armature()
            self.report({'INFO'}, "Pasted pose weights from clipboard.")
        except:
            pass
        return {'FINISHED'}

class PB_OT_key(bpy.types.Operator):
    #tooltip
    """Keyframes the current pose"""

    bl_idname = 'pose_blender.key'
    bl_label = "Keyframe Poses"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.name in PB_PT_pose_blender.pose_blenders

    def execute(self, context):
        cls = PB_PT_pose_blender
        obj = context.object

        pose_blender = cls.pose_blenders.get(obj.name)
        if pose_blender:
            pose_blender.key_pose_weights()

        return {'FINISHED'}

class PB_PT_pose_blender(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Tool"
    bl_label = "Pose Blender"

    pose_blenders = {}

    @classmethod
    def poll(cls, context):
        return (context.mode in {'OBJECT', 'POSE'}
            and context.object and context.object.type == 'ARMATURE')

    @classmethod
    def clear(cls):
        for pose_blender in cls.pose_blenders.values():
            pose_blender.unregister()
        cls.pose_blenders.clear()

    def draw(self, context):
        cls = __class__
        obj = context.object
        layout = self.layout

        row = layout.row(align=True)
        row.operator('pose_blender.add', icon='MOD_ARMATURE')
        row.operator('pose_blender.remove', icon='X', text="")

        pose_blender = cls.pose_blenders.get(obj.name)
        if pose_blender:
            col = layout.column(align=True)
            for pose_row in pose_blender.pose_rows:
                row = col.row(align=True)
                for pose_name in pose_row:
                    row.prop(obj, '["%s"]' % pose_name, slider=True)

            row = layout.row(align=True)
            row.operator('pose_blender.clear', icon='X', text="")
            row.operator('pose_blender.flip', icon='ARROW_LEFTRIGHT', text="")
            row.operator('pose_blender.copy', icon='COPYDOWN', text="")
            row.operator('pose_blender.paste', icon='PASTEDOWN', text="")
            row.operator('pose_blender.key', icon='KEYINGSET', text="")

classes = (
    PB_OT_add,
    PB_OT_remove,
    PB_OT_clear,
    PB_OT_flip,
    PB_OT_copy,
    PB_OT_paste,
    PB_OT_key,
    PB_PT_pose_blender,
)

@persistent
def load_pre_handler(scene):
    PB_PT_pose_blender.clear()

@persistent
def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.app.handlers.load_pre.append(load_pre_handler)

def unregister():
    PB_PT_pose_blender.clear()
    bpy.app.handlers.load_pre.remove(load_pre_handler)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
