bl_info = {
    'name': "My Tools",
    'author': "greisane",
    'description': "",
    'version': (0, 2),
    'blender': (2, 80, 0),
    'location': "3D View > Tools > My Tools",
    'category': "My Tools"
}

import bpy
import importlib
import sys

module_names = [
    'helpers',
    'export',
    'scene_export',
    'rig_export',
    'character_tools',
    'action_tools',
    'scene_tools',
    'texture_baking',
]
ensure_starts_with = lambda s, prefix: s if s.startswith(prefix) else prefix + s
module_names[:] = [ensure_starts_with(module_name, f'{__name__}.') for module_name in module_names]

for module_name in module_names:
    module = sys.modules.get(module_name)
    if module:
        importlib.reload(module)
    else:
        globals()[module_name] = importlib.import_module(module_name)

class MY_PG_settings(bpy.types.PropertyGroup):
    @classmethod
    def add_property(cls, name, annotation):
        if not hasattr(cls, '__annotations__'):
            cls.__annotations__ = {}
        cls.__annotations__[name] = annotation

def register():
    # On registering, each module can add its own settings to the main group via add_property()
    for module_name in module_names:
        module = sys.modules.get(module_name)
        if hasattr(module, 'register'):
            module.register(MY_PG_settings)

    # Settings used to live in WindowManager, however pointer properties break with global undo
    bpy.utils.register_class(MY_PG_settings)
    bpy.types.Scene.my_tools = bpy.props.PointerProperty(type=MY_PG_settings)

def unregister():
    del bpy.types.Scene.my_tools
    bpy.utils.unregister_class(MY_PG_settings)

    for module_name in reversed(module_names):
        module = sys.modules.get(module_name)
        if hasattr(module, 'unregister'):
            module.unregister()

if __name__ == '__main__':
    register()