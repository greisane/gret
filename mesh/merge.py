from itertools import chain
from math import inf, cos, pi, sqrt, radians
from mathutils import Vector
import bmesh
import bpy
import numpy as np

from ..cache import lru_cache, hash_key
from ..helpers import get_collection, get_vgroup, with_object, instant_modifier, select_only
from ..math import get_direction_safe, grid_snap
from .helpers import bmesh_vertex_group_bleed_internal

normal_mask_vg_name = "_merge_mask"
temp_collection_name = "__merge"

@lru_cache(maxsize=1,
    key=lambda context, objs, dst_obj: 0)
def do_union(context, objs, dst_obj):
    """Boolean merge objects and return the resulting bmesh."""

    # Boolean modifier behaves very differently on compound meshes even with use_self
    select_only(context, objs)
    context.view_layer.objects.active = dst_obj
    bpy.ops.object.editmode_toggle()
    bpy.ops.mesh.separate(type='LOOSE')
    bpy.ops.object.editmode_toggle()
    objs[:] = context.selected_objects

    collection = get_collection(context, "__merge")

    # Preprocess meshes. Fill and taper holes, and identify mesh parts before merging
    for obj_idx, obj in enumerate(objs):
        if obj.data.users > 1:
            obj.data = obj.data.copy()
        collection.objects.link(obj)

        bm = bmesh.new()
        bm.from_mesh(obj.data)
        id_layer = bm.verts.layers.float.new('id')

        hole_faces = bmesh.ops.holes_fill(bm, edges=bm.edges)['faces']
        # Don't use center mode MEAN_WEIGHTED, breaks when face is too small (probably div by zero)
        bmesh.ops.poke(bm, faces=hole_faces, offset=1.0, center_mode='MEAN', use_relative_offset=True)

        bm.to_mesh(obj.data)
        bm.free()

    # Boolean merge
    with instant_modifier(dst_obj, type='BOOLEAN') as bool_mod:
        bool_mod.operation = 'UNION'
        bool_mod.operand_type = 'COLLECTION'
        bool_mod.collection = collection
        bool_mod.solver = 'EXACT'
        if bpy.app.version >= (2, 93):
            bool_mod.use_hole_tolerant = False

    bm = bmesh.new()
    bm.from_mesh(dst_obj.data)
    return bm

@lru_cache(maxsize=20)
def do_clean(union_bm, weld_distance=0.0, weld_uv_direction='XY', weld_uv_distance=0.0,
    weld_iterations=0, delete_non_manifold=False):
    """Reduce bmesh excess geometry and ensure it is watertight. Returns the resulting bmesh."""

    bm = union_bm.copy()
    sq_weld_uv_distance = weld_uv_distance * weld_uv_distance

    # Collapse edges based on the UV distance covered
    uv_layer = bm.loops.layers.uv.active
    if uv_layer and weld_iterations > 0 and weld_uv_distance > 0.0:
        for _ in range(weld_iterations):
            collapse_edges = []
            for edge in bm.edges:
                if edge.tag:
                    continue
                if any(len(vert.link_edges) > 5 for vert in edge.verts):
                    continue
                for bmloop in edge.link_loops:
                    uv0 = bmloop[uv_layer].uv
                    uv1 = bmloop.link_loop_next[uv_layer].uv
                    uvx, uvy = abs(uv1.x - uv0.x), abs(uv1.y - uv0.y)
                    if (weld_uv_direction == 'X' and uvx > uvy and uvx < weld_uv_distance
                        or weld_uv_direction == 'Y' and uvy > uvx and uvy < weld_uv_distance
                        or weld_uv_direction == 'XY' and uvx*uvx + uvy*uvy < sq_weld_uv_distance):
                        collapse_edges.append(edge)
                        for vert in edge.verts:
                            for other_edge in vert.link_edges:
                                other_edge.tag = True
                        break
            bmesh.ops.collapse(bm, edges=list(set(collapse_edges)), uvs=True)
            for edge in bm.edges:
                edge.tag = False

    if weld_distance > 0.0:
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=weld_distance)

    # Delete non-manifold edges, then verts. This will likely create holes, close them too
    # bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=0.0001)
    bmesh.ops.holes_fill(bm, edges=bm.edges)
    bmesh.ops.delete(bm, geom=[e for e in bm.edges if not e.is_manifold], context='EDGES')
    bmesh.ops.holes_fill(bm, edges=bm.edges)
    if delete_non_manifold:
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.is_manifold])
        bmesh.ops.holes_fill(bm, edges=bm.edges)

    # Get rid of excess verts
    # bmesh.ops.dissolve_limit(bm, angle_limit=radians(1.0),
    #     verts=list(set(chain.from_iterable(f.verts for f in bm.faces if len(f.verts) != 4))),
    #     edges=bm.edges, use_dissolve_boundaries=False, delimit=set())
    bmesh.ops.dissolve_degenerate(bm, dist=0.001, edges=bm.edges)
    bmesh.ops.connect_verts_concave(bm, faces=bm.faces)
    bmesh.ops.holes_fill(bm, edges=bm.edges)

    # Crudely close any remaining holes by collapsing boundaries
    for _ in range(2):
        bmesh.ops.collapse(bm, edges=[e for e in bm.edges if e.is_boundary], uvs=False)

    # Delete loose geometry
    bmesh.ops.delete(bm, geom=[f for f in bm.faces if all(e.is_boundary for e in f.edges)], context='FACES')
    bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces])

    return bm

