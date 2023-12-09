from functools import partial
import bpy

from .. import prefs
from ..rig.helpers import clear_pose

class GRET_OT_action_set(bpy.types.Operator):
    """Edit this action. Ctrl-Click to rename"""

    bl_idname = 'gret.action_set'
    bl_label = "Set Action"
    bl_options = {'INTERNAL', 'UNDO'}

    name: bpy.props.StringProperty(options={'HIDDEN'})
    new_name: bpy.props.StringProperty(name="New name", default="")
    play: bpy.props.BoolProperty(options={'HIDDEN'}, default=False)

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        obj = context.active_object
        if not self.name:
            obj.animation_data.action = None
            return {'FINISHED'}

        action = bpy.data.actions.get(self.name, None)
        if not action:
            return {'CANCELLED'}

        # Always save it, just in case
        action.use_fake_user = True

        if self.new_name:
            # Rename
            action.name = self.new_name
        elif not self.play and obj.animation_data and obj.animation_data.action == action:
            # Action was already active, stop editing
            obj.animation_data.action = None
        else:
            clear_pose(obj)
            obj.animation_data_create()
            obj.animation_data.action = action
            sync_frame_range()

            if self.play:
                context.scene.frame_current = int(action.curve_frame_range[0])
                bpy.ops.screen.animation_cancel(restore_frame=False)
                bpy.ops.screen.animation_play()

        return {'FINISHED'}

    def invoke(self, context, event):
        if event.ctrl:
            # Rename
            self.new_name = self.name
            return context.window_manager.invoke_props_dialog(self)
        else:
            self.new_name = ""
            return self.execute(context)

class GRET_OT_action_add(bpy.types.Operator):
    """Add a new action"""

    bl_idname = 'gret.action_add'
    bl_label = "Add Action"
    bl_options = {'INTERNAL', 'UNDO'}

    name: bpy.props.StringProperty(default="New action")

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        obj = context.active_object

        new_action = bpy.data.actions.new(self.name)
        new_action.use_fake_user = True
        clear_pose(obj)
        obj.animation_data_create()
        obj.animation_data.action = new_action

        return {'FINISHED'}

class GRET_OT_action_remove(bpy.types.Operator):
    """Delete the action"""

    bl_idname = 'gret.action_remove'
    bl_label = "Remove Action"
    bl_options = {'INTERNAL', 'UNDO'}

    name: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        obj = context.active_object
        action = bpy.data.actions.get(self.name, None)
        if not action:
            return {'CANCELLED'}

        bpy.data.actions.remove(action)

        return {'FINISHED'}

class GRET_OT_action_duplicate(bpy.types.Operator):
    """Duplicate this action"""

    bl_idname = 'gret.action_duplicate'
    bl_label = "Duplicate Action"
    bl_options = {'INTERNAL', 'UNDO'}

    name: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        obj = context.active_object
        action = bpy.data.actions.get(self.name, None)
        if not action:
            return {'CANCELLED'}

        new_action = action.copy()
        new_action.use_fake_user = True

        return {'FINISHED'}

def get_actions_for_rig(rig):
    for action in bpy.data.actions:
        if action.library:
            # Never show linked actions
            continue
        yield action

class GRET_PT_actions(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "gret"
    bl_label = "Actions"
    bl_parent_id = 'GRET_PT_animation'

    @classmethod
    def poll(cls, context):
        return True
        # (context.active_object
        #     and context.active_object.type == 'ARMATURE'
        #     and context.mode in {'OBJECT', 'POSE'})

    def draw_header(self, context):
        layout = self.layout
        layout.label(text="", icon='ACTION')

    def draw(self, context):
        layout = self.layout
        settings = context.scene.gret
        obj = context.active_object

        layout.operator('gret.action_add', icon='ADD')

        rig_actions = list(get_actions_for_rig(obj))
        active_action = obj.animation_data.action if obj.animation_data else None
        if rig_actions:
            col = layout.column(align=True)
            for action in rig_actions:
                selected = action == active_action
                row = col.row(align=True)

                sub = row.column(align=True)
                sub.ui_units_x = 1.0
                if selected and context.screen.is_animation_playing:
                    op = sub.operator('screen.animation_cancel', icon='PAUSE', text="", emboss=False)
                    op.restore_frame = False
                else:
                    icon = 'PLAY' if selected else 'TRIA_RIGHT'
                    op = sub.operator('gret.action_set', icon=icon, text="", emboss=False)
                    op.name = action.name
                    op.play = True

                op = row.operator('gret.action_set', text=action.name)
                op.name = action.name
                op.play = False
                row.operator('gret.action_duplicate', icon='DUPLICATE', text="").name = action.name
                row.operator('gret.action_remove', icon='X', text="").name = action.name

                if prefs.animation__show_action_frame_range and selected:
                    row = col.row(align=True)
                    sub = row.column(align=True)
                    sub.ui_units_x = 0.95  # Eyeballed to make it line up, beats split() madness
                    sub.separator()  # Whitespace
                    row.prop(active_action, 'use_frame_range', text="Range")
                    sub = row.row(align=True)
                    sub.prop(active_action, 'frame_start', text="")
                    sub.prop(active_action, 'frame_end', text="")
                    sub.prop(active_action, 'use_cyclic', icon='CON_FOLLOWPATH', text="")
                    sub.enabled = active_action.use_frame_range
                    col.separator()

classes = (
    GRET_OT_action_add,
    GRET_OT_action_duplicate,
    GRET_OT_action_remove,
    GRET_OT_action_set,
    GRET_PT_actions,
)

def sync_frame_range():
    if not prefs.animation__sync_action_frame_range:
        return

    context = bpy.context
    obj = context.active_object
    if obj and obj.animation_data and obj.animation_data.action:
        action = obj.animation_data.action
        if action.use_frame_range:
            context.scene.frame_preview_start = int(action.frame_start)
            context.scene.frame_preview_end = int(action.frame_end)
        else:
            context.scene.frame_preview_start = int(action.curve_frame_range[0])
            context.scene.frame_preview_end = int(action.curve_frame_range[1])
        context.scene.use_preview_range = True

owner = object()
def subscribe_all():
    subscribe = partial(bpy.msgbus.subscribe_rna, owner=owner, args=())
    subscribe(key=(bpy.types.Action, 'use_frame_range'), notify=sync_frame_range)
    subscribe(key=(bpy.types.Action, 'frame_start'), notify=sync_frame_range)
    subscribe(key=(bpy.types.Action, 'frame_end'), notify=sync_frame_range)

def unsubscribe_all():
    bpy.msgbus.clear_by_owner(owner)

def on_prefs_updated():
    if prefs.animation__sync_action_frame_range:
        unsubscribe_all()
        subscribe_all()
        sync_frame_range()
    else:
        unsubscribe_all()

def register(settings, prefs):
    for cls in classes:
        bpy.utils.register_class(cls)

    if prefs.animation__sync_action_frame_range:
        subscribe_all()

def unregister():
    unsubscribe_all()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
