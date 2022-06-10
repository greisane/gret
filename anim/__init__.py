import bpy

module_names = [
    'actions',
    'pose_blender',
]
from .. import import_or_reload_modules, register_submodules, unregister_submodules
modules = import_or_reload_modules(module_names, __name__)

class GRET_PT_anim(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "gret"
    bl_label = "Animation"

    draw_funcs = []

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj and obj.type == 'ARMATURE' and context.mode in {'OBJECT', 'POSE'}

    def draw(self, context):
        for draw_func in __class__.draw_funcs:
            draw_func(self, context)

def register(settings, prefs):
    global registered_modules
    registered_modules = register_submodules(modules, settings, GRET_PT_anim.draw_funcs)

    bpy.utils.register_class(GRET_PT_anim)

def unregister():
    bpy.utils.unregister_class(GRET_PT_anim)

    unregister_submodules(registered_modules, GRET_PT_anim.draw_funcs)
