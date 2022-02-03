from itertools import chain
from math import inf, cos, pi, radians
from mathutils import Vector
import bmesh
import bpy

from ..math import get_direction_safe
from ..helpers import get_context
from .helpers import get_vgroup, TempModifier, bmesh_vertex_group_bleed

def get_connected_verts_along_direction(from_vert, to_vert, max_angle=60.0):
    min_dot = cos(radians(max_angle))
    result = [to_vert]
    while True:
        direction = get_direction_safe(from_vert.co, to_vert.co)
        if direction.length_squared <= 0.0:
            break
        best_dot = -inf
        best_edge = None
        for edge in to_vert.link_edges:
            other_vert = edge.other_vert(to_vert)
            if other_vert == from_vert or other_vert.tag:
                continue
            edge_dir = get_direction_safe(to_vert.co, other_vert.co)
            if edge_dir.length_squared <= 0.0:
                continue
            dot = edge_dir.dot(direction)
            if dot > best_dot and dot >= min_dot:
                best_dot = dot
                best_edge = edge
        if not best_edge or best_edge.tag:
            break
        from_vert = to_vert
        to_vert = best_edge.other_vert(from_vert)
        to_vert.tag = True
        result.append(to_vert)
    return result

def delete_obj_with_mesh(obj):
    mesh = obj.data
    bpy.data.objects.remove(obj)
    bpy.data.meshes.remove(mesh)

