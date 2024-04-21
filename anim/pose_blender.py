from bpy.app.handlers import persistent
from collections import namedtuple
from itertools import chain
from mathutils import Euler
import bpy
import json
import re

from ..helpers import flip_name
from ..math import saturate, Transform

Pose = namedtuple('Pose', 'name transforms')

class GRET_OT_pose_blender_clear(bpy.types.Operator):
    """Clear weights for all poses"""

    bl_idname = 'gret.pose_blender_clear'
    bl_label = "Clear Poses"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'ARMATURE' and obj.pose_blender.enabled

    def execute(self, context):
        obj = context.active_object
        pbl = obj.pose_blender

        for pose in pbl.get_transient_data().poses:
            if pose.name in obj:
                obj[pose.name] = 0.0
        pbl.update_pose(force=True)

        return {'FINISHED'}

class GRET_OT_pose_blender_flip(bpy.types.Operator):
    """Swaps weights for symmetric poses"""

    bl_idname = "gret.pose_blender_flip"
    bl_label = "Flip Poses"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'ARMATURE' and obj.pose_blender.enabled

    def execute(self, context):
        obj = context.active_object
        pbl = obj.pose_blender

        for pose_row in pbl.get_transient_data().pose_pairs:
            if len(pose_row) == 2:
                (pose_name0, _), (pose_name1, _) = pose_row
                obj[pose_name0], obj[pose_name1] = obj[pose_name1], obj[pose_name0]
        pbl.update_pose(force=True)

        return {'FINISHED'}

class GRET_OT_pose_blender_copy(bpy.types.Operator):
    """Copies pose weights to clipboard"""

    bl_idname = 'gret.pose_blender_copy'
    bl_label = "Copy Poses"

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'ARMATURE' and obj.pose_blender.enabled

    def execute(self, context):
        obj = context.active_object
        pbl = obj.pose_blender

        pose_weights = {pose.name: obj.get(pose.name, 0.0) for pose in pbl.get_transient_data().poses}
        context.window_manager.clipboard = json.dumps(pose_weights)
        self.report({'INFO'}, "Copied pose weights to clipboard.")

        return {'FINISHED'}

class GRET_OT_pose_blender_paste(bpy.types.Operator):
    """Pastes pose weights from clipboard"""

    bl_idname = 'gret.pose_blender_paste'
    bl_label = "Paste Poses"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'ARMATURE' and obj.pose_blender.enabled

    def execute(self, context):
        obj = context.active_object
        pbl = context.active_object.pose_blender

        try:
            pose_weights = json.loads(context.window_manager.clipboard)
        except:
            return {'CANCELLED'}

        try:
            for pose_name, weight in pose_weights.items():
                if pose_name in obj:
                    obj[pose_name] = weight
            pbl.update_pose(force=True)
            self.report({'INFO'}, "Pasted pose weights from clipboard.")
        except:
            raise

        return {'FINISHED'}

class GRET_OT_pose_blender_key(bpy.types.Operator):
    """Keyframes the current pose"""

    bl_idname = 'gret.pose_blender_key'
    bl_label = "Keyframe Poses"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'ARMATURE' and obj.pose_blender.enabled

    def execute(self, context):
        obj = context.active_object
        pbl = obj.pose_blender
        frame = context.scene.frame_current

        num_failed = 0
        for pose in pbl.get_transient_data().poses:
            try:
                obj.keyframe_insert(f'["{pose.name}"]', frame=frame, group="Poses")
            except RuntimeError:
                num_failed += 1
                pass

        if num_failed:
            self.report({'WARNING'}, "Some poses could not be keyframed, ensure that they are not "
                "locked or sampled, and try removing F-Modifiers.")

        return {'FINISHED'}

class GRET_OT_pose_blender_fix(bpy.types.Operator):
    """Edit the poses action"""

    bl_idname = 'gret.pose_blender_fix'
    bl_label = "Edit Poses Action"
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'ARMATURE' and not obj.pose_blender.enabled

    def execute(self, context):
        obj = context.active_object
        pbl = obj.pose_blender

        if not obj.animation_data:
            obj.animation_data_create()
        obj.animation_data.action = pbl.action

        bpy.ops.gret.pose_make('INVOKE_DEFAULT')

        return {'FINISHED'}

class GRET_OT_pose_blender_refresh(bpy.types.Operator):
    """Rebuild pose cache. Use if you tweaked the individual poses"""

    bl_idname = 'gret.pose_blender_refresh'
    bl_label = "Refresh Poses"
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'ARMATURE' and obj.pose_blender.enabled

    def execute(self, context):
        obj = context.active_object
        pbl = obj.pose_blender
        pbl.clear_transient_data()

        return {'FINISHED'}

def clear_transient_data(self, context):
    self.clear_transient_data()

