import bpy
import bmesh

bl_info = {
    "name": "Shape Key Normalize",
    "description": "Resets Min/Max of shape keys while keeping the range of motion",
    "author": "greisane",
    "version": (0, 2, 0),
    "blender": (2, 90, 1),
    "location": "Properties Editor > Object Data > Shape Keys > Specials Menu",
    "category": "Mesh"
}

class OBJECT_OT_shape_key_normalize(bpy.types.Operator):
    #tooltip
    """Resets Min/Max of shape keys while keeping the range of motion"""

    bl_idname = 'object.shape_key_normalize'
    bl_label = "Normalize Shape Key"
    bl_context = 'objectmode'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (context.object
            and context.object.mode == "OBJECT"
            and context.object.type == "MESH"
            and context.object.active_shape_key_index > 0)

    def execute(self, context):
        obj = context.object
        this_sk = obj.active_shape_key

        # Store state
        saved_show_only_shape_key = obj.show_only_shape_key
        saved_active_shape_key_index = obj.active_shape_key_index
        saved_unmuted_shape_keys = [sk for sk in obj.data.shape_keys.key_blocks if not sk.mute]
        new_value = (this_sk.value - this_sk.slider_min) / (this_sk.slider_max - this_sk.slider_min)

        # Create a new shape key from the maximum range of motion by muting all except current
        # Can't use show_only_shape_key for this because it ignores the value slider
        for sk_idx, sk in enumerate(obj.data.shape_keys.key_blocks):
            sk.mute = sk_idx != obj.active_shape_key_index
        this_sk.slider_max = this_sk.slider_max - this_sk.slider_min
        this_sk.value = this_sk.slider_max
        obj.show_only_shape_key = False
        new_sk = obj.shape_key_add(name="New", from_mix=True)

        new_basis = None
        if this_sk.slider_min < 0.0:
            # Need to create new basis
            this_sk.value = this_sk.slider_min
            new_basis = obj.shape_key_add(name="New Basis", from_mix=True)
            bm = bmesh.new()
            bm.from_mesh(obj.data)
            new_basis_layer = bm.verts.layers.shape[new_basis.name]
            for vert in bm.verts:
                vert.co[:] = vert[new_basis_layer]
            bm.to_mesh(obj.data)
            bm.free()

        # Replace current with new
        this_sk.slider_min = 0.0
        this_sk.slider_max = 1.0
        this_sk.value = new_value
        for vert, new_vert in zip(this_sk.data, new_sk.data):
            vert.co[:] = new_vert.co
        obj.data.update()

        # Restore state
        obj.shape_key_remove(new_sk)
        if new_basis:
            obj.shape_key_remove(new_basis)
        obj.show_only_shape_key = saved_show_only_shape_key
        obj.active_shape_key_index = saved_active_shape_key_index
        for sk in saved_unmuted_shape_keys:
            sk.mute = False

        return {'FINISHED'}

def shape_key_specials_draw(self, context):
    self.layout.operator(OBJECT_OT_shape_key_normalize.bl_idname)

def register():
    bpy.utils.register_class(OBJECT_OT_shape_key_normalize)
    shape_key_menu = (bpy.types.MESH_MT_shape_key_specials if bpy.app.version < (2, 80) else
        bpy.types.MESH_MT_shape_key_context_menu)
    shape_key_menu.append(shape_key_specials_draw)

def unregister():
    bpy.utils.unregister_class(OBJECT_OT_shape_key_normalize)
    shape_key_menu = (bpy.types.MESH_MT_shape_key_specials if bpy.app.version < (2, 80) else
        bpy.types.MESH_MT_shape_key_context_menu)
    shape_key_menu.remove(shape_key_specials_draw)

if __name__ == '__main__':
    register()