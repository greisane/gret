from collections import namedtuple, defaultdict
from itertools import chain, zip_longest
from math import pi
import bpy
import re
import shlex

from .. import prefs
from ..log import logger, log, logd
from ..helpers import (
    beep,
    ensure_starts_with,
    fail_if_invalid_export_path,
    get_export_path,
    get_name_safe,
    get_nice_export_report,
    get_topmost_parent,
    intercept,
    select_only,
    viewport_reveal_all,
    with_object,
)
from ..rig.helpers import export_presets
from ..mesh.helpers import (
    apply_modifiers,
    delete_faces_with_no_material,
    encode_shape_keys,
    merge_shape_keys_pattern,
    unsubdivide_preserve_uvs,
)
from ..mesh.vertex_color_mapping import get_first_mapping
from ..mesh.collision import collision_prefixes, get_collision_objects
from ..operator import SaveContext

def export_fbx(filepath, context, objects):
    preset = export_presets.get(prefs.jobs__export_preset, {})

    select_only(context, objects)
    return bpy.ops.export_scene.fbx(
        filepath=filepath
        , check_existing=False
        , use_selection=True
        , use_visible=False
        , use_active_collection=False
        , global_scale=1.0
        , apply_unit_scale=True
        , apply_scale_options='FBX_SCALE_NONE'
        , use_space_transform=True
        , bake_space_transform=True
        , object_types={'MESH', 'EMPTY'}
        , use_mesh_modifiers=True
        , use_mesh_modifiers_render=False
        , mesh_smooth_type=preset.get('mesh_smooth_type', 'OFF')
        , colors_type='SRGB'
        , prioritize_active_color=False
        , use_subsurf=False
        , use_mesh_edges=False
        , use_tspace=prefs.jobs__use_tspace
        , use_triangles=prefs.jobs__use_triangles
        , use_custom_props=False
        , add_leaf_bones=False
        , primary_bone_axis=preset.get('primary_bone_axis', 'Y')
        , secondary_bone_axis=preset.get('secondary_bone_axis', 'X')
        , use_armature_deform_only=True
        , armature_nodetype='NULL'
        , bake_anim=False
        , path_mode='STRIP'
        , embed_textures=False
        , batch_mode='OFF'
        , use_batch_own_dir=False
        , use_metadata=False
        , axis_forward='-Z'
        , axis_up='Y'
    )

def set_parent_keep_parent_inverse(objs, new_parent):
    for obj in objs:
        m = obj.matrix_parent_inverse.copy()
        obj.parent = new_parent
        obj.matrix_parent_inverse = m