class GRET_OT_merge(bpy.types.Operator):
    #tooltip
    """Boolean merge one or more objects, cleaning up the result for normal transfer"""

    bl_idname = 'gret.merge'
    bl_label = "Merge & Clean"
    bl_options = {'REGISTER', 'UNDO'}

    weld_distance: bpy.props.FloatProperty(
        name="Weld Distance",
        description="Limit below which to merge vertices",
        subtype='DISTANCE',
        default=0.005,
        min=0.0,
    )
    weld_only_loops: bpy.props.BoolProperty(
        name="Along Edge Loops Only",
        description="""Merge vertices only if they belong to the same edge loop.
Requires meshes to have an open boundary, which is used to find the edge loops""",
        default=True,
    )
    subdivisions: bpy.props.IntProperty(
        name="Subdivisions",
        description="Transfer normals from a subdivided surface",
        default=0,
        min=0,
    )
    cage_distance: bpy.props.FloatProperty(
        name="Cage Distance",
        description="Amount to displace geometry for cage generation",
        subtype='DISTANCE',
        default=0.01,
    )
    cage_show: bpy.props.BoolProperty(
        options={'HIDDEN'},
        name="Show Cage",
        description="Show the generated cage",
        default=False,
    )
    cage_mix_factor: bpy.props.FloatProperty(
        name="Cage Normals",
        description="Mix factor of normals transferred from the cage",
        subtype='FACTOR',
        default=0.0,
        min=0.0,
        max=1.0,
    )
    boundary_mask_distance: bpy.props.FloatProperty(
        name="Boundary Mask",
        description="Mask normals up to a certain distance. Useful to keep tip normals sharp",
        subtype='DISTANCE',
        default=0.0,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True

        layout.prop(self, 'weld_distance')
        layout.prop(self, 'weld_only_loops')

        layout.label(text="Normals:")
        layout.prop(self, 'subdivisions')
        row = layout.row(align=True)
        row.prop(self, 'cage_distance')
        row.prop(self, 'cage_show', icon='HIDE_OFF' if self.cage_show else 'HIDE_ON', text="")
        layout.prop(self, 'cage_mix_factor')
        layout.prop(self, 'boundary_mask_distance')

    def invoke(self, context, event):
        self.cached_boolean_bm = None
        self.cached_cage_settings = None
        self.cached_cage_bm = None
        self.cached_clean_settings = None
        self.cached_clean_bm = None
        self.cached_subdiv_settings = None
        self.cached_subdiv_bm = None
        self.cage_show = False
        return self.execute(context)

    def execute(self, context):
        dst_obj = context.active_object
        objs = [o for o in context.selected_objects if o.type == 'MESH']

        if not objs:
            self.report({'ERROR'}, f"Select one or more meshes to merge.")
            return {'CANCELLED'}
        if not dst_obj or dst_obj.type != 'MESH' or dst_obj not in context.selected_objects:
            self.report({'ERROR'}, f"Active object is not a selected mesh.")
            return {'CANCELLED'}

        if not self.cached_boolean_bm:
            # Boolean modifier behaves very differently on compound meshes even with use_self
            bpy.ops.object.editmode_toggle()
            bpy.ops.mesh.separate(type='LOOSE')
            bpy.ops.object.editmode_toggle()
            objs = [o for o in context.selected_objects if o.type == 'MESH']

            bool_collection = bpy.data.collections.new("__merge")
            context.scene.collection.children.link(bool_collection)

            # Preprocess meshes
            for obj in objs:
                if obj.data.users > 1:
                    obj.data = obj.data.copy()
                bool_collection.objects.link(obj)

                boundary_vg = get_vgroup(obj, "__boundary")
                boundary_vg_index = boundary_vg.index
                bm = bmesh.new()
                bm.from_mesh(obj.data)
                deform_layer = bm.verts.layers.deform.verify()

                # The use_hole_tolerant flag causes artifacts and generally doesn't work very well
                # Closed meshes produce better results, assuming the input meshes aren't too broken
                # Remember where the boundaries were before filling holes
                for vert in bm.verts:
                    if vert.is_boundary:
                        vert[deform_layer][boundary_vg_index] = 1.0
                bmesh.ops.holes_fill(bm, edges=bm.edges)

                bm.to_mesh(obj.data)
                bm.free()

            # Boolean merge
            with TempModifier(dst_obj, type='BOOLEAN') as bool_mod:
                bool_mod.operation = 'UNION'
                bool_mod.operand_type = 'COLLECTION'
                bool_mod.collection = bool_collection
                bool_mod.solver = 'EXACT'
                if bpy.app.version >= (2, 93):
                    bool_mod.use_hole_tolerant = False
            bpy.data.collections.remove(bool_collection)

            # Write to cache
            self.cached_boolean_bm = bmesh.new()
            self.cached_boolean_bm.from_mesh(dst_obj.data)
        else:
            # Read from cache
            boundary_vg = get_vgroup(dst_obj, "__boundary")
            self.cached_boolean_bm.to_mesh(dst_obj.data)
        boundary_vg_index = dst_obj.vertex_groups["__boundary"].index

        # Calculate voxelized cage
        cage_mesh = bpy.data.meshes.new(f"__{dst_obj.name}_cage")
        cage_obj = bpy.data.objects.new(cage_mesh.name, object_data=cage_mesh)
        cage_settings = (self.cage_distance, )
        if self.cage_distance > 0.0:
            if not self.cached_cage_bm or cage_settings != self.cached_cage_settings:
                self.cached_boolean_bm.to_mesh(cage_mesh)

                with TempModifier(cage_obj, type='DISPLACE') as displace_mod:
                    displace_mod.strength = self.cage_distance
                    displace_mod.mid_level = 0.0
                with TempModifier(cage_obj, type='REMESH') as remesh_mod:
                    remesh_mod.voxel_size = 0.02
                    remesh_mod.adaptivity = self.cage_distance
                    remesh_mod.use_smooth_shade = True

                # Write to cache
                self.cached_cage_settings = cage_settings
                if self.cached_cage_bm:
                    self.cached_cage_bm.free()
                self.cached_cage_bm = bmesh.new()
                self.cached_cage_bm.from_mesh(cage_mesh)
            else:
                # Read from cache
                self.cached_cage_bm.to_mesh(cage_mesh)

        clean_settings = (self.weld_distance, self.weld_only_loops)
        if not self.cached_clean_bm or clean_settings != self.cached_clean_settings:
            # Begin mesh cleanup after boolean
            bm = bmesh.new()
            bm.from_mesh(dst_obj.data)

            if self.weld_only_loops:
                # Very ugly because removing doubles invalidates indices. Should rewrite somehow
                def get_weight(vert, vg_idx):
                    return vert[deform_layer].get(vg_idx, 0.0)
                def next_weld_along_direction(bm, dist):
                    for vert in bm.verts:
                        if get_weight(vert, boundary_vg_index) == 1.0:
                            for edge in vert.link_edges:
                                if edge.tag:
                                    continue
                                edge.tag = True
                                verts = get_connected_verts_along_direction(vert, edge.other_vert(vert))
                                bmesh.ops.remove_doubles(bm, verts=verts, dist=dist)
                                return True
                    return False
                if self.weld_distance > 0.0:
                    deform_layer = bm.verts.layers.deform.verify()
                    for edge in bm.edges:
                        edge.tag = False
                    for vert in bm.verts:
                        vert.tag = False
                    while next_weld_along_direction(bm, self.weld_distance):
                        pass
            else:
                bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=self.weld_distance)

            # Delete non-manifold edges, then verts. This will likely create holes, close them too
            bmesh.ops.holes_fill(bm, edges=bm.edges)
            bmesh.ops.delete(bm, geom=[e for e in bm.edges if not e.is_manifold], context='EDGES')
            bmesh.ops.holes_fill(bm, edges=bm.edges)
            bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.is_manifold])
            bmesh.ops.holes_fill(bm, edges=bm.edges)

            # Get rid of excess verts
            bmesh.ops.dissolve_limit(bm, angle_limit=radians(1.0),
                verts=list(set(chain.from_iterable(f.verts for f in bm.faces if len(f.verts) != 4))),
                edges=bm.edges, use_dissolve_boundaries=False, delimit=set())
            bmesh.ops.dissolve_degenerate(bm, dist=0.001, edges=bm.edges)
            bmesh.ops.connect_verts_concave(bm, faces=bm.faces)
            bmesh.ops.holes_fill(bm, edges=bm.edges)

            # Crudely close any remaining holes by collapsing boundaries
            for _ in range(2):
                bmesh.ops.collapse(bm, edges=[e for e in bm.edges if e.is_boundary], uvs=False)

            # Delete loose geometry
            bmesh.ops.delete(bm, geom=[f for f in bm.faces if all(e.is_boundary for e in f.edges)], context='FACES')
            bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.link_faces])

            # Write to cache and invalidate further steps
            self.cached_clean_settings = clean_settings
            self.cached_clean_bm = bm.copy()
            if self.cached_subdiv_bm:
                self.cached_subdiv_bm.free()
                self.cached_subdiv_bm = None

            # Adjust boundary mask
            if self.boundary_mask_distance > 0.0:
                bmesh_vertex_group_bleed(bm, boundary_vg_index, distance=self.boundary_mask_distance, power=2.0)

            bm.to_mesh(dst_obj.data)
            bm.free()
        elif self.boundary_mask_distance > 0.0:
            # Read from cache adjusting boundary mask
            bm = self.cached_clean_bm.copy()

            bmesh_vertex_group_bleed(bm, boundary_vg_index, distance=self.boundary_mask_distance, power=2.0)

            bm.to_mesh(dst_obj.data)
            bm.free()
        else:
            # Read from cache
            self.cached_clean_bm.to_mesh(dst_obj.data)

        # Calculate subdivision surface
        if self.subdivisions > 0:
            subdiv_mesh = bpy.data.meshes.new(f"__{dst_obj.name}_subdiv")
            subdiv_obj = bpy.data.objects.new(subdiv_mesh.name, object_data=subdiv_mesh)
            subdiv_settings = (self.subdivisions, )
            if not self.cached_subdiv_bm or subdiv_settings != self.cached_subdiv_settings:
                self.cached_clean_bm.to_mesh(subdiv_mesh)

                with TempModifier(subdiv_obj, type='SUBSURF') as subdiv_mod:
                    subdiv_mod.levels = self.subdivisions

                # Write to cache
                self.cached_subdiv_settings = subdiv_settings
                if self.cached_subdiv_bm:
                    self.cached_subdiv_bm.free()
                self.cached_subdiv_bm = bmesh.new()
                self.cached_subdiv_bm.from_mesh(subdiv_mesh)
            else:
                # Read from cache
                self.cached_subdiv_bm.to_mesh(subdiv_mesh)

        # Normals post-processing and transfer
        ctx = get_context(dst_obj)
        dst_obj.data.use_auto_smooth = True
        dst_obj.data.auto_smooth_angle = pi
        bpy.ops.mesh.customdata_custom_splitnormals_clear(ctx)

        with TempModifier(dst_obj, type='WEIGHTED_NORMAL') as wnorm_mod:
            wnorm_mod.mode = 'FACE_AREA_WITH_ANGLE'

        if self.subdivisions > 0:
            with TempModifier(dst_obj, type='DATA_TRANSFER') as data_mod:
                data_mod.object = subdiv_obj
                data_mod.use_object_transform = False
                if self.boundary_mask_distance > 0.0:
                    data_mod.vertex_group = "__boundary"
                    data_mod.invert_vertex_group = True
                data_mod.use_loop_data = True
                data_mod.data_types_loops = {'CUSTOM_NORMAL'}
                data_mod.loop_mapping = 'POLYINTERP_NEAREST'
            delete_obj_with_mesh(subdiv_obj)

        if self.cage_distance > 0.0 and self.cage_mix_factor > 0.0:
            with TempModifier(dst_obj, type='DATA_TRANSFER') as data_mod:
                data_mod.object = cage_obj
                data_mod.use_object_transform = False
                data_mod.mix_factor = self.cage_mix_factor
                if self.boundary_mask_distance > 0.0:
                    data_mod.vertex_group = "__boundary"
                    data_mod.invert_vertex_group = True
                data_mod.use_loop_data = True
                data_mod.data_types_loops = {'CUSTOM_NORMAL'}
                data_mod.loop_mapping = 'POLYINTERP_NEAREST'
                # data_mod.mix_mode = 'MIX'  # Only valid to set mix mode after, huh

        # Clean up
        for obj in objs:
            if obj == dst_obj:
                continue
            delete_obj_with_mesh(obj)

        if self.cage_show:
            context.scene.collection.objects.link(cage_obj)
            cage_obj.matrix_world = dst_obj.matrix_world
        else:
            delete_obj_with_mesh(cage_obj)

        return {'FINISHED'}

def draw_panel(self, context):
    layout = self.layout

    col = layout.column(align=True)
    col.operator('gret.merge', icon='MOD_BOOLEAN')

def register(settings):
    bpy.utils.register_class(GRET_OT_merge)

def unregister():
    bpy.utils.unregister_class(GRET_OT_merge)
