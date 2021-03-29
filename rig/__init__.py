import bpy

module_names = [
    'helpers',
    'auto_hide_arp',
    'propagate_bone_inherit_scale',
    'properties',
    'selection_sets',
]
from gret import import_or_reload_modules
modules = import_or_reload_modules(module_names, __name__)

class GRET_PT_rig(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "gret"
    bl_label = "Rig"

    draw_funcs = []

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'POSE'} and context.object

    def draw(self, context):
        for draw_func in __class__.draw_funcs:
            draw_func(self, context)

def register(settings):
    for module in modules:
        if hasattr(module, 'register'):
            module.register(settings)
        # if hasattr(module, 'draw'):
        #     module.register(GRET_PG_settings)

    bpy.utils.register_class(GRET_PT_rig)

def unregister():
    bpy.utils.unregister_class(GRET_PT_rig)

    for module in reversed(modules):
        if hasattr(module, 'unregister'):
            module.unregister()
