import bpy

module_names = [
    'helpers',
    'auto_hide_arp',
    'autoname_bone_chain',
    'bone_lock',
    'propagate_bone_inherit_scale',
    'properties',
    'selection_sets',
    'retarget_armature',
]
from .. import import_or_reload_modules
modules = import_or_reload_modules(module_names, __name__)

class GRET_PT_rig(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "gret"
    bl_label = "Rig"

    draw_funcs = []

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj and obj.type == 'ARMATURE' and context.mode in {'OBJECT', 'POSE', 'EDIT_ARMATURE'}

    def draw(self, context):
        for draw_func in __class__.draw_funcs:
            draw_func(self, context)

def register(settings):
    for module in modules:
        if hasattr(module, 'register'):
            module.register(settings)
        if hasattr(module, 'draw_panel'):
            GRET_PT_rig.draw_funcs.append(module.draw_panel)

    bpy.utils.register_class(GRET_PT_rig)

def unregister():
    bpy.utils.unregister_class(GRET_PT_rig)

    GRET_PT_rig.draw_funcs.clear()

    for module in reversed(modules):
        if hasattr(module, 'unregister'):
            module.unregister()
