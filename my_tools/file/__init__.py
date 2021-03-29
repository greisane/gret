import bpy
import importlib
import sys

module_names = [
    'deduplicate_materials',
    'replace_references',
]
ensure_starts_with = lambda s, prefix: s if s.startswith(prefix) else prefix + s
module_names[:] = [ensure_starts_with(module_name, f'{__name__}.') for module_name in module_names]

for module_name in module_names:
    module = sys.modules.get(module_name)
    if module:
        importlib.reload(module)
    else:
        globals()[module_name] = importlib.import_module(module_name)

def register(settings):
    # On registering, each module can add its own settings to the main group via add_property()
    for module_name in module_names:
        module = sys.modules.get(module_name)
        if hasattr(module, 'register'):
            module.register(settings)

def unregister():
    for module_name in reversed(module_names):
        module = sys.modules.get(module_name)
        if hasattr(module, 'unregister'):
            module.unregister()
