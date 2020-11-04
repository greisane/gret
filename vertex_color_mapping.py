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

def values_to_vcol(mesh, src_values, dst_vcol, dst_channel_idx):
    for loop_idx, loop in enumerate(mesh.loops):
        dst_vcol.data[loop_idx].color[dst_channel_idx] = src_values[loop.vertex_index]

def update_vcol_from_src(obj, src, dst_vcol, dst_channel_idx):
    mesh = obj.data
    if src == 'ZERO':
        values = [0.0] * len(mesh.vertices)
        values_to_vcol(mesh, values, dst_vcol, dst_channel_idx)
    elif src == 'ONE':
        values = [1.0] * len(mesh.vertices)
        values_to_vcol(mesh, values, dst_vcol, dst_channel_idx)
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
        values_to_vcol(mesh, values, dst_vcol, dst_channel_idx)

class MESH_OT_vertex_color_from_mappings(bpy.types.Operator):
    #tooltip
    """Refreshes vertex colors from source mappings"""

    bl_idname = 'mesh.vertex_color_from_mappings'
    bl_label = "Vertex Colors From Source Mappings"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH'

    def execute(self, context):
        obj = context.object
        mesh = context.object.data

        if all(src == 'NONE' for src in (obj.vcolr_src, obj.vcolg_src, obj.vcolb_src, obj.vcola_src)):
            # Avoid creating a vertex group if nothing would be done anyway
            return {'FINISHED'}

        vcol = mesh.vertex_colors.active if mesh.vertex_colors else mesh.vertex_colors.new()
        update_vcol_from_src(obj, obj.vcolr_src, vcol, 0)
        update_vcol_from_src(obj, obj.vcolg_src, vcol, 1)
        update_vcol_from_src(obj, obj.vcolb_src, vcol, 2)
        update_vcol_from_src(obj, obj.vcola_src, vcol, 3)
        mesh.update()

        return {'FINISHED'}

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
        bpy.ops.mesh.vertex_color_from_mappings()

def vcol_panel_draw(self, context):
    layout = self.layout
    obj = context.object

    box = layout.box()
    row = box.row()
    row.label(text="Vertex Color Map", icon='GROUP_VCOL')
    row.operator("mesh.vertex_color_from_mappings", icon='FILE_REFRESH', text="")

    col = box.column(align=True)
    col.prop(obj, 'vcolr_src', text="R")
    col.prop(obj, 'vcolg_src', text="G")
    col.prop(obj, 'vcolb_src', text="B")
    col.prop(obj, 'vcola_src', text="A")

classes = (
    MESH_OT_vertex_color_from_mappings,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Object.vcolr_src = bpy.props.EnumProperty(
        name="Vertex Color R Source",
        description="Source mapping to vertex color channel red",
        items=vcol_src_items,
        update=vcol_src_update,
    )
    bpy.types.Object.vcolg_src = bpy.props.EnumProperty(
        name="Vertex Color G Source",
        description="Source mapping to vertex color channel green",
        items=vcol_src_items,
        update=vcol_src_update,
    )
    bpy.types.Object.vcolb_src = bpy.props.EnumProperty(
        name="Vertex Color B Source",
        description="Source mapping to vertex color channel blue",
        items=vcol_src_items,
        update=vcol_src_update,
    )
    bpy.types.Object.vcola_src = bpy.props.EnumProperty(
        name="Vertex Color A Source",
        description="Source mapping to vertex color channel alpha",
        items=vcol_src_items,
        update=vcol_src_update,
    )

    bpy.types.DATA_PT_vertex_colors.append(vcol_panel_draw)

def unregister():
    bpy.types.DATA_PT_vertex_colors.remove(vcol_panel_draw)

    del bpy.types.Object.vcolr_src
    del bpy.types.Object.vcolg_src
    del bpy.types.Object.vcolb_src
    del bpy.types.Object.vcola_src

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
