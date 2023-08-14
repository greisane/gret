from math import ceil, modf, pi, acos
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree
import bmesh
import bpy
import numpy as np
import sys

from ..math import SMALL_NUMBER, saturate, lerp, get_dist
from ..helpers import show_only
from .helpers import get_vcolor
from ..operator import SaveContext, SaveState

src_items = [
    ('NONE', "", "Leave the channel unchanged"),
    ('ZERO', "Zero", "Fill the channel with the minimum value"),
    ('ONE', "One", "Fill the channel with the maximum value"),
    ('VERTEX_GROUP', "Group", "Weight of specified vertex group"),
    ('BEVEL', "Bevel", "Vertex bevel weight"),
    ('HASH', "Random", "Random value based on the object's name"),
    ('PIVOTLOC', "Location", "Object pivot location"),
    ('PIVOTROT', "Rotation", "Object pivot rotation"),
    ('VERTEX', "Vertex", "Vertex world coordinates"),
    ('VALUE', "Value", "Constant value"),
    ('DISTANCE', "Distance", "Geometric distance to another mesh or curve"),
    ('CAVITY', "Cavity", "Approximation of the curvature of the mesh"),
]

component_items = [
    ('X', "X", "X component of the vector"),
    ('Y', "Y", "Y component of the vector"),
    ('Z', "Z", "Z component of the vector"),
]

def get_first_mapping(obj):
    if hasattr(obj, 'vertex_color_mapping') and obj.vertex_color_mapping:
        return obj.vertex_color_mapping[0]
    return None

def copy_mapping(obj, other_obj):
    mapping = get_first_mapping(obj)
    if mapping and not other_obj.vertex_color_mapping:
        other_obj.vertex_color_mapping.add()
    elif not mapping and other_obj.vertex_color_mapping:
        other_obj.vertex_color_mapping.clear()
    other_mapping = get_first_mapping(other_obj)

    if mapping and other_mapping:
        other_mapping.invert = mapping.invert
        for prefix in ('r', 'g', 'b', 'a'):
            for suffix in ('', 'invert', 'vertex_group', 'invert_vertex_group', 'component',
                'extents', 'value', 'object', 'along_curve', 'blur', 'scale'):
                property_name = f'{prefix}_{suffix}' if suffix else prefix
                setattr(other_mapping, property_name, getattr(mapping, property_name))

def values_to_vcol(mesh, src_values, dst_vcol, channel_idx, loops=False, invert=False):
    assert len(src_values) in (len(mesh.loops), len(mesh.vertices)), "Wrong number of elements"
    is_loops = len(src_values) == len(mesh.loops)

    for loop_idx, loop in enumerate(mesh.loops):
        value = saturate(src_values[loop_idx if is_loops else loop.vertex_index])
        if invert:
            value = 1.0 - value
        dst_vcol.data[loop_idx].color[channel_idx] = value

