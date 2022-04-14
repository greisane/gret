from math import pi
import bpy
import sys

from ..math import saturate

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
]

component_items = [
    ('X', "X", "X component of the vector"),
    ('Y', "Y", "Y component of the vector"),
    ('Z', "Z", "Z component of the vector"),
]

def get_first_mapping(obj):
    return obj.vertex_color_mapping[0] if obj.vertex_color_mapping else None

def copy_mapping(obj, other_obj):
    mapping = get_first_mapping(obj)
    if mapping and not other_obj.vertex_color_mapping:
        other_obj.vertex_color_mapping.add()
    elif not mapping and other_obj.vertex_color_mapping:
        other_obj.vertex_color_mapping.clear()
    other_mapping = get_first_mapping(other_obj)

    if mapping and other_mapping:
        other_mapping.r = mapping.r
        other_mapping.g = mapping.g
        other_mapping.b = mapping.b
        other_mapping.a = mapping.a
        other_mapping.invert = mapping.invert
        other_mapping.r_invert = mapping.r_invert
        other_mapping.g_invert = mapping.g_invert
        other_mapping.b_invert = mapping.b_invert
        other_mapping.a_invert = mapping.a_invert
        other_mapping.r_vertex_group = mapping.r_vertex_group
        other_mapping.g_vertex_group = mapping.g_vertex_group
        other_mapping.b_vertex_group = mapping.b_vertex_group
        other_mapping.a_vertex_group = mapping.a_vertex_group
        other_mapping.r_component = mapping.r_component
        other_mapping.g_component = mapping.g_component
        other_mapping.b_component = mapping.b_component
        other_mapping.a_component = mapping.a_component
        other_mapping.r_extents = mapping.r_extents
        other_mapping.g_extents = mapping.g_extents
        other_mapping.b_extents = mapping.b_extents
        other_mapping.a_extents = mapping.a_extents

def values_to_vcol(mesh, src_values, dst_vcol, dst_channel_idx, invert=False):
    for loop_idx, loop in enumerate(mesh.loops):
        value = saturate(src_values[loop.vertex_index])
        if invert:
            value = 1.0 - value
        dst_vcol.data[loop_idx].color[dst_channel_idx] = value

def update_vcol_from(obj, mapping, src_property, dst_vcol, dst_channel_idx, invert=False):
    mesh = obj.data
    values = None
    src = getattr(mapping, src_property)
    invert = invert != getattr(mapping, src_property + '_invert')

    if src == 'ZERO':
        values = 0.0
    elif src == 'ONE':
        values = 1.0
    elif src == 'VERTEX_GROUP':
        vertex_group = getattr(mapping, src_property + '_vertex_group')
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
        component = getattr(mapping, src_property + '_component')
        component_idx = ['X', 'Y', 'Z'].index(component)
        extents = getattr(mapping, src_property + '_extents')
        remap_co = lambda co: (co[component_idx] / extents) + 0.5
        if src == 'PIVOTLOC':
            values = remap_co(obj.location)
        elif src == 'PIVOTROT':
            values = (obj.rotation_euler[component_idx] % pi) / pi
        elif src == 'VERTEX':
            m = obj.matrix_world
            values = [remap_co(m @ vert.co) for vert in mesh.vertices]

    if type(values) is float:
        values = [values] * len(mesh.vertices)
    if values:
        assert len(values) == len(mesh.vertices)
        values_to_vcol(mesh, values, dst_vcol, dst_channel_idx, invert=invert)

def update_vcols(obj, invert=False):
    mapping = get_first_mapping(obj)
    if not mapping:
        return
    if all(src == 'NONE' for src in (mapping.r, mapping.g, mapping.b, mapping.a)):
        # Avoid creating a vertex group if nothing would be done anyway
        return

    mesh = obj.data
    vcol = mesh.vertex_colors.get(mapping.vertex_color_layer_name)
    if not vcol:
        vcol = mesh.vertex_colors.new(name=mapping.vertex_color_layer_name)

    invert = invert != mapping.invert
    update_vcol_from(obj, mapping, 'r', vcol, 0, invert)
    update_vcol_from(obj, mapping, 'g', vcol, 1, invert)
    update_vcol_from(obj, mapping, 'b', vcol, 2, invert)
    update_vcol_from(obj, mapping, 'a', vcol, 3, invert)
    mesh.update()

class GRET_OT_vertex_color_mapping_refresh(bpy.types.Operator):
    #tooltip
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
    #tooltip
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
    #tooltip
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
    #tooltip
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
    #tooltip
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

class GRET_PG_vertex_color_mapping(bpy.types.PropertyGroup):
    vertex_color_layer_name: bpy.props.StringProperty(
        name="Vertex Color Layer",
        description="Name of the target vertex color layer",
        default="Col",
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
        description="Vertex group to store in this channel",
    )
    g_vertex_group: bpy.props.StringProperty(
        name="Vertex Group",
        description="Vertex group to store in this channel",
    )
    b_vertex_group: bpy.props.StringProperty(
        name="Vertex Group",
        description="Vertex group to store in this channel",
    )
    a_vertex_group: bpy.props.StringProperty(
        name="Vertex Group",
        description="Vertex group to store in this channel",
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
        default=4.0, min=0.001, precision=4, step=1, unit='LENGTH',
    )
    g_extents: bpy.props.FloatProperty(
        name="Extents",
        description="Maximum distance representable by this channel",
        default=4.0, min=0.001, precision=4, step=1, unit='LENGTH',
    )
    b_extents: bpy.props.FloatProperty(
        name="Extents",
        description="Maximum distance representable by this channel",
        default=4.0, min=0.001, precision=4, step=1, unit='LENGTH',
    )
    a_extents: bpy.props.FloatProperty(
        name="Extents",
        description="Maximum distance representable by this channel",
        default=4.0, min=0.001, precision=4, step=1, unit='LENGTH',
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

    def draw_vcol_layout(layout, mapping, src_property, icon):
        row = layout.row(align=True)
        row.prop(mapping, src_property, icon=icon, text="")
        src = getattr(mapping, src_property)
        if src == 'VERTEX_GROUP':
            sub = row.split(align=True)
            sub.prop_search(mapping, src_property + '_vertex_group', obj, 'vertex_groups', text="")
            sub.ui_units_x = 14.0
        elif src == 'PIVOTROT':
            sub = row.split(align=True)
            sub.prop(mapping, src_property + '_component', text="")
            sub.ui_units_x = 14.0
        elif src in {'PIVOTLOC', 'VERTEX'}:
            sub = row.split(align=True)
            row2 = sub.row(align=True)
            row2.prop(mapping, src_property + '_component', text="")
            row2.prop(mapping, src_property + '_extents', text="")
            sub.ui_units_x = 14.0
        row.prop(mapping, src_property + '_invert', icon='REMOVE', text="")

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
    GRET_OT_vertex_color_mapping_refresh,
    GRET_PG_vertex_color_mapping,
)

def register(settings):
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
