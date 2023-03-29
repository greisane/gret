import bpy
import re

class GRET_OT_channels_auto_group(bpy.types.Operator):
    #tooltip
    """Group curves by bone name"""

    bl_idname = 'gret.channels_auto_group'
    bl_label = "Auto-Group Channels"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.space_data and context.space_data.type in {'DOPESHEET_EDITOR', 'GRAPH_EDITOR'}

    def execute(self, context):
        obj = context.active_object
        action = obj.animation_data.action if (obj and obj.animation_data) else None
        if not action:
            return {'CANCELLED'}

        fcurves = []

        # Create the necessary groups first THEN assign them to prevent the following error
        # https://github.com/blender/blender/blob/v3.4.1/source/blender/makesrna/intern/rna_fcurve.c#L527
        for fc in action.fcurves:
            group_name = (re.match(r'^pose\.bones\[\"([^\"]+)"\]', fc.data_path) or ['', ''])[1]
            if group_name and (not fc.group or fc.group.name != group_name):
                fcurves.append((fc, group_name))
                if group_name not in action.groups:
                    action.groups.new(name=group_name)

        for fc, group_name in fcurves:
            old_group, fc.group = fc.group, action.groups.get(group_name)
            if fc.group:
                fc.group.show_expanded = True
                fc.group.show_expanded_graph = True
            if old_group and not old_group.channels:
                action.groups.remove(old_group)

        return {'FINISHED'}

def draw_menu(self, context):
    self.layout.operator(GRET_OT_channels_auto_group.bl_idname)

def register(settings, prefs):
    if not prefs.animation__enable_channels_auto_group:
        return False

    # Would be nice to have this menu item next to the other group operators
    bpy.utils.register_class(GRET_OT_channels_auto_group)
    bpy.types.GRAPH_MT_channel.append(draw_menu)
    bpy.types.DOPESHEET_MT_channel.append(draw_menu)

def unregister():
    bpy.types.GRAPH_MT_channel.remove(draw_menu)
    bpy.types.DOPESHEET_MT_channel.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_channels_auto_group)
