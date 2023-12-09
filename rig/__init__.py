import bpy

module_names = [
    'helpers',
    'autoname_bone_chain',
    'bone_lock',
    'constraints_stretchto_reset',
    'properties',
    'retarget_armature',
    'selection_sets',
]
from .. import import_or_reload_modules, register_submodules, unregister_submodules
modules = import_or_reload_modules(module_names, __name__)

class GRET_PT_rig(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "gret"
    bl_label = "Rig"

    draw_funcs = []

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'ARMATURE'

    def draw(self, context):
        for draw_func in __class__.draw_funcs:
            draw_func(self, context)

def register(settings, prefs):
    bpy.utils.register_class(GRET_PT_rig)

    global registered_modules
    registered_modules = register_submodules(modules, settings, GRET_PT_rig.draw_funcs)

def unregister():
    unregister_submodules(registered_modules, GRET_PT_rig.draw_funcs)

    bpy.utils.unregister_class(GRET_PT_rig)
