bl_info = {
    'name': "gret",
    'author': "greisane",
    'description': "",
    'version': (0, 2, 0),
    'blender': (2, 93, 0),
    'location': "3D View > Tools",
    'category': "Object"
}

from bpy.app.handlers import persistent
import bpy
import importlib
import sys

# Names here will be accessible as imports from other modules
class AddonPreferencesWrapper:
    def __getattr__(self, attr):
        return getattr(bpy.context.preferences.addons[__package__].preferences, attr)
prefs = AddonPreferencesWrapper()

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
    'log',
    'helpers',
    'patcher',
    'math',
    'rbf',
    'file',
    'material',
    'mesh',
    'rig',
    'uv',
    'anim',  # Depends on rig
    'jobs',  # Depends on mesh, rig
]
modules = import_or_reload_modules(module_names, __name__)

class GretAddonPreferences(bpy.types.AddonPreferences):
    # This must match the addon name, use '__package__'
    # when defining this in a submodule of a python package.
    bl_idname = __name__

    jobs_panel_enable: bpy.props.BoolProperty(
        name="Jobs Panel",
        description="Show the export jobs panel",
        default=False,
    )
    texture_bake_uv_layer_name: bpy.props.StringProperty(
        name="Default UV Layer",
        description="Name of the default UV layer for texture bakes",
        default="UVMap",
    )
    tileset_uv_layer_name: bpy.props.StringProperty(
        name="Default UV Layer",
        description="Name of the default UV layer for tileset paint",
        default="UVMap",
    )
    debug: bpy.props.BoolProperty(
        name="Debug Mode",
        description="Enables verbose output",
        default=False,
    )

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)

        col.prop(self, 'jobs_panel_enable')
        col.prop(self, 'debug')
        col.separator()

        col.label(text='Texture Bake:')
        col.prop(self, 'texture_bake_uv_layer_name')
        col.separator()

        col.label(text='Tile Paint:')
        col.prop(self, 'tileset_uv_layer_name')
        col.separator()

class GRET_PG_settings(bpy.types.PropertyGroup):
    @classmethod
    def add_property(cls, name, annotation):
        if not hasattr(cls, '__annotations__'):
            cls.__annotations__ = {}
        cls.__annotations__[name] = annotation

def register():
    # Register prefs first so that modules can access them through gret.prefs
    bpy.utils.register_class(GretAddonPreferences)

    # Each module adds its own settings to the main group via add_property()
    for module in modules:
        if hasattr(module, 'register'):
            module.register(GRET_PG_settings)
    bpy.utils.register_class(GRET_PG_settings)

    bpy.types.Scene.gret = bpy.props.PointerProperty(type=GRET_PG_settings)

def unregister():
    del bpy.types.Scene.gret

    bpy.utils.unregister_class(GRET_PG_settings)
    for module in reversed(modules):
        if hasattr(module, 'unregister'):
            module.unregister()

    bpy.utils.unregister_class(GretAddonPreferences)

if __name__ == '__main__':
    register()
