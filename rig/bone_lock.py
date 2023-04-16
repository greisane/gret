import bpy

# I would like to make this into a more general space switching solution
# Right now it suffices to have a way of tweaking the parent of a bone without moving its children

class GRET_OT_bone_lock(bpy.types.Operator):
    """Add or remove constraints that lock the selected bones in place"""

    bl_idname = 'gret.bone_lock'
    bl_label = "Toggle Bone Lock"
    bl_options = {'REGISTER', 'UNDO'}

    remove: bpy.props.BoolProperty(
        name="Remove Lock",
        description="Remove lock constraints",
        default=True,
    )
    lock_location_x: bpy.props.BoolProperty(
        name="Lock Location X",
        description="Lock location X in world space",
        default=True,
    )
    lock_location_y: bpy.props.BoolProperty(
        name="Lock Location Y",
        description="Lock location Y in world space",
        default=True,
    )
    lock_location_z: bpy.props.BoolProperty(
        name="Lock Location X",
        description="Lock location Z in world space",
        default=True,
    )
    lock_rotation_x: bpy.props.BoolProperty(
        name="Lock Rotation X",
        description="Lock rotation X in world space",
        default=True,
    )
    lock_rotation_y: bpy.props.BoolProperty(
        name="Lock Rotation Y",
        description="Lock rotation Y in world space",
        default=True,
    )
    lock_rotation_z: bpy.props.BoolProperty(
        name="Lock Rotation X",
        description="Lock rotation Z in world space",
        default=True,
    )
    lock_scale_x: bpy.props.BoolProperty(
        name="Lock Scale X",
        description="Lock scale X in local space",
        default=True,
    )
    lock_scale_y: bpy.props.BoolProperty(
        name="Lock Scale Y",
        description="Lock scale Y in local space",
        default=True,
    )
    lock_scale_z: bpy.props.BoolProperty(
        name="Lock Scale X",
        description="Lock scale Z in local space",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE'

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True

        layout.prop(self, 'remove')
        sub = layout.split()
        sub.enabled = not self.remove
        col = sub.column()
        row = col.row(align=True, heading="Lock Location")
        row.prop(self, 'lock_location_x', text="X", toggle=1)
        row.prop(self, 'lock_location_y', text="Y", toggle=1)
        row.prop(self, 'lock_location_z', text="Z", toggle=1)
        row = col.row(align=True, heading="Lock Rotation")
        row.prop(self, 'lock_rotation_x', text="X", toggle=1)
        row.prop(self, 'lock_rotation_y', text="Y", toggle=1)
        row.prop(self, 'lock_rotation_z', text="Z", toggle=1)
        row = col.row(align=True, heading="Lock Scale")
        row.prop(self, 'lock_scale_x', text="X", toggle=1)
        row.prop(self, 'lock_scale_y', text="Y", toggle=1)
        row.prop(self, 'lock_scale_z', text="Z", toggle=1)

    def execute(self, context):
        if self.remove:
            for pbone in context.selected_pose_bones:
                for name in ("Lock Location", "Lock Rotation", "Lock Scale"):
                    constraint = pbone.constraints.get(name)
                    if constraint:
                        obj = pbone.id_data
                        mat = obj.matrix_world @ pbone.matrix
                        pbone.constraints.remove(constraint)
                        pbone.matrix = obj.matrix_world.inverted() @ mat
                        removed = True
        else:
            # When leaving some axes unlocked this makes the move tool behave oddly
            # For consistency leave it always off
            use_transform_limit = False

            for pbone in context.selected_pose_bones:
                obj = pbone.id_data
                mat = obj.matrix_world @ pbone.matrix
                loc, quat, scale = mat.decompose()
                rot = quat.to_euler()

                if self.lock_location_x or self.lock_location_y or self.lock_location_z:
                    limit_loc = pbone.constraints.get("Lock Location")
                    if not limit_loc:
                        limit_loc = pbone.constraints.new(type='LIMIT_LOCATION')
                        limit_loc.show_expanded = False
                        limit_loc.name = "Lock Location"
                    limit_loc.use_min_x = limit_loc.use_max_x = self.lock_location_x
                    limit_loc.use_min_y = limit_loc.use_max_y = self.lock_location_y
                    limit_loc.use_min_z = limit_loc.use_max_z = self.lock_location_z
                    limit_loc.min_x = limit_loc.max_x = loc.x
                    limit_loc.min_y = limit_loc.max_y = loc.y
                    limit_loc.min_z = limit_loc.max_z = loc.z
                    limit_loc.owner_space = 'WORLD'
                    limit_loc.use_transform_limit = use_transform_limit

                if self.lock_rotation_x or self.lock_rotation_y or self.lock_rotation_z:
                    limit_rot = pbone.constraints.get("Lock Rotation")
                    if not limit_rot:
                        limit_rot = pbone.constraints.new(type='LIMIT_ROTATION')
                        limit_rot.show_expanded = False
                        limit_rot.name = "Lock Rotation"
                    limit_rot.use_limit_x = self.lock_rotation_x
                    limit_rot.use_limit_y = self.lock_rotation_y
                    limit_rot.use_limit_z = self.lock_rotation_z
                    limit_rot.min_x = limit_rot.max_x = rot.x
                    limit_rot.min_y = limit_rot.max_y = rot.y
                    limit_rot.min_z = limit_rot.max_z = rot.z
                    limit_rot.owner_space = 'WORLD'
                    limit_rot.use_transform_limit = use_transform_limit

                if self.lock_scale_x or self.lock_scale_y or self.lock_scale_z:
                    limit_scale = pbone.constraints.get("Lock Scale")
                    if not limit_scale:
                        limit_scale = pbone.constraints.new(type='LIMIT_SCALE')
                        limit_scale.show_expanded = False
                        limit_scale.name = "Lock Scale"
                    limit_scale.use_min_x = limit_scale.use_max_x = self.lock_scale_x
                    limit_scale.use_min_y = limit_scale.use_max_y = self.lock_scale_y
                    limit_scale.use_min_z = limit_scale.use_max_z = self.lock_scale_z
                    limit_scale.min_x = limit_scale.max_x = scale.x
                    limit_scale.min_y = limit_scale.max_y = scale.y
                    limit_scale.min_z = limit_scale.max_z = scale.z
                    limit_scale.owner_space = 'LOCAL'  # World will never work as expected (no shear)
                    limit_scale.use_transform_limit = use_transform_limit

        return {'FINISHED'}

    def invoke(self, context, event):
        self.remove = False
        for pb in context.selected_pose_bones:
            if any(s in pb.constraints for s in ("Lock Location", "Lock Rotation", "Lock Scale")):
                self.remove = True
                break
        return self.execute(context)

def draw_menu(self, context):
    self.layout.operator(GRET_OT_bone_lock.bl_idname)

def register(settings, prefs):
    bpy.utils.register_class(GRET_OT_bone_lock)
    bpy.types.VIEW3D_MT_pose_constraints.append(draw_menu)

def unregister():
    bpy.types.VIEW3D_MT_pose_constraints.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_bone_lock)
