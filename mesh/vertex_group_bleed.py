import bmesh
import bpy

from .helpers import bmesh_vertex_group_bleed, get_operator_target_vertex_groups

class GRET_OT_vertex_group_bleed(bpy.types.Operator):
    """Expand weights for selected vertices via flood fill"""

    bl_idname = 'gret.vertex_group_bleed'
    bl_label = "Bleed"
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
    only_unlocked: bpy.props.BoolProperty(
        name="Unlocked Only",
        description="Ignore vertex groups that are locked",
        default=False,
    )
    distance: bpy.props.FloatProperty(
        name="Distance",
        description="Maximum smoothing distance",
        subtype='DISTANCE',
        default=1.0,
        min=0.0,
    )
    power: bpy.props.FloatProperty(
        name="Power",
        description="Scaling factor for new weights",
        default=1.0,
        min=0.01,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and context.mode == 'PAINT_WEIGHT'

    def execute(self, context):
        obj = context.active_object
        vg_idxs = get_operator_target_vertex_groups(obj, self.group_select_mode, self.only_unlocked)
        if not vg_idxs:
            return {'FINISHED'}

        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()

        if obj.data.use_paint_mask_vertex:
            for vert in bm.verts:
                vert.tag = vert.select
        for vg_idx in vg_idxs:
            bmesh_vertex_group_bleed(bm, vg_idx, distance=self.distance, power=self.power,
                only_tagged=obj.data.use_paint_mask_vertex)

        bm.to_mesh(obj.data)
        bm.free()
        context.area.tag_redraw()

        return{'FINISHED'}

def draw_menu(self, context):
    self.layout.operator(GRET_OT_vertex_group_bleed.bl_idname)

def register(settings, prefs):
    if not prefs.mesh__enable_vertex_group_bleed:
        return False

    bpy.utils.register_class(GRET_OT_vertex_group_bleed)
    bpy.types.VIEW3D_MT_paint_weight.append(draw_menu)

def unregister():
    bpy.types.VIEW3D_MT_paint_weight.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_vertex_group_bleed)
