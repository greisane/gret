import bpy
import importlib
import sys

module_names = [
    'properties',
    'selection_sets',
]
ensure_starts_with = lambda s, prefix: s if s.startswith(prefix) else prefix + s
module_names[:] = [ensure_starts_with(module_name, f'{__name__}.') for module_name in module_names]

for module_name in module_names:
    module = sys.modules.get(module_name)
    if module:
        importlib.reload(module)
    else:
        globals()[module_name] = importlib.import_module(module_name)

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
    # On registering, each module can add its own settings to the main group via add_property()
    for module_name in module_names:
        module = sys.modules.get(module_name)
        if hasattr(module, 'register'):
            module.register(settings)
        # if hasattr(module, 'draw'):
        #     module.register(GRET_PG_settings)

    bpy.utils.register_class(GRET_PT_rig)

def unregister():
    bpy.utils.unregister_class(GRET_PT_rig)

    for module_name in reversed(module_names):
        module = sys.modules.get(module_name)
        if hasattr(module, 'unregister'):
            module.unregister()
