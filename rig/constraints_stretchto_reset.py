import bpy

class GRET_OT_constraints_stretchto_reset(bpy.types.Operator):
    """Reset rest length of "Stretch To" constraints in selected bones, or all bones if none are selected"""

    bl_idname = "gret.constraints_stretchto_reset"
    bl_label = "Reset Stretch To Constraints"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE'

    def execute(self, context):
        obj = context.active_object

        num_reset = 0
        for pb in (context.selected_pose_bones if context.selected_pose_bones else obj.pose.bones):
            for con in pb.constraints:
                if con.type == 'STRETCH_TO':
                    con.rest_length = 0.0
                    num_reset += 1

        if num_reset:
            self.report({'INFO'}, f"{num_reset} \"Stretch To\" constraints were reset.")
        else:
            self.report({'INFO'}, f"No \"Stretch To\" constraints were reset.")

        return {'FINISHED'}

def draw_menu(self, context):
    self.layout.operator(GRET_OT_constraints_stretchto_reset.bl_idname)

def register(settings, prefs):
    if not prefs.rig__enable_constraints_stretchto_reset:
        return False

    bpy.utils.register_class(GRET_OT_constraints_stretchto_reset)
    bpy.types.VIEW3D_MT_pose_constraints.append(draw_menu)

def unregister():
    bpy.types.VIEW3D_MT_pose_constraints.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_constraints_stretchto_reset)
