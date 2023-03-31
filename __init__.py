bl_info = {
    'name': "gret",
    'author': "greisane",
    'description': "",
    'version': (1, 1, 0),
    'blender': (3, 4, 0),
    'location': "3D View > Tools",
    'category': "Object"
}

from bpy.app.handlers import persistent
from collections import defaultdict
import bpy
import importlib
import sys

from .log import log, logd, logger
# logger.categories.add("DEBUG")

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
        logd(f"Importing module {module_name}")
        module = sys.modules.get(module_name)
        if module:
            module = importlib.reload(module)
        else:
            module = globals()[module_name] = importlib.import_module(module_name)
        modules.append(module)
    return modules

def register_submodules(modules, settings, draw_funcs=[]):
    registered_modules = []
    for module in modules:
        if hasattr(module, 'register'):
            logd(f"Registering module {module.__name__}")
            # Explicitly check for False to avoid having to return True every time
            if module.register(settings, prefs) != False:
                registered_modules.append(module)
                if hasattr(module, 'draw_panel'):
                    draw_funcs.append(module.draw_panel)
    return registered_modules

def unregister_submodules(modules, draw_funcs=[]):
    for module in reversed(modules):
        if hasattr(module, 'unregister'):
            logd(f"Unregistering module {module.__name__}")
            module.unregister()
    draw_funcs.clear()
    modules.clear()

