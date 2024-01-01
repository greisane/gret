import bpy
import re

from ..helpers import sentence_join
from ..operator import PropertyWrapper
from ..rig.helpers import is_object_arp, arp_nondefault_pose_values

class GRET_OT_channels_delete_redundant(bpy.types.Operator):
    """Delete empty channels and non-contributing channels where all keys are default"""

    bl_idname = 'gret.channels_delete_redundant'
    bl_label = "Delete Redundant Channels"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.space_data and context.space_data.type in {'DOPESHEET_EDITOR', 'GRAPH_EDITOR'}

    def execute(self, context):
        obj = context.active_object
        action = obj.animation_data.action if (obj and obj.animation_data) else None
        if not action:
            return {'CANCELLED'}

        is_arp = is_object_arp(obj)
        remove_fcurves = []
        num_empty = num_redundant = 0

        for fc in action.fcurves:
            if fc.modifiers:
                # Could be a procedural curve, don't touch it
                continue

            if not fc.keyframe_points:
                remove_fcurves.append(fc)
                num_empty += 1
                continue

            if prop := PropertyWrapper.from_path(obj, fc.data_path):
                if is_arp and prop.prop_name in arp_nondefault_pose_values:
                    default_value = arp_nondefault_pose_values[prop.prop_name]
                else:
                    try:
                        default_value = prop.default_value[fc.array_index]
                    except TypeError:
                        default_value = prop.default_value

                if all(kf.co.y == default_value for kf in fc.keyframe_points):
                    remove_fcurves.append(fc)
                    num_redundant += 1

        for fc in remove_fcurves:
            action.fcurves.remove(fc)

        num_removed_str = sentence_join([
            f"{num_empty} empty" if num_empty else "",
            f"{num_redundant} redundant" if num_redundant else "",
        ])
        if num_removed_str:
            self.report({'INFO'}, f"Removed {num_removed_str} curves.")

        return {'FINISHED'}

def draw_menu(self, context):
    self.layout.operator(GRET_OT_channels_delete_redundant.bl_idname)

def register(settings, prefs):
    if not prefs.animation__enable_channels_delete_redundant:
        return False

    bpy.utils.register_class(GRET_OT_channels_delete_redundant)
    bpy.types.GRAPH_MT_channel.append(draw_menu)
    bpy.types.DOPESHEET_MT_channel.append(draw_menu)

def unregister():
    bpy.types.GRAPH_MT_channel.remove(draw_menu)
    bpy.types.DOPESHEET_MT_channel.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_channels_delete_redundant)
