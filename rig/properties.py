import bpy
import re

from ..helpers import titlecase

custom_prop_re = re.compile(r'(.+)?\["([^"]+)"\]$')
prop_re = re.compile(r'(.+)\.([^"\.]+)$')

def parse_prop_path(obj, prop_path):
    # Returns target data, property path and pretty property text if the property was found
    # Otherwise returns None, None, prop_path

    try:
        prop_match = custom_prop_re.search(prop_path)
        if prop_match:
            if prop_match[1]:
                obj = obj.path_resolve(prop_match[1])
            prop_path = f'["{prop_match[2]}"]'
            # Fetch value to make sure the property exists
            value = obj.path_resolve(prop_path)
            # Don't attach the object name to text, custom property name should be descriptive enough
            text = titlecase(prop_match[2])
            return obj, prop_path, text

        prop_match = prop_re.search(prop_path)
        if prop_match:
            obj = obj.path_resolve(prop_match[1])
            prop_path = prop_match[2]
            # Fetch value to make sure the property exists
            value = obj.path_resolve(prop_path)
            text = f"{obj.name} {titlecase(prop_match[2])}"
            return obj, prop_path, text
    except ValueError:
        pass

    return None, None, prop_path

class GRET_OT_property_add(bpy.types.Operator):
    #tooltip
    """Add a property to the list"""

    bl_idname = 'gret.property_add'
    bl_label = "Add Property"
    bl_options = {'INTERNAL', 'UNDO'}

    path: bpy.props.StringProperty(
        name="Property Path",
        description="Path to an existing property or name of a new custom property",
        default="",
    )

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        obj = context.object

        if not self.path:
            return {'CANCELLED'}

        # Simple name, not a path. Assume user wants to get or create a custom property on the rig
        if not any(c in self.path for c in '."[]'):
            if self.path not in obj:
                obj[self.path] = 0.0
                obj.id_properties_ui(self.path).update(min=0.0, max=1.0,
                    soft_min=0.0, soft_max=1.0, default=0.0)
                obj.update_tag()
            self.path = f'["{self.path}"]'

        properties = list(obj.get('properties', []))
        properties.append(self.path)
        properties.sort(key=lambda prop_path: parse_prop_path(obj, prop_path)[2])
        obj['properties'] = properties

        return {'FINISHED'}

    def invoke(self, context, event):
        # Check if the clipboard already has a correct path and paste it
        clipboard = bpy.context.window_manager.clipboard
        self.path = clipboard if parse_prop_path(context.object, clipboard)[0] else ""
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.ui_units_x = 20.0

        col = layout.column(align=True)
        col.label(text="Enter a path or name of a new property.")
        col.label(text="You can Shift-Ctrl-C any field (like the influence of a constraint) to get its path.")
        col.separator()
        col.prop(self, 'path', text="")

class GRET_OT_property_remove(bpy.types.Operator):
    #tooltip
    """Remove the property from the list"""

    bl_idname = 'gret.property_remove'
    bl_label = "Remove Property"
    bl_options = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        obj = context.object

        properties = list(obj.get('properties', []))
        if self.index >= 0 and self.index < len(properties):
            del properties[self.index]
        obj['properties'] = properties

        return {'FINISHED'}

def draw_panel(self, context):
    layout = self.layout
    settings = context.scene.gret
    obj = context.object

    box = layout.box()
    row = box.row()
    row.label(text="Properties", icon='PROPERTIES')
    row = row.row(align=True)
    if settings.properties_show_edit:
        row.operator('gret.property_add', icon='ADD', text="")
    row.prop(settings, 'properties_show_edit', icon='SETTINGS', text="")

    properties = obj.get('properties')
    if properties:
        col = box.column(align=True)

        for idx, prop_path in enumerate(properties):
            row = col.row(align=True)
            data, prop_path, label = parse_prop_path(obj, prop_path)

            if data:
                row.prop(data, prop_path, text=label)
            else:
                row.alert = True
                row.label(text=f"Missing: {label}")

            if settings.properties_show_edit:
                row.operator('gret.property_remove', icon='X', text="").index = idx

classes = (
    GRET_OT_property_add,
    GRET_OT_property_remove,
)

def register(settings, prefs):
    for cls in classes:
        bpy.utils.register_class(cls)

    settings.add_property('properties_show_edit', bpy.props.BoolProperty(
        name="Edit Properties",
        description="Show buttons to edit rig properties",
        default=False,
    ))

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