module_names = [
    'helpers',
    'math',
    'drawing',
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
registered_modules = []

def prefs_updated(self, context):
    for module in registered_modules:
        for submodule in getattr(module, "registered_modules", []):
            if hasattr(submodule, "on_prefs_updated"):
                submodule.on_prefs_updated()

needs_restart = False
def registered_updated(self, context):
    global needs_restart
    needs_restart = True

def debug_updated(self, context):
    if prefs.debug:
        logger.categories.add("DEBUG")
    else:
        logger.categories.discard("DEBUG")

class GretAddonPreferences(bpy.types.AddonPreferences):
    # This must match the addon name, use '__package__'
    # when defining this in a submodule of a python package.
    bl_idname = __name__

    animation__register_pose_blender: bpy.props.BoolProperty(
        name="Enable Pose Blender",
        description="""Allows blending poses together, similar to the UE4 AnimGraph node.
NEEDS UPDATING TO 3.0""",
        default=False,
        update=registered_updated,
    )
    animation__enable_channels_auto_group: bpy.props.BoolProperty(
        name="Enable \"Auto-Group Channels\"",
        description="Group animation channels by their bone name",
        default=True,
        update=registered_updated,
    )
    animation__enable_channels_delete_unavailable: bpy.props.BoolProperty(
        name="Enable \"Delete Unavailable Channels\"",
        description="Delete location/rotation/scale channels that are locked in the transform panel",
        default=True,
        update=registered_updated,
    )
    animation__show_action_frame_range: bpy.props.BoolProperty(
        name="Show Action Frame Range",
        description="Show custom frame range controls in the action panel",
        default=True,
    )
    animation__sync_action_frame_range: bpy.props.BoolProperty(
        name="Sync Action Frame Range",
        description="Keep preview range in sync with the action's custom frame range",
        default=True,
        update=prefs_updated,
    )
    jobs__enable: bpy.props.BoolProperty(
        name="Enable",
        description="Jobs automate the export process for multiple objects or complex setups",
        default=False,
        update=registered_updated,
    )
    jobs__beep_on_finish: bpy.props.BoolProperty(
        name="Beep On Finish",
        description="Play a beep sound after an export job finishes",
        default=False,
    )
    jobs__limit_vertex_weights: bpy.props.IntProperty(
        name="Vertex Weight Limit",
        description="Limit number of bone influences per vertex. No limit if 0",
        default=8,
        min=0,
        max=32,
    )
    texture_bake__enable: bpy.props.BoolProperty(
        name="Enable",
        description="One-click bake and export of curvature and AO masks",
        default=False,
        update=registered_updated,
    )
    texture_bake__uv_layer_name: bpy.props.StringProperty(
        name="UV Layer",
        description="Name of the default UV layer for texture bakes",
        default="UVMap",
    )
    texture_bake__beep_on_finish: bpy.props.BoolProperty(
        name="Beep On Finish",
        description="Play a beep sound after a texture bake finishes",
        default=False,
    )
    mesh__enable_make_collision: bpy.props.BoolProperty(
        name="Enable \"Make Collision\"",
        description="Generate collision selected geometry",
        default=True,
        update=registered_updated,
    )
    mesh__enable_add_rope: bpy.props.BoolProperty(
        name="Enable \"Add Rope\"",
        description="Construct a rope mesh following the selected curve",
        default=True,
        update=registered_updated,
    )
    mesh__enable_graft: bpy.props.BoolProperty(
        name="Enable \"Graft\"",
        description="Connect boundaries of selected objects to the active object",
        default=True,
        update=registered_updated,
    )
    mesh__enable_merge: bpy.props.BoolProperty(
        name="Enable \"Merge\"",
        description="Boolean merge one or more objects, cleaning up the result for normal transfer",
        default=True,
        update=registered_updated,
    )
    mesh__enable_retarget_mesh: bpy.props.BoolProperty(
        name="Enable \"Retarget Mesh\"",
        description="Retarget meshes to fit a modified version of the source mesh",
        default=True,
        update=registered_updated,
    )
    mesh__enable_shape_key_store: bpy.props.BoolProperty(
        name="Enable \"Shape Key Store\"",
        description="Adds buttons to load and save shape key values",
        default=True,
        update=registered_updated,
    )
    mesh__enable_vertex_color_mapping: bpy.props.BoolProperty(
        name="Enable \"Vertex Color Mapping\"",
        description="Procedurally generates vertex colors from various sources",
        default=True,
        update=registered_updated,
    )
    mesh__enable_cut_faces_smooth: bpy.props.BoolProperty(
        name="Enable \"Cut Faces (Subdivide)\"",
        description="Subdivide selected faces and join the result with the surrounding geometry",
        default=True,
        update=registered_updated,
    )
    mesh__retarget_num_vertices_low: bpy.props.IntProperty(
        name="Retarget Vertex Cap (Default)",
        description="Maximum vertices sampled when retargeting",
        default=2000,
        min=1,
    )
    mesh__retarget_num_vertices_high: bpy.props.IntProperty(
        name="Retarget Vertex Cap (High Quality)",
        description="Maximum vertices sampled when retargeting with 'High Quality' enabled",
        default=4000,
        min=1,
    )
    rig__enable_retarget_armature: bpy.props.BoolProperty(
        name="Enable \"Retarget Armature\"",
        description="Retarget an armature or selected bones to fit a modified version of the source mesh",
        default=True,
        update=registered_updated,
    )
    rig__enable_properties: bpy.props.BoolProperty(
        name="Enable \"Rig Properties\"",
        description="""Panel for frequently used rig or bone properties""",
        default=True,
        update=registered_updated,
    )
    rig__enable_selection_sets: bpy.props.BoolProperty(
        name="Enable \"Selection Sets\"",
        description="""Panel for quick bone selection""",
        default=True,
        update=registered_updated,
    )
    rig__enable_selection_sets: bpy.props.BoolProperty(
        name="Enable \"Selection Sets\"",
        description="""Panel for quick bone selection""",
        default=True,
        update=registered_updated,
    )
    uv__texture_sync: bpy.props.BoolProperty(
        name="Enable \"Reorder UV Maps\"",
        description="Adds a few buttons that allow reordering UV maps",
        default=True,
        update=registered_updated,
    )
    uv_paint__enable: bpy.props.BoolProperty(
        name="Enable",
        description="Assign UVs from a previously configured tileset or trim sheet",
        default=True,
        update=registered_updated,
    )
    uv_paint__layer_name: bpy.props.StringProperty(
        name="UV Layer",
        description="Default UV layer to paint to. Leave empty to use the active UV layer",
        default="",
    )
    uv_paint__picker_show_info: bpy.props.BoolProperty(
        name="Show UV Picker Info",
        description="Display information when hovering the UV picker",
        default=True,
    )
    uv_paint__picker_copy_color: bpy.props.BoolProperty(
        name="Clicking UV Picker Copies Color",
        description="Copy image color from the UV picker to the clipboard on click",
        default=False,
    )
    uv_paint__picker_copy_color_format: bpy.props.StringProperty(
        name="Clipboard Color Format",
        description="""Specifies the color format when copied to clipboard.
Use `rgb` for floats and `RGB` for bytes. Color space is sRGB, prefix `l` or `L` for linear.
Examples:

Hex -- "#{R:X}{G:X}{B:X}{A:X}" (use "x" for lowercase)
RGB -- "{R},{G},{B}"
UE4 -- "(R={lr:f},G={lg:f},B={lb:f},A={a:f})\"""",
        default="#{R:X}{G:X}{B:X}{A:X}",
    )
    debug: bpy.props.BoolProperty(
        name="Debug Mode",
        description="Enables verbose output",
        default=False,
        update=debug_updated,
    )
    categories = None

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True

        if not self.categories:
            # Cache grouped props by category (the part left of the double underscore "__")
            from .helpers import titlecase
            d = defaultdict(list)
            unnamed_category_name = "Miscellaneous"
            for prop_name in self.__annotations__:
                cpos = prop_name.find("__")
                category_name = titlecase(prop_name[:cpos]) if cpos > 0 else unnamed_category_name
                d[category_name].append(prop_name)
            prop_sort_key = lambda s: "" if s.endswith("__enable") else s  # Main toggle first
            category_sort_key = lambda s: "ZZ" if s == unnamed_category_name else s  # Unnamed last
            self.categories = [(k, sorted(d[k], key=prop_sort_key))
                for k in sorted(d.keys(), key=category_sort_key)]

        if needs_restart:
            alert_row = layout.row()
            alert_row.alert = True
            alert_row.operator("gret.save_userpref_and_quit_blender", icon='ERROR',
                text="Save preferences and quit Blender")

        row0 = layout.row(align=False)
        row0.alignment = 'CENTER'  # For padding
        col0 = row0.column()
        col0.ui_units_x = 20  # Otherwise it becomes tiny with no textboxes
        use_combining_underscores = False

        for category_name, prop_names in self.categories:
            box = col0.box()
            col = box.column(align=True)
            if use_combining_underscores:
                # Renders correctly, though the line is very uneven
                col.label(text="\u0332".join(category_name), icon='BLANK1')  # Icon just for margin
            else:
                # Label overlay. Use em-dashes since underscores have gaps in the default font
                row = col.row()
                row.ui_units_y = 0.4
                row.label(text=category_name, icon='BLANK1')  # Icon just for margin
                col.label(text=" " * 8 + "\u2014" * 8)
            for prop_name in prop_names:
                col.prop(self, prop_name)
                if prop_name.endswith("__enable") and not getattr(self, prop_name):
                    # Category main toggle is off, don't show the other propeties
                    break
            col.separator()
            col0.separator()

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
    if prefs.debug:
        logger.categories.add("DEBUG")
    else:
        logger.categories.discard("DEBUG")

    # Each module adds its own settings to the main group via add_property()
    global registered_modules
    registered_modules = register_submodules(modules, GRET_PG_settings)

    bpy.utils.register_class(GRET_PG_settings)
    bpy.utils.register_class(GRET_OT_save_userpref_and_quit_blender)

    bpy.types.Scene.gret = bpy.props.PointerProperty(type=GRET_PG_settings)
    bpy.app.handlers.load_post.append(load_post)

def unregister():
    bpy.app.handlers.load_post.remove(load_post)
    del bpy.types.Scene.gret

    bpy.utils.unregister_class(GRET_OT_save_userpref_and_quit_blender)
    bpy.utils.unregister_class(GRET_PG_settings)

    unregister_submodules(registered_modules)

    bpy.utils.unregister_class(GretAddonPreferences)

if __name__ == '__main__':
    register()
