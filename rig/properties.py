import bpy

from ..operator import PropertyWrapper, draw_warning_if_not_overridable

class GRET_OT_property_add(bpy.types.Operator):
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
        return context.active_object is not None

    def execute(self, context):
        obj = context.active_object

        if not self.path:
            return {'CANCELLED'}

        # Simple name, not a path. Assume user wants to get or create a custom property on the rig
        if not any(c in self.path for c in '."[]'):
            if self.path not in obj:
                obj[self.path] = 0.0
                obj.id_properties_ui(self.path).update(min=0.0, max=1.0,
                    soft_min=0.0, soft_max=1.0, default=0.0)
                obj.property_overridable_library_set(f'["{self.path}"]', True)
                obj.update_tag()
            self.path = f'["{self.path}"]'

        properties = list(obj.get('properties', []))
        properties.append(self.path)
        def get_property_title(data_path):
            prop_wrapper = PropertyWrapper.from_path(obj, data_path)
            return prop_wrapper.title if prop_wrapper else data_path
        properties.sort(key=get_property_title)
        obj['properties'] = properties

        return {'FINISHED'}

    def invoke(self, context, event):
        # Check if the clipboard already has a correct path and paste it
        clipboard = bpy.context.window_manager.clipboard
        self.path = clipboard if PropertyWrapper.from_path(context.active_object, clipboard) else ""
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        layout.ui_units_x = 20.0

        col = layout.column(align=True)
        col.label(text="Enter a path or name of a new property.")
        col.label(text="You can Shift-Ctrl-C any field (like the influence slider of a constraint) "
            "to copy its path to clipboard.")
        col.separator()
        col.prop(self, 'path', text="")

class GRET_OT_property_remove(bpy.types.Operator):
    """Remove the property from the list"""

    bl_idname = 'gret.property_remove'
    bl_label = "Remove Property"
    bl_options = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def execute(self, context):
        obj = context.active_object

        properties = list(obj.get('properties', []))
        if self.index >= 0 and self.index < len(properties):
            del properties[self.index]
        obj['properties'] = properties

        return {'FINISHED'}

def draw_panel(self, context):
    layout = self.layout
    settings = context.scene.gret
    obj = context.active_object

    box = layout.box()
    row = box.row()
    row.label(text="Properties", icon='PROPERTIES')
    row = row.row(align=True)
    if settings.properties_show_edit:
        if draw_warning_if_not_overridable(row, obj, '["properties"]'):
            row.separator()
        row.operator('gret.property_add', icon='ADD', text="")
    row.prop(settings, 'properties_show_edit', icon='SETTINGS', text="")

    properties = obj.get('properties')
    if properties:
        col = box.column(align=True)

        for idx, data_path in enumerate(properties):
            row = col.row(align=True)
            prop_wrapper = PropertyWrapper.from_path(obj, data_path)

            if prop_wrapper:
                row.prop(prop_wrapper.struct, prop_wrapper.data_path, text=prop_wrapper.title)
            else:
                row.alert = True
                row.label(text=f"Missing: {data_path}")

            if settings.properties_show_edit:
                row.operator('gret.property_remove', icon='X', text="").index = idx

classes = (
    GRET_OT_property_add,
    GRET_OT_property_remove,
)

def register(settings, prefs):
    if not prefs.rig__enable_properties:
        return False

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