def _scene_export(context, job, save, results):
    save.mode()
    save.selection()
    viewport_reveal_all(context)

    # Find and clone objects to be exported
    # Original objects that aren't exported will be hidden for render, only for driver purposes
    if not job.selection_only:
        export_objs, job_cls = job.get_export_objects(context)
    elif context.selected_objects:
        export_objs, job_cls = [o for o in context.selected_objects if o.type in {'MESH', 'CURVE'}], []
    else:
        # Nothing to export
        return

    ExportItem = namedtuple('ExportItem', 'original obj job_collection collision_objs socket_objs')
    items = []
    groups = defaultdict(list)  # Filepath to list of ExportItems
    for obj in context.scene.objects:
        obj.hide_render = True
    for obj, job_cl in zip_longest(export_objs, job_cls):
        if any(obj.name.startswith(prefix) for prefix in collision_prefixes):
            # Never export collision objects by themselves
            continue
        obj.hide_render = False
        new_obj = save.clone_obj(obj, parent=None)
        items.append(ExportItem(obj, new_obj, job_cl, [], []))

    # Process individual meshes
    for item in items:
        log(f"Processing {item.original.name}")
        obj = item.obj
        job_cl = item.job_collection
        logger.indent += 1

        # Simplify if specified in job collection
        subd_level = job_cl.subdivision_levels if job_cl else 0
        if subd_level < 0:
            unsubdivide_preserve_uvs(obj, -subd_level)
            log(f"Unsubdivided {-subd_level} times")
        elif subd_level > 0:
            subd_mod = obj.modifiers.new(type='SUBSURF', name="")
            subd_mod.levels = subd_level
            subd_mod.use_creases = True
            subd_mod.use_custom_normals = True

        if job.merge_basis_shape_keys:
            for shape_key_pattern in shlex.split(job.basis_shape_key_pattern):
                merge_shape_keys_pattern(obj, shape_key_pattern)

        # Clear shape keys if they won't be needed later
        if not job.encode_shape_keys:
            obj.shape_key_clear()

        apply_modifiers(obj, should_apply_modifier=job.should_apply_modifier)

        if job.use_postprocess_script and job.postprocess_script:
            try:
                log(f"Running post-process script {job.postprocess_script.name}")
                global_dict = globals().copy()
                global_dict.update({'obj': obj})
                exec(job.postprocess_script.as_string(), global_dict, global_dict)
            except:
                raise

        if obj.instance_type != 'NONE' and item.original.children:
            # Instancing is a bit annoying since it relies on hierarchy and matrices get reset
            original_children = item.original.children[:]
            set_parent_keep_parent_inverse(original_children, obj)
            with_object(bpy.ops.object.duplicates_make_real, obj, use_base_parent=True)
            set_parent_keep_parent_inverse(original_children, item.original)
            if obj.children:
                with_object(bpy.ops.object.join, obj, obj.children)
                log(f"Joined {len(obj.children)} instanced objects")

        # Remap materials, any objects or faces with no material won't be exported
        all_none = lambda iterable: all(not el for el in iterable)

        remapped_to_none = False
        for remap in job.remap_materials:
            if remap.source:
                for mat_idx, mat in enumerate(obj.data.materials):
                    if mat and mat == remap.source:
                        log(f"Remapped material {mat.name} to {get_name_safe(remap.destination)}")
                        obj.data.materials[mat_idx] = remap.destination
                        remapped_to_none = remapped_to_none or not remap.destination
            elif remap.destination and all_none(obj.data.materials):
                log(f"Added material {get_name_safe(remap.destination)}")
                obj.data.materials.append(remap.destination)

        if all_none(obj.data.materials):
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
        if job.export_collision:
            item.collision_objs.extend(get_collision_objects(context, item.original))
        if item.collision_objs:
            log(f"Collected {len(item.collision_objs)} collision primitives")

        if job.export_sockets:
            item.socket_objs.extend(o for o in item.original.children if o.type == 'EMPTY')

        # If enabled, move main object to world center while keeping collision relative transforms
        if not job.keep_transforms:
            if item.original.parent:
                pivot_obj = get_topmost_parent(item.original)
                world_to_pivot = pivot_obj.matrix_world.inverted()
                obj.matrix_world = world_to_pivot @ obj.matrix_world
                logd(f"Zero transform for {obj.name} relative to {pivot_obj.name}")
            else:
                world_to_pivot = obj.matrix_world.inverted()
                obj.matrix_world.identity()
                logd(f"Zero transform for {obj.name}")

            for other_obj in chain(item.collision_objs, item.socket_objs):
                save.prop(other_obj, 'matrix_world', world_to_pivot @ other_obj.matrix_world)
                logd(f"Moved collision/socket {other_obj.name}")

        obj.data.transform(obj.matrix_basis, shape_keys=True)
        obj.matrix_basis.identity()

        if job.encode_shape_keys:
            encode_shape_keys(obj, "*_UV")

        obj.shape_key_clear()

        # Bake and clear vertex color mappings before merging
        if get_first_mapping(obj):
            if not obj.data.vertex_colors:
                log("Baking vertex color mappings")
                with_object(bpy.ops.gret.vertex_color_mapping_refresh, obj,
                    invert=job.invert_vertex_color_mappings)
            with_object(bpy.ops.gret.vertex_color_mapping_clear, obj)

        if job.ensure_vertex_color and not obj.data.vertex_colors:
            log("Created default vertex color layer")
            vcol = obj.data.vertex_colors.new()
            for colloop in vcol.data:
                colloop.color = job.default_vertex_color
        elif len(obj.data.vertex_colors) > 1:
            log(f"More than one vertex color layer, is this intended?",
                ", ".join(vc.name for vc in obj.data.vertex_colors))

        # Optionally ensure UV layer. Zero coords to avoid all kinds of problems
        if job.ensure_uv_layers and not obj.data.uv_layers:
            log("Created empty UV layer")
            for uvloop in obj.data.uv_layers.new().data:
                uvloop.uv = (0.0, 0.0)

        # Put the objects in a group
        cl = job_cl.get_collection(context) if job_cl else item.original.users_collection[0]
        path_fields = {
            'job': job.name,
            'scene': context.scene.name,
            'object': item.original.name,
            'topobject': get_topmost_parent(item.original).name,
            'collection': cl.name,
        }
        filepath = get_export_path(job.scene_export_path, path_fields)
        groups[filepath].append(item)
        logger.indent -= 1

    # Export each file
    for filepath, items in sorted(groups.items()):
        filename = bpy.path.basename(filepath)
        objs = [item.obj for item in items]
        all_objs = list(chain.from_iterable([item.obj] + item.collision_objs + item.socket_objs
            for item in items))

        log(f"Exporting {filename} with {len(all_objs)} objects")
        logger.indent += 1

        with SaveContext(context, "_scene_export") as save2:
            for item in items:
                # Export with the original names
                save2.rename(item.obj, item.original.name)

                # Drop Blender number suffixes (.001, .002) from socket names
                for socket_obj in item.socket_objs:
                    socket_name = "SOCKET_" + re.sub(r"\.\d\d\d$", "", socket_obj.name)
                    save2.rename(socket_obj, socket_name)

            # If set, ensure prefix for exported materials
            materials_used = set(chain.from_iterable(obj.data.materials for obj in objs
                if obj.type == 'MESH'))
            materials_used.discard(None)
            if job.material_name_prefix:
                for mat in materials_used:
                    save2.rename(mat, ensure_starts_with(mat.name, job.material_name_prefix))
            log(f"Materials used: {', '.join(sorted(mat.name for mat in materials_used))}")

            # Finally export
            result = export_fbx(filepath, context, all_objs)

            if result == {'FINISHED'}:
                results.append(filename)
            else:
                log(f"Failed to export!")

        logger.indent -= 1

def scene_export(self, context, job):
    assert job.what == 'SCENE'

    # Validate job settings
    try:
        field_names = ['job', 'scene', 'object', 'topobject', 'collection']
        fail_if_invalid_export_path(job.scene_export_path, field_names)
    except Exception as e:
        self.report({'ERROR'}, str(e))
        return {'CANCELLED'}

    results = []
    logger.start_logging()
    log(f"Beginning scene export job '{job.name}'")

    try:
        with SaveContext(context, "scene_export") as save:
            _scene_export(context, job, save, results)

        # Finished without errors
        log("Job complete")
        if prefs.jobs__beep_on_finish:
            beep(pitch=2, num=1)
        if not results:
            self.report({'INFO'}, "No results. See job output log for details.")
        else:
            self.report({'INFO'}, get_nice_export_report(results, logger.time_elapsed))
    finally:
        job.log = logger.end_logging()