def get_distance_values(obj, src_obj, extents=0.0, along_curve=False):
    assert obj and src_obj
    mesh = obj.data
    obj_to_src = src_obj.matrix_world.inverted() @ obj.matrix_world
    dg = bpy.context.evaluated_depsgraph_get()
    values = 0.0

    if src_obj.type == 'MESH':
        bvh = BVHTree.FromObject(src_obj, dg)

        extents = max(extents, SMALL_NUMBER)
        values = [1.0] * len(mesh.vertices)
        for vert_idx, vert in enumerate(mesh.vertices):
            loc, norm, index, dist = bvh.find_nearest(obj_to_src @ vert.co, extents)
            if dist is not None:
                values[vert_idx] = dist / extents

    elif src_obj.type == 'CURVE' and not along_curve:
        # Convert curve to a temporary mesh. Curve API is very limited, doing the math here
        # would be a huge mess and likely slower. See https://blender.stackexchange.com/a/34276
        src_mesh = src_obj.to_mesh(preserve_all_data_layers=False, depsgraph=dg)
        bm = bmesh.new()
        bm.from_mesh(src_mesh)
        if not bm.faces:
            bmesh.ops.extrude_edge_only(bm, edges=bm.edges)
        bvh = BVHTree.FromBMesh(bm)
        bm.free()
        src_obj.to_mesh_clear()

        extents = max(extents, SMALL_NUMBER)
        values = [0.0] * len(mesh.vertices)
        for vert_idx, vert in enumerate(mesh.vertices):
            co, norm, index, dist = bvh.find_nearest(obj_to_src @ vert.co, extents)
            if dist is not None:
                values[vert_idx] = dist / extents

    elif src_obj.type == 'CURVE' and along_curve:
        # To find the progress along the curve it would be enough to look at the generated UVs
        # Again the API isn't very useful, so measure edge lengths to obtain distance instead
        with SaveContext(bpy.context, 'get_distance_values') as save:
            save.prop(src_obj.data, 'extrude bevel_depth', 0.0)
            src_mesh = src_obj.to_mesh(preserve_all_data_layers=False, depsgraph=dg)
        src_verts = src_mesh.vertices
        kd = KDTree(len(src_verts))
        for vert_idx, vert in enumerate(src_verts):
            kd.insert(vert.co, vert_idx)
        kd.balance()

        # Cache sum of edge lengths up to each vertex
        dist_along = [0.0] * len(src_verts)
        for vert_idx in range(1, len(src_verts)):
            edge_length = get_dist(src_verts[vert_idx].co, src_verts[vert_idx - 1].co)
            dist_along[vert_idx] = dist_along[vert_idx - 1] + edge_length
        src_obj.to_mesh_clear()
        total_dist_along = dist_along[-1]

        if total_dist_along > 0.0:
            extents = extents if extents > 0.0 else total_dist_along
            values = [0.0] * len(mesh.vertices)
            for vert_idx, vert in enumerate(mesh.vertices):
                co, index, dist = kd.find(obj_to_src @ vert.co)
                values[vert_idx] = dist_along[index] / extents

    return values

def get_cavity_values(obj, valley_factor=1.0, ridge_factor=1.0, valley_only=False, scale=1.0,
    blur_strength=1.0, blur_iterations=0, mask_vertex_group=None, invert_mask_vertex_group=False):
    # Original code and method by Keith "Wahooney" Boshoff
    # See release/scripts/startup/bl_operators/vertexpaint_dirt.py

    bm = bmesh.new()
    bm.from_mesh(obj.data)

    deform_layer = bm.verts.layers.deform.active
    mask_vg_index = obj.vertex_groups.find(mask_vertex_group or "")
    values = np.zeros(len(bm.verts))

    for vert in bm.verts:
        vec = Vector()
        co = vert.co

        # Get the direction of the vectors between the vertex and its connected vertices
        for edge in vert.link_edges:
            vec += (edge.other_vert(vert).co - co).normalized()
        num_connected = len(vert.link_edges)
        if num_connected == 0:
            value = 0.5  # Assume flat
        else:
            vec /= num_connected
            value = saturate(acos(vert.normal.dot(vec)) / pi)  # > 0.5 convex, < 0.5 concave

        value = max(lerp(0.5, 0.25, valley_factor), value)
        if not valley_only:
            value = min(lerp(0.5, 0.75, ridge_factor), value)
        values[vert.index] = value

    # Blur values
    vert_to_verts = [[e.other_vert(v).index for e in v.link_edges] for v in bm.verts]
    for _ in range(blur_iterations):
        orig_values = values.copy()
        for idx, link_verts in enumerate(vert_to_verts):
            for other_idx in link_verts:
                values[idx] += blur_strength * orig_values[other_idx]
            values[idx] /= len(link_verts) * blur_strength + 1
        del orig_values

    if mask_vg_index >= 0:
        if invert_mask_vertex_group:
            scale = [(1.0 - vert[deform_layer].get(mask_vg_index, 0.0)) * scale for vert in bm.verts]
        else:
            scale = [vert[deform_layer].get(mask_vg_index, 0.0) * scale for vert in bm.verts]

    values = 0.5 - (0.5 - values) * scale
    if valley_only:
        values = np.minimum(values, 0.5) * 2.0

    return values

