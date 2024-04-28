from collections import defaultdict
from functools import partial
from itertools import chain
from math import pi
import bpy
import os
import shlex

from .. import prefs
from ..log import logger, log, logd
from ..helpers import (
    beep,
    ensure_starts_with,
    fail_if_invalid_export_path,
    get_bid_filepath,
    get_export_path,
    get_modifier,
    get_name_safe,
    get_nice_export_report,
    gret_operator_exists,
    instant_modifier,
    namedtupleish,
    partition,
    viewport_reveal_all,
    with_object,
)
from ..rig.helpers import (
    copy_drivers,
    export_autorig,
    export_fbx,
    is_object_arp,
    is_object_arp_humanoid,
)
from ..mesh.helpers import (
    apply_modifiers,
    apply_shape_keys_with_vertex_groups,
    delete_faces_with_no_material,
    edit_face_map_elements,
    encode_shape_keys,
    get_operator_target_vertex_groups,
    merge_islands,
    merge_shape_keys_pattern,
    mirror_shape_keys,
    remove_shape_keys,
    unsubdivide_preserve_uvs,
)
from ..mesh.vertex_color_mapping import get_first_mapping
from ..operator import SaveContext

def sanitize_mesh(obj):
    # Ensure basis is selected
    obj.active_shape_key_index = 0
    obj.show_only_shape_key = False

    # After messing with shape keys, basis may be left in an undesirable state
    # Not sure why, and data.update() doesn't seem to fix it
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.object.mode_set(mode='OBJECT')

    # Delete drivers made invalid by deleted modifiers and so on
    if obj.animation_data:
        for fc in obj.animation_data.drivers[:]:
            if not fc.driver.is_valid:
                logd(f"Removing invalid driver {fc.data_path}")
                obj.animation_data.drivers.remove(fc)

    # Prefer no shape keys at all if only basis is left
    if obj.data.shape_keys and len(obj.data.shape_keys.key_blocks) == 1:
        obj.shape_key_clear()

