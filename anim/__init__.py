import bpy

module_names = [
    'actions',
    'channels_auto_group',
    'channels_delete_redundant',
    'channels_delete_unavailable',
    'pose_blender',
    'pose_markers',
]
from .. import import_or_reload_modules, register_submodules, unregister_submodules
modules = import_or_reload_modules(module_names, __name__)

class GRET_PT_animation(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "gret"
    bl_label = "Animation"

    draw_funcs = []

    @classmethod
    def poll(cls, context):
        return cls.bl_category and context.active_object and context.mode in {'OBJECT', 'POSE'}

    def draw(self, context):
        for draw_func in __class__.draw_funcs:
            draw_func(self, context)

def register(settings, prefs):
    bpy.utils.register_class(GRET_PT_animation)

    global registered_modules
    registered_modules = register_submodules(modules, settings, GRET_PT_animation.draw_funcs)

def unregister():
    unregister_submodules(registered_modules, GRET_PT_animation.draw_funcs)

    bpy.utils.unregister_class(GRET_PT_animation)