import bpy
import re

from ..stringcase import titlecase

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
        description="Path to an existing property",
        default="",
    )

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        obj = context.object

        if not self.path:
            return {'CANCELLED'}

        properties = list(obj.get('properties', []))
        properties.append(self.path)
        properties.sort(key=lambda prop_path: parse_prop_path(obj, prop_path)[2])
        obj['properties'] = properties

        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

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
    obj = context.object
    layout = self.layout

    box = layout.box()
    row = box.row()
    row.label(text="Properties", icon='PROPERTIES')
    row.operator('gret.property_add', icon='ADD', text="")

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

            row.operator('gret.property_remove', icon='X', text="").index = idx

classes = (
    GRET_OT_property_add,
    GRET_OT_property_remove,
)

def register(settings):
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
