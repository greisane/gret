import bpy

# I would like to make this into a more general space switching solution
# Right now it suffices to have a way of tweaking the parent of a bone without moving its children

class GRET_OT_bone_lock(bpy.types.Operator):
    #tooltip
    """Add or remove constraints that lock the selected bones in place"""

    bl_idname = 'gret.bone_lock'
    bl_label = "Toggle Bone Lock"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE'

    def execute(self, context):
        removed = False
        for pbone in context.selected_pose_bones:
            for name in ("Lock Location", "Lock Rotation", "Lock Scale"):
                constraint = pbone.constraints.get(name)
                if constraint:
                    obj = pbone.id_data
                    mat = obj.matrix_world @ pbone.matrix
                    pbone.constraints.remove(constraint)
                    pbone.matrix = obj.matrix_world.inverted() @ mat
                    removed = True

        if not removed:
            for pbone in context.selected_pose_bones:
                obj = pbone.id_data
                mat = obj.matrix_world @ pbone.matrix
                loc, quat, scale = mat.decompose()
                rot = quat.to_euler()

                limit_loc = pbone.constraints.new(type='LIMIT_LOCATION')
                limit_loc.show_expanded = False
                limit_loc.name = "Lock Location"
                limit_loc.use_min_x = limit_loc.use_min_y = limit_loc.use_min_z = True
                limit_loc.use_max_x = limit_loc.use_max_y = limit_loc.use_max_z = True
                limit_loc.min_x = limit_loc.max_x = loc.x
                limit_loc.min_y = limit_loc.max_y = loc.y
                limit_loc.min_z = limit_loc.max_z = loc.z
                limit_loc.owner_space = 'WORLD'
                limit_loc.use_transform_limit = True

                limit_rot = pbone.constraints.new(type='LIMIT_ROTATION')
                limit_rot.show_expanded = False
                limit_rot.name = "Lock Rotation"
                limit_rot.use_limit_x = limit_rot.use_limit_y = limit_rot.use_limit_z = True
                limit_rot.min_x = limit_rot.max_x = rot.x
                limit_rot.min_y = limit_rot.max_y = rot.y
                limit_rot.min_z = limit_rot.max_z = rot.z
                limit_rot.owner_space = 'WORLD'
                limit_rot.use_transform_limit = True

                limit_scale = pbone.constraints.new(type='LIMIT_SCALE')
                limit_scale.show_expanded = False
                limit_scale.name = "Lock Scale"
                limit_scale.use_min_x = limit_scale.use_min_y = limit_scale.use_min_z = True
                limit_scale.use_max_x = limit_scale.use_max_y = limit_scale.use_max_z = True
                limit_scale.min_x = limit_scale.max_x = scale.x
                limit_scale.min_y = limit_scale.max_y = scale.y
                limit_scale.min_z = limit_scale.max_z = scale.z
                limit_scale.owner_space = 'WORLD'
                limit_scale.use_transform_limit = True

        return {'FINISHED'}

def draw_menu(self, context):
    self.layout.operator(GRET_OT_bone_lock.bl_idname)

def register(settings):
    bpy.utils.register_class(GRET_OT_bone_lock)
    bpy.types.VIEW3D_MT_pose_constraints.append(draw_menu)

def unregister():
    bpy.types.VIEW3D_MT_pose_constraints.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_bone_lock)
