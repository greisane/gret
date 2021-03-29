bl_info = {
    'name': "gret",
    'author': "greisane",
    'description': "",
    'version': (1, 0),
    'blender': (2, 92, 0),
    'location': "3D View > Tools",
    'category': "Object"
}

from bpy.app.handlers import persistent
import bpy
import importlib
import sys

def import_or_reload_modules(module_names, package_name):
    ensure_starts_with = lambda s, prefix: s if s.startswith(prefix) else prefix + s
    module_names = [ensure_starts_with(name, f'{package_name}.') for name in module_names]
    modules = []
    for module_name in module_names:
        module = sys.modules.get(module_name)
        if module:
            module = importlib.reload(module)
        else:
            module = globals()[module_name] = importlib.import_module(module_name)
        modules.append(module)
    return modules

module_names = [
    'helpers',
    'log',
    # 'stringcase',  # Third party, no need to register or reload
    'file',
    'material',
    'mesh',
    'rig',
    'anim',  # Depends on rig
    'jobs',  # Depends on mesh, rig
]
modules = import_or_reload_modules(module_names, __name__)

class GRET_PG_settings(bpy.types.PropertyGroup):
    @classmethod
    def add_property(cls, name, annotation):
        if not hasattr(cls, '__annotations__'):
            cls.__annotations__ = {}
        cls.__annotations__[name] = annotation

@persistent
def load_pre(dummy):
    bpy.types.Scene.my_tools = bpy.props.PointerProperty(type=GRET_PG_settings)

@persistent
def load_post(dummy):
    from gret.helpers import is_defaulted, save_properties, load_properties
    for scene in bpy.data.scenes:
        if not is_defaulted(scene.my_tools):
            print("Found old gret settings in file, restoring")
            load_properties(scene.gret, save_properties(scene.my_tools))
    del bpy.types.Scene.my_tools

backwards_compat = True

def register():
    # On registering, each module can add its own settings to the main group via add_property()
    for module in modules:
        if hasattr(module, 'register'):
            module.register(GRET_PG_settings)

    # Settings used to live in WindowManager, however pointer properties break with global undo
    bpy.utils.register_class(GRET_PG_settings)
    bpy.types.Scene.gret = bpy.props.PointerProperty(type=GRET_PG_settings)

    if backwards_compat:
        bpy.app.handlers.load_pre.append(load_pre)
        bpy.app.handlers.load_post.append(load_post)

def unregister():
    if backwards_compat:
        bpy.app.handlers.load_pre.remove(load_pre)
        bpy.app.handlers.load_post.remove(load_post)

    del bpy.types.Scene.gret
    bpy.utils.unregister_class(GRET_PG_settings)

    for module in reversed(modules):
        if hasattr(module, 'unregister'):
            module.unregister()

if __name__ == '__main__':
    register()
