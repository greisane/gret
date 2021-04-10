import bmesh
import bpy
import itertools

from gret.uv.helpers import get_selection_loops
from gret.math import calc_bounds_2d

class GRET_OT_align_each(bpy.types.Operator):
    #tooltip
    """Separately align each vertex in two or more selected edge loops"""

    bl_idname = 'gret.align_each'
    bl_label = "Align Each"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj and obj.type == 'MESH' and obj.mode == 'EDIT' and obj.data.uv_layers
            and context.area.type == 'IMAGE_EDITOR'
            and not context.scene.tool_settings.use_uv_select_sync
            and context.scene.tool_settings.uv_select_mode in {'EDGE', 'VERTEX'})

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        uv_layer = bm.loops.layers.uv.verify()

        # TODO Shouldn't need loops, bag vertices by bounding boxes then sort by x/y before pairing
        loops = get_selection_loops(bm)

        # Align each
        if len(loops) >= 2:
            for points in zip(*loops):
                mn, mx, axis = calc_bounds_2d(points)
                center = (mn + mx) / 2
                for bmloop in itertools.chain.from_iterable(point.bmloops for point in points):
                    bmloop[uv_layer].uv[1-axis] = center[1-axis]

        bmesh.update_edit_mesh(obj.data)
        return {'FINISHED'}

def draw_menu(self, context):
    self.layout.operator(GRET_OT_align_each.bl_idname)

def register(settings):
    bpy.utils.register_class(GRET_OT_align_each)
    bpy.types.IMAGE_MT_uvs_align.append(draw_menu)

def unregister():
    bpy.types.IMAGE_MT_uvs_align.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_align_each)
