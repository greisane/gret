from collections import OrderedDict
import bpy
import json

from ..helpers import flip_name
from ..operator import draw_warning_if_not_overridable

class GRET_OT_selection_set_toggle(bpy.types.Operator):
    """Toggle this bone selection set. Shift-click to extend selection"""

    bl_idname = 'gret.selection_set_toggle'
    bl_label = "Toggle Selection Set"
    bl_options = {'INTERNAL', 'UNDO'}

    name: bpy.props.StringProperty(options={'HIDDEN'})
    extend: bpy.props.BoolProperty(options={'HIDDEN'}, default=False)

    @classmethod
    def poll(cls, context):
        return context.active_object and context.mode == 'POSE'

    def execute(self, context):
        obj = context.active_object
        sel_set = obj.selection_sets.get(self.name, None)
        if not sel_set:
            return {'CANCELLED'}

        sel_set.is_selected = not sel_set.is_selected
        for pbone in context.visible_pose_bones:
            bone = pbone.bone
            if not self.extend:
                bone.select = False
            if pbone.name in sel_set.bone_ids:
                bone.select = sel_set.is_selected

        sel_set_index = obj.selection_sets.find(self.name)
        if not self.extend:
            for idx, sel_set in enumerate(obj.selection_sets):
                if idx != sel_set_index:
                    sel_set.is_selected = False
        return {'FINISHED'}

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

class GRET_OT_selection_set_copy(bpy.types.Operator):
    """Copy bone selection sets to clipboard"""

    bl_idname = 'gret.selection_set_copy'
    bl_label = "Copy Selection Sets"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        obj = context.active_object

        sel_sets = getattr(obj, 'selection_sets')
        if sel_sets:
            sel_sets_list = [(name, sel_set.bone_ids.keys()) for name, sel_set in sel_sets.items()]
            sel_sets_json = json.dumps(sel_sets_list)
            context.window_manager.clipboard = sel_sets_json
            self.report({'INFO'}, "Copied bone selection sets to clipboard.")
        return {'FINISHED'}

class GRET_OT_selection_set_paste(bpy.types.Operator):
    """Pastes bone selection sets from clipboard"""

    bl_idname = 'gret.selection_set_paste'
    bl_label = "Paste Selection Sets"
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        obj = context.active_object

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

class GRET_OT_selection_set_add(bpy.types.Operator):
    """Add a new selection set with the currently selected bones"""

    bl_idname = 'gret.selection_set_add'
    bl_label = "Add Selection Set"
    bl_options = {'INTERNAL', 'UNDO'}

    name: bpy.props.StringProperty(
        name="Name",
        description="Name of the new selection set",
    )

    @classmethod
    def poll(cls, context):
        return context.active_object and context.mode == 'POSE' and context.selected_pose_bones

    def execute(self, context):
        obj = context.active_object

        name = self.name
        name_number = 0
        while name in obj.selection_sets:
            name_number += 1
            name = "{}.{:03d}".format(self.name, name_number)

        new_sel_set = obj.selection_sets.add()
        new_sel_set.name = name
        for bone in context.selected_pose_bones:
            if bone.name not in new_sel_set.bone_ids:
                bone_id = new_sel_set.bone_ids.add()
                bone_id.name = bone.name
        return {'FINISHED'}

    def invoke(self, context, event):
        # Find an appropriate default name
        self.name = context.selected_pose_bones[0].name if context.selected_pose_bones else "Empty"
        return context.window_manager.invoke_props_dialog(self)

class GRET_OT_selection_set_remove(bpy.types.Operator):
    """Remove this selection set"""

    bl_idname = 'gret.selection_set_remove'
    bl_label = "Remove Selection Set"
    bl_options = {'INTERNAL', 'UNDO'}

    name: bpy.props.StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        obj = context.active_object
        index = obj.selection_sets.find(self.name)
        if index > -1:
            try:
                obj.selection_sets.remove(index)
            except TypeError:
                self.report({'WARNING'}, "This selection set can't be removed.")
                return {'CANCELLED'}
        return {'FINISHED'}

class GRET_OT_selection_set_overwrite(bpy.types.Operator):
    """Overwrite this selection set with the currently selected bones"""

    bl_idname = 'gret.selection_set_overwrite'
    bl_label = "Overwrite Selection Set"
    bl_options = {'INTERNAL', 'UNDO'}

    name: bpy.props.StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        obj = context.active_object
        sel_set = obj.selection_sets.get(self.name, None)
        if not sel_set:
            return {'CANCELLED'}

        sel_set.bone_ids.clear()
        for bone in context.selected_pose_bones:
            if bone.name not in sel_set.bone_ids:
                bone_id = sel_set.bone_ids.add()
                bone_id.name = bone.name
        return {'FINISHED'}

def draw_panel(self, context):
    if context.mode != 'POSE':
        return

    layout = self.layout
    settings = context.scene.gret
    obj = context.active_object

    if not hasattr(obj, 'selection_sets'):
        return

    box = layout.box()
    row = box.row()
    row.label(text="Bone Selection Sets", icon='GROUP_BONE')
    row = row.row(align=True)
    if settings.selection_sets_show_edit:
        if draw_warning_if_not_overridable(row, obj, 'selection_sets'):
            row.separator()
        row.operator('gret.selection_set_copy', icon='COPYDOWN', text="")
        row.operator('gret.selection_set_paste', icon='PASTEDOWN', text="")
        row.operator('gret.selection_set_add', icon='ADD', text="")
    row.prop(settings, 'selection_sets_show_edit', icon='SETTINGS', text="")

    def draw_sel_set_item(layout, sel_set):
        name = sel_set.name
        op = layout.operator('gret.selection_set_toggle', text=name, depress=sel_set.is_selected)
        op.name = name
        if settings.selection_sets_show_edit:
            layout.operator('gret.selection_set_overwrite', icon='ADD', text="").name = name
            layout.operator('gret.selection_set_remove', icon='X', text="").name = name

    selection_sets = OrderedDict(reversed(obj.selection_sets.items()))
    if selection_sets:
        col = box.column(align=True)
        while selection_sets:
            name, sel_set = selection_sets.popitem()
            other_name = flip_name(name)
            other_sel_set = selection_sets.pop(other_name, None)

            row = col.row(align=True)
            if other_sel_set:
                draw_sel_set_item(row, other_sel_set)
                if settings.selection_sets_show_edit:
                    row.separator()
            draw_sel_set_item(row, sel_set)

classes = (
    GRET_OT_selection_set_add,
    GRET_OT_selection_set_copy,
    GRET_OT_selection_set_overwrite,
    GRET_OT_selection_set_paste,
    GRET_OT_selection_set_remove,
    GRET_OT_selection_set_toggle,
)

def register(settings, prefs):
    if not prefs.rig__enable_selection_sets:
        return False

    for cls in classes:
        bpy.utils.register_class(cls)

    settings.add_property('selection_sets_show_edit', bpy.props.BoolProperty(
        name="Edit Selection Sets",
        description="Show buttons to edit bone selection sets",
        default=False,
        options=set(),
    ))

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