@lru_cache(maxsize=20)
def do_curvature_mask(clean_bm, factor, distance):
    """Calculate mesh curvature at every vertex and bleed it to nearby vertices.
    Returns a list of (k, k, k) tuples for each vertex where k is a [0..1] factor."""

    if factor <= 0.0:
        return None

    # Shell factor starts at 1.0, not sure about the upper bound. Sort of like curvature
    vnor_mask = np.fromiter((vert.calc_shell_factor() for vert in clean_bm.verts), np.float,
        count=len(clean_bm.verts))
    # vnor_mask = (vnor_mask - 1.0) / (max(vnor_mask) - 1.0)  # Normalize

    # Map [1.5..2.0]->[0..factor]. These are good enough values, way too many parameters already
    min_value, max_value = 1.5, 2.0
    divisor = max_value - min_value
    vnor_mask = np.clip((vnor_mask - min_value) / divisor, 0.0, 1.0) * factor

    # Smooth out mask
    if distance > 0.0:
        def get_mask(vert):
            return sqrt(vnor_mask[vert.index])
        def set_mask(vert, value):
            vnor_mask[vert.index] = value * value
        bmesh_vertex_group_bleed_internal(clean_bm, get_mask, set_mask, distance)

    vnor_mask = np.stack(np.repeat(1.0 - vnor_mask, 3)).reshape(-1, 3)
    return vnor_mask

@lru_cache(maxsize=10,
    key=lambda clean_bm, iterations, mesh: hash_key(clean_bm, iterations))
def do_smooth_normals(clean_bm, iterations, mesh):
    """Smooth normals similarly to bpy.ops.mesh.smooth_normals.
    Returns the original normals and the resulting smoothed normals."""

    num_verts = len(mesh.vertices)

    vnors_orig = np.empty(num_verts * 3)
    mesh.vertices.foreach_get('normal', vnors_orig)
    vnors_orig = vnors_orig.reshape(-1, 3)
    vnors0 = vnors_orig.copy()
    vnors1 = vnors_orig.copy()

    connected_verts = [[] for _ in range(num_verts)]
    for edge in mesh.edges:
        vert_idx0, vert_idx1 = edge.vertices
        connected_verts[vert_idx0].append(vert_idx1)
        connected_verts[vert_idx1].append(vert_idx0)

    for _ in range(iterations):
        vnors0, vnors1 = vnors1, vnors0
        for vert_idx in range(num_verts):
            vnor = vnors0[vert_idx]
            for other_vert_idx in connected_verts[vert_idx]:
                vnor += vnors0[other_vert_idx]
            vnor /= np.sqrt(vnor.dot(vnor))  # Normalize
            vnors1[vert_idx] = vnor

    return vnors_orig, vnors1

