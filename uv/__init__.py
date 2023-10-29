import bpy

module_names = [
    # 'align_each',  # Needs work
    'helpers',
    'relax_loops',
    'uv_texture_sync',
    'uv_sheet',
    'uv_paint',  # Depends on uv_sheet (only so idnames aren't hardcoded)
    'uv_picker',  # Depends on uv_paint (same)
]
from .. import import_or_reload_modules, register_submodules, unregister_submodules
modules = import_or_reload_modules(module_names, __name__)

def register(settings, prefs):
    global registered_modules
    registered_modules = register_submodules(modules, settings)

def unregister():
    unregister_submodules(registered_modules)
