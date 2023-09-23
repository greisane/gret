import bpy
import bmesh

from ..math import invlerp

class GRET_OT_shape_key_normalize(bpy.types.Operator):
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
        sk = obj.active_shape_key
        sk_value, sk_max, sk_min = sk.value, sk.slider_max, sk.slider_min
        sk.slider_min = 0.0
        sk.slider_max = 1.0
        sk.value = invlerp(sk_min, sk_max, sk_value)

        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.verts.layers.shape.verify()
        if sk.relative_key == obj.data.shape_keys.key_blocks[0]:
            base_shape_layer = None
        else:
            base_shape_layer = bm.verts.layers.shape[sk.relative_key.name]
        sk_layer = bm.verts.layers.shape[sk.name]
        if self.apply_vertex_group and sk.vertex_group:
            deform_layer = bm.verts.layers.deform.verify()
            vertex_group_index = obj.vertex_groups[sk.vertex_group].index
            sk.vertex_group = ''
        else:
            vertex_group_index = -1

        for vert in bm.verts:
            w = vert[deform_layer].get(vertex_group_index, 0.0) if vertex_group_index >= 0 else 1.0
            if base_shape_layer is None:
                co = vert.co.copy()
                if sk_min < 0.0:
                    vert.co = vert.co.lerp(vert[sk_layer], sk_min * w)
                vert[sk_layer] = co.lerp(vert[sk_layer], (sk_max - sk_min) * w)
            else:
                vert[sk_layer] = vert[base_shape_layer].lerp(vert[sk_layer], sk_max * w)
                if sk_min < 0.0:
                    vert[base_shape_layer] = vert[base_shape_layer].lerp(vert[sk_layer], sk_min * w)

        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()

        if sk_min < 0.0:
            self.report({'INFO'}, f"{sk.relative_key.name} was updated to accomodate negative minimum.")

        return {'FINISHED'}

def draw_menu(self, context):
    self.layout.operator(GRET_OT_shape_key_normalize.bl_idname)

def register(settings, prefs):
    if not prefs.mesh__enable_shape_key_normalize:
        return False

    bpy.utils.register_class(GRET_OT_shape_key_normalize)
    bpy.types.MESH_MT_shape_key_context_menu.append(draw_menu)

def unregister():
    bpy.types.MESH_MT_shape_key_context_menu.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_shape_key_normalize)
