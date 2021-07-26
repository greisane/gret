import bmesh
import bpy

from .helpers import bmesh_vertex_group_expand

class GRET_OT_vertex_group_expand(bpy.types.Operator):
    #tooltip
    """Expand weights for selected vertices"""

    bl_idname = 'gret.vertex_group_blur'
    bl_label = "Blur"
    bl_options = {'REGISTER', 'UNDO'}

    group_select_mode: bpy.props.EnumProperty(
        name="Subset",
        items=(
            ('ACTIVE', "Active Group", "The active vertex group"),
            ('BONE_DEFORM', "Deform Pose Bones", "All vertex groups assigned to deform bones"),
            ('ALL', "All", "All vertex groups"),
        ),
        description="Subset of vertex groups to modify",
        default='ACTIVE',
    )
    distance: bpy.props.FloatProperty(
        name="Distance",
        description="Smoothing distance",
        default=0.1,
        min=0.0,
    )
    power: bpy.props.FloatProperty(
        name="Power",
        description="Smoothing power",
        default=1.0,
        min=1.0,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj and obj.type == 'MESH'
            and context.mode == 'PAINT_WEIGHT' and obj.data.use_paint_mask_vertex)

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()

        # Get list of vertex groups to work on
        if self.group_select_mode == 'ACTIVE':
            vg_idxs = [obj.vertex_groups.active_index]
        elif self.group_select_mode == 'BONE_DEFORM':
            vg_idxs = []
            armature = obj.find_armature()
            if armature:
                bones = armature.data.bones
                vg_idxs = [vg.index for vg in obj.vertex_groups
                    if vg.name in bones and bones[vg.name].use_deform]
        elif self.group_select_mode == 'ALL':
            vg_idxs = list(range(len(obj.vertex_groups)))

        if vg_idxs:
            for vert in bm.verts:
                vert.tag = vert.select
            for vg_idx in vg_idxs:
                bmesh_vertex_group_expand(bm, vg_idx, distance=self.distance, power=self.power,
                    only_tagged=True)

        bm.to_mesh(obj.data)
        bm.free()
        context.area.tag_redraw()

        return{'FINISHED'}

def draw_menu(self, context):
    self.layout.operator(GRET_OT_vertex_group_expand.bl_idname)

def register(settings):
    bpy.utils.register_class(GRET_OT_vertex_group_expand)
    bpy.types.VIEW3D_MT_paint_weight.append(draw_menu)

def unregister():
    bpy.types.VIEW3D_MT_paint_weight.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_vertex_group_expand)
