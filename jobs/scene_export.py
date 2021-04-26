from collections import namedtuple, defaultdict
from itertools import chain, zip_longest
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
    swap_object_names,
)
from gret.log import logger, log, logd
from gret.mesh.helpers import (
    apply_modifiers,
    delete_faces_with_no_material,
    merge_basis_shape_keys,
    unsubdivide_preserve_uvs,
)

def export_fbx(filepath, context, objects):
    ctx = get_context(selected_objs=objects)
    return bpy.ops.export_scene.fbx(ctx
        , filepath=filepath
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
        new_obj.name = obj.name + "_"
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

        if job.to_collection and job.clean_collection:
            # Clean the target collection first
            log(f"Cleaning target collection")
            for obj in job.export_collection.objects:
                bpy.data.objects.remove(obj, do_unlink=True)

        # Find and clone objects to be exported
        # Original objects that aren't exported will be hidden for render, only for driver purposes
        if not job.selection_only:
            export_objs, job_cls = job.get_export_objects(context, types={'MESH'})
        elif context.selected_objects:
            export_objs, job_cls = [o for o in context.selected_objects if o.type == 'MESH'], []
        else:
            # Nothing to export
            return

        ExportItem = namedtuple('ExportObject', ['original', 'obj', 'job_collection', 'col_objs'])
        items = []
        groups = defaultdict(list)  # Filepath to item list
        for obj in context.scene.objects:
            obj.hide_render = True
        for obj, job_cl in zip_longest(export_objs, job_cls):
            obj.hide_render = False
            items.append(ExportItem(obj, self.copy_obj(obj), job_cl, []))

        # Process individual meshes
        job_tags = job.modifier_tags.split(' ')
        def should_enable_modifier(mod):
            for tag in re.findall(r"g:(\S+)", mod.name):
                if tag.startswith('!'):
                    # Blacklisted tag
                    return tag[1:] not in job_tags
                else:
                    return tag in job_tags
            return mod.show_render

        for item in items:
            if any(item.original.name.startswith(prefix) for prefix in collision_prefixes):
                # Never export collision objects by themselves
                item.skip = True
                continue

            log(f"Processing {item.original.name}")
            obj = item.obj
            job_cl = item.job_collection
            ctx = get_context(obj)
            logger.indent += 1

            # Simplify if specified in job collection
            levels = job_cl.subdivision_levels if job_cl else 0
            if levels < 0:
                unsubdivide_preserve_uvs(obj, -levels)

            if job.merge_basis_shape_keys:
                merge_basis_shape_keys(obj)

            obj.shape_key_clear()

            if job.apply_modifiers:
                apply_modifiers(obj, key=should_enable_modifier)

            # Remap materials, any objects or faces with no material won't be exported
            remapped_to_none = False
            for mat_idx, mat in enumerate(obj.data.materials):
                for remap in job.remap_materials:
                    if mat and mat == remap.source:
                        logd(f"Remapped material {mat.name} to {remap.destination}")
                        obj.data.materials[mat_idx] = remap.destination
                        remapped_to_none = remapped_to_none or not remap.destination
                        break

            if all(not mat for mat in obj.data.materials):
                log(f"Object has no materials and won't be exported")
                logger.indent -= 1
                continue

            if remapped_to_none:
                delete_faces_with_no_material(obj)
                if not obj.data.polygons:
                    log(f"Object has no faces and won't be exported")
                    logger.indent -= 1
                    continue

            # If enabled, pick up UE4 collision objects
            col_objs = item.col_objs
            if job.export_collision:
                pattern = r"^(?:%s)_%s_\d+$" % ('|'.join(collision_prefixes), obj.name)
                col_objs.extend(o for o in context.scene.objects if re.match(pattern, o.name))
            if col_objs:
                log(f"Collected {len(col_objs)} collision primitives")

            # If enabled, move main object to world center while keeping collision relative transforms
            if not job.keep_transforms:
                for col in col_objs:
                    logd(f"Moving collision {col.name}")
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

            # Ensure UV layer, Substance Painter complains. Zero coords to avoid all kinds of problems
            if not obj.data.uv_layers:
                log("Created empty UV layer")
                for uvloop in obj.data.uv_layers.new(name="UVMap").data:
                    uvloop.uv = (0.0, 0.0)

            # It's more intuitive to author masks starting from black, however UE4 defaults to white
            # Invert vertex colors, materials should use OneMinus to get the original value
            if not obj.data.vertex_colors and not obj.vertex_color_mapping:
                bpy.ops.gret.vertex_color_mapping_add(ctx)
            bpy.ops.gret.vertex_color_mapping_refresh(ctx, invert=True)
            bpy.ops.gret.vertex_color_mapping_clear(ctx)

            # Put the objects in a group
            cl = job_cl.get_collection(context) if job_cl else item.original.users_collection[0]
            path_fields = {
                'object': item.original.name,
                'collection': cl.name,
            }
            filepath = get_export_path(job.scene_export_path, path_fields)
            groups[filepath].append(item)
            logger.indent -= 1

        # Export each file
        for filepath, items in groups.items():
            # Export with the original object names
            for item in items:
                swap_object_names(item.original, item.obj)

            filename = bpy.path.basename(filepath)
            objs = list(chain.from_iterable([item.obj] + item.col_objs for item in items))

            result = export_fbx(filepath, context, objs)
            if result == {'FINISHED'}:
                log(f"Exported {filename} with {len(objs)} objects")
                self.exported_files.append(filename)
            else:
                log(f"Failed to export {filename}")

            for item in items:
                swap_object_names(item.original, item.obj)

    def execute(self, context):
        job = context.scene.gret.export_jobs[self.index]
        rig = job.rig
        assert job.what == 'SCENE'

        # Check addon availability and export path
        try:
            fail_if_no_operator('vertex_color_mapping_refresh', submodule=bpy.ops.mesh)
            field_names = ['object', 'collection']
            fail_if_invalid_export_path(job.scene_export_path, field_names)
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        saved_selection = save_selection(all_objects=True)
        saved_use_global_undo = context.preferences.edit.use_global_undo
        context.preferences.edit.use_global_undo = False
        self.exported_files = []
        self.new_objs = []
        self.new_meshes = []
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
            for mat, name in self.saved_material_names.items():
                mat.name = name
            del self.saved_transforms
            del self.saved_material_names

            load_selection(saved_selection)
            context.preferences.edit.use_global_undo = saved_use_global_undo
            logger.end_logging()

        return {'FINISHED'}

def register(settings):
    bpy.utils.register_class(GRET_OT_scene_export)

def unregister():
    bpy.utils.unregister_class(GRET_OT_scene_export)
