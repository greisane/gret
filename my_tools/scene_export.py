from collections import defaultdict
import bpy
import re
import time
from .helpers import (
    beep,
    fail_if_invalid_export_path,
    fail_if_no_operator,
    get_export_path,
    get_nice_export_report,
    load_selection,
    log,
    logger,
    save_selection,
    select_only,
)
from .mesh_helpers import (
    merge_basis_shape_keys,
)
from .export import (
    export_fbx,
)

class MY_OT_scene_export(bpy.types.Operator):
    bl_idname = 'export_scene.my_fbx'
    bl_label = "Scene Export"
    bl_context = 'objectmode'
    bl_options = {'REGISTER'}

    export_path: bpy.props.StringProperty(
        name="Export Path",
        description="""Export path relative to the current folder.
{file} = Name of this .blend file without extension.
{object} = Name of the object being exported.
{collection} = Name of the first collection the object belongs to""",
        default="//export/S_{object}.fbx",
        subtype='FILE_PATH',
    )
    export_collision: bpy.props.BoolProperty(
        name="Export Collision",
        description="Exports collision objects that follow the UE4 naming pattern",
        default=True,
    )
    keep_transforms: bpy.props.BoolProperty(
        name="Keep Transforms",
        description="Keep the position and rotation of objects relative to world center",
        default=False,
    )
    material_name_prefix: bpy.props.StringProperty(
        name="Material Prefix",
        description="Ensures that exported material names begin with a prefix",
        default="MI_",
    )
    debug: bpy.props.BoolProperty(
        name="Debug",
        description="Debug mode with verbose output. Exceptions are caught but not handled",
        default=False,
    )

    def copy_obj(self, obj, copy_data=True):
        new_obj = obj.copy()
        # New object takes the original name as a temporary measure to export collision
        # new_obj.name = obj.name + "_"
        self.saved_object_names[obj] = original_name = obj.name
        obj.name = original_name + "_"
        new_obj.name = original_name
        if copy_data:
            new_data = obj.data.copy()
            if isinstance(new_data, bpy.types.Mesh):
                self.new_meshes.append(new_data)
            else:
                log(f"Copied data of object {obj.name} won't be released!")
            new_obj.data = new_data
        self.new_objs.append(new_obj)

        # New objects are moved to the scene collection, ensuring they're visible
        bpy.context.scene.collection.objects.link(new_obj)
        new_obj.hide_set(False)
        new_obj.hide_viewport = False
        new_obj.hide_select = False
        return new_obj

    def _execute(self, context):
        collision_prefixes = ("UCX", "UBX", "UCP", "USP")

        export_groups = defaultdict(list)  # Filepath to object list
        for obj in context.selected_objects[:]:
            if obj.type != 'MESH':
                # Only meshes
                continue
            if any(obj.name.startswith(s) for s in collision_prefixes):
                # Never export collision objects by themselves
                continue

            log(f"Processing {obj.name}")
            logger.log_indent += 1

            orig_obj, obj = obj, self.copy_obj(obj)
            select_only(context, obj)

            merge_basis_shape_keys(obj)

            for modifier in obj.modifiers[:]:
                if modifier.show_viewport:
                    try:
                        bpy.ops.object.modifier_apply(modifier=modifier.name)
                    except RuntimeError:
                        log(f"Couldn't apply {modifier.type} modifier '{modifier.name}'")

            col_objs = []
            if self.export_collision:
                # Extend selection with pertaining collision objects
                pattern = r"^(?:%s)_%s_\d+$" % ('|'.join(collision_prefixes), obj.name)
                col_objs = [o for o in context.scene.objects if re.match(pattern, o.name)]
            if col_objs:
                log(f"Collected {len(col_objs)} collision primitives")

            if not self.keep_transforms:
                # Move main object to world center while keeping collision relative transforms
                for col in col_objs:
                    self.saved_transforms[col] = col.matrix_world.copy()
                    col.matrix_world = obj.matrix_world.inverted() @ col.matrix_world
                obj.matrix_world.identity()

            # If set, ensure prefix for any exported materials
            if self.material_name_prefix:
                for mat_slot in obj.material_slots:
                    mat = mat_slot.material
                    if not mat.name.startswith(self.material_name_prefix):
                        self.saved_material_names[mat] = mat.name
                        mat.name = self.material_name_prefix + mat.name

            # Refresh vertex color and clear the mappings to avoid issues when meshes are merged
            # While in Blender it's more intuitive to author masks starting from black, however
            # UE4 defaults to white. Materials should then use OneMinus to get the original value
            if not obj.data.vertex_colors and not obj.vertex_color_mapping:
                bpy.ops.mesh.vertex_color_mapping_add()
            bpy.ops.mesh.vertex_color_mapping_refresh(invert=True)
            bpy.ops.mesh.vertex_color_mapping_clear()

            path_fields = {
                'object': obj.name,
                'collection': orig_obj.users_collection[0].name,
            }
            filepath = get_export_path(self.export_path, path_fields)
            export_groups[filepath].append(obj)
            export_groups[filepath].extend(col_objs)

            logger.log_indent -= 1

        # Export each file
        for filepath, objs in export_groups.items():
            select_only(context, objs)

            filename = bpy.path.basename(filepath)
            result = export_fbx(context, filepath, [], no_intercept=self.debug)
            if result == {'FINISHED'}:
                log(f"Exported {filename} with {len(objs)} objects")
                self.exported_files.append(filename)
            else:
                log(f"Failed to export {filename}")

    def execute(self, context):
        # Check addon availability and export path
        try:
            fail_if_no_operator('vertex_color_mapping_refresh', submodule=bpy.ops.mesh)
            fail_if_invalid_export_path(self.export_path, ['object', 'collection'])
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        saved_selection = save_selection()
        saved_use_global_undo = context.preferences.edit.use_global_undo
        context.preferences.edit.use_global_undo = False
        self.exported_files = []
        self.new_objs = []
        self.new_meshes = []
        self.saved_object_names = {}
        self.saved_material_names = {}
        self.saved_transforms = {}
        logger.start_logging()

        try:
            start_time = time.time()
            self._execute(context)
            # Finished without errors
            elapsed = time.time() - start_time
            self.report({'INFO'}, get_nice_export_report(self.exported_files, elapsed))
            beep(pitch=2, num=1)
        finally:
            # Clean up
            while self.new_objs:
                bpy.data.objects.remove(self.new_objs.pop())
            while self.new_meshes:
                bpy.data.meshes.remove(self.new_meshes.pop())
            for obj, matrix_world in self.saved_transforms.items():
                obj.matrix_world = matrix_world
            for obj, name in self.saved_object_names.items():
                obj.name = name
            for mat, name in self.saved_material_names.items():
                mat.name = name
            del self.saved_transforms
            del self.saved_object_names
            del self.saved_material_names

            load_selection(saved_selection)
            context.preferences.edit.use_global_undo = saved_use_global_undo
            logger.end_logging()

        return {'FINISHED'}

classes = (
    MY_OT_scene_export,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
