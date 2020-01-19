from collections import namedtuple
import bpy
from bpy.props import FloatVectorProperty, IntVectorProperty, EnumProperty
from bpy.props import IntProperty, BoolProperty, StringProperty
import json

bl_info = {
    "name": "Copy Things",
    "author": "greisane",
    "description": "Allows transport of some types of data between files",
    "version": (0, 1),
    "blender": (2, 80, 0),
    "location": "3D View > Quick Search",
    "category": "Object"
}

ClipboardData = namedtuple("ClipboardData", ["what", "infos"])
EditBoneInfo = namedtuple("EditBoneInfo", [
    "name",
    "head",
    "tail",
    "roll",
])
ActionInfo = namedtuple("ActionInfo", [
    "fcurve_infos",
    "name",
])
FCurveInfo = namedtuple("FCurveInfo", [
    "array_index",
    "auto_smoothing",
    "data_path",
    "extrapolation",
    "keyframe_infos",
])
KeyframeInfo = namedtuple("KeyframeInfo", [
    "amplitude",
    "back",
    "co",
    "easing",
    "handle_left",
    "handle_left_type",
    "handle_right",
    "handle_right_type",
    "interpolation",
    "period",
    "type",
])

def get_edit_bone_infos(operator, context):
    obj = context.object
    edit_bones = obj.data.edit_bones
    infos = []

    for bone in edit_bones:
        if bone.select:
            infos.append(EditBoneInfo(
                name=bone.name,
                head=bone.head[:],
                tail=bone.tail[:],
                roll=bone.roll,
            ))

    operator.report({'INFO'}, "Copied %s bones." % len(infos))
    return infos

def poll_paste_edit_bones(context):
    return context.object and context.mode == 'EDIT_ARMATURE'

def paste_edit_bones(operator, context, data):
    obj = context.object
    edit_bones = obj.data.edit_bones
    bone_infos = [EditBoneInfo(*info) for info in data.infos]
    num_bones = 0

    for bone_info in bone_infos:
        bone = edit_bones.get(bone_info.name)
        if not bone:
            continue # Maybe create?

        bone.head = bone_info.head
        bone.tail = bone_info.tail
        bone.roll = bone_info.roll
        num_bones += 1

    operator.report({'INFO'}, "Pasted %s bones." % num_bones)

def get_action_infos(operator, context):
    infos = []

    actions = set()
    for obj in bpy.data.objects:
        if obj.animation_data:
            for nla_track in obj.animation_data.nla_tracks:
                for strip in nla_track.strips:
                    if strip.action and strip.select:
                        actions.add(strip.action)

    for action in actions:
        fcurve_infos = []
        for fcurve in action.fcurves:
            keyframe_infos = []
            for keyframe in fcurve.keyframe_points:
                keyframe_infos.append(KeyframeInfo(
                    amplitude=keyframe.amplitude,
                    back=keyframe.back,
                    co=keyframe.co[:],
                    easing=keyframe.easing,
                    handle_left=keyframe.handle_left[:],
                    handle_left_type=keyframe.handle_left_type,
                    handle_right=keyframe.handle_right[:],
                    handle_right_type=keyframe.handle_right_type,
                    interpolation=keyframe.interpolation,
                    period=keyframe.period,
                    type=keyframe.type,
                ))
            fcurve_infos.append(FCurveInfo(
                array_index=fcurve.array_index,
                auto_smoothing=fcurve.auto_smoothing if bpy.app.version >= (2, 80) else 'NONE',
                data_path=fcurve.data_path,
                extrapolation=fcurve.extrapolation,
                keyframe_infos=keyframe_infos,
            ))
        infos.append(ActionInfo(
            fcurve_infos=fcurve_infos,
            name=action.name,
        ))

    operator.report({'INFO'}, "Copied %s actions." % len(infos))
    return infos

def paste_actions(operator, context, data):
    action_infos = [ActionInfo(*info) for info in data.infos]
    num_actions = 0

    for action_info in action_infos:
        action = bpy.data.actions.new(action_info.name)
        action.use_fake_user = True

        fcurve_infos = [FCurveInfo(*info) for info in action_info.fcurve_infos]
        for fcurve_info in fcurve_infos:
            fcurve = action.fcurves.new(data_path=fcurve_info.data_path, index=fcurve_info.array_index)
            if bpy.app.version >= (2, 80):
                fcurve.auto_smoothing = fcurve_info.auto_smoothing
            fcurve.extrapolation = fcurve_info.extrapolation

            keyframe_infos = [KeyframeInfo(*info) for info in fcurve_info.keyframe_infos]
            for keyframe_info in keyframe_infos:
                keyframe = fcurve.keyframe_points.insert(keyframe_info.co[0], keyframe_info.co[1])
                keyframe.amplitude = keyframe_info.amplitude
                keyframe.back = keyframe_info.back
                keyframe.easing = keyframe_info.easing
                keyframe.handle_left = keyframe_info.handle_left
                keyframe.handle_left_type = keyframe_info.handle_left_type
                keyframe.handle_right = keyframe_info.handle_right
                keyframe.handle_right_type = keyframe_info.handle_right_type
                keyframe.interpolation = keyframe_info.interpolation
                keyframe.period = keyframe_info.period
                keyframe.type = keyframe_info.type

        num_actions += 1

    operator.report({'INFO'}, "Pasted %s actions." % num_actions)

class MY_OT_copy_things(bpy.types.Operator):
    bl_idname = "wm.copy_things"
    bl_label = "Copy Things"

    def execute(self, context):
        data = None
        if context.area.type == 'VIEW_3D':
            if context.mode == 'EDIT_ARMATURE':
                infos = get_edit_bone_infos(self, context)
                data = ClipboardData('EDIT_BONES', infos)
        elif context.area.type == 'NLA_EDITOR':
            infos = get_action_infos(self, context)
            data = ClipboardData('ACTIONS', infos)

        if not data:
            self.report({'INFO'}, "Couldn't find anything to copy.")
            return {'FINISHED'}

        jsons = json.dumps(data)
        bpy.context.window_manager.clipboard = jsons

        return {'FINISHED'}

class MY_OT_paste_things(bpy.types.Operator):
    bl_idname = "wm.paste_things"
    bl_label = "Paste Things"
    bl_info = {'UNDO'}

    def execute(self, context):
        jsons = bpy.context.window_manager.clipboard
        if not jsons:
            self.report({'ERROR'}, "Clipboard is empty.")
            return {'CANCELLED'}

        data = None
        try:
            data = ClipboardData(*json.loads(jsons))
        except:
            self.report({'ERROR'}, "Error decoding clipboard.")
            return {'CANCELLED'}

        if data.what == 'EDIT_BONES':
            if not poll_paste_edit_bones(context):
                self.report({'ERROR'}, "Clipboard data is edit bones, context is incorrect.")
                return {'CANCELLED'}

            try:
                paste_edit_bones(self, context, data)
            except:
                self.report({'ERROR'}, "Error pasting edit bones.")
        elif data.what == 'ACTIONS':
            paste_actions(self, context, data)
            # try:
            #     paste_actions(self, context, data)
            # except:
            #     self.report({'ERROR'}, "Error pasting actions.")


        return {'FINISHED'}

def register():
    bpy.utils.register_class(MY_OT_copy_things)
    bpy.utils.register_class(MY_OT_paste_things)

def unregister():
    bpy.utils.unregister_class(MY_OT_copy_things)
    bpy.utils.unregister_class(MY_OT_paste_things)

if __name__ == '__main__':
    register()