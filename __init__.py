bl_info = {
    'name': "gret",
    'author': "greisane",
    'description': "Collection of Blender tools",
    'version': (1, 4, 0),
    'blender': (4, 0, 1),
    'location': "3D View > Tools",
    'category': "Object",
    'doc_url': "https://github.com/greisane/gret#readme",
    'tracker_url': "https://github.com/greisane/gret/issues",
}

from bpy.app.handlers import persistent
from collections import defaultdict
import bpy
import importlib
import sys

from .log import log, logd, logger

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
    'material',
    'mesh',
    'rig',
    'file',  # Depends on mesh
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
        logger.categories.add("SAVE")
    else:
        logger.categories.discard("DEBUG")
        logger.categories.discard("SAVE")

class GretAddonPreferences(bpy.types.AddonPreferences):
    # This must match the addon name, use '__package__'
    # when defining this in a submodule of a python package.
    bl_idname = __name__

    animation__register_pose_blender: bpy.props.BoolProperty(
        name="Enable \"Pose Blender\"",
        description="Allows blending poses together, similar to the UE4 AnimGraph node",
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
    file__enable_copybuffer_flatten: bpy.props.BoolProperty(
        name="Enable \"Copy Alone\"",
        description="Selected objects alone are copied to the clipboard, "
            "even if they reference other objects",
        default=True,
        update=registered_updated,
    )
    file__enable_deduplicate_materials: bpy.props.BoolProperty(
        name="Enable \"Deduplicate Materials\"",
        description="Squashes duplicate materials (.001, .002) into the original material",
        default=True,
        update=registered_updated,
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
    jobs__rig_export_name: bpy.props.StringProperty(
        name="Rig Name",
        description="Name of the exported skeleton",
        default="root",
    )
    jobs__export_preset: bpy.props.EnumProperty(
        items=[
            ('UE4', "UE4", """Bone axes: Y (primary), X (secondary).
Mesh smoothing: Edge.
ARP humanoids require 4 spine bones"""),
            ('UE5', "UE5", """Bone axes: Y (primary), X (secondary).
Mesh smoothing: Edge.
ARP humanoids require 6 spine bones and 2 neck bones"""),
            ('UNITY', "Unity", """Bone axes: X (primary), Z (secondary).
Mesh smoothing: Normals only"""),
        ],
        name="Export Preset",
        description="Settings applied on export",
        default='UE4',
    )
    jobs__use_tspace: bpy.props.BoolProperty(
        name="Tangent Space",
        description="Add binormal and tangent vectors. Will only work correctly with tris/quads meshes",
        default=False,
    )
    jobs__use_triangles: bpy.props.BoolProperty(
        name="Triangulate Faces",
        description="Convert all faces to triangles",
        default=False,
    )
    texture_bake__enable: bpy.props.BoolProperty(
        name="Enable",
        description="One-click bake and export of curvature and AO masks",
        default=False,
        update=registered_updated,
    )
    texture_bake__explode_objects: bpy.props.BoolProperty(
        name="Explode Objects",
        description="Spread out objects in every direction. Should not be necessary",
        default=False,
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
        description="Generate collision for selected geometry",
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
        description="Boolean merge one or more objects and smooth normals",
        default=True,
        update=registered_updated,
    )
    mesh__enable_sculpt_selection: bpy.props.BoolProperty(
        name="Enable \"Sculpt Selection\"",
        description="Set the sculpt mask from the current edit-mode vertex selection",
        default=True,
        update=registered_updated,
    )
    mesh__enable_attribute_selection: bpy.props.BoolProperty(
        name="Enable \"Attribute Selection\"",
        description="Boolean attribute selection operators as a stand-in for Face Maps",
        default=True,
        update=registered_updated,
    )
    mesh__enable_shape_key_normalize: bpy.props.BoolProperty(
        name="Enable \"Normalize Shape Key\"",
        description="Resets Min and Max of shape keys while keeping the range of motion",
        default=True,
        update=registered_updated,
    )
    mesh__enable_shape_key_presets: bpy.props.BoolProperty(
        name="Shape Key Presets",
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
    mesh__enable_vertex_group_bleed: bpy.props.BoolProperty(
        name="Enable \"Vertex Group Bleed\"",
        description="Expand vertex weights via flood fill",
        default=True,
        update=registered_updated,
    )
    mesh__enable_vertex_group_create_mirrored: bpy.props.BoolProperty(
        name="Enable \"Create Mirrored Vertex Groups\"",
        description="Create any missing mirror vertex groups. New vertex groups will be empty",
        default=True,
        update=registered_updated,
    )
    mesh__enable_vertex_group_remove_unused: bpy.props.BoolProperty(
        name="Enable \"Remove Unused Vertex Groups\"",
        description="Delete vertex groups with no assigned weights",
        default=True,
        update=registered_updated,
    )
    mesh__enable_vertex_group_smooth_loops: bpy.props.BoolProperty(
        name="Enable Vertex Group \"Smooth Loops\"",
        description="Smooth weights for selected vertex loops",
        default=True,
        update=registered_updated,
    )
    mesh__enable_cut_faces_smooth: bpy.props.BoolProperty(
        name="Enable \"Cut Faces (Subdivide)\"",
        description="Subdivide selected faces and join the result with the surrounding geometry",
        default=True,
        update=registered_updated,
    )
    mesh__enable_shape_key_select: bpy.props.BoolProperty(
        name="Enable \"Select Shape Key\"",
        description="Select vertices affected by the current shape key",
        default=True,
        update=registered_updated,
    )
    retarget__enable: bpy.props.BoolProperty(
        name="Enable",
        description="Retarget meshes or armatures to fit a modified version of the source mesh",
        default=True,
        update=registered_updated,
    )
    retarget__max_vertices_low: bpy.props.IntProperty(
        name="Max Vertices (Default)",
        description="Maximum vertices sampled when retargeting",
        default=2000,
        min=1,
    )
    retarget__max_vertices_high: bpy.props.IntProperty(
        name="Max Vertices (High Quality)",
        description="Maximum vertices sampled when retargeting with 'High Quality' enabled",
        default=4000,
        min=1,
    )
    retarget__overwrite_shape_key: bpy.props.BoolProperty(
        name="Overwrite Shape Keys",
        description="When retargeting to a shape key, overwrite it if it already exists",
        default=False,
    )
    mesh__shape_key_presets_num_slots: bpy.props.IntProperty(
        name="Shape Key Presets Slots",
        description="Number of shape key preset buttons to add if Shape Key Presets are enabled",
        default=5,
        min=1,
        max=10,
        update=registered_updated,
    )
    mesh__shape_key_presets_load_minmax: bpy.props.BoolProperty(
        name="Shape Key Presets Store Range",
        description="Load shape key min/max from shape key presets",
        default=False,
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
    rig__enable_constraints_stretchto_reset: bpy.props.BoolProperty(
        name="Enable \"Reset Stretch To Constraints\"",
        description="""Reset rest length of "Stretch To" constraints""",
        default=True,
        update=registered_updated,
    )
    uv__enable_relax_loops: bpy.props.BoolProperty(
        name="Enable \"Relax Loops\"",
        description="Relax selected edge loops to their respective mesh length",
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
        description="""Specifies the color format when copying to clipboard.
Use `rgb` for floats and `RGB` for bytes. Color space is sRGB, prefix `l` or `L` for linear.
Examples:

Hex -- "#{R:02X}{G:02X}{B:02X}{A:02X}" (use "x" for lowercase)
RGB -- "{R},{G},{B}"
UE4 -- "(R={lr:f},G={lg:f},B={lb:f},A={a:f})\"""",
        default="#{R:02X}{G:02X}{B:02X}{A:02X}",
    )
    debug: bpy.props.BoolProperty(
        name="Debug Mode",
        description="Enables verbose output",
        default=False,
        update=debug_updated,
    )
    use_panel_patcher: bpy.props.BoolProperty(
        name="Modify Native Layout",
        description="Allow modifying panels for better layouts or to replace existing buttons",
        default=True,
        update=registered_updated,
    )
    categories = None

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True

        if self.categories is None:
            # Cache grouped props by category (the part left of the double underscore "__")
            from .helpers import titlecase
            d = defaultdict(list)
            unnamed_category_name = "Miscellaneous"
            for prop_name in self.__annotations__:
                cpos = prop_name.find("__")
                category_name = titlecase(prop_name[:cpos]) if cpos > 0 else unnamed_category_name
                d[category_name].append(prop_name)
            def get_prop_title(prop_name):
                try:
                    return self.__annotations__[prop_name].keywords['name']
                except:
                    return prop_name
            prop_sort_key = lambda s: "" if s.endswith("__enable") else get_prop_title(s)  # Toggles first
            category_sort_key = lambda s: "ZZ" if s == unnamed_category_name else s  # Unnamed last
            category_icons = {
                "Animation": 'CAMERA_DATA',
                "File": 'FILE',
                "Jobs": 'SCRIPT',
                "Mesh": 'MESH_DATA',
                "Retarget": 'MOD_MESHDEFORM',
                "Rig": 'ARMATURE_DATA',
                "Texture Bake": 'MATERIAL',
                "UV Paint": 'BRUSH_DATA',
                "UV": 'UV',
            }
            self.categories = [(k, category_icons.get(k, 'BLANK1'), sorted(d[k], key=prop_sort_key))
                for k in sorted(d.keys(), key=category_sort_key)]

        if needs_restart:
            alert_row = layout.row()
            alert_row.alert = True
            alert_row.operator('gret.save_userpref_and_quit_blender', icon='ERROR',
                text="Save preferences and quit Blender")

        row0 = layout.row(align=False)
        row0.alignment = 'CENTER'  # For padding
        col0 = row0.column()
        col0.ui_units_x = 20  # Otherwise it becomes tiny with no textboxes

        for category_name, icon, prop_names in self.categories:
            box = col0.box()
            col = box.column(align=True)
            # Header
            sub = col.split(factor=0.35)
            sub.ui_units_y = 0.8
            box0 = sub.box()
            box0.label(text=category_name, icon=icon)
            # Sorted properties
            for prop_name in prop_names:
                row = col.row(align=True)
                row.prop(self, prop_name, expand=True)
                if prop_name.endswith('__enable') and not getattr(self, prop_name):
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
    debug_updated(None, None)

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
