import os
import bpy
from .helpers import clear_pose, try_key

class MY_OT_action_set(bpy.types.Operator):
    #tooltip
    """Edit this action. Ctrl-click to rename"""

    bl_idname = 'my_tools.action_set'
    bl_label = "Set Action"
    bl_options = {'INTERNAL', 'UNDO'}

    name: bpy.props.StringProperty(options={'HIDDEN'})
    new_name: bpy.props.StringProperty(name="New name", default="")
    play: bpy.props.BoolProperty(options={'HIDDEN'}, default=False)

    @classmethod
    def poll(cls, context):
        return context.object and context.object.animation_data

    def execute(self, context):
        obj = context.object

        if not self.name:
            obj.animation_data.action = None
            return {'FINISHED'}

        action = bpy.data.actions.get(self.name, None)

        if action:
            # Always save it, just in case
            action.use_fake_user = True

            if self.new_name:
                # Rename
                action.name = self.new_name
            elif not self.play and obj.animation_data.action == action:
                # Action was already active, stop editing
                obj.animation_data.action = None
            else:
                clear_pose(obj)
                obj.animation_data.action = action

                # Set preview range
                context.scene.frame_preview_start = action.frame_range[0]
                context.scene.frame_preview_end = action.frame_range[1] #- 1
                context.scene.use_preview_range = True

                if self.play:
                    context.scene.frame_current = action.frame_range[0]
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

class MY_OT_action_add(bpy.types.Operator):
    #tooltip
    """Add a new action"""

    bl_idname = 'my_tools.action_add'
    bl_label = "Add Action"
    bl_options = {'INTERNAL', 'UNDO'}

    name: bpy.props.StringProperty(default="New action")

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        obj = context.object

        if not obj.animation_data:
            obj.animation_data_create()

        new_action = bpy.data.actions.new(self.name)
        new_action.use_fake_user = True
        clear_pose(obj)
        obj.animation_data.action = new_action

        # Key the rig properties at the start frame
        # try_key('pose.bones["c_hand_ik_l"]["ik_fk_switch"]')
        # try_key('pose.bones["c_hand_ik_r"]["ik_fk_switch"]')
        # try_key('pose.bones["c_foot_ik_l"]["ik_fk_switch"]')
        # try_key('pose.bones["c_foot_ik_r"]["ik_fk_switch"]')
        # try_key('pose.bones["c_head"]["inherit_rotation"]')

        return {'FINISHED'}

class MY_OT_action_remove(bpy.types.Operator):
    #tooltip
    """Delete the action"""

    bl_idname = 'my_tools.action_remove'
    bl_label = "Remove Action"
    bl_options = {'INTERNAL', 'UNDO'}

    name: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return context.object and context.object.animation_data

    def execute(self, context):
        obj = context.object
        action = bpy.data.actions.get(self.name, None)

        if action:
            bpy.data.actions.remove(action)

        return {'FINISHED'}

class MY_PT_actions(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "My Tools"
    bl_label = "Actions"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return context.mode in {'OBJECT', 'POSE'} and obj and obj.type == 'ARMATURE'

    def draw(self, context):
        obj = context.object
        layout = self.layout

        if obj and obj.type == 'ARMATURE':
            box = layout.box()
            row = box.row()
            row.label(text="Available Actions", icon='ACTION')
            row.operator('my_tools.action_add', icon='ADD', text="")

            if bpy.data.actions:
                col = box.column(align=True)

                for action in bpy.data.actions:
                    row = col.row(align=True)

                    selected = obj and obj.animation_data and obj.animation_data.action == action
                    if selected and context.screen.is_animation_playing:
                        op = row.operator('screen.animation_cancel', icon='PAUSE', text="", emboss=False)
                        op.restore_frame = False
                    else:
                        icon = 'PLAY' if selected else 'TRIA_RIGHT'
                        op = row.operator('my_tools.action_set', icon=icon, text="", emboss=False)
                        op.name = action.name
                        op.play = True

                    op = row.operator('my_tools.action_set', text=action.name, emboss=True)
                    op.name = action.name
                    op.play = False

                    row.operator('my_tools.action_remove', icon='X', text="").name = action.name

classes = (
    MY_OT_action_set,
    MY_OT_action_add,
    MY_OT_action_remove,
    MY_PT_actions,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
