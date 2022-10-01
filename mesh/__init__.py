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
    'vertex_color_mapping',
    'vertex_group_bleed',
    'vertex_group_smooth_loops',
]
from .. import import_or_reload_modules, register_submodules, unregister_submodules
modules = import_or_reload_modules(module_names, __name__)

class GRET_PT_mesh(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "gret"
    bl_label = "Mesh"

    draw_funcs = []

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH' and cls.draw_funcs

    def draw(self, context):
        for draw_func in __class__.draw_funcs:
            draw_func(self, context)

def register(settings, prefs):
    global registered_modules
    registered_modules = register_submodules(modules, settings, GRET_PT_mesh.draw_funcs)

    bpy.utils.register_class(GRET_PT_mesh)

def unregister():
    bpy.utils.unregister_class(GRET_PT_mesh)

    unregister_submodules(registered_modules, GRET_PT_mesh.draw_funcs)
