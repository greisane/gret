import bpy
import bmesh

from .helpers import encode_shape_keys

class GRET_OT_shape_key_encode(bpy.types.Operator):
    """Encode shape key deltas and normals to UV channels"""

    bl_idname = 'gret.shape_key_encode'
    bl_label = "Encode Shape Key"
    bl_context = 'objectmode'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.mode == 'OBJECT' and obj.type == 'MESH' and obj.active_shape_key_index > 0

    def execute(self, context):
        obj = context.active_object
        encode_shape_keys(obj, obj.active_shape_key.name, keep=True)

        return {'FINISHED'}

def draw_menu(self, context):
    self.layout.operator(GRET_OT_shape_key_encode.bl_idname)

def register(settings, prefs):
    bpy.utils.register_class(GRET_OT_shape_key_encode)
    # Seldom used tool, don't add to menu to reduce clutter. Should usually be automatic on job export
    # bpy.types.MESH_MT_shape_key_context_menu.append(draw_menu)

def unregister():
    # bpy.types.MESH_MT_shape_key_context_menu.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_shape_key_encode)
