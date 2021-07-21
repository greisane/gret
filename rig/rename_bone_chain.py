import bpy
import re

class GRET_OT_rename_bone_chain(bpy.types.Operator):
    #tooltip
    """Rename a chain of bones starting at the selected bone"""

    bl_idname = 'gret.rename_bone_chain'
    bl_label = "Rename Bone Chain"
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
        return context.mode == 'EDIT_ARMATURE' and context.selected_editable_bones

    def execute(self, context):
        obj = context.object

        new_name = re.sub(r"#+", lambda m: f"{{n:0{len(m.group(0))}}}", self.new_name)
        selected_editable_bones = set(context.selected_editable_bones)
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

def register(settings):
    bpy.utils.register_class(GRET_OT_rename_bone_chain)

def unregister():
    bpy.utils.unregister_class(GRET_OT_rename_bone_chain)