def _rig_export(context, job, rig, save, results):
    rig_filepath = get_bid_filepath(rig)
    rig_basename = os.path.splitext(bpy.path.basename(rig_filepath))[0]

    save.mode()
    save.selection()
    if rig.name not in context.view_layer.objects:
        # Workaround for ARP, ensure the rig is in the scene
        context.scene.collection.objects.link(rig)
        save.temporary(context.scene.collection.objects, rig)
    viewport_reveal_all(context)
    assert rig.visible_get()
    context.view_layer.objects.active = rig
    save.prop(rig, 'pose_blender.enabled', False)  # ARP doesn't like it
    save.prop(rig.data, 'pose_position', 'REST')
    save.prop_foreach(rig.data.bones, 'use_deform')

    # Find and clone objects to be exported
    # Original objects that aren't exported will be hidden for render, only for driver purposes
    export_objs, job_cls = job.get_export_objects(context)

    ExportItem = namedtupleish('ExportItem', 'original obj job_collection subd_level')
    items = []
    groups = defaultdict(list)  # Filepath to list of ExportItems
    for obj in context.scene.objects:
        obj.hide_render = True
    for obj, job_cl in zip(export_objs, job_cls):
        obj.hide_render = False
        new_obj = save.clone_obj(obj, parent=rig)
        items.append(ExportItem(obj, new_obj, job_cl, job_cl.subdivision_levels))

    # Process individual meshes
    for item in items:
        log(f"Processing {item.original.name}")
        obj = item.obj
        mesh = obj.data
        job_cl = item.job_collection
        logger.indent += 1

        # Simplify now if specified in job collection. Subdivision is handled after merging
        if item.subd_level < 0:
            unsubdivide_preserve_uvs(obj, -item.subd_level)
            log(f"Unsubdivided {-item.subd_level} times")
            item.subd_level = 0

        # Ensure mesh has custom normals so that they won't be recalculated on masking
        with_object(bpy.ops.mesh.customdata_custom_splitnormals_add, obj)
        if hasattr(mesh, "use_auto_smooth"):
            mesh.use_auto_smooth = True
            mesh.auto_smooth_angle = pi
        else:
            mesh.shade_smooth()

        # Remove vertex group filtering from shapekeys before merging
        apply_shape_keys_with_vertex_groups(obj)

        if job.merge_basis_shape_keys:
            for shape_key_pattern in shlex.split(job.basis_shape_key_pattern):
                merge_shape_keys_pattern(obj, shape_key_pattern)

        # Don't export muted shape keys
        if mesh.shape_keys and mesh.shape_keys.key_blocks:
            for sk in mesh.shape_keys.key_blocks:
                if sk.mute:
                    obj.shape_key_remove(sk)

        if job.mirror_shape_keys:
            # Create shape keys with L/R vertex group masking, to be applied after mirroring
            mirror_shape_keys(obj, job.side_vgroup_name)

        apply_modifiers(obj, should_apply_modifier=job.should_apply_modifier)

        if job.mirror_shape_keys:
            apply_shape_keys_with_vertex_groups(obj)

        # Remap materials, any objects or faces with no material won't be exported
        all_none = lambda iterable: all(not el for el in iterable)

        remapped_to_none = False
        for remap in job.remap_materials:
            if remap.source:
                for mat_index, mat in enumerate(mesh.materials):
                    if mat and mat == remap.source:
                        log(f"Remapped material {mat.name} to {get_name_safe(remap.destination)}")
                        mesh.materials[mat_index] = remap.destination
                        remapped_to_none = remapped_to_none or not remap.destination
            elif remap.destination and all_none(mesh.materials):
                log(f"Added material {get_name_safe(remap.destination)}")
                mesh.materials.append(remap.destination)

        if all_none(mesh.materials):
            log(f"Object has no materials and won't be exported")
            logger.indent -= 1
            continue

        if remapped_to_none:
            delete_faces_with_no_material(obj)
            if not mesh.polygons:
                log(f"Object has no faces and won't be exported")
                logger.indent -= 1
                continue

        # Holes in the material list tend to mess everything up on joining objects
        # Note this is not the same as bpy.ops.object.material_slot_remove_unused
        for mat_index in range(len(mesh.materials) - 1, -1, -1):
            if not mesh.materials[mat_index]:
                logd(f"Popped empty material #{mat_index}")
                mesh.materials.pop(index=mat_index)

        # Bake and clear vertex color mappings before merging
        if get_first_mapping(obj):
            if not mesh.vertex_colors:
                log("Baking vertex color mappings")
                with_object(bpy.ops.gret.vertex_color_mapping_refresh, obj,
                    invert=job.invert_vertex_color_mappings)
            with_object(bpy.ops.gret.vertex_color_mapping_clear, obj)

        if job.ensure_vertex_color and not mesh.vertex_colors:
            log("Created default vertex color layer")
            vcol = mesh.vertex_colors.new()
            for loop in vcol.data:
                loop.color = job.default_vertex_color

        # Ensure vertex color layers share a single name so they merge correctly
        default_vcol_name = "Col"
        if len(mesh.vertex_colors) > 1:
            log(f"More than one vertex color layer, is this intended?",
                ", ".join(vcol.name for vcol in mesh.vertex_colors))
        elif mesh.vertex_colors.active and mesh.vertex_colors.active.name != default_vcol_name:
            logd(f"Renamed vertex color layer {mesh.vertex_colors.active.name} to {default_vcol_name}")
            mesh.vertex_colors.active.name = default_vcol_name

        # Ensure proper mesh state
        sanitize_mesh(obj)
        if gret_operator_exists('vertex_group_remove_unused'):
            with_object(bpy.ops.gret.vertex_group_remove_unused, obj)
        mesh.transform(obj.matrix_basis, shape_keys=True)
        obj.matrix_basis.identity()

        # Put the objects in a group
        path_fields = {
            'job': job.name,
            'scene': context.scene.name,
            'rigfile': rig_basename,
            'rig': rig.name.removesuffix('_rig'),
            'object': item.original.name,
            'collection': job_cl.get_collection(context).name,
        }
        filepath = None if job.to_collection else get_export_path(job.rig_export_path, path_fields)
        groups[filepath].append(item)
        logger.indent -= 1
    del items  # These objects might become invalid soon

    def merge_items(items):
        if len(items) <= 1:
            return items[0]

        # Pick the object that all others will be merged into
        # First choice should be the character's body, otherwise pick the densest mesh
        merged_item = next((it for it in items if it.original.name.lower() == "body"), None)
        if merged_item is None:
            merged_item = max(items, key=lambda it: len(it.obj.data.vertices))

        with_object(bpy.ops.object.join, merged_item.obj, [item.obj for item in items])
        log(f"Merged {', '.join(it.original.name for it in items if it is not merged_item)} "
            f"into {merged_item.original.name}")

        num_verts_merged = merge_islands(merged_item.obj,
            mode=job.weld_mode,
            threshold=job.weld_distance)
        if num_verts_merged > 0:
            log(f"Welded {num_verts_merged} vertices")

        items[:] = [merged_item]
        return merged_item

    # Process groups. Meshes in each group are merged together
    for filepath, group_items in sorted(groups.items()):
        log(f"Processing {bpy.path.basename(filepath) if filepath else 'unnamed group'}")
        logger.indent += 1

        items = group_items[:]
        while len(items) > 1:
            # Merge items with the same requested subdiv level. Repeat until there's one item left
            max_subd_level = max(it.subd_level for it in items)
            items_to_merge, items = partition(items, lambda it: it.subd_level == max_subd_level)
            item = merge_items(items_to_merge)
            items.append(item)

            if item.subd_level > 0:
                # Meshes can deform unpredictably if weights weren't normalized before subdivision
                # with_object(bpy.ops.object.vertex_group_normalize_all, item.obj,
                    # group_select_mode='BONE_DEFORM', lock_active=False)
                with instant_modifier(item.obj, type='SUBSURF') as subd_mod:
                    subd_mod.levels = item.subd_level
                    subd_mod.use_creases = True
                    subd_mod.use_custom_normals = True
                log(f"Subdivided {item.original.name} {item.subd_level} times")
                item.subd_level = 0

        # Joining objects loses drivers, restore them
        for item in group_items:
            copy_drivers(item.original.data.shape_keys, items[0].obj.data.shape_keys)

        group_items[:] = items
        logger.indent -= 1

    # Post-process
    for item in chain.from_iterable(groups.values()):
        log(f"Post-processing mesh {item.original.name}")
        obj = item.obj
        logger.indent += 1

        if job.subdivide_faces:
            for face_map_name in shlex.split(job.subdivide_face_map_names):
                num_selected = edit_face_map_elements(obj, face_map_name)
                if num_selected:
                    log(f"Subdividing face map {face_map_name} ({num_selected} faces)")
                    with_object(bpy.ops.gret.cut_faces_smooth, obj)
                else:
                    log(f"Face map {face_map_name} doesn't exist or has no faces for subdivision")
                bpy.ops.object.editmode_toggle()

        if job.encode_shape_keys:
            encode_shape_keys(obj, "*_UV")
        else:
            remove_shape_keys(obj, "*_UV")

        # Assign to rig. Parenting is not strictly necessary, only ARP wants it that way
        rig_mod = get_modifier(obj, type='ARMATURE', name="rig")
        rig_mod.object = rig
        rig_mod.show_expanded = False
        obj.parent = rig

        if prefs.jobs__limit_vertex_weights > 0:
            log(f"Limiting vertex weights to {prefs.jobs__limit_vertex_weights}")
            with_object(bpy.ops.object.vertex_group_limit_total, obj,
                group_select_mode='BONE_DEFORM',
                limit=prefs.jobs__limit_vertex_weights)

        # Ensure proper mesh state
        sanitize_mesh(obj)
        logger.indent -= 1

    if job.to_collection:
        # Keep new objects in the target collection
        objs = [item.obj for item in chain.from_iterable(groups.values())]
        old_objs = {}

        if job.clean_collection:
            log(f"Cleaning target collection")
            for obj in job.export_collection.objects:
                old_objs[obj.name] = obj
                obj.name += "_"
                obj.data.name += "_"
                job.export_collection.objects.unlink(obj)

        for obj in objs:
            if len(objs) == 1:
                # If producing a single object, rename it to match the collection
                obj.name = job.export_collection.name
                obj.data.name = job.export_collection.name

            old_obj = old_objs.get(obj.name)
            if old_obj:
                old_data = old_obj.data
                logd(f"Remap object {old_obj.name} -> {obj.name} ({old_obj.users} users)")
                logd(f"Remap data {old_data.name} -> {obj.data.name} ({old_data.users} users)")
                old_obj.data.user_remap(obj.data)
                old_obj.user_remap(obj)
                old_obj.data = old_data  # Revert remap for the old object

            job.export_collection.objects.link(obj)
            context.scene.collection.objects.unlink(obj)
            apply_shape_keys_with_vertex_groups(obj)

            # Remove superfluous data
            if hasattr(obj.data, 'shape_key_storage'):
                obj.data.shape_key_storage.clear()
            clean_vertex_groups = False
            if clean_vertex_groups:
                for vg_index in reversed(get_operator_target_vertex_groups(obj, 'BONE_NOT_DEFORM')):
                    obj.vertex_groups.remove(obj.vertex_groups[vg_index])

            # Auto-smooth has a noticeable impact in performance while animating,
            # disable unless the user explicitly enabled it in the previous build result
            if hasattr(obj.data, "use_auto_smooth"):
                # Auto-smooth doesn't exist since 4.1
                obj.data.use_auto_smooth = False
                if old_obj and old_obj.type == 'MESH':
                    obj.data.use_auto_smooth = old_obj.data.use_auto_smooth

            results.extend([obj, obj.data])

        for old_obj in old_objs.values():
            old_data = obj.data
            bpy.data.objects.remove(old_obj)
            if old_data.users == 0 and isinstance(old_data, bpy.types.Mesh):
                bpy.data.meshes.remove(old_data)

        # Don't delete the produced objects
        save.keep_temporary_bids(results)
    else:
        # Prepare export options
        remove_bone_names = shlex.split(job.remove_bone_names) if job.remove_bones else []
        rename_bone_pairs = job.get_rename_bone_pairs()

        # Export each file
        for filepath, items in groups.items():
            filename = bpy.path.basename(filepath)
            objs = [item.obj for item in items]

            if is_object_arp_humanoid(rig):
                log(f"Exporting {filename} via Auto-Rig export (humanoid)")
                exporter = partial(export_autorig, humanoid=True)
            elif is_object_arp(rig):
                log(f"Exporting {filename} via Auto-Rig export (universal)")
                exporter = partial(export_autorig, humanoid=False)
            else:
                log(f"Exporting {filename}")
                exporter = export_fbx
            logger.indent += 1

            with SaveContext(context, "_rig_export") as save2:
                logd(f"{len(objs)} object{'s' if len(objs) > 1 else ''} in group")

                # Export with the original names
                for item in items:
                    save2.rename(item.obj, item.original.name)

                # If set, ensure prefix for exported materials
                materials_used = set(chain.from_iterable(obj.data.materials for obj in objs
                    if obj.type == 'MESH'))
                if job.material_name_prefix:
                    for mat in materials_used:
                        save2.rename(mat, ensure_starts_with(mat.name, job.material_name_prefix))
                log(f"Materials used: {', '.join(sorted(mat.name for mat in materials_used))}")

                # Finally export
                result = exporter(filepath, context, rig,
                    objects=objs,
                    minimize_bones=job.minimize_bones,
                    remove_bone_names=remove_bone_names,
                    rename_bone_pairs=rename_bone_pairs)

                if result == {'FINISHED'}:
                    results.append(filepath)
                else:
                    log("Failed to export!")

            logger.indent -= 1

