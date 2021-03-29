import bpy
import importlib
import sys

module_names = [
    'helpers',
    'collision',
    'graft',
    'remove_unused_vertex_groups',
    'retarget',
    'sculpt_selection',
    'shape_key_apply_modifiers',
    'shape_key_normalize',
    'vertex_color_mapping',
]
ensure_starts_with = lambda s, prefix: s if s.startswith(prefix) else prefix + s
module_names[:] = [ensure_starts_with(module_name, f'{__name__}.') for module_name in module_names]

for module_name in module_names:
    module = sys.modules.get(module_name)
    if module:
        importlib.reload(module)
    else:
        globals()[module_name] = importlib.import_module(module_name)

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
    # On registering, each module can add its own settings to the main group via add_property()
    for module_name in module_names:
        module = sys.modules.get(module_name)
        if hasattr(module, 'register'):
            module.register(settings)
        # if hasattr(module, 'draw'):
        #     module.register(GRET_PG_settings)

    bpy.utils.register_class(GRET_PT_mesh)

def unregister():
    bpy.utils.unregister_class(GRET_PT_mesh)

    for module_name in reversed(module_names):
        module = sys.modules.get(module_name)
        if hasattr(module, 'unregister'):
            module.unregister()
