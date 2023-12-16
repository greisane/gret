import bmesh
import bpy

from ..helpers import with_object, instant_modifier

class GRET_OT_cut_faces_smooth(bpy.types.Operator):
    """Subdivide selected faces and join the result with the surrounding geometry"""

    bl_idname = "gret.cut_faces_smooth"
    bl_label = "Cut Faces (Subdivide)"
    bl_context = 'mesh_edit'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.mode == 'EDIT_MESH'

    def execute(self, context):
        # Could make it work on all objects in mode but neither does Cut Faces so whatever
        obj = context.active_object
        mesh = obj.data

        # Copy wholesale, don't want to miss any data
        bpy.ops.object.editmode_toggle()
        new_mesh = mesh.copy()
        bm = bmesh.new()
        bm.from_mesh(new_mesh)
        crease_layer = bm.verts.layers.float.get('crease_vert') or bm.verts.layers.float.new('crease_vert')

        if not any(f.select for f in bm.faces):
            self.report({'WARNING'}, "No suitable selection found.")
            bm.free()
            bpy.data.meshes.remove(new_mesh)
            bpy.ops.object.editmode_toggle()
            return {'CANCELLED'}

        # Subdivide split mesh, creasing boundary vertices so that merging it back is painless
        bmesh.ops.delete(bm, geom=[f for f in bm.faces if not f.select], context='FACES')
        for vert in bm.verts:
            vert[crease_layer] += vert.is_boundary

        bm.to_mesh(new_mesh)
        bm.free()
        new_obj = bpy.data.objects.new(obj.name + "_split", new_mesh)
        new_obj.matrix_world = obj.matrix_world
        context.scene.collection.objects.link(new_obj)

        with instant_modifier(new_obj, type='SUBSURF') as subd_mod:
            subd_mod.levels = 1
            subd_mod.use_creases = True
            subd_mod.use_custom_normals = False

        # Anything over 2 unsubdivide iterations won't work due to the triangulation
        bm = bmesh.new()
        bm.from_mesh(new_mesh)
        bmesh.ops.unsubdivide(bm, verts=[v for v in bm.verts if v.is_boundary], iterations=2)
        # dissolve_faces doesn't produce the optimal result so just skip it for now
        #bmesh.ops.dissolve_faces(bm, faces=[f for f in bm.faces if any(e.is_boundary for e in f.edges)])
        for vert in bm.verts:
            vert.select = vert.is_boundary
        bm.select_flush(False)
        bm.to_mesh(new_mesh)

        # Join and delete temporary mesh
        with_object(bpy.ops.object.join, obj, [obj, new_obj])
        bpy.data.meshes.remove(new_mesh)

        bpy.ops.object.editmode_toggle()
        bm = bmesh.from_edit_mesh(mesh)

        bmesh.ops.delete(bm, geom=[f for f in bm.faces if f.select], context='FACES')
        bmesh.ops.remove_doubles(bm, verts=[v for v in bm.verts if v.select], dist=1e-5)

        bmesh.update_edit_mesh(mesh)

        return {'FINISHED'}

def draw_menu(self, context):
    self.layout.separator()
    self.layout.operator(GRET_OT_cut_faces_smooth.bl_idname)

def register(settings, prefs):
    if not prefs.mesh__enable_cut_faces_smooth:
        return False

    bpy.utils.register_class(GRET_OT_cut_faces_smooth)
    bpy.types.VIEW3D_MT_edit_mesh_faces.append(draw_menu)

def unregister():
    bpy.types.VIEW3D_MT_edit_mesh_faces.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_cut_faces_smooth)