class PoseBlenderData:
    def __init__(self):
        self.poses = []
        self.poses_by_name = {}
        self.pose_pairs = []
        self.base_pose = None
        self.relevant_bones = []
        self.depsgraph_update_pre_handler = None
        self.frame_change_post_handler = None
        self.undo_post_handler = None

class GRET_PG_pose_blender(bpy.types.PropertyGroup):
    enabled: bpy.props.BoolProperty(
        name="Enable",
        description="""Enable pose blending.
Requires a valid poses action""",
        default=False,
        override={'LIBRARY_OVERRIDABLE'},
    )
    action: bpy.props.PointerProperty(
        name="Poses Action",
        description="""Action containing one pose per frame.
Use the Pose Markers panel to create the pose markers""",
        type=bpy.types.Action,
        options=set(),
        override={'LIBRARY_OVERRIDABLE'},
        update=clear_transient_data,
    )
    use_additive: bpy.props.BoolProperty(
        name="Additive",
        description="Blend poses additively",
        default=True,
        options=set(),
        override={'LIBRARY_OVERRIDABLE'},
        update=clear_transient_data,
    )
    base_pose_name: bpy.props.StringProperty(
        name="Base Pose",
        description="""Optional, other poses are made relative to this pose.
A base pose is useful if the rest pose is not neutral (e.g. mouth is open)""",
        default="",
        options=set(),
        override={'LIBRARY_OVERRIDABLE'},
        update=clear_transient_data,
    )
    _transient_data = {}

    def is_valid(self):
        return bool(self.action
            and self.action.pose_markers
            and (not self.use_additive or not self.base_pose_name
                or self.base_pose_name in self.action.pose_markers)
            and all(marker.name in self.id_data for marker in self.action.pose_markers))

    @property
    def transient_data_key(self):
        return hash(self.id_data)  # Remains to be seen how stable this is

    def get_transient_data(self):
        data = __class__._transient_data.get(self.transient_data_key)
        if data is None:
            __class__._transient_data[self.transient_data_key] = data = PoseBlenderData()
            print("Pose blender missing transient data, caching")
            self.cache_transient_data()
        return data

    def clear_transient_data(self):
        try:
            del __class__._transient_data[self.transient_data_key]
            print("Cleared pose blender transient data")
        except KeyError:
            pass

    def cache_transient_data(self):
        data = self.get_transient_data()
        default_transform = Transform()  # Ref pose used to determine if a transform is significant
        pose_transforms = []  # List of bonename->transform maps

        for marker in self.action.pose_markers:
            transforms = {}
            curves = {}
            euler_rotations = {}
            pose_transforms.append(transforms)

            for fc in self.action.fcurves:
                if pb_match := re.fullmatch(r'pose\.bones\["(.+)"\]\.(\w+)', fc.data_path):
                    # TODO non-XYZ euler and axis angle
                    # Handle bone curves
                    bone_name, prop_name = pb_match[1], pb_match[2]
                    array_index, value = fc.array_index, fc.evaluate(marker.frame)

                    transform = transforms.get(bone_name)
                    if not transform:
                        transforms[bone_name] = transform = default_transform.copy()

                    if prop_name == 'location':
                        transform.location[array_index] = value
                    elif prop_name == 'rotation_euler':
                        # Need the whole rotation to convert, so save it for later
                        euler_rotations[bone_name] = euler_rotations.get(bone_name, Euler())
                        euler_rotations[bone_name][array_index] = value
                    elif prop_name == 'rotation_axis_angle':
                        pass  # Not implemented
                    elif prop_name == 'rotation_quaternion':
                        transform.rotation[array_index] = value
                    elif prop_name == 'scale':
                        transform.scale[array_index] = value
                else:
                    # TODO
                    # print(fc.data_path)
                    # curves[fc.data_path] = fc.evaluate(marker.frame)
                    pass

            # Convert eulers to quaternions
            for bone_name, euler in euler_rotations.items():
                transforms[bone_name].rotation = euler.to_quaternion()

            # Remove bones that don't contribute to the pose
            for bone_name in list(transforms.keys()):
                if transforms[bone_name].equals(default_transform):
                    del transforms[bone_name]

        # Collect the names of the bones used in the poses
        data.relevant_bones = sorted(set(chain.from_iterable(transforms.keys()
            for transforms in pose_transforms)))

        # Finalize poses, changing dicts to lists for performance. The indices correspond
        # to relevant_bones, relevant_curves etc. and have None where the pose isn't affected
        data.poses.clear()
        data.poses_by_name.clear()
        for marker, transforms in zip(self.action.pose_markers, pose_transforms):
            pose = Pose(marker.name, [transforms.get(bone_name) for bone_name in data.relevant_bones])
            data.poses.append(pose)
            data.poses_by_name[marker.name] = pose

        # If additive, make other poses relative to the base pose then take it out of the list
        data.base_pose = None
        if self.use_additive and self.base_pose_name and self.base_pose_name in data.poses_by_name:
            data.base_pose = data.poses_by_name[self.base_pose_name]
            del data.poses_by_name[self.base_pose_name]
            data.poses.remove(data.base_pose)

            for pose in data.poses:
                for transform, base_transform in zip(pose.transforms, data.base_pose.transforms):
                    if transform is not None:
                        transform.make_additive(base_transform or default_transform)
                # TODO
                # for curve, base_curve in zip(pose.curves, base_pose.curves):
                #     curve.value -= base_curve.value

        # Pair up symmetric poses. This is mostly for layout
        def get_pose_name_tuple(pose_name):
            return pose_name, pose_name.removesuffix('_pose')

        data.pose_pairs.clear()
        pose_names = [pose.name for pose in reversed(data.poses)]
        while pose_names:
            pose_name = pose_names.pop()
            flipped_name = flip_name(pose_name)
            if flipped_name and flipped_name in pose_names:
                pose_names.remove(flipped_name)
                # R/L is more intuitive since you usually pose the character in front view
                data.pose_pairs.append((get_pose_name_tuple(flipped_name), get_pose_name_tuple(pose_name)))
            else:
                data.pose_pairs.append((get_pose_name_tuple(pose_name),))

    def add_custom_properties(self):
        # Add the custom properties that will drive the poses
        obj = self.id_data
        for pose in data.poses:
            if pose.name not in obj:
                obj[pose.name] = 0.0
                obj.property_overridable_library_set(f'["{pose.name}"]', True)
                obj.id_properties_ui(pose_name).update(default=0.0, description="Pose weight",
                    min=0.0, max=1.0, soft_min=0.0, soft_max=1.0)
        # obj.update_tag()

    def get_pose(self, out_pose):
        # Mirrors UE4 implementation at Runtime/Engine/Private/Animation/PoseAsset.cpp
        obj = self.id_data
        data = self.get_transient_data()
        weight_sum = sum(obj.get(pose.name, 0.0) for pose in data.poses)

        for bone_idx, bone_name in enumerate(data.relevant_bones):
            blending = []
            local_weight_sum = 0.0

            for pose in data.poses:
                transform = pose.transforms[bone_idx]
                pose_weight = obj.get(pose.name, 0.0)

                if transform and pose_weight > 0.0:
                    if weight_sum > 1.0:
                        pose_weight /= weight_sum
                    blending.append((transform, pose_weight))
                    local_weight_sum += pose_weight

            blend_idx = 0 if local_weight_sum < 1.0 else 1

            if not blending:
                blended = out_pose[bone_idx]
            elif blend_idx == 0:
                blended = out_pose[bone_idx] * (1.0 - local_weight_sum)
            else:
                blended = blending[0][0] * blending[0][1]

            for blend_idx in range(blend_idx, len(blending)):
                transform, weight = blending[blend_idx]
                blended.accumulate_with_shortest_rotation(transform, weight)
            blended.rotation.normalize()

            out_pose[bone_idx] = blended

    def get_pose_additive(self, out_pose):
        obj = self.id_data
        data = self.get_transient_data()

        for bone_idx, bone_name in enumerate(data.relevant_bones):
            blended = out_pose[bone_idx]

            for pose in data.poses:
                transform = pose.transforms[bone_idx]
                pose_weight = obj.get(pose.name, 0.0)

                if transform and pose_weight > 0.0:
                    blended.rotation.normalize()
                    Transform.blend_from_identity_and_accumulate(blended, transform, pose_weight)
            blended.rotation.normalize()

            out_pose[bone_idx] = blended

    def update_pose(self, force=False):
        obj = self.id_data
        data = self.get_transient_data()

        if self.use_additive:
            if data.base_pose:
                current_pose = [transform.copy() if transform else Transform()
                    for transform in data.base_pose.transforms]
            else:
                current_pose = [Transform() for _ in range(len(data.relevant_bones))]
            self.get_pose_additive(current_pose)
        else:
            current_pose = [Transform() for _ in range(len(data.relevant_bones))]
            self.get_pose(current_pose)

        pose_bones = obj.pose.bones
        for bone_name, transform in zip(data.relevant_bones, current_pose):
            if pb := pose_bones.get(bone_name):
                if pb.rotation_mode == 'QUATERNION':
                    pb.rotation_quaternion = transform.rotation
                elif pb.rotation_mode == 'AXIS_ANGLE':
                    axis, angle = transform.rotation.to_axis_angle()
                    pb.rotation_axis_angle = angle, *axis
                else:
                    pb.rotation_euler = transform.rotation.to_euler(pb.rotation_mode)
                pb.location = transform.location
                pb.scale = transform.scale

        if force:
            obj.update_tag()

