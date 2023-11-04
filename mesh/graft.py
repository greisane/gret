from itertools import dropwhile, chain
from math import pi
import bmesh
import bpy

from ..math import get_dist_sq
from .helpers import edit_mesh_elements, bmesh_vertex_group_bleed
from ..helpers import get_context, get_modifier, get_vgroup, select_only, TempModifier
from ..operator import SaveContext

# TODO Detect multiple source mesh boundaries and fail with a helpful message

class GraftError(Exception):
    pass

def do_graft(context, save, obj, dst_obj, expand=0, cuts=0, blend_distance=0.0, blend_power=0.0,
    face_map_name="", copy_normals=False, copy_vertex_groups=False, copy_uv_layers=False):
    """Bridge the open boundary of a source mesh with a target mesh.
    Returns (vertex indices, face indices) of the intersection on the target mesh."""

    # Initial setup
    dst_mesh = dst_obj.data
    obj_to_world = obj.matrix_world.copy()
    world_to_obj = obj.matrix_world.inverted()
    dst_to_obj = world_to_obj @ dst_obj.matrix_world
    obj_to_dst = dst_to_obj.inverted()

    blend_vg = get_vgroup(obj)
    save.temporary(obj.vertex_groups, blend_vg.name)
    bm = bmesh.new()
    bm.from_mesh(obj.data)

    # The source edge loop is currently the mesh boundary. Not doing any validation
    edges1 = [e for e in bm.edges if e.is_boundary]
    if not edges1:
        bm.free()
        raise GraftError("Needs an open boundary.")

    for edge in edges1:
        blend_vg.add([edge.verts[0].index, edge.verts[1].index], 1.0, 'REPLACE')

    # Push the boundary into the destination mesh and get the boolean intersection
    # Use fast since exact solver demands the object is manifold. Might need to close holes
    save.prop_foreach(obj.modifiers, 'show_viewport', False)
    wrap_mod = obj.modifiers.new(type='SHRINKWRAP', name="")
    wrap_mod.wrap_method = 'TARGET_PROJECT' # 'NEAREST_SURFACEPOINT'
    wrap_mod.wrap_mode = 'INSIDE'
    wrap_mod.target = dst_obj
    wrap_mod.vertex_group = blend_vg.name
    wrap_mod.offset = 0.01
    bool_mod = obj.modifiers.new(type='BOOLEAN', name="")
    bool_mod.operation = 'INTERSECT'
    bool_mod.solver = 'FAST'
    bool_mod.object = dst_obj
    dg = context.evaluated_depsgraph_get()
    bool_bm = bmesh.new()
    bool_bm.from_object(obj, dg)
    obj.modifiers.remove(bool_mod)
    obj.modifiers.remove(wrap_mod)

    # Because the result of the boolean operation mostly matches the destination geometry,
    # all that's needed is finding those same faces in the original mesh
    intersection_face_indices = []
    for face in bool_bm.faces:
        p = obj_to_dst @ face.calc_center_median()
        result, closest_point, normal, face_idx = dst_obj.closest_point_on_mesh(p)
        if result:
            if get_dist_sq(p, dst_mesh.polygons[face_idx].center) <= 0.05:
                intersection_face_indices.append(face_idx)
    bool_bm.free()
    del bool_bm

    if not intersection_face_indices:
        bm.free()
        raise GraftError("No intersection found.")

    # The target edge loop is the boundary of the intersection. Recreate it in working bmesh.
    # This part takes a while, could use some optimization
    edit_mesh_elements(dst_obj, 'FACE', intersection_face_indices)
    for _ in range(expand):
        bpy.ops.mesh.select_more()
    bpy.ops.object.editmode_toggle()
    intersection_vert_indices = [v.index for v in dst_mesh.vertices if v.select]
    bpy.ops.object.editmode_toggle()
    bpy.ops.mesh.region_to_loop()
    bpy.ops.object.editmode_toggle()
    idx_to_bmvert = {v.index: bm.verts.new(dst_to_obj @ v.co)
        for v in dst_mesh.vertices if v.select}
    bm.verts.index_update()
    edges2 = [bm.edges.new((idx_to_bmvert[e.vertices[0]], idx_to_bmvert[e.vertices[1]]))
        for e in dst_mesh.edges if e.select]
    bm.edges.index_update()
    fm_layer = bm.faces.layers.face_map.verify()

    try:
        ret = bmesh.ops.bridge_loops(bm, edges=edges1+edges2, use_pairs=False,
            use_cyclic=False, use_merge=False, merge_factor=0.5, twist_offset=0)
        new_faces = ret['faces']
        # for face in new_faces:
        #     face.smooth = True
    except RuntimeError:
        bm.free()
        raise GraftError("Couldn't bridge loops.")

    # If requested, fill a face map with the new faces
    if face_map_name:
        face_map = obj.face_maps.get(face_map_name) or obj.face_maps.new(name=face_map_name)
        for face in new_faces:
            face[fm_layer] = face_map.index

    if cuts > 0:
        bmesh.ops.subdivide_edges(bm, edges=ret['edges'], smooth=1.0, smooth_falloff='LINEAR', cuts=cuts)

    # Begin transferring data from the destination mesh
    deform_layer = bm.verts.layers.deform.verify()
    for edge in bm.edges:
        if edge.is_boundary:
            for vert in edge.verts:
                vert[deform_layer][blend_vg.index] = 1.0
    if copy_normals and blend_distance > 0.0:
        bmesh_vertex_group_bleed(bm, blend_vg.index, distance=blend_distance, power=blend_power)

    # Apply the result
    bm.to_mesh(obj.data)
    bm.free()

    # Transfer stuff
    ctx = get_context(obj)

    if copy_normals:
        obj.data.use_auto_smooth = True
        obj.data.auto_smooth_angle = pi
        bpy.ops.mesh.customdata_custom_splitnormals_clear(ctx)

        with TempModifier(obj, type='DATA_TRANSFER') as data_mod:
            data_mod.object = dst_obj
            data_mod.vertex_group = blend_vg.name
            data_mod.use_object_transform = True
            data_mod.use_loop_data = True
            data_mod.data_types_loops = {'CUSTOM_NORMAL'}
            data_mod.loop_mapping = 'POLYINTERP_NEAREST'

    if copy_vertex_groups or copy_uv_layers:
        with TempModifier(obj, type='DATA_TRANSFER') as data_mod:
            data_mod.object = dst_obj
            data_mod.use_object_transform = True
            if copy_vertex_groups:
                data_mod.use_vert_data = True
                data_mod.data_types_verts = {'VGROUP_WEIGHTS'}
                data_mod.vert_mapping = 'EDGEINTERP_NEAREST'
            if copy_uv_layers:
                data_mod.use_loop_data = True
                data_mod.data_types_loops = {'UV'}  # Automatically turns on use_poly_data
                data_mod.loop_mapping = 'POLYINTERP_NEAREST'
            bpy.ops.object.datalayout_transfer(ctx, modifier=data_mod.name)

    return intersection_vert_indices, intersection_face_indices