def get_vcol_values(obj, mapping, prefix, src_vcol, src_channel_idx):
    mesh = obj.data
    src = getattr(mapping, prefix)
    values = 0.0

    if src == 'NONE':
        values = [vcolloop.color[src_channel_idx] for vcolloop in src_vcol.data] if src_vcol else 1.0

    if src == 'ZERO':
        values = 0.0

    elif src == 'ONE':
        values = 1.0

    elif src == 'VERTEX_GROUP':
        vertex_group = getattr(mapping, prefix + '_vertex_group')
        values = [0.0] * len(mesh.vertices)
        vgroup = obj.vertex_groups.get(vertex_group)

        if vgroup:
            vgroup_idx = vgroup.index
            for vert_idx, vert in enumerate(mesh.vertices):
                for vg in vert.groups:
                    if vg.group == vgroup_idx:
                        values[vert_idx] = vg.weight
                        break

    elif src == 'BEVEL':
        values = [vert.bevel_weight for vert in mesh.vertices]

    elif src == 'HASH':
        min_hash = -sys.maxsize - 1
        max_hash = sys.maxsize
        values = (hash(obj.name) - min_hash) / (max_hash - min_hash)

    elif src in {'PIVOTLOC', 'PIVOTROT', 'VERTEX'}:
        component = getattr(mapping, prefix + '_component')
        component_idx = ('X', 'Y', 'Z').index(component)
        extents = max(getattr(mapping, prefix + '_extents'), SMALL_NUMBER)
        remap_co = lambda co: (co[component_idx] / extents) + 0.5

        if src == 'PIVOTLOC':
            values = remap_co(obj.location)
        elif src == 'PIVOTROT':
            values = (obj.rotation_euler[component_idx] % pi) / pi
        elif src == 'VERTEX':
            m = obj.matrix_world
            values = [remap_co(m @ vert.co) for vert in mesh.vertices]

    elif src == 'VALUE':
        values = getattr(mapping, prefix + '_value')

    elif src == 'DISTANCE':
        src_obj = bpy.data.objects.get(getattr(mapping, prefix + '_object'))
        if src_obj:
            extents = getattr(mapping, prefix + '_extents')
            along_curve = getattr(mapping, prefix + '_along_curve')
            values = get_distance_values(obj, src_obj, extents, along_curve)
        else:
            values = 0.0

    elif src == 'CAVITY':
        blur_f, blur_i = modf(min(5.0, getattr(mapping, prefix + '_blur')))
        blur_f = max(0.001, blur_i / 5 + blur_f * (1 - blur_i / 5))
        blur_i = int(blur_i + ceil(blur_f))
        scale = getattr(mapping, prefix + '_scale')
        vertex_group = getattr(mapping, prefix + '_vertex_group')
        invert_vertex_group = getattr(mapping, prefix + '_invert_vertex_group')
        values = get_cavity_values(obj, blur_strength=blur_f, blur_iterations=blur_i, scale=scale,
            mask_vertex_group=vertex_group, invert_mask_vertex_group=invert_vertex_group)

    if type(values) is float:
        values = [values] * len(mesh.vertices)

    return values

def update_vcol_from(obj, mapping, prefix, src_vcol, dst_vcol, channel_idx, invert=False):
    values = get_vcol_values(obj, mapping, prefix, src_vcol, channel_idx)
    invert = invert != getattr(mapping, prefix + '_invert')
    values_to_vcol(obj.data, values, dst_vcol, channel_idx, invert=invert)

def update_vcols(obj, invert=False):
    mapping = get_first_mapping(obj)
    if not mapping:
        return
    if all(src == 'NONE' for src in (mapping.r, mapping.g, mapping.b, mapping.a)):
        # Avoid creating a vertex group if nothing would be done anyway
        return

    vcol = get_vcolor(obj, mapping.vertex_color_layer_name)
    invert = invert != mapping.invert
    update_vcol_from(obj, mapping, 'r', vcol, vcol, 0, invert)
    update_vcol_from(obj, mapping, 'g', vcol, vcol, 1, invert)
    update_vcol_from(obj, mapping, 'b', vcol, vcol, 2, invert)
    update_vcol_from(obj, mapping, 'a', vcol, vcol, 3, invert)
    obj.data.update()