class GRET_OT_merge(bpy.types.Operator):
    """Boolean merge one or more objects, cleaning up the result for normal transfer"""

    bl_idname = 'gret.merge'
    bl_label = "Merge & Clean"
    bl_options = {'REGISTER', 'UNDO', 'PRESET'}

    use_cache: bpy.props.BoolProperty(
        name="Use Cache",
        default=True,
        options={'HIDDEN'},
    )
    weld_uv_direction: bpy.props.EnumProperty(
        name="Weld UV Direction",
        description="""Measure UV distance covered by the edge in this direction.
Used to simplify the mesh along, and not across when merging hair strands""",
        items=[
            ('X', "Horizontal", "Only horizontal UV edges considered"),
            ('Y', "Vertical", "Only vertical UV edges considered"),
            ('XY', "Unconstrained", "All edges considered"),
        ],
        default='Y',
    )
    weld_uv_distance: bpy.props.FloatProperty(
        name="Weld UV Distance",
        description="UV distance below which to merge vertices",
        subtype='DISTANCE',
        default=0.0,
        min=0.0,
    )
    weld_iterations: bpy.props.IntProperty(
        name="Weld Iterations",
        description="",
        default=5,
        min=0,
    )
    weld_distance: bpy.props.FloatProperty(
        name="Weld Distance",
        description="Limit below which to merge vertices",
        subtype='DISTANCE',
        default=1e-4,
        min=0.0,
    )
    delete_non_manifold: bpy.props.BoolProperty(
        name="Delete Non-Manifold",
        description="Delete non manifold vertices. Uncheck if parts of the mesh disappear on merging",
        default=True,
    )
    smooth_iterations: bpy.props.IntProperty(
        name="Smooth Normal Iterations",
        description="Number of times to smooth normals",
        default=0,
        min=0,
        soft_max=20,
    )
    curvature_mask: bpy.props.FloatProperty(
        name="Curvature Mask",
        description="""Mask smooth normals by mesh curvature.
Use to leave normals intact on hair ends and crevices""",
        subtype='FACTOR',
        default=0.0,
        min=0.0,
        max=1.0,
    )
    curvature_distance: bpy.props.FloatProperty(
        name="Curvature Distance",
        description="Extend curvature mask",
        subtype='DISTANCE',
        default=0.05,
        min=0.0,
    )
    show_weld_by_uv = False  # Not a property because it shouldn't be saved with presets

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True

        layout.label(text="Geometry:")
        if self.show_weld_by_uv:
            layout.prop(self, 'weld_uv_direction', text="UV Direction")
            layout.prop(self, 'weld_uv_distance', text="UV Distance")
            layout.prop(self, 'weld_iterations', text="Iterations")
        else:
            col = layout.column(align=True)
            col.label(text="One or more objects have no UV layers.", icon='ERROR')
            layout.prop(self, 'weld_distance', text="Distance")
        layout.prop(self, 'delete_non_manifold', text="Delete Non-Manifold")

        layout.label(text="Normals:")
        layout.prop(self, 'smooth_iterations', text="Smooth")
        row = layout.row(align=True)
        layout.prop(self, 'curvature_mask')
        layout.prop(self, 'curvature_distance')

    def cache_clear(self):
        do_union.cache_clear()
        do_clean.cache_clear()
        do_curvature_mask.cache_clear()
        do_smooth_normals.cache_clear()

    def invoke(self, context, event):
        has_uvs = all(o.data.uv_layers.active for o in context.selected_objects if o.type == 'MESH')
        self.show_weld_by_uv = has_uvs
        self.cache_clear()
        return self.execute(context)

    def _execute(self, context, objs, dst_obj):
        dst_mesh = dst_obj.data

        # Boolean union of the input meshes. Result doesn't change with parameters so it's only
        # done once and cached, which greatly speeds things up when using Redo Last.
        union_bm = do_union(context, objs, dst_obj)
        union_bm.to_mesh(dst_mesh)
        mask_vg_index = get_vgroup(dst_obj, normal_mask_vg_name).index

        # Process the boolean result, fixing and removing excess geometry
        if dst_mesh.uv_layers.active and self.weld_iterations > 0 and self.weld_uv_distance > 0.0:
            clean_bm = do_clean(union_bm,
                weld_uv_direction=self.weld_uv_direction,
                weld_uv_distance=self.weld_uv_distance,
                weld_iterations=self.weld_iterations,
                delete_non_manifold=self.delete_non_manifold)
        else:
            clean_bm = do_clean(union_bm,
                weld_distance=self.weld_distance,
                delete_non_manifold=self.delete_non_manifold)

        curvature_mask = grid_snap(self.curvature_mask, 0.05)  # Less precision, less cache misses
        vnor_mask = do_curvature_mask(clean_bm, curvature_mask, self.curvature_distance)

        # Move it to a vertex group so data transfer can use it, works as visualization too
        if vnor_mask is not None:
            deform_layer = clean_bm.verts.layers.deform.verify()
            for vert_idx, vert in enumerate(clean_bm.verts):
                vert[deform_layer][mask_vg_index] = vnor_mask[vert_idx][0]

        clean_bm.to_mesh(dst_mesh)

        # Normals post-processing and transfer
        dst_mesh.use_auto_smooth = True
        dst_mesh.auto_smooth_angle = pi
        with_object(bpy.ops.mesh.customdata_custom_splitnormals_clear, dst_obj)

        if self.smooth_iterations > 0:
            vnors_orig, vnors_smooth = do_smooth_normals(clean_bm, self.smooth_iterations, dst_mesh)
            if vnor_mask is not None:
                # Mix based on curvature mask
                vnors_smooth = vnors_orig + (vnors_smooth - vnors_orig) * vnor_mask

            dst_mesh.normals_split_custom_set_from_vertices(vnors_smooth)
            dst_mesh.update()

        # Clean up
        for obj in objs:
            if obj == dst_obj:
                continue
            mesh = obj.data
            bpy.data.objects.remove(obj)
            bpy.data.meshes.remove(mesh)

    def execute(self, context):
        if not self.use_cache:
            self.cache_clear()

        obj = context.active_object
        objs = [o for o in context.selected_objects if o.type == 'MESH']

        if not objs:
            self.report({'ERROR'}, f"Select one or more meshes to merge.")
            return {'CANCELLED'}
        if not obj or obj.type != 'MESH' or obj not in context.selected_objects:
            self.report({'ERROR'}, f"Active object is not a selected mesh.")
            return {'CANCELLED'}
        if obj.data.shape_keys:
            self.report({'ERROR'}, f"Active object cannot have shape keys.")  # Why?
            return {'CANCELLED'}

        try:
            self._execute(context, objs, obj)
        finally:
            # Clean up
            tips_vg = obj.vertex_groups.get(normal_mask_vg_name)
            if tips_vg:
                obj.vertex_groups.remove(tips_vg)

            collection = bpy.data.collections.get(temp_collection_name)
            if collection:
                bpy.data.collections.remove(collection)

        return {'FINISHED'}

def draw_panel(self, context):
    layout = self.layout

    col = layout.column(align=True)
    col.operator('gret.merge', icon='MOD_BOOLEAN')

def register(settings, prefs):
    if not prefs.mesh__enable_merge:
        return False

    bpy.utils.register_class(GRET_OT_merge)

def unregister():
    bpy.utils.unregister_class(GRET_OT_merge)
