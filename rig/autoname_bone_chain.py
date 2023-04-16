import bpy
import re

class GRET_OT_autoname_bone_chain(bpy.types.Operator):
    """Automatically renames a chain of bones starting at the selected bone"""

    bl_idname = 'gret.autoname_bone_chain'
    bl_label = "Auto-Name Bone Chain"
    bl_options = {'REGISTER', 'UNDO'}

    new_name: bpy.props.StringProperty(
        name="New Name",
        description="Format of the new name. Use # to denote index",
        default="chain_##"
    )
    starting_index: bpy.props.IntProperty(
        name="Starting Index",
        description="First number in the sequence",
        default=1,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'EDIT_ARMATURE' or context.mode == 'POSE'

    def execute(self, context):
        if context.mode == 'EDIT_ARMATURE':
            selected_editable_bones = set(context.selected_editable_bones)
        elif context.mode == 'POSE':
            selected_editable_bones = set(pb.bone for pb in context.selected_pose_bones)

        new_name = re.sub(r"#+", lambda m: f"{{n:0{len(m.group(0))}}}", self.new_name)
        while selected_editable_bones:
            bone = selected_editable_bones.pop()
            bone_chain = []
            # Collect first instead of renaming in place to avoid collisions when re-renaming
            while bone:
                selected_editable_bones.discard(bone)
                bone_chain.append(bone)
                bone.name = "__chain"
                bone = bone.children[0] if len(bone.children) == 1 else None
            for n, bone in enumerate(bone_chain):
                bone.name = new_name.format(n=n + self.starting_index)

        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

def draw_menu(self, context):
    self.layout.operator(GRET_OT_autoname_bone_chain.bl_idname)

def register(settings, prefs):
    bpy.utils.register_class(GRET_OT_autoname_bone_chain)
    bpy.types.VIEW3D_MT_edit_armature_names.append(draw_menu)
    bpy.types.VIEW3D_MT_pose_names.append(draw_menu)

def unregister():
    bpy.types.VIEW3D_MT_pose_names.remove(draw_menu)
    bpy.types.VIEW3D_MT_edit_armature_names.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_autoname_bone_chain)
