import bpy

module_names = [
    'helpers',
    'graft',  # So it appears at the top, fix this later
    'merge',
    'collision',
    'extra_objects',
    'remove_unused_vertex_groups',
    'retarget_mesh',
    'sculpt_selection',
    'shape_key_apply_modifiers',
    'shape_key_encode',
    'shape_key_normalize',
    'shape_key_select',
    'shape_key_store',
    'uv_texture_sync',
    'vertex_color_mapping',
    'vertex_group_bleed',
    'vertex_group_smooth_loops',
]
from .. import import_or_reload_modules
modules = import_or_reload_modules(module_names, __name__)

class GRET_PT_mesh(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "gret"
    bl_label = "Mesh"

    draw_funcs = []

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj and obj.type == 'MESH'

    def draw(self, context):
        for draw_func in __class__.draw_funcs:
            draw_func(self, context)

def register(settings):
    for module in modules:
        if hasattr(module, 'register'):
            module.register(settings)
        if hasattr(module, 'draw_panel'):
            GRET_PT_mesh.draw_funcs.append(module.draw_panel)

    bpy.utils.register_class(GRET_PT_mesh)

def unregister():
    bpy.utils.unregister_class(GRET_PT_mesh)

    GRET_PT_mesh.draw_funcs.clear()

    for module in reversed(modules):
        if hasattr(module, 'unregister'):
            module.unregister()