class GRET_OT_vertex_color_mapping_refresh(bpy.types.Operator):
    """Creates or refreshes the active vertex color layer from source mappings"""

    bl_idname = 'gret.vertex_color_mapping_refresh'
    bl_label = "Refresh Vertex Color Mapping"
    bl_options = {'REGISTER', 'UNDO'}

    invert: bpy.props.BoolProperty(
        name="Invert",
        description="Invert the result",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        if obj.vertex_color_mapping:
            update_vcols(obj, invert=self.invert)

        return {'FINISHED'}

class GRET_OT_vertex_color_mapping_add(bpy.types.Operator):
    """Add vertex color mapping"""

    bl_idname = 'gret.vertex_color_mapping_add'
    bl_label = "Add Vertex Color Mapping"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        if obj.vertex_color_mapping:
            return {'CANCELLED'}

        mapping = obj.vertex_color_mapping.add()
        mapping.r = mapping.g = mapping.b = mapping.a = 'ZERO'

        return {'FINISHED'}

class GRET_OT_vertex_color_mapping_clear(bpy.types.Operator):
    """Clear vertex color mapping"""

    bl_idname = 'gret.vertex_color_mapping_clear'
    bl_label = "Clear Vertex Color Mapping"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        obj.vertex_color_mapping.clear()

        return {'FINISHED'}

class GRET_OT_vertex_color_mapping_copy_to_linked(bpy.types.Operator):
    """Copy vertex color mapping from active to linked objects"""

    bl_idname = 'gret.vertex_color_mapping_copy_to_linked'
    bl_label = "Copy Vertex Color Mapping to Linked"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.active_object

        for other_obj in bpy.data.objects:
            if other_obj != obj and other_obj.data == obj.data:
                copy_mapping(obj, other_obj)

        return {'FINISHED'}

class GRET_OT_vertex_color_mapping_copy_to_selected(bpy.types.Operator):
    """Copy vertex color mapping from active to selected objects"""

    bl_idname = 'gret.vertex_color_mapping_copy_to_selected'
    bl_label = "Copy Vertex Color Mapping to Selected"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.active_object

        for other_obj in context.selected_objects:
            if other_obj != obj:
                copy_mapping(obj, other_obj)

        return {'FINISHED'}

class GRET_OT_vertex_color_mapping_preview(bpy.types.Operator):
    """Preview this mask in the viewport. Click anywhere to stop previewing"""
    # This is a modal operator because it would be far too messy to revert the changes otherwise

    bl_idname = 'gret.vertex_color_mapping_preview'
    bl_label = "Preview Vertex Color Mapping"
    bl_options = {'INTERNAL'}

    prefix: bpy.props.StringProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def modal(self, context, event):
        if event.type in {'LEFTMOUSE', 'RIGHTMOUSE', 'ESC', 'RET', 'SPACE'}:
            self.save.revert()
            del self.save
            context.active_object.data.update()

            return {'CANCELLED'}

        elif event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'MIDDLEMOUSE', 'WHEELDOWNMOUSE',
            'WHEELUPMOUSE', 'LEFT_CTRL', 'LEFT_SHIFT', 'LEFT_ALT'}:
            # Only allow navigation keys. Kind of sucks, see https://developer.blender.org/T37427
            return {'PASS_THROUGH'}

        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        obj = context.active_object
        mesh = obj.data
        mapping = get_first_mapping(obj)
        if not mapping or self.prefix not in {'r', 'g', 'b', 'a', 'rgb'}:
            return {'CANCELLED'}

        try:
            self.save = save = SaveState(context, "gret.vertex_color_mapping_preview")
            save.selection()
            save.prop(mesh.attributes, 'active_color_index')
            src_vcol = mesh.vertex_colors.get(mapping.vertex_color_layer_name) or mesh.vertex_colors.active
            dst_vcol = get_vcolor(obj, "__preview")
            save.temporary(mesh.vertex_colors, dst_vcol)

            if self.prefix == 'rgb':
                src = "RGB"
                update_vcol_from(obj, mapping, 'r', src_vcol, dst_vcol, 0, invert=mapping.invert)
                update_vcol_from(obj, mapping, 'g', src_vcol, dst_vcol, 1, invert=mapping.invert)
                update_vcol_from(obj, mapping, 'b', src_vcol, dst_vcol, 2, invert=mapping.invert)
            else:
                src = f"{getattr(mapping, self.prefix)} ({self.prefix.upper()})"
                src_channel_idx = ('r', 'g', 'b', 'a').index(self.prefix)
                values = get_vcol_values(obj, mapping, self.prefix, src_vcol, src_channel_idx)
                invert = mapping.invert != getattr(mapping, self.prefix + '_invert')
                values_to_vcol(obj.data, values, dst_vcol, 0, invert=invert)
                values_to_vcol(obj.data, values, dst_vcol, 1, invert=invert)
                values_to_vcol(obj.data, values, dst_vcol, 2, invert=invert)
            mesh.update()
            mesh.attributes.active_color_index = len(obj.data.color_attributes) - 1

            # Set all 3D views to flat shading
            show_only(context, obj)
            save.viewports(
                header_text=f"Previewing {src} vertex color mapping",
                type='SOLID',
                light='FLAT',
                color_type='VERTEX',
                show_xray=False,
                show_shadows=False,
                show_cavity=False,
                use_dof=False,
                show_object_outline=False,
                show_overlays=False,
            )
        except:
            self.save.revert()
            del self.save
            raise

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

