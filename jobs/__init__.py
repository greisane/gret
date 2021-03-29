import bpy
import importlib
import sys

module_names = [
    'export',
    'rig_export',
    'scene_export',
]
from gret import import_or_reload_modules
modules = import_or_reload_modules(module_names, __name__)

def register(settings):
    for module in modules:
        if hasattr(module, 'register'):
            module.register(settings)

def unregister():
    for module in reversed(modules):
        if hasattr(module, 'unregister'):
            module.unregister()
