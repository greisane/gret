bl_info = {
    'name': "gret",
    'author': "greisane",
    'description': "",
    'version': (0, 3, 0),
    'blender': (3, 1, 0),
    'location': "3D View > Tools",
    'category': "Object"
}

from bpy.app.handlers import persistent
from collections import defaultdict
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
    'math',
    'drawing',
    'cache',
    'operator',
    'patcher',
    'rbf',
    # Submodules
    'file',
    'material',
    'mesh',
    'rig',
    'uv',  # Depends on material
    'anim',  # Depends on rig
    'jobs',  # Depends on mesh, rig
]
modules = import_or_reload_modules(module_names, __name__)
submodules = []

from .helpers import titlecase

def prefs_updated(self, context):
    for module in submodules:
        if hasattr(module, 'on_prefs_updated'):
            module.on_prefs_updated()

needs_restart = False
def registered_updated(self, context):
    global needs_restart
    needs_restart = True

class GretAddonPreferences(bpy.types.AddonPreferences):
    # This must match the addon name, use '__package__'
    # when defining this in a submodule of a python package.
    bl_idname = __name__

    jobs__panel_enable: bpy.props.BoolProperty(
        name="Enable Panel",
        description="Show the export jobs panel",
        default=False,
    )
    jobs__beep: bpy.props.BoolProperty(
        name="Beep At End",
        description="Beep after the job is done",
        default=True,
    )
    texture_bake__uv_layer_name: bpy.props.StringProperty(
        name="UV Layer",
        description="Name of the default UV layer for texture bakes",
        default="UVMap",
    )
    uv_paint__layer_name: bpy.props.StringProperty(
        name="UV Layer",
        description="Default UV layer to paint to. Leave empty to use the active UV layer",
        default="",
    )
    actions__show_frame_range: bpy.props.BoolProperty(
        name="Show Frame Range",
        description="Show custom frame range controls in the action panel",
        default=True,
    )
    actions__sync_frame_range: bpy.props.BoolProperty(
        name="Sync Frame Range",
        description="Keep preview range in sync with the action's custom frame range",
        default=True,
        update=prefs_updated,
    )
    rig__register_autoname_bone_chain: bpy.props.BoolProperty(
        name="Register \"Auto-Name Bone Chain\"",
        description="Automatically renames a chain of bones starting at the selected bone",
        default=True,
        update=registered_updated,
    )
    debug: bpy.props.BoolProperty(
        name="Debug Mode",
        description="Enables verbose output",
        default=False,
    )
    categories = None

    def draw(self, context):
        layout = self.layout

        if not self.categories:
            # Cache grouped props by category (the part left of the double underscore "__")
            d = defaultdict(list)
            for prop_name in self.__annotations__:
                cpos = prop_name.find("__")
                category_name = titlecase(prop_name[:cpos]) if cpos > 0 else "Miscellaneous"
                d[category_name].append(prop_name)
            self.categories = [(k, sorted(d[k])) for k in sorted(d.keys())]

        if needs_restart:
            alert_row = layout.row()
            alert_row.alert = True
            alert_row.operator("gret.save_userpref_and_quit_blender", icon='ERROR',
                text="Blender restart is required")

        # Display properties in two columns of boxes side to side
        # Avoiding use_property_split because the indent is too big
        split = layout.split(factor=0.5)
        boxes = split.column(align=True)
        boxes2 = split.column(align=True)
        for category_name, prop_names in self.categories:
            box = boxes.box()
            # box = box.column(align=True)
            box.label(text=category_name + ":", icon='DOT')
            split = box.split(factor=0.05)
            split.separator()
            col = split.column(align=True)
            for prop_name in prop_names:
                col.prop(self, prop_name)
            col.separator()
            boxes, boxes2 = boxes2, boxes

class GRET_PG_settings(bpy.types.PropertyGroup):
    @classmethod
    def add_property(cls, name, annotation):
        if not hasattr(cls, '__annotations__'):
            cls.__annotations__ = {}
        cls.__annotations__[name] = annotation

class GRET_OT_save_userpref_and_quit_blender(bpy.types.Operator):
    #tooltip
    """Make the current preferences default then quit blender"""

    bl_idname = 'gret.save_userpref_and_quit_blender'
    bl_label = "Save Preferences and Quit"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        bpy.ops.wm.save_userpref()
        bpy.ops.wm.quit_blender()

        return {'FINISHED'}

@persistent
def load_post(_):
    prefs_updated(bpy.context.preferences.addons[__package__].preferences, bpy.context)

def register():
    # Register prefs first so that modules can access them through gret.prefs
    bpy.utils.register_class(GretAddonPreferences)

    # Each module adds its own settings to the main group via add_property()
    for module in modules:
        if hasattr(module, 'register'):
            module.register(GRET_PG_settings)
        submodules.extend(getattr(module, 'modules', []))
    bpy.utils.register_class(GRET_PG_settings)
    bpy.utils.register_class(GRET_OT_save_userpref_and_quit_blender)

    bpy.types.Scene.gret = bpy.props.PointerProperty(type=GRET_PG_settings)
    bpy.app.handlers.load_post.append(load_post)

def unregister():
    bpy.app.handlers.load_post.remove(load_post)
    del bpy.types.Scene.gret

    bpy.utils.unregister_class(GRET_OT_save_userpref_and_quit_blender)
    bpy.utils.unregister_class(GRET_PG_settings)
    for module in reversed(modules):
        if hasattr(module, 'unregister'):
            module.unregister()

    bpy.utils.unregister_class(GretAddonPreferences)

if __name__ == '__main__':
    register()
