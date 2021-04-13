from collections import defaultdict
import bpy
import re
import time

from gret.helpers import (
    beep,
    fail_if_invalid_export_path,
    fail_if_no_operator,
    get_context,
    get_export_path,
    get_nice_export_report,
    intercept,
    load_selection,
    save_selection,
    select_only,
)
from gret.log import logger, log, logd
from gret.mesh.helpers import merge_basis_shape_keys

def export_fbx(context, filepath):
    return bpy.ops.export_scene.fbx(
        filepath=filepath
        , check_existing=False
        , axis_forward='-Z'
        , axis_up='Y'
        , use_selection=True
        , use_active_collection=False
        , global_scale=1.0
        , apply_unit_scale=True
        , apply_scale_options='FBX_SCALE_NONE'
        , object_types={'MESH'}
        , use_mesh_modifiers=True
        , use_mesh_modifiers_render=False
        , mesh_smooth_type='EDGE'
        , bake_space_transform=True
        , use_subsurf=False
        , use_mesh_edges=False
        , use_tspace=False
        , use_custom_props=False
        , bake_anim=False
        , path_mode='STRIP'
        , embed_textures=False
        , batch_mode='OFF'
        , use_batch_own_dir=False
    )

class GRET_OT_scene_export(bpy.types.Operator):
    bl_idname = 'gret.scene_export'
    bl_label = "Scene Export"
    bl_context = 'objectmode'
    bl_options = {'REGISTER'}

    index: bpy.props.IntProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

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

    def _execute(self, context, job):
        collision_prefixes = ("UCX", "UBX", "UCP", "USP")

        # Find objects that should be considered for export
        if not job.selection_only:
            export_objs, job_cls = job.get_export_objects(context, types={'MESH'})
        elif context.selected_objects:
            export_objs = [obj for obj in context.selected_objects if obj.type == 'MESH']
        else:
            # Nothing to export
            return

        groups = defaultdict(list)  # Filepath to object list
        for obj in export_objs:
            if any(obj.name.startswith(s) for s in collision_prefixes):
                # Never export collision objects by themselves
                continue

            log(f"Processing {obj.name}")
            ctx = get_context(obj)
            logger.indent += 1

            orig_obj, obj = obj, self.copy_obj(obj)
            select_only(context, obj)

            merge_basis_shape_keys(obj)

            for modifier in obj.modifiers[:]:
                if modifier.show_viewport:
                    try:
                        bpy.ops.object.modifier_apply(ctx, modifier=modifier.name)
                    except RuntimeError:
                        log(f"Couldn't apply {modifier.type} modifier '{modifier.name}'")

            col_objs = []
            if job.export_collision:
                # Extend selection with pertaining collision objects
                pattern = r"^(?:%s)_%s_\d+$" % ('|'.join(collision_prefixes), obj.name)
                col_objs = [o for o in context.scene.objects if re.match(pattern, o.name)]
            if col_objs:
                log(f"Collected {len(col_objs)} collision primitives")

            if not job.keep_transforms:
                # Move main object to world center while keeping collision relative transforms
                for col in col_objs:
                    self.saved_transforms[col] = col.matrix_world.copy()
                    col.matrix_world = obj.matrix_world.inverted() @ col.matrix_world
                obj.matrix_world.identity()

            # If set, ensure prefix for exported materials
            if job.material_name_prefix:
                for mat_slot in obj.material_slots:
                    mat = mat_slot.material
                    if not mat.name.startswith(job.material_name_prefix):
                        self.saved_material_names[mat] = mat.name
                        mat.name = job.material_name_prefix + mat.name

            # Refresh vertex color and clear the mappings to avoid issues when meshes are merged
            # While in Blender it's more intuitive to author masks starting from black, however
            # UE4 defaults to white. Materials should then use OneMinus to get the original value
            if not obj.data.vertex_colors and not obj.vertex_color_mapping:
                bpy.ops.mesh.vertex_color_mapping_add(ctx)
            bpy.ops.mesh.vertex_color_mapping_refresh(ctx, invert=True)
            bpy.ops.mesh.vertex_color_mapping_clear(ctx)

            path_fields = {
                'object': obj.name,
                'collection': orig_obj.users_collection[0].name,
            }
            filepath = get_export_path(job.scene_export_path, path_fields)
            groups[filepath].append(obj)
            groups[filepath].extend(col_objs)

            logger.indent -= 1

        # Export each file
        for filepath, objs in groups.items():
            select_only(context, objs)

            filename = bpy.path.basename(filepath)
            result = export_fbx(context, filepath, [])
            if result == {'FINISHED'}:
                log(f"Exported {filename} with {len(objs)} objects")
                self.exported_files.append(filename)
            else:
                log(f"Failed to export {filename}")

    def execute(self, context):
        job = context.scene.gret.export_jobs[self.index]
        rig = job.rig
        assert job.what == 'SCENE'

        # Check addon availability and export path
        try:
            fail_if_no_operator('vertex_color_mapping_refresh', submodule=bpy.ops.mesh)
            fail_if_invalid_export_path(job.scene_export_path, ['object', 'collection'])
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        saved_selection = save_selection(all_objects=True)
        saved_use_global_undo = context.preferences.edit.use_global_undo
        context.preferences.edit.use_global_undo = False
        self.exported_files = []
        self.new_objs = []
        self.new_meshes = []
        self.saved_object_names = {}
        self.saved_material_names = {}
        self.saved_transforms = {}
        logger.start_logging()
        log(f"Beginning scene export job '{job.name}'")

        try:
            start_time = time.time()
            self._execute(context, job)
            # Finished without errors
            elapsed = time.time() - start_time
            self.report({'INFO'}, get_nice_export_report(self.exported_files, elapsed))
            log("Job complete")
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

def register(settings):
    bpy.utils.register_class(GRET_OT_scene_export)

def unregister():
    bpy.utils.unregister_class(GRET_OT_scene_export)
