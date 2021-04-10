import bmesh
import bpy
import itertools

from gret.uv.helpers import get_selection_bags, UVBag

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

        bags = []
        for bag in get_selection_bags(bm):
            bag = bag.to_chain()
            if not bag:
                self.report({'ERROR'}, "Works only on edge loops.")
                return {'CANCELLED'}
            bags.append(bag)
        if len(bags) < 2:
            self.report({'ERROR'}, "Select two or more edge loops.")
            return {'CANCELLED'}

        # Align each
        for items in zip(*bags):
            bag = UVBag(items)
            center = bag.calc_center()
            axis = 1 - bag.axis
            for loop in itertools.chain.from_iterable(item.loops for item in items):
                loop[uv_layer].uv[axis] = center[axis]

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