def rig_export(self, context, job):
    assert job.what == 'RIG'
    rig = job.rig

    # Validate job settings
    if not rig or rig.type != 'ARMATURE':
        self.report({'ERROR'}, "No armature selected.")
        return {'CANCELLED'}
    if job.to_collection and not job.export_collection:
        self.report({'ERROR'}, "No collection selected to export to.")
        return {'CANCELLED'}
    try:
        if not job.to_collection:
            field_names = ['job', 'scene', 'rigfile', 'rig', 'object', 'collection']
            fail_if_invalid_export_path(job.rig_export_path, field_names)
    except Exception as e:
        self.report({'ERROR'}, str(e))
        return {'CANCELLED'}

    results = []
    logger.start_logging()
    log(f"Beginning rig export job '{job.name}'")

    try:
        with SaveContext(context, "rig_export") as save:
            _rig_export(context, job, rig, save, results)

        # Finished without errors
        log("Job complete")
        if prefs.jobs__beep_on_finish:
            beep(pitch=0)
        if not results:
            self.report({'INFO'}, "No results. See job output log for details.")
        elif job.to_collection:
            self.report({'INFO'}, f"Result placed in collection '{job.export_collection.name}'.")
        else:
            self.report({'INFO'}, get_nice_export_report(results, logger.time_elapsed))
    finally:
        job.log = logger.end_logging()

    if job.to_collection:
        # Scene has new objects
        bpy.ops.ed.undo_push()

    return {'FINISHED'}
