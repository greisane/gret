bl_info = {
    'name': "My Tools",
    'author': "greisane",
    'description': "",
    'version': (0, 1),
    'blender': (2, 80, 0),
    'location': "3D View > Tools > My Tools",
    'category': "My Tools"
}

if 'bpy' in locals():
    import importlib
    if 'helpers' in locals():
        importlib.reload(helpers)
    if 'settings' in locals():
        importlib.reload(settings)
    if 'export' in locals():
        importlib.reload(export)
    if 'character_tools' in locals():
        importlib.reload(character_tools)
    if 'action_tools' in locals():
        importlib.reload(action_tools)
    if 'scene_tools' in locals():
        importlib.reload(scene_tools)

import bpy
from . import settings
from . import export
from . import character_tools
from . import action_tools
from . import scene_tools

modules = (
    settings,
    export,
    character_tools,
    action_tools,
    scene_tools,
)

def register():
    for module in modules:
        module.register()

def unregister():
    for module in reversed(modules):
        module.unregister()

if __name__ == '__main__':
    register()