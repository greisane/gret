import bpy
import re
from ..helpers import (
    get_context,
    load_selection,
    save_selection,
    select_only,
    swap_names,
    try_call,
)

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
    clear_vertex_colors: bpy.props.BoolProperty(
        name="Clear Vertex Colors",
        description="Don't copy vertex colors",
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

    def clone_flatten_obj(self, context, obj):
        # Make evaluated mesh copy, shape keys will be cleared
        dg = context.evaluated_depsgraph_get()
        new_obj = obj.copy()
        new_obj.parent = None
        self.new_objs.add(new_obj)
        new_obj.name = new_obj.name + "_"
        if obj.type == 'MESH':
            new_obj.data = bpy.data.meshes.new_from_object(obj.evaluated_get(dg),
                preserve_all_data_layers=True, depsgraph=dg)
            self.new_meshes.add(new_obj.data)
        elif obj.data:
            # No special handling here, however copy the data since clear_pointers might change it
            # Not cleaning it up either... it's orphaned anyway and will be purged on save
            new_obj.data = obj.data.copy()
        new_data = new_obj.data

        # Modifiers have been applied, clear other data that doesn't make sense to keep
        new_obj.modifiers.clear()
        new_obj.constraints.clear()
        new_obj.animation_data_clear()
        # Didn't bother to find out how to check if forcefield is active, rarely used
        # bpy.ops.object.forcefield_toggle(new_obj)
        ctx = get_context(new_obj)
        try_call(bpy.ops.rigidbody.constraint_remove, ctx)
        try_call(bpy.ops.rigidbody.object_remove, ctx)
        if new_obj.type == 'MESH':
            try_call(bpy.ops.mesh.customdata_mask_clear, ctx)

        # Clear optional data
        if self.clear_vertex_groups and hasattr(new_obj, 'vertex_groups'):
            try:
                new_obj.vertex_groups.clear()
            except RuntimeError:
                pass
        if self.clear_face_maps and hasattr(new_obj, 'face_maps'):
            new_obj.face_maps.clear()
        if self.clear_custom_properties:
            for key in list(new_obj.keys()):
                del new_obj[key]
        if new_data:
            if self.clear_uv_layers and hasattr(new_data, 'uv_layers'):
                while new_data.uv_layers.active:
                    new_data.uv_layers.remove(new_data.uv_layers.active)
            if self.clear_vertex_colors and hasattr(new_data, 'vertex_colors'):
                while new_data.vertex_colors.active:
                    new_data.vertex_colors.remove(new_data.vertex_colors.active)
            if self.clear_face_maps and hasattr(new_data, 'face_maps'):
                # Don't think these are shown in the user interface anywhere, unlike object face maps
                while new_data.face_maps.active:
                    new_data.face_maps.remove(new_data.face_maps.active)
            if self.clear_materials and hasattr(new_data, 'materials'):
                # I left a note somewhere that mesh.materials.clear() was crashing
                # Bug may be fixed, iterating achieves the same effect so I'm doing that to be safe
                while new_data.materials:
                    new_data.materials.pop()
            if self.clear_custom_properties:
                for key in new_data.keys():
                    del new_data[key]

        # Sever all remaining references
        # TODO Armatures will still copy custom bone shapes, pointer not available unless in pose mode
        clear_pointers(new_obj)

        if new_obj.name not in context.scene.collection.objects:
            context.scene.collection.objects.link(new_obj)
        return new_obj

    def execute(self, context):
        saved_selection = save_selection()
        self.new_objs = set()
        self.new_meshes = set()

        obj_to_new_obj = {}
        try:
            for obj in context.selected_objects:
                obj_to_new_obj[obj] = self.clone_flatten_obj(context, obj)

            # Copy with the original names
            for obj, new_obj in obj_to_new_obj.items():
                swap_names(obj, new_obj)
            select_only(context, obj_to_new_obj.values())
            bpy.ops.view3d.copybuffer()
            for obj, new_obj in obj_to_new_obj.items():
                swap_names(obj, new_obj)
        except:
            obj_to_new_obj.clear()
            raise
        finally:
            # Clean up
            while self.new_objs:
                bpy.data.objects.remove(self.new_objs.pop())
            while self.new_meshes:
                bpy.data.meshes.remove(self.new_meshes.pop())
            load_selection(saved_selection)

        self.report({'INFO'}, f"Flattened and copied {len(obj_to_new_obj)} selected object(s)")
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
