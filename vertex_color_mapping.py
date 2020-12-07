import bpy

bl_info = {
    "name": "Vertex Color Mapping",
    "author": "greisane",
    "description": "Build vertex colors from other sources, like vertex groups",
    "version": (0, 1),
    "blender": (2, 90, 1),
    "location": "Properties Editor > Object Data > Vertex Colors",
    "category": "Mesh"
}

def values_to_vcol(mesh, src_values, dst_vcol, dst_channel_idx, invert=False):
    for loop_idx, loop in enumerate(mesh.loops):
        value = src_values[loop.vertex_index]
        if invert:
            value = 1.0 - value
        dst_vcol.data[loop_idx].color[dst_channel_idx] = value

def update_vcol_from_src(obj, src, dst_vcol, dst_channel_idx, invert=False):
    mesh = obj.data
    if src == 'ZERO':
        values = [0.0] * len(mesh.vertices)
        values_to_vcol(mesh, values, dst_vcol, dst_channel_idx, invert=invert)
    elif src == 'ONE':
        values = [1.0] * len(mesh.vertices)
        values_to_vcol(mesh, values, dst_vcol, dst_channel_idx, invert=invert)
    elif src.startswith('vg_'):
        # Get values from vertex group
        values = [0.0] * len(mesh.vertices)
        vgroup_name = src[3:]
        vgroup = obj.vertex_groups.get(vgroup_name)
        if vgroup:
            vgroup_idx = vgroup.index
            for vert_idx, vert in enumerate(mesh.vertices):
                for vg in vert.groups:
                    if vg.group == vgroup_idx:
                        values[vert_idx] = vg.weight
                        break
        values_to_vcol(mesh, values, dst_vcol, dst_channel_idx, invert=invert)

def vcol_src_items(self, context):
    obj = context.active_object
    items = []
    if obj and obj.type == 'MESH':
        items.extend([
            ('NONE', "", "Leave the channel unchanged"),
            ('ZERO', "Zero", "Fill the channel with the minimum value"),
            ('ONE', "One", "Fill the channel with the maximum value"),
        ])
        if obj.vertex_groups:
            items.extend([(f'vg_{vg.name}', vg.name, "Vertex group") for vg in obj.vertex_groups])
    return items

def vcol_src_update(self, context):
    obj = context.active_object
    if obj and obj.type == 'MESH' and obj.data.vertex_colors:
        # Automatically refresh mappings only if it wouldn't create a vcol layer
        bpy.ops.mesh.vertex_color_mapping_refresh()

