import bpy
import re

class GRET_OT_channels_auto_group(bpy.types.Operator):
    #tooltip
    """Groups all bone curves within groups"""

    bl_idname = 'gret.channels_auto_group'
    bl_label = "Auto-Group Channels"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        # There's context.active_action, not sure when it actually points to anything
        obj = context.active_object
        return obj and obj.animation_data and obj.animation_data.action

    def execute(self, context):
        action = context.active_object.animation_data.action
        num_grouped = 0

        for fc in action.fcurves:
            if not fc.group:
                group_name = (re.match(r'^pose\.bones\[\"([^\"]+)"\]', fc.data_path) or ['', ''])[1]
                if group_name:
                    group = action.groups.get(group_name)
                    if not group:
                        action.groups.new(name=group_name)
                    fc.group = group
                    num_grouped += 1

        return {'FINISHED'}

def draw_menu(self, context):
    self.layout.operator(GRET_OT_channels_auto_group.bl_idname)

def register(settings, prefs):
    # Would be nice to have this menu item next to the other group operators
    bpy.utils.register_class(GRET_OT_channels_auto_group)
    bpy.types.GRAPH_MT_channel.append(draw_menu)

def unregister():
    bpy.types.GRAPH_MT_channel.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_channels_auto_group)