class GRET_PG_vertex_color_mapping(bpy.types.PropertyGroup):
    vertex_color_layer_name: bpy.props.StringProperty(
        name="Vertex Color Layer",
        description="Name of the target vertex color layer",
        default="",
    )
    r: bpy.props.EnumProperty(
        name="Vertex Color R Source",
        description="Source mapping to vertex color channel red",
        items=src_items,
    )
    g: bpy.props.EnumProperty(
        name="Vertex Color G Source",
        description="Source mapping to vertex color channel green",
        items=src_items,
    )
    b: bpy.props.EnumProperty(
        name="Vertex Color B Source",
        description="Source mapping to vertex color channel blue",
        items=src_items,
    )
    a: bpy.props.EnumProperty(
        name="Vertex Color A Source",
        description="Source mapping to vertex color channel alpha",
        items=src_items,
    )
    invert: bpy.props.BoolProperty(
        name="Invert",
        description="Invert all channels",
        default=False,
    )
    r_invert: bpy.props.BoolProperty(
        name="Invert",
        description="Invert this channel",
        default=False,
    )
    g_invert: bpy.props.BoolProperty(
        name="Invert",
        description="Invert this channel",
        default=False,
    )
    b_invert: bpy.props.BoolProperty(
        name="Invert",
        description="Invert this channel",
        default=False,
    )
    a_invert: bpy.props.BoolProperty(
        name="Invert",
        description="Invert this channel",
        default=False,
    )
    r_vertex_group: bpy.props.StringProperty(
        name="Vertex Group",
        description="Mask vertex group name",
    )
    g_vertex_group: bpy.props.StringProperty(
        name="Vertex Group",
        description="Mask vertex group name",
    )
    b_vertex_group: bpy.props.StringProperty(
        name="Vertex Group",
        description="Mask vertex group name",
    )
    a_vertex_group: bpy.props.StringProperty(
        name="Vertex Group",
        description="Mask vertex group name",
    )
    r_invert_vertex_group: bpy.props.BoolProperty(
        name="Invert Vertex Group",
        description="Invert mask vertex group influence",
        default=False,
    )
    g_invert_vertex_group: bpy.props.BoolProperty(
        name="Invert Vertex Group",
        description="Invert mask vertex group influence",
        default=False,
    )
    b_invert_vertex_group: bpy.props.BoolProperty(
        name="Invert Vertex Group",
        description="Invert mask vertex group influence",
        default=False,
    )
    a_invert_vertex_group: bpy.props.BoolProperty(
        name="Invert Vertex Group",
        description="Invert mask vertex group influence",
        default=False,
    )
    r_component: bpy.props.EnumProperty(
        name="Component",
        description="Source vector component",
        items=component_items,
        default='X',
    )
    g_component: bpy.props.EnumProperty(
        name="Component",
        description="Source vector component",
        items=component_items,
        default='Y',
    )
    b_component: bpy.props.EnumProperty(
        name="Component",
        description="Source vector component",
        items=component_items,
        default='Z',
    )
    a_component: bpy.props.EnumProperty(
        name="Component",
        description="Source vector component",
        items=component_items,
    )
    r_extents: bpy.props.FloatProperty(
        name="Extents",
        description="Maximum distance representable by this channel",
        default=4.0, min=0.0, precision=4, step=1, unit='LENGTH',
    )
    g_extents: bpy.props.FloatProperty(
        name="Extents",
        description="Maximum distance representable by this channel",
        default=4.0, min=0.0, precision=4, step=1, unit='LENGTH',
    )
    b_extents: bpy.props.FloatProperty(
        name="Extents",
        description="Maximum distance representable by this channel",
        default=4.0, min=0.0, precision=4, step=1, unit='LENGTH',
    )
    a_extents: bpy.props.FloatProperty(
        name="Extents",
        description="Maximum distance representable by this channel",
        default=4.0, min=0.0, precision=4, step=1, unit='LENGTH',
    )
    r_value: bpy.props.FloatProperty(
        name="Value",
        description="Constant value",
    )
    g_value: bpy.props.FloatProperty(
        name="Value",
        description="Constant value",
    )
    b_value: bpy.props.FloatProperty(
        name="Value",
        description="Constant value",
    )
    a_value: bpy.props.FloatProperty(
        name="Value",
        description="Constant value",
    )
    r_object: bpy.props.StringProperty(
        name="Object",
        description="Target object",
    )
    g_object: bpy.props.StringProperty(
        name="Object",
        description="Target object",
    )
    b_object: bpy.props.StringProperty(
        name="Object",
        description="Target object",
    )
    a_object: bpy.props.StringProperty(
        name="Object",
        description="Target object",
    )
    r_along_curve: bpy.props.BoolProperty(
        name="Along Curve",
        description="Calculate distance along the curve if the object is a curve",
        default=False,
    )
    g_along_curve: bpy.props.BoolProperty(
        name="Along Curve",
        description="Calculate distance along the curve if the object is a curve",
        default=False,
    )
    b_along_curve: bpy.props.BoolProperty(
        name="Along Curve",
        description="Calculate distance along the curve if the object is a curve",
        default=False,
    )
    a_along_curve: bpy.props.BoolProperty(
        name="Along Curve",
        description="Calculate distance along the curve if the object is a curve",
        default=False,
    )
    r_blur: bpy.props.FloatProperty(
        name="Blur Strength",
        description="Blur strength",
        min=0.0, max=5.0, default=0.0, subtype='FACTOR',
    )
    g_blur: bpy.props.FloatProperty(
        name="Blur Strength",
        description="Blur strength",
        min=0.0, max=5.0, default=0.0, subtype='FACTOR',
    )
    b_blur: bpy.props.FloatProperty(
        name="Blur Strength",
        description="Blur strength",
        min=0.0, max=5.0, default=0.0, subtype='FACTOR',
    )
    a_blur: bpy.props.FloatProperty(
        name="Blur Strength",
        description="Blur strength",
        min=0.0, max=5.0, default=0.0, subtype='FACTOR',
    )
    r_scale: bpy.props.FloatProperty(
        name="Scale",
        description="Contrast increase",
        default=1.0, soft_min=0.0, soft_max=10.0,
    )
    g_scale: bpy.props.FloatProperty(
        name="Scale",
        description="Contrast increase",
        default=1.0, soft_min=0.0, soft_max=10.0,
    )
    b_scale: bpy.props.FloatProperty(
        name="Scale",
        description="Contrast increase",
        default=1.0, soft_min=0.0, soft_max=10.0,
    )
    a_scale: bpy.props.FloatProperty(
        name="Scale",
        description="Contrast increase",
        default=1.0, soft_min=0.0, soft_max=10.0,
    )