class MESH_OT_vertex_color_mapping_refresh(bpy.types.Operator):
    #tooltip
    """Creates or refreshes the active vertex color layer from source mappings"""

    bl_idname = 'mesh.vertex_color_mapping_refresh'
    bl_label = "Refresh Vertex Color Mapping"
    bl_options = {'REGISTER', 'UNDO'}

    invert: bpy.props.BoolProperty(
        name="Invert",
        description="Invert the result",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH' and context.object.vertex_color_mapping

    def execute(self, context):
        obj = context.object
        mesh = context.object.data
        mapping = obj.vertex_color_mapping[0] if obj.vertex_color_mapping else None
        if not mapping:
            # No mapping, do nothing. Maybe it would be more correct to error instead
            return {'CANCELLED'}

        if all(src == 'NONE' for src in (mapping.r, mapping.g, mapping.b, mapping.a)):
            # Avoid creating a vertex group if nothing would be done anyway
            return {'CANCELLED'}

        invert = self.invert != mapping.invert
        vcol = mesh.vertex_colors.active if mesh.vertex_colors else mesh.vertex_colors.new()
        update_vcol_from_src(obj, mapping.r, vcol, 0, invert=invert)
        update_vcol_from_src(obj, mapping.g, vcol, 1, invert=invert)
        update_vcol_from_src(obj, mapping.b, vcol, 2, invert=invert)
        update_vcol_from_src(obj, mapping.a, vcol, 3, invert=invert)
        mesh.update()

        return {'FINISHED'}

class MESH_OT_vertex_color_mapping_add(bpy.types.Operator):
    #tooltip
    """Add vertex color mapping"""

    bl_idname = 'mesh.vertex_color_mapping_add'
    bl_label = "Add Vertex Color Mapping"
    bl_options = {'REGISTER', 'UNDO'}

    r: bpy.props.EnumProperty(
        name="Vertex Color R Source",
        description="Source mapping to vertex color channel red",
        items=vcol_src_items,
        update=vcol_src_update,
    )
    g: bpy.props.EnumProperty(
        name="Vertex Color G Source",
        description="Source mapping to vertex color channel green",
        items=vcol_src_items,
        update=vcol_src_update,
    )
    b: bpy.props.EnumProperty(
        name="Vertex Color B Source",
        description="Source mapping to vertex color channel blue",
        items=vcol_src_items,
        update=vcol_src_update,
    )
    a: bpy.props.EnumProperty(
        name="Vertex Color A Source",
        description="Source mapping to vertex color channel alpha",
        items=vcol_src_items,
        update=vcol_src_update,
    )

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH'

    def execute(self, context):
        if context.object.vertex_color_mapping:
            # Currently only allow only one mapping
            return {'CANCELLED'}

        mapping = context.object.vertex_color_mapping.add()
        mapping.r = self.r
        mapping.g = self.g
        mapping.b = self.b
        mapping.a = self.a

        return {'FINISHED'}

class MESH_OT_vertex_color_mapping_clear(bpy.types.Operator):
    #tooltip
    """Clear vertex color mapping"""

    bl_idname = 'mesh.vertex_color_mapping_clear'
    bl_label = "Clear Vertex Color Mapping"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH'

    def execute(self, context):
        context.object.vertex_color_mapping.clear()

        return {'FINISHED'}

class MESH_PG_vertex_color_mapping(bpy.types.PropertyGroup):
    r: bpy.props.EnumProperty(
        name="Vertex Color R Source",
        description="Source mapping to vertex color channel red",
        items=vcol_src_items,
        update=vcol_src_update,
    )
    g: bpy.props.EnumProperty(
        name="Vertex Color G Source",
        description="Source mapping to vertex color channel green",
        items=vcol_src_items,
        update=vcol_src_update,
    )
    b: bpy.props.EnumProperty(
        name="Vertex Color B Source",
        description="Source mapping to vertex color channel blue",
        items=vcol_src_items,
        update=vcol_src_update,
    )
    a: bpy.props.EnumProperty(
        name="Vertex Color A Source",
        description="Source mapping to vertex color channel alpha",
        items=vcol_src_items,
        update=vcol_src_update,
    )
    invert: bpy.props.BoolProperty(
        name="Invert Values",
        description="Make the result 1-value for each vertex color channel",
        default=False,
    )

def vcol_panel_draw(self, context):
    layout = self.layout
    obj = context.active_object
    mapping = obj.vertex_color_mapping[0] if obj.vertex_color_mapping else None

    if not mapping:
        layout.operator('mesh.vertex_color_mapping_add', icon='ADD')
    else:
        col = layout.column(align=True)
        col.operator('mesh.vertex_color_mapping_clear', icon='X')
        row = col.row(align=True)
        row.prop(mapping, 'r', icon='COLOR_RED', text="")
        row.prop(mapping, 'g', icon='COLOR_GREEN', text="")
        row.prop(mapping, 'b', icon='COLOR_BLUE', text="")
        row.prop(mapping, 'a', icon='OUTLINER_DATA_FONT', text="")
        row.prop(mapping, 'invert', icon='REMOVE', text="")
        row.operator('mesh.vertex_color_mapping_refresh', icon='FILE_REFRESH', text="")

classes = (
    MESH_OT_vertex_color_mapping_add,
    MESH_OT_vertex_color_mapping_clear,
    MESH_OT_vertex_color_mapping_refresh,
    MESH_PG_vertex_color_mapping,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Object.vertex_color_mapping = bpy.props.CollectionProperty(
        type=MESH_PG_vertex_color_mapping,
    )
    bpy.types.DATA_PT_vertex_colors.append(vcol_panel_draw)

def unregister():
    bpy.types.DATA_PT_vertex_colors.remove(vcol_panel_draw)
    del bpy.types.Object.vertex_color_mapping

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
