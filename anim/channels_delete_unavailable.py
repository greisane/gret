import bpy
import re

from ..helpers import sentence_join

default_lock = False
default_lock_array = [default_lock] * 3
component_names = ('X', 'Y', 'Z', 'W')

def is_prop_locked(pb, name, component_index):
    if name == 'location':
        return getattr(pb, 'lock_location', default_lock_array)[component_index]
    elif name in {'rotation_euler', 'rotation_quaternion', 'rotation_axis_angle'}:
        if component_index < 3:
            return getattr(pb, 'lock_rotation', default_lock_array)[component_index]
        else:
            return getattr(pb, 'lock_rotation_w', default_lock)
    elif name == 'scale':
        return getattr(pb, 'lock_scale', default_lock_array)[component_index]

class GRET_OT_channels_delete_unavailable(bpy.types.Operator):
    """Delete location/rotation/scale channels that are locked in the transform panel"""

    bl_idname = 'gret.channels_delete_unavailable'
    bl_label = "Delete Unavailable Channels"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.space_data and context.space_data.type in {'DOPESHEET_EDITOR', 'GRAPH_EDITOR'}

    def execute(self, context):
        obj = context.active_object
        action = obj.animation_data.action if (obj and obj.animation_data) else None
        if not action:
            return {'CANCELLED'}

        remove_fcurves = []
        num_invalid = num_locked = 0

        for fc in action.fcurves:
            prop = obj.path_resolve(fc.data_path, False)
            if not prop:
                print(f"Removing curve, can't resolve {fc.data_path}")
                remove_fcurves.append(fc)
                num_invalid += 1
                continue

            pb_match = re.match(r'^pose\.bones\[\"([^\"]+)"\]\.(\w+)$', fc.data_path)
            if pb_match:
                pb = obj.pose.bones.get(pb_match[1])
                prop_name = pb_match[2]
                if pb and is_prop_locked(pb, prop_name, fc.array_index):
                    print(f"Removing curve, bone {pb.name} {component_names[fc.array_index]} "
                        f"{prop_name} is locked")
                    remove_fcurves.append(fc)
                    num_locked += 1
                    continue

        for fc in remove_fcurves:
            action.fcurves.remove(fc)

        num_removed_str = sentence_join([
            f"{num_invalid} invalid" if num_invalid else "",
            f"{num_locked} locked transform" if num_locked else "",
        ])
        if num_removed_str:
            self.report({'INFO'}, f"Removed {num_removed_str} curves.")

        return {'FINISHED'}

def draw_menu(self, context):
    self.layout.operator(GRET_OT_channels_delete_unavailable.bl_idname)

def register(settings, prefs):
    if not prefs.animation__enable_channels_delete_unavailable:
        return False

    bpy.utils.register_class(GRET_OT_channels_delete_unavailable)
    bpy.types.GRAPH_MT_channel.append(draw_menu)
    bpy.types.DOPESHEET_MT_channel.append(draw_menu)

def unregister():
    bpy.types.GRAPH_MT_channel.remove(draw_menu)
    bpy.types.DOPESHEET_MT_channel.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_channels_delete_unavailable)
