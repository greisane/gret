import bmesh
import bpy

bl_info = {
    "name": "Sculpt Mask from Vertex Selection",
    "author": "greisane",
    "description": "Sets the sculpt mask from the current edit-mode vertex selection",
    "version": (0, 1),
    "blender": (2, 83, 0),
    "location": "View3D > Select",
    "category": "Mesh",
}

class MESH_OT_sculpt_selection(bpy.types.Operator):
    #tooltip
    """Sculpt the selected vertices"""

    bl_idname = "mesh.sculpt_selection"
    bl_label = "Sculpt Selection"
    bl_context = 'mesh_edit'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.mode == 'EDIT_MESH'

    def execute(self, context):
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

def draw_func(self, context):
    self.layout.separator()
    self.layout.operator(MESH_OT_sculpt_selection.bl_idname)

def register():
    bpy.utils.register_class(MESH_OT_sculpt_selection)
    bpy.types.VIEW3D_MT_select_edit_mesh.append(draw_func)

def unregister():
    bpy.types.VIEW3D_MT_select_edit_mesh.remove(draw_func)
    bpy.utils.unregister_class(MESH_OT_sculpt_selection)

if __name__ == '__main__':
    register()