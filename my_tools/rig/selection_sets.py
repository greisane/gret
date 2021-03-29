from collections import OrderedDict
import bpy
import json

from gret.helpers import get_flipped_name

class GRET_OT_selection_set_toggle(bpy.types.Operator):
    #tooltip
    """Toggle this bone selection set. Shift-click to extend selection"""

    bl_idname = 'gret.selection_set_toggle'
    bl_label = "Toggle Bone Selection Set"
    bl_options = {'INTERNAL', 'UNDO'}

    name: bpy.props.StringProperty(options={'HIDDEN'})
    extend: bpy.props.BoolProperty(options={'HIDDEN'}, default=False)

    @classmethod
    def poll(cls, context):
        return context.object and context.mode == 'POSE'

    def execute(self, context):
        obj = context.object
        sel_set = obj.selection_sets.get(self.name, None)
        sel_set_index = obj.selection_sets.find(self.name)
        if not sel_set:
            return {'CANCELLED'}

        sel_set.is_selected = not sel_set.is_selected
        for pbone in context.visible_pose_bones:
            bone = pbone.bone
            if not self.extend:
                bone.select = False
            if pbone.name in sel_set.bone_ids:
                bone.select = sel_set.is_selected

        if not self.extend:
            for idx, sel_set in enumerate(obj.selection_sets):
                if idx != sel_set_index:
                    sel_set.is_selected = False

        return {'FINISHED'}

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

class GRET_OT_selection_set_copy(bpy.types.Operator):
    #tooltip
    """Copy bone selection sets to clipboard"""

    bl_idname = 'gret.selection_set_copy'
    bl_label = "Copy Bone Selection Sets"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        obj = context.object

        sel_sets = getattr(obj, 'selection_sets')
        if sel_sets:
            sel_sets_list = [(name, sel_set.bone_ids.keys()) for name, sel_set in sel_sets.items()]
            sel_sets_json = json.dumps(sel_sets_list)
            context.window_manager.clipboard = sel_sets_json
            self.report({'INFO'}, "Copied bone selection sets to clipboard.")

        return {'FINISHED'}

class GRET_OT_selection_set_paste(bpy.types.Operator):
    #tooltip
    """Pastes bone selection sets from clipboard"""

    bl_idname = 'gret.selection_set_paste'
    bl_label = "Paste Bone Selection Sets"
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        obj = context.object

        sel_sets = getattr(obj, 'selection_sets')
        if sel_sets is None:
            return {'CANCELLED'}

        try:
            sel_sets_list = json.loads(context.window_manager.clipboard)
        except:
            return {'CANCELLED'}

        try:
            for name, bone_names in sel_sets_list:
                if name not in sel_sets:
                    sel_set = sel_sets.add()
                    sel_set.name = name
                    for bone_name in bone_names:
                        sel_set_bone = sel_set.bone_ids.add()
                        sel_set_bone.name = bone_name
            self.report({'INFO'}, "Pasted bone selection sets from clipboard.")
        except:
            pass
        return {'FINISHED'}

        return {'FINISHED'}

def draw(self, context):
    obj = context.object
    layout = self.layout

    if hasattr(obj, 'selection_sets'):
        box = layout.box()
        row = box.row()
        row.label(text="Bone Selection Sets", icon='GROUP_BONE')
        row = row.row(align=True)
        row.operator('gret.selection_set_copy', icon='COPYDOWN', text="")
        row.operator('gret.selection_set_paste', icon='PASTEDOWN', text="")

        selection_sets = OrderedDict(reversed(obj.selection_sets.items()))
        if selection_sets:
            col = box.column(align=True)
            while selection_sets:
                name, sel_set = selection_sets.popitem()
                other_name = get_flipped_name(name)
                other_sel_set = selection_sets.pop(other_name, None)

                row = col.row(align=True)
                if other_sel_set:
                    row.operator('gret.selection_set_toggle', text=other_name,
                        depress=other_sel_set.is_selected).name = other_name
                row.operator('gret.selection_set_toggle', text=name,
                    depress=sel_set.is_selected).name = name

classes = (
    GRET_OT_selection_set_copy,
    GRET_OT_selection_set_paste,
    GRET_OT_selection_set_toggle,
)

def register(settings):
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
