import bpy
import bmesh

class GRET_OT_shape_key_normalize(bpy.types.Operator):
    #tooltip
    """Resets Min and Max of shape keys while keeping the range of motion.
Basis will change if Min is negative"""

    bl_idname = 'gret.shape_key_normalize'
    bl_label = "Normalize Shape Key"
    bl_context = 'objectmode'
    bl_options = {'REGISTER', 'UNDO'}

    apply_vertex_group: bpy.props.BoolProperty(
        name="Apply Vertex Group",
        description="Apply vertex weight group",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.mode == 'OBJECT' and obj.type == 'MESH' and obj.active_shape_key_index > 0

    def execute(self, context):
        obj = context.active_object
        this_sk = obj.active_shape_key

        # Store state
        saved_show_only_shape_key = obj.show_only_shape_key
        saved_active_shape_key_index = obj.active_shape_key_index
        saved_unmuted_shape_keys = [sk for sk in obj.data.shape_keys.key_blocks if not sk.mute]
        saved_vertex_group = this_sk.vertex_group
        new_value = (this_sk.value - this_sk.slider_min) / (this_sk.slider_max - this_sk.slider_min)

        # Create a new shape key from the maximum range of motion by muting all except current
        # Can't use show_only_shape_key for this because it ignores the value slider
        for sk_idx, sk in enumerate(obj.data.shape_keys.key_blocks):
            sk.mute = sk_idx != obj.active_shape_key_index
        this_sk.slider_max = this_sk.slider_max - this_sk.slider_min
        this_sk.value = this_sk.slider_max
        if not self.apply_vertex_group:
            this_sk.vertex_group = ""
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
        this_sk.vertex_group = saved_vertex_group if not self.apply_vertex_group else ""
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

def draw_menu(self, context):
    self.layout.operator(GRET_OT_shape_key_normalize.bl_idname)

def register(settings, prefs):
    bpy.utils.register_class(GRET_OT_shape_key_normalize)
    bpy.types.MESH_MT_shape_key_context_menu.append(draw_menu)

def unregister():
    bpy.types.MESH_MT_shape_key_context_menu.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_shape_key_normalize)
