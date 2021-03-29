import bpy

class GRET_OT_propagate_bone_inherit_scale(bpy.types.Operator):
    #tooltip
    """Propagates 'Inherit Scale' from the selected bone to children"""

    bl_idname = 'gret.propagate_bone_inherit_scale'
    bl_label = "Propagate Bone Inherit Scale"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE' and context.selected_pose_bones_from_active_object

    def execute(self, context):
        obj = context.object

        for active_pbone in context.selected_pose_bones_from_active_object:
            active_bone = obj.data.bones[active_pbone.name]
            for bone in active_bone.children_recursive:
                bone.inherit_scale = active_bone.inherit_scale

        return {'FINISHED'}

classes = (
    GRET_OT_propagate_bone_inherit_scale,
)

def register(settings):
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
