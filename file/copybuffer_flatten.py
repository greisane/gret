import bpy

from ..helpers import select_only
from ..mesh.helpers import clear_object_data, clear_mesh_customdata
from ..operator import SaveContext

# TODO
# - Make flattening optional and rename to Copy Advanced
# - Add icon
# - Poll should ensure view3d context
# - Option to freeze world transform
# - Fix armatures copying custom bone shapes

def clear_pointers(obj):
    my_data = getattr(obj.id_data, 'data', None)
    for prop in obj.bl_rna.properties:
        prop_id = prop.identifier
        if prop.type == 'COLLECTION':
            for item in getattr(obj, prop_id):
                clear_pointers(item)
        elif prop.type == 'POINTER' and prop.is_runtime and not prop.is_readonly:
            if getattr(obj, prop_id) != my_data:
                setattr(obj, prop_id, None)

class GRET_OT_copybuffer_flatten(bpy.types.Operator):
    """Selected objects alone are copied to the clipboard, even if they reference other objects.
Modifiers and shape keys are applied, optionally other data may be removed"""

    bl_idname = 'gret.copybuffer_flatten'
    bl_label = "Copy Alone"
    bl_options = {'REGISTER'}

    clear_vertex_groups: bpy.props.BoolProperty(
        name="Clear Vertex Groups",
        description="Don't copy vertex groups",
        default=False,
    )
    clear_uv_layers: bpy.props.BoolProperty(
        name="Clear UV Layers",
        description="Don't copy UV layers",
        default=False,
    )
    clear_attributes: bpy.props.BoolProperty(
        name="Clear Attributes",
        description="Don't copy non-essential attributes",
        default=False,
    )
    clear_face_maps: bpy.props.BoolProperty(
        name="Clear Face Maps",
        description="Don't copy face maps",
        default=False,
    )
    clear_materials: bpy.props.BoolProperty(
        name="Clear Materials",
        description="Don't copy materials",
        default=True,
    )
    clear_custom_properties: bpy.props.BoolProperty(
        name="Clear Custom Properties",
        description="Don't copy custom properties",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.selected_objects

    def execute(self, context):
        new_objs = []

        with SaveContext(context, "gret.copybuffer_flatten") as save:
            for original_obj in context.selected_objects:
                obj = save.clone_obj(original_obj)
                new_objs.append(obj)
                save.rename(obj, original_obj.name)  # Copy with the original names

                # Modifiers have been applied, clear other data that doesn't make sense to keep
                obj.modifiers.clear()
                obj.constraints.clear()
                obj.animation_data_clear()
                clear_object_data(obj,
                    vertex_groups=self.clear_vertex_groups,
                    shape_keys=True,
                    face_maps=self.clear_face_maps,
                    custom_properties=self.clear_custom_properties,
                    uv_layers=self.clear_uv_layers,
                    materials=self.clear_materials,
                    attributes=self.clear_attributes)
                clear_mesh_customdata(obj,
                    sculpt_mask_data=True,
                    skin_data=False,
                    custom_split_normals=False,
                    edge_bevel_weight=False,
                    vertex_bevel_weight=False,
                    edge_crease=False,
                    vertex_crease=False)

                # Sever all remaining references
                # TODO Armatures will still copy custom bone shapes, need to check in pose mode
                clear_pointers(obj)

            save.selection()
            select_only(context, new_objs)
            bpy.ops.view3d.copybuffer()

        self.report({'INFO'}, f"Flattened and copied {len(new_objs)} selected object(s)")
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

def draw_menu(self, context):
    self.layout.operator(GRET_OT_copybuffer_flatten.bl_idname)

def register(settings, prefs):
    bpy.utils.register_class(GRET_OT_copybuffer_flatten)
    bpy.types.VIEW3D_MT_object.append(draw_menu)

def unregister():
    bpy.types.VIEW3D_MT_object.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_copybuffer_flatten)