def vcol_panel_draw(self, context):
    layout = self.layout
    obj = context.active_object

    if not obj.vertex_color_mapping:
        row = layout.row(align=True)
        row.operator('gret.vertex_color_mapping_add', icon='ADD')
        row.menu('GRET_MT_vertex_color_mapping', text='', icon='DOWNARROW_HLT')
    else:
        row = layout.row(align=True)
        row.operator('gret.vertex_color_mapping_clear', icon='X')
        row.menu('GRET_MT_vertex_color_mapping', text='', icon='DOWNARROW_HLT')

    def draw_vcol_layout(layout, mapping, prefix, icon):
        row = layout.row(align=True)
        row.prop(mapping, prefix, icon=icon, text="")
        src = getattr(mapping, prefix)
        ui_units_x = 16.0
        if src == 'VERTEX_GROUP':
            sub = row.split(align=True)
            sub.prop_search(mapping, prefix + '_vertex_group', obj, 'vertex_groups', text="")
            sub.ui_units_x = ui_units_x
        elif src == 'PIVOTROT':
            sub = row.split(align=True)
            sub.prop(mapping, prefix + '_component', text="")
            sub.ui_units_x = ui_units_x
        elif src in {'PIVOTLOC', 'VERTEX'}:
            sub = row.split(align=True)
            row2 = sub.row(align=True)
            row2.prop(mapping, prefix + '_component', text="")
            row2.prop(mapping, prefix + '_extents', text="")
            sub.ui_units_x = ui_units_x
        elif src == 'VALUE':
            sub = row.split(align=True)
            sub.prop(mapping, prefix + '_value', text="")
            sub.ui_units_x = ui_units_x
        elif src == 'DISTANCE':
            sub = row.split(align=True)
            row2 = sub.row(align=True)
            row2.prop_search(mapping, prefix + '_object', bpy.data, 'objects', text="")
            sub2 = sub.split(align=True)
            sub2.prop(mapping, prefix + '_extents', text="")
            sub2.prop(mapping, prefix + '_along_curve', icon='CURVE_PATH', text="")
            sub.ui_units_x = ui_units_x
        elif src == 'CAVITY':
            sub = row.split(align=True)
            row2 = sub.row(align=True)
            row2.prop_search(mapping, prefix + '_vertex_group', obj, 'vertex_groups', text="")
            row2.prop(mapping, prefix + '_invert_vertex_group', icon='ARROW_LEFTRIGHT', text="")
            sub2 = sub.split(align=True)
            sub2.prop(mapping, prefix + '_scale', text="")
            sub2.prop(mapping, prefix + '_blur', text="")
            sub.ui_units_x = ui_units_x
        row.prop(mapping, prefix + '_invert', icon='REMOVE', text="")
        op = row.operator('gret.vertex_color_mapping_preview', icon='HIDE_OFF', text="")
        op.prefix = prefix

    for mapping_idx, mapping in enumerate(obj.vertex_color_mapping):
        box = layout
        col = box.column(align=True)

        draw_vcol_layout(col, mapping, 'r', 'COLOR_RED')
        draw_vcol_layout(col, mapping, 'g', 'COLOR_GREEN')
        draw_vcol_layout(col, mapping, 'b', 'COLOR_BLUE')
        draw_vcol_layout(col, mapping, 'a', 'OUTLINER_DATA_FONT')

        col.separator()

        row = col.row(align=True)
        row.prop(mapping, 'vertex_color_layer_name', icon='GROUP_VCOL', text="")
        row.prop(mapping, 'invert', icon='REMOVE', text="")
        op = row.operator('gret.vertex_color_mapping_preview', icon='HIDE_OFF', text="")
        op.prefix = 'rgb'
        col.operator('gret.vertex_color_mapping_refresh', icon='FILE_REFRESH', text="Update Vertex Color")