class GRET_OT_graft(bpy.types.Operator):
    """Connect boundaries of selected objects to the active object"""

    bl_idname = 'gret.graft'
    bl_label = "Graft"
    bl_options = {'REGISTER', 'UNDO', 'PRESET'}

    use_viewport_modifiers: bpy.props.BoolProperty(
        name="Use Viewport Modifiers",
        description="Use the target mesh as visible on the viewport",
        default=False,
    )
    expand: bpy.props.IntProperty(
        name="Expand",
        description="Expand the intersection area on the target mesh",
        default=0,
        min=0,
    )
    cuts: bpy.props.IntProperty(
        name="Number of Cuts",
        description="Number of cuts along the bridge faces",
        default=0,
        min=0,
        soft_max=100,
    )
    blend_distance: bpy.props.FloatProperty(
        name="Normal Blend Distance",
        description="Blend in normals from the target mesh",
        subtype='DISTANCE',
        default=0.0,
        min=0.0,
    )
    blend_power: bpy.props.FloatProperty(
        name="Normal Blend Power",
        description="Adjust the strength of normal blending",
        default=1.0,
        min=0.01,
    )
    face_map_name: bpy.props.StringProperty(
        name="Face Map Name",
        description="Optional name of a face map that contains the new geometry",
        default="Grafts",
    )
    vertex_group_name: bpy.props.StringProperty(
        name="Vertex Group Name",
        description="Optional name of a vertex group that contains the intersection (for masking)",
        default="_mask_graft_{object}",
    )
    copy_normals: bpy.props.BoolProperty(
        name="Copy Normals",
        description="Transfer normals from the target mesh",
        default=True,
    )
    copy_vertex_groups: bpy.props.BoolProperty(
        name="Copy Vertex Groups",
        description="Transfer vertex groups from the target mesh",
        default=True,
    )
    copy_uv_layers: bpy.props.BoolProperty(
        name="Copy Uv Layers",
        description="Transfer UV layers from the target mesh",
        default=True,
    )
    copy_modifiers: bpy.props.BoolProperty(
        name="Copy Modifiers",
        description="Transfer modifiers from the target mesh",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        orig_dst_obj = context.active_object
        objs = [obj for obj in context.selected_objects if obj.type == 'MESH' and obj != orig_dst_obj]

        if not objs:
            self.report({'ERROR'}, f"Select one or more meshes then the target object to graft them to.")
            return {'CANCELLED'}
        if not orig_dst_obj or orig_dst_obj.type != 'MESH' or orig_dst_obj not in context.selected_objects:
            self.report({'ERROR'}, f"Active object is not a selected mesh.")
            return {'CANCELLED'}

        with SaveContext(context, "gret.graft") as save:
            save.selection()

            if self.use_viewport_modifiers:
                # Get an evaluated version of the destination object
                # Can't use to_mesh because we will need to enter edit mode on it
                dg = context.evaluated_depsgraph_get()
                eval_dst_obj = orig_dst_obj.evaluated_get(dg)
                dst_mesh = bpy.data.meshes.new_from_object(eval_dst_obj)
                dst_obj = bpy.data.objects.new(eval_dst_obj.name, dst_mesh)
                dst_obj.matrix_world = eval_dst_obj.matrix_world
                del eval_dst_obj
                context.scene.collection.objects.link(dst_obj)
                save.temporary_bids([dst_mesh, dst_obj])
            else:
                dst_obj = orig_dst_obj
                dst_mesh = dst_obj.data
                save.prop_foreach(dst_obj.modifiers, 'show_viewport', False)

            for obj in objs:
                # Separate by loose parts
                select_only(context, obj)
                bpy.ops.object.editmode_toggle()
                bpy.ops.mesh.separate(type='LOOSE')
                bpy.ops.object.editmode_toggle()
                src_objs = context.selected_objects

                for src_obj in src_objs:
                    try:
                        intersection_vert_indices, _ = do_graft(context, save,
                            obj=src_obj,
                            dst_obj=dst_obj,
                            expand=self.expand,
                            cuts=self.cuts,
                            blend_distance=self.blend_distance,
                            blend_power=self.blend_power,
                            face_map_name=self.face_map_name,
                            copy_normals=self.copy_normals,
                            copy_vertex_groups=self.copy_vertex_groups,
                            copy_uv_layers=self.copy_uv_layers)
                    except GraftError as e:
                        self.report({'WARNING'}, f"Can't graft {obj.name}: {e}")
                        continue

                    # If requested, fill a vertex group with the verts of the intersection
                    try:
                        vg_name = self.vertex_group_name.format(object=obj.name)
                    except KeyError:
                        vg_name = self.vertex_group_name
                    if vg_name:
                        vg = get_vgroup(orig_dst_obj, vg_name, clean=False)
                        intersecting_verts = (dst_mesh.vertices[i] for i in intersection_vert_indices)
                        vg.add([v.index for v in intersecting_verts if not v.select], 1.0, 'REPLACE')

                # Rejoin loose parts
                if len(src_objs) > 1:
                    ctx = get_context(active_obj=obj, selected_objs=src_objs)
                    bpy.ops.object.join(ctx)

        # Transfer more stuff
        for obj in objs:
            obj.color = orig_dst_obj.color
            if orig_dst_obj.parent:
                obj_matrix_world = obj.matrix_world.copy()
                obj.parent = orig_dst_obj.parent
                obj.matrix_world = obj_matrix_world

        if self.copy_modifiers:
            ctx = get_context(active_obj=orig_dst_obj, selected_objs=objs)
            bpy.ops.object.make_links_data(ctx, type='MODIFIERS')

        return {'FINISHED'}

    def draw(self, context):
        obj = context.active_object
        layout = self.layout
        layout.use_property_split = True

        layout.prop(self, 'use_viewport_modifiers')
        layout.prop(self, 'expand')
        layout.prop(self, 'cuts')

        # layout.prop_search(self, 'vertex_group_name', obj, 'vertex_groups')
        layout.prop(self, 'vertex_group_name', icon='GROUP_VERTEX')
        layout.prop(self, 'face_map_name', icon='FACE_MAPS')

        row = layout.row(align=True, heading="Copy")
        row.prop(self, 'copy_normals', text="Norms.", toggle=1)
        row.prop(self, 'copy_vertex_groups', text="Groups", toggle=1)
        row.prop(self, 'copy_uv_layers', text="UVs", toggle=1)
        row.prop(self, 'copy_modifiers', text="Modif.", toggle=1)

        row = layout.row(align=True)
        row.prop(self, 'blend_distance', text="Blend Distance")
        row.prop(self, 'blend_power', text="Power")
        row.enabled = self.copy_normals

def draw_panel(self, context):
    layout = self.layout

    col = layout.column(align=True)
    col.operator('gret.graft', icon='AUTOMERGE_ON')

def register(settings, prefs):
    if not prefs.mesh__enable_graft:
        return False

    bpy.utils.register_class(GRET_OT_graft)

def unregister():
    bpy.utils.unregister_class(GRET_OT_graft)