class GRET_PT_pose_blender(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "gret"
    bl_label = "Pose Blender"
    bl_parent_id = 'GRET_PT_animation'

    @classmethod
    def poll(cls, context):
        return (context.active_object
            and context.active_object.type == 'ARMATURE')

    def draw_header(self, context):
        layout = self.layout
        obj = context.active_object
        pbl = obj.pose_blender

        layout.label(text="", icon='MOD_ARMATURE')
        row = layout.row()
        row.prop(pbl, 'enabled', text="")
        row.enabled = pbl.enabled or pbl.is_valid()

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        pbl = obj.pose_blender

        def draw_error(text, can_fix=False):
            if can_fix:
                split = layout.split(align=True, factor=0.8)
                col = split.column(align=True)
            else:
                col = layout.column(align=True)
            col.scale_y = 0.8
            for n, line in enumerate(text.split('\n')):
                col.label(text=line, icon='ERROR' if n == 0 else 'BLANK1')
            if can_fix:
                split.operator('gret.pose_blender_fix', text="Fix")

        def draw_button_row():
            row = layout.row(align=True)
            row.alignment = 'CENTER'
            row.operator('gret.pose_blender_clear', icon='X', text="")
            row.operator('gret.pose_blender_flip', icon='ARROW_LEFTRIGHT', text="")
            row.operator('gret.pose_blender_copy', icon='COPYDOWN', text="")
            row.operator('gret.pose_blender_paste', icon='PASTEDOWN', text="")
            row.operator('gret.pose_blender_key', icon='KEYINGSET', text="")
            row.operator('gret.pose_blender_refresh', icon='FILE_REFRESH', text="")

        if not pbl.enabled:
            col = layout.column(align=True)
            col.use_property_split = True
            col.prop(pbl, 'action', text="Poses")
            col.prop(pbl, 'use_additive')
            if pbl.use_additive:
                if pbl.action and pbl.action.pose_markers:
                    col.prop_search(pbl, 'base_pose_name', pbl.action, 'pose_markers', icon='PMARKER_ACT')
                    if pbl.base_pose_name and pbl.base_pose_name not in pbl.action.pose_markers:
                        draw_error(text="Base pose not found in the action.")
                else:
                    col.prop(pbl, 'base_pose_name', icon='PMARKER_ACT')
            if pbl.action:
                if not pbl.action.pose_markers:
                    draw_error(text="Action has no pose markers.", can_fix=not pbl.action.library)
                    if pbl.action.library:
                        draw_error("Can't fix action because it is linked.")
                elif any(m.name not in obj for m in pbl.action.pose_markers):
                    draw_error(text="Rig is missing pose properties.", can_fix=not obj.override_library)
                    if obj.override_library:
                        draw_error("Can't fix rig from an override data-block.")
        else:
            draw_button_row()

            col = layout.column(align=True)
            pose_pairs = pbl.get_transient_data().pose_pairs
            for pose_row in pose_pairs:
                row = col.row(align=True)
                for pose_name, text in pose_row:
                    if pose_name in obj:
                        row.prop(obj, f'["{pose_name}"]', text=text, slider=True)

            if len(pose_pairs) > 10:
                # If there are enough rows, repeat the buttons at the end
                draw_button_row()

classes = (
    GRET_OT_pose_blender_clear,
    GRET_OT_pose_blender_copy,
    GRET_OT_pose_blender_fix,
    GRET_OT_pose_blender_flip,
    GRET_OT_pose_blender_key,
    GRET_OT_pose_blender_paste,
    GRET_OT_pose_blender_refresh,
    GRET_PG_pose_blender,
    GRET_PT_pose_blender,
)

@persistent
def depsgraph_update_pre_handler(scene):
    for obj in scene.objects:
        if obj.pose_blender.enabled:
            obj.pose_blender.update_pose()

@persistent
def frame_change_update_post_handler(scene):
    for obj in scene.objects:
        if obj.pose_blender.enabled:
            obj.pose_blender.update_pose()

def register(settings, prefs):
    if not prefs.animation__register_pose_blender:
        return False

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Object.pose_blender = bpy.props.PointerProperty(
        type=GRET_PG_pose_blender,
        override={'LIBRARY_OVERRIDABLE'},
    )

    bpy.app.handlers.depsgraph_update_pre.append(depsgraph_update_pre_handler)
    bpy.app.handlers.frame_change_post.append(frame_change_update_post_handler)

def unregister():
    bpy.app.handlers.depsgraph_update_pre.remove(depsgraph_update_pre_handler)
    bpy.app.handlers.frame_change_post.remove(frame_change_update_post_handler)

    del bpy.types.Object.pose_blender

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
