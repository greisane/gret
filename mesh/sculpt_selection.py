import bmesh
import bpy

class GRET_OT_sculpt_selection(bpy.types.Operator):
    #tooltip
    """Sculpt the selected vertices"""

    bl_idname = "gret.sculpt_selection"
    bl_label = "Sculpt Selection"
    bl_context = 'mesh_edit'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.mode == 'EDIT_MESH'

    def execute(self, context):
        # Set the sculpt mask from the current edit-mode vertex selection
        obj = context.object
        bpy.ops.object.mode_set(mode='SCULPT')

        bm = bmesh.new()
        bm.from_mesh(obj.data)

        mask = bm.verts.layers.paint_mask.verify()
        bm.verts.ensure_lookup_table()
        for vert in obj.data.vertices:
            bm.verts[vert.index][mask] = 0.0 if vert.select else 1.0

        bm.to_mesh(obj.data)
        bm.free()

        return {'FINISHED'}

def draw_menu(self, context):
    self.layout.separator()
    self.layout.operator(GRET_OT_sculpt_selection.bl_idname)

def register(settings, prefs):
    bpy.utils.register_class(GRET_OT_sculpt_selection)
    bpy.types.VIEW3D_MT_select_edit_mesh.append(draw_menu)

def unregister():
    bpy.types.VIEW3D_MT_select_edit_mesh.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_sculpt_selection)