class GRET_MT_vertex_color_mapping(bpy.types.Menu):
    bl_label = "Vertex Color Mapping Menu"

    def draw(self, context):
        layout = self.layout

        layout.operator('gret.vertex_color_mapping_copy_to_linked')
        layout.operator('gret.vertex_color_mapping_copy_to_selected')

classes = (
    GRET_MT_vertex_color_mapping,
    GRET_OT_vertex_color_mapping_add,
    GRET_OT_vertex_color_mapping_clear,
    GRET_OT_vertex_color_mapping_copy_to_linked,
    GRET_OT_vertex_color_mapping_copy_to_selected,
    GRET_OT_vertex_color_mapping_preview,
    GRET_OT_vertex_color_mapping_refresh,
    GRET_PG_vertex_color_mapping,
)

def register(settings, prefs):
    if not prefs.mesh__enable_vertex_color_mapping:
        return False

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Object.vertex_color_mapping = bpy.props.CollectionProperty(
        type=GRET_PG_vertex_color_mapping,
    )
    if hasattr(bpy.types, "DATA_PT_vertex_colors"):
        bpy.types.DATA_PT_vertex_colors.append(vcol_panel_draw)

def unregister():
    if hasattr(bpy.types, "DATA_PT_vertex_colors"):
        bpy.types.DATA_PT_vertex_colors.remove(vcol_panel_draw)
    del bpy.types.Object.vertex_color_mapping

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
