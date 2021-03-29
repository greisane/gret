import bpy

module_names = [
    'actions',
    'pose_blender',
]
from gret import import_or_reload_modules
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
        return context.mode in {'OBJECT', 'POSE'} and obj and obj.type == 'ARMATURE'

    def draw(self, context):
        for draw_func in __class__.draw_funcs:
            draw_func(self, context)

def register(settings):
    for module in modules:
        if hasattr(module, 'register'):
            module.register(settings)
        # if hasattr(module, 'draw'):
        #     module.register(GRET_PG_settings)

    bpy.utils.register_class(GRET_PT_anim)

def unregister():
    bpy.utils.unregister_class(GRET_PT_anim)

    for module in reversed(modules):
        if hasattr(module, 'unregister'):
            module.unregister()
