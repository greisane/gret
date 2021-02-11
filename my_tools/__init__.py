bl_info = {
    'name': "My Tools",
    'author': "greisane",
    'description': "",
    'version': (0, 2),
    'blender': (2, 80, 0),
    'location': "3D View > Tools > My Tools",
    'category': "My Tools"
}

import sys
import importlib

module_names = [
    'helpers',
    'settings',
    'export',
    'scene_export',
    'rig_export',
    'character_tools',
    'action_tools',
    'scene_tools',
]
ensure_starts_with = lambda s, prefix: s if s.startswith(prefix) else prefix + s
module_names[:] = [ensure_starts_with(module_name, f'{__name__}.') for module_name in module_names]

for module_name in module_names:
    module = sys.modules.get(module_name)
    if module:
        importlib.reload(module)
    else:
        globals()[module_name] = importlib.import_module(module_name)

def register():
    for module_name in module_names:
        module = sys.modules.get(module_name)
        if hasattr(module, 'register'):
            module.register()

def unregister():
    for module_name in module_names:
        module = sys.modules.get(module_name)
        if hasattr(module, 'unregister'):
            module.unregister()

if __name__ == '__main__':
    register()