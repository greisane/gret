import bpy

module_names = [
    'helpers',
    'collision',
    'extra_objects',
    'graft',
    'remove_unused_vertex_groups',
    'retarget',
    'sculpt_selection',
    'shape_key_apply_modifiers',
    'shape_key_normalize',
    'vertex_color_mapping',
]
from gret import import_or_reload_modules
modules = import_or_reload_modules(module_names, __name__)

class GRET_PT_mesh(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "gret"
    bl_label = "Mesh"

    draw_funcs = []

    def draw(self, context):
        for draw_func in __class__.draw_funcs:
            draw_func(self, context)

def register(settings):
    for module in modules:
        if hasattr(module, 'register'):
            module.register(settings)
        # if hasattr(module, 'draw'):
        #     module.register(GRET_PG_settings)

    bpy.utils.register_class(GRET_PT_mesh)

def unregister():
    bpy.utils.unregister_class(GRET_PT_mesh)

    for module in reversed(modules):
        if hasattr(module, 'unregister'):
            module.unregister()
