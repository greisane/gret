from collections import namedtuple
from math import sqrt
import bpy

bl_info = {
    "name": "Apply Shape Keys with Vertex Groups",
    "author": "greisane",
    "description": "Applies vertex group filtering directly to shape key data",
    "version": (0, 1),
    "blender": (2, 80, 0),
    "location": "Properties Editor > Object Data > Shape Keys > Specials Menu",
    "category": "Mesh"
}

class OBJECT_OT_apply_shape_keys_with_vertex_groups(bpy.types.Operator):
    bl_idname = "object.apply_shape_keys_with_vertex_groups"
    bl_label = "Apply Shape Keys with Vertex Groups"
    bl_context = "objectmode"
    bl_description = "Applies vertex group filtering directly to shape key data"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.mode == "OBJECT" and context.object.type == "MESH"

    def execute(self, context):
        obj = context.object

        # Save the current state of the shape keys before they're deleted and
        # store the vertices of the resulting flattened meshes
        ShapeKeyInfo = namedtuple('ShapeKeyInfo', ['cos', 'interpolation', 'mute',
            'name', 'slider_max', 'slider_min', 'value', 'vertex_group'])
        shape_keys = obj.data.shape_keys.key_blocks[:] if obj.data.shape_keys else []

        for shape_key_index, shape_key in enumerate(shape_keys):
            if not shape_key.vertex_group:
                continue
            vertex_group = obj.vertex_groups[shape_key.vertex_group]
            shape_key.vertex_group = ''

            for i, v in enumerate(shape_key.data):
                v0 = shape_key.relative_key.data[i].co
                try:
                    v.co[:] = v0.lerp(v.co, vertex_group.weight(i))
                except RuntimeError:
                    v.co[:] = v0

        return {'FINISHED'}

def shape_key_specials_draw(self, context):
    layout = self.layout
    layout.operator(OBJECT_OT_apply_shape_keys_with_vertex_groups.bl_idname, text='Apply Vertex Groups')

def register():
    bpy.utils.register_class(OBJECT_OT_apply_shape_keys_with_vertex_groups)
    shape_key_menu = (bpy.types.MESH_MT_shape_key_specials if bpy.app.version < (2, 80) else
        bpy.types.MESH_MT_shape_key_context_menu)
    shape_key_menu.append(shape_key_specials_draw)

def unregister():
    bpy.utils.unregister_class(OBJECT_OT_apply_shape_keys_with_vertex_groups)
    shape_key_menu = (bpy.types.MESH_MT_shape_key_specials if bpy.app.version < (2, 80) else
        bpy.types.MESH_MT_shape_key_context_menu)
    shape_key_menu.remove(shape_key_specials_draw)

if __name__ == '__main__':
    register()