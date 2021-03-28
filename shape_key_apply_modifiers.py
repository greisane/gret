from collections import namedtuple
from math import sqrt
import bpy

bl_info = {
    "name": "Shape Key Apply Modifiers",
    "author": "greisane",
    "description": "Applies viewport modifiers while preserving shape keys",
    "version": (0, 8),
    "blender": (2, 90, 0),
    "location": "Properties Editor > Object Data > Shape Keys > Specials Menu",
    "category": "Mesh"
}

def mirror_merge(merge_x, merge_y, merge_z, merge_threshold=0.0):
    # TODO: Fails in some cases where mirror doesn't
    obj = bpy.context.object
    verts = obj.data.vertices
    half_merge_threshold = merge_threshold * 0.5
    saved_mode = bpy.context.mode

    # Need vertex mode to be set then object mode to actually select
    if bpy.context.mode != 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='VERT')
    bpy.ops.object.mode_set(mode='OBJECT')

    for vert_idx in range(len(verts)):
        v = verts[vert_idx]
        verts[vert_idx].select = ((merge_x and abs(v.co.x) <= half_merge_threshold)
            or (merge_y and abs(v.co.y) <= half_merge_threshold)
            or (merge_z and abs(v.co.z) <= half_merge_threshold))

    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.remove_doubles(threshold=merge_threshold, use_unselected=False)

    # Clean up
    if bpy.context.mode != saved_mode:
        bpy.ops.object.mode_set(mode=saved_mode)

class OBJECT_OT_shape_key_apply_modifiers(bpy.types.Operator):
    #tooltip
    """Applies viewport modifiers while preserving shape keys"""

    bl_idname = "object.shape_key_apply_modifiers"
    bl_label = "Apply Modifiers with Shape Keys"
    bl_context = "objectmode"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.object and context.object.type == 'MESH'

    def execute(self, context):
        obj = context.object

        if not any(m.show_viewport for m in obj.modifiers):
            # There are no modifiers to apply, don't do anything
            return {'FINISHED'}

        if obj.data.users > 1:
            # Make single user copy
            obj.data = obj.data.copy()

        # Disable mirror merge to avoid issues when shapekeys push vertices past the threshold
        merge_x = merge_y = merge_z = False
        merge_threshold = 0.0
        for modifier in obj.modifiers:
            if not modifier.show_viewport:
                continue
            if modifier.type == 'MIRROR' and modifier.use_mirror_merge:
                modifier.use_mirror_merge = False
                merge_x |= modifier.use_axis[0]
                merge_y |= modifier.use_axis[1]
                merge_z |= modifier.use_axis[2]
                merge_threshold = max(merge_threshold, modifier.merge_threshold)

        # Make a copy of the mesh. This is just for convenience to be able to
        # call from_existing(fcurve) instead of manually recreating the drivers
        mesh_copy = obj.data.copy()

        ShapeKeyInfo = namedtuple('ShapeKeyInfo', ['points', 'interpolation', 'mute',
            'name', 'slider_max', 'slider_min', 'value', 'vertex_group'])
        shape_keys = obj.data.shape_keys.key_blocks[:] if obj.data.shape_keys else []
        new_shape_keys = []

        saved_active_shape_key_index = obj.active_shape_key_index
        saved_show_only_shape_key = obj.show_only_shape_key

        for shape_key_index, shape_key in enumerate(shape_keys):
            # Create a temporary mesh of each shape key with modifiers applied,
            # then save the vertex coordinates (don't need anything else)
            saved_shape_key_mute = shape_key.mute
            shape_key.mute = False
            obj.show_only_shape_key = True
            obj.active_shape_key_index = shape_key_index

            # Disable vertex blend temporarily, vertex groups haven't been mirrored yet
            vertex_group = shape_key.vertex_group
            shape_key.vertex_group = ''

            dg = context.evaluated_depsgraph_get()
            eval_obj = obj.evaluated_get(dg)
            eval_mesh = eval_obj.to_mesh()

            new_shape_key = ShapeKeyInfo(
                points=[0.0] * (len(eval_mesh.vertices) * 3),
                interpolation=shape_key.interpolation,
                mute=saved_shape_key_mute,
                name=shape_key.name,
                slider_max=shape_key.slider_max,
                slider_min=shape_key.slider_min,
                value=shape_key.value,
                vertex_group=vertex_group
            )
            eval_mesh.vertices.foreach_get('co', new_shape_key.points)
            new_shape_keys.append(new_shape_key)

            # Clean up
            eval_obj.to_mesh_clear()

        # Clear shape keys to allow applying modifiers
        obj.shape_key_clear()

        for modifier in obj.modifiers[:]:
            if modifier.show_viewport:
                try:
                    bpy.ops.object.modifier_apply(modifier=modifier.name)
                except RuntimeError:
                    pass

        # Finally add the applied shape keys back
        for new_shape_key in new_shape_keys:
            shape_key = obj.shape_key_add()
            shape_key.interpolation = new_shape_key.interpolation
            shape_key.mute = new_shape_key.mute
            shape_key.name = new_shape_key.name
            shape_key.slider_max = new_shape_key.slider_max
            shape_key.slider_min = new_shape_key.slider_min
            shape_key.value = new_shape_key.value
            shape_key.vertex_group = new_shape_key.vertex_group

            if len(shape_key.data) * 3 != len(new_shape_key.points):
                self.report({'ERROR'}, "Old and new vertex counts for shape key '%s' did not match, "
                    "could be caused by a Mirror modifier with Merge on." % shape_key.name)
                continue

            shape_key.data.foreach_set('co', new_shape_key.points)

        # Manual mirror merge
        if merge_threshold > 0.0:
            mirror_merge(merge_x, merge_y, merge_z, merge_threshold)

        # Recreate drivers
        if mesh_copy.shape_keys and mesh_copy.shape_keys.animation_data:
            for fcurve in mesh_copy.shape_keys.animation_data.drivers:
                if obj.data.shape_keys.animation_data is None:
                    obj.data.shape_keys.animation_data_create()
                obj.data.shape_keys.animation_data.drivers.from_existing(src_driver=fcurve)

        # Clean up
        obj.show_only_shape_key = saved_show_only_shape_key
        obj.active_shape_key_index = saved_active_shape_key_index
        bpy.data.meshes.remove(mesh_copy)

        return {'FINISHED'}

def draw_func(self, context):
    self.layout.operator(OBJECT_OT_shape_key_apply_modifiers.bl_idname, icon='CHECKMARK')

def register():
    bpy.utils.register_class(OBJECT_OT_shape_key_apply_modifiers)
    bpy.types.MESH_MT_shape_key_context_menu.append(draw_func)

def unregister():
    bpy.types.MESH_MT_shape_key_context_menu.remove(draw_func)
    bpy.utils.unregister_class(OBJECT_OT_shape_key_apply_modifiers)

if __name__ == "__main__":
    register()