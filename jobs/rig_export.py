from collections import namedtuple, defaultdict
from itertools import chain
from math import pi
import bpy
import os
import re
import shlex
import time

from .. import prefs
from ..helpers import (
    beep,
    fail_if_invalid_export_path,
    get_context,
    get_export_path,
    get_name_safe,
    get_nice_export_report,
    get_object_filepath,
    gret_operator_exists,
    load_properties,
    load_selection,
    save_properties,
    save_selection,
    viewport_reveal_all,
)
from ..mesh.helpers import (
    apply_modifiers,
    apply_shape_keys_with_vertex_groups,
    delete_faces_with_no_material,
    encode_shape_keys,
    merge_shape_keys,
    merge_freestyle_edges,
    mirror_shape_keys,
    unsubdivide_preserve_uvs,
)
from ..log import logger, log, logd
from ..rig.helpers import (
    copy_drivers,
    export_autorig,
    export_autorig_universal,
    export_fbx,
    is_object_arp,
    is_object_arp_humanoid,
)

def copy_obj(self, obj, copy_data=True):
    new_obj = obj.copy()
    new_obj.name = obj.name + "_"
    if copy_data:
        new_data = obj.data.copy()
        if isinstance(new_data, bpy.types.Mesh):
            self.new_meshes.add(new_data)
        else:
            log(f"Copied data of object {obj.name} won't be released!")
        new_obj.data = new_data
    self.new_objs.add(new_obj)

    # Move object materials to mesh
    for mat_idx, mat_slot in enumerate(obj.material_slots):
        if mat_slot.link == 'OBJECT':
            new_data.materials[mat_idx] = mat_slot.material

    # New objects are moved to the scene collection, ensuring they're visible
    bpy.context.scene.collection.objects.link(new_obj)
    new_obj.hide_set(False)
    new_obj.hide_viewport = False
    new_obj.hide_select = False
    return new_obj

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
                obj.animation_data.drivers.remove(fc)

    # Prefer no shape keys at all if only basis is left
    if obj.data.shape_keys and len(obj.data.shape_keys.key_blocks) == 1:
        obj.shape_key_clear()

def _rig_export(self, context, job, rig):
    rig_filepath = get_object_filepath(rig)
    rig_basename = os.path.splitext(bpy.path.basename(rig_filepath))[0]
    rig.data.pose_position = 'REST'

    use_auto_smooth = False
    if job.to_collection and job.clean_collection:
        # Clean the target collection first
        # Currently not checking whether the rig is in here, it will probably explode
        log(f"Cleaning target collection")
        if len(job.export_collection.objects) == 1:
            # Remember auto smooth setting
            only_obj = job.export_collection.objects[0]
            use_auto_smooth = only_obj.type == 'MESH' and only_obj.data.use_auto_smooth
        for obj in job.export_collection.objects:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data.users == 0 and isinstance(data, bpy.types.Mesh):
                bpy.data.meshes.remove(data, do_unlink=True)

    # Find and clone objects to be exported
    # Original objects that aren't exported will be hidden for render, only for driver purposes
    export_objs, job_cls = job.get_export_objects(context, types={'MESH'}, armature=rig)

    ExportItem = namedtuple('ExportItem', ['original', 'obj', 'job_collection'])
    items = []
    groups = defaultdict(list)  # Filepath to list of ExportItems
    for obj in context.scene.objects:
        obj.hide_render = True
    for obj, job_cl in zip(export_objs, job_cls):
        obj.hide_render = False
        items.append(ExportItem(obj, copy_obj(self, obj), job_cl))

    # Process individual meshes
    job_tags = job.modifier_tags.split(' ')
    def should_apply_modifier(modifier):
        if modifier.type == 'ARMATURE':
            return False
        for tag in re.findall(r"g:(\S+)", modifier.name):
            if tag.startswith('!'):
                # Blacklisted tag
                return tag[1:] not in job_tags
            else:
                return tag in job_tags
        return modifier.show_render

    for item in items:
        log(f"Processing {item.original.name}")
        obj = item.obj
        job_cl = item.job_collection
        ctx = get_context(obj)
        logger.indent += 1

        # Simplify if specified in job collection
        levels = job_cl.subdivision_levels
        if levels < 0:
            unsubdivide_preserve_uvs(obj, -levels)

        # Ensure mesh has custom normals so that they won't be recalculated on masking
        bpy.ops.mesh.customdata_custom_splitnormals_add(ctx)
        obj.data.use_auto_smooth = True
        obj.data.auto_smooth_angle = pi

        if job.merge_basis_shape_keys:
            for shape_key_pattern in shlex.split(job.basis_shape_key_pattern):
                try:
                    shape_key_pattern, target_shape_key_name = shape_key_pattern.split("->")
                    merge_shape_keys(obj, [shape_key_pattern], target_shape_key_name)
                except ValueError:
                    merge_shape_keys(obj, [shape_key_pattern])

        if job.mirror_shape_keys:
            mirror_shape_keys(obj, job.side_vgroup_name)

        if job.apply_modifiers:
            apply_modifiers(obj, key=should_apply_modifier, keep_armature=True)

        # Remap materials, any objects or faces with no material won't be exported
        all_none = lambda iterable: all(not el for el in iterable)

        remapped_to_none = False
        for remap in job.remap_materials:
            if remap.source:
                for mat_idx, mat in enumerate(obj.data.materials):
                    if mat and mat == remap.source:
                        logd(f"Remapped material {mat.name} to {get_name_safe(remap.destination)}")
                        obj.data.materials[mat_idx] = remap.destination
                        remapped_to_none = remapped_to_none or not remap.destination
            elif remap.destination and all_none(obj.data.materials):
                logd(f"Added material {get_name_safe(remap.destination)}")
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

        # Holes in the material list tend to mess everything up on joining objects
        # Note this is not the same as bpy.ops.object.material_slot_remove_unused
        for mat_idx in range(len(obj.data.materials) - 1, -1, -1):
            if not obj.data.materials[mat_idx]:
                logd(f"Popped empty material #{mat_idx}")
                obj.data.materials.pop(index=mat_idx)

        # If set, ensure prefix for exported materials
        if job.material_name_prefix:
            for mat_slot in obj.material_slots:
                mat = mat_slot.material
                if mat and not mat.name.startswith(job.material_name_prefix):
                    self.saved_material_names[mat] = mat.name
                    mat.name = job.material_name_prefix + mat.name

        # Remove vertex group filtering from shapekeys
        apply_shape_keys_with_vertex_groups(obj)

        # Refresh vertex color and clear the mappings to avoid issues when meshes are merged
        # It's more intuitive to author masks starting from black, however UE4 defaults to white
        # Invert vertex colors, materials should use OneMinus to get the original value
        if gret_operator_exists("gret.vertex_color_mapping_add"):
            if not obj.data.vertex_colors and not obj.vertex_color_mapping:
                logd("Created default vertex color mapping")
                bpy.ops.gret.vertex_color_mapping_add(ctx)
            bpy.ops.gret.vertex_color_mapping_refresh(ctx, invert=job.invert_vertex_color_mappings)
            bpy.ops.gret.vertex_color_mapping_clear(ctx)
            if len(obj.data.vertex_colors) > 1:
                logd(f"More than one vertex color layer, is this intended?",
                    ", ".join(vc.name for vc in obj.data.vertex_colors))

        # Ensure proper mesh state
        sanitize_mesh(obj)
        bpy.ops.gret.vertex_group_remove_unused(ctx)
        obj.data.transform(obj.matrix_basis, shape_keys=True)
        obj.matrix_basis.identity()

        # Put the objects in a group
        path_fields = {
            'rigfile': rig_basename,
            'rig': rig.name.removesuffix('_rig'),
            'object': item.original.name,
            'collection': job_cl.get_collection(context).name,
        }
        filepath = None if job.to_collection else get_export_path(job.rig_export_path, path_fields)
        groups[filepath].append(item)
        logger.indent -= 1
    del items  # These objects might become invalid soon

    # Process groups. Meshes in each group are merged together
    for filepath, items in sorted(groups.items()):
        if len(items) <= 1:
            continue

        # Pick the object that all others will be merged into
        # First choice should be the character's body, otherwise pick the densest mesh
        merged_item = next((it for it in items if it.original.name.lower() == 'body'), None)
        if merged_item is None:
            merged_item = max(items, key=lambda it: len(it.obj.data.vertices))

        log(f"Merging {', '.join(it.original.name for it in items if it is not merged_item)} "
            f"into {merged_item.original.name}")
        logger.indent += 1

        # TODO this sucks
        for obj in (it.obj for it in items if it is not merged_item):
            self.new_objs.discard(obj)
            self.new_meshes.discard(obj.data)
        obj = merged_item.obj
        objs = [item.obj for item in items]
        ctx = get_context(active_obj=obj, selected_objs=objs)
        bpy.ops.object.join(ctx)
        groups[filepath] = [merged_item]

        # Joining objects loses drivers, restore them
        for item in items:
            copy_drivers(item.original.data.shape_keys, obj.data.shape_keys)

        num_verts_merged = merge_freestyle_edges(obj)
        if num_verts_merged > 0:
            log(f"Welded {num_verts_merged} verts (edges were marked freestyle)")
        logger.indent -= 1

    # Post-process
    for item in chain.from_iterable(groups.values()):
        log(f"Post-processing mesh {item.original.name}")
        obj = item.obj
        logger.indent += 1

        if job.encode_shape_keys:
            encode_shape_keys(obj, ["*_UV"])

        # Ensure proper mesh state
        sanitize_mesh(obj)
        logger.indent -= 1

    if job.to_collection:
        # Keep new objects in the target collection
        objs = [item.obj for item in chain.from_iterable(groups.values())]

        for obj in objs:
            if len(objs) == 1:
                # If producing a single object, rename it to match the collection
                obj.name = job.export_collection.name
                obj.data.name = job.export_collection.name
            job.export_collection.objects.link(obj)
            context.scene.collection.objects.unlink(obj)
            # Auto-smooth has a noticeable impact in performance while animating,
            # disable unless the user explicitly enabled it back in the previous build result
            obj.data.use_auto_smooth = use_auto_smooth
            # Don't delete this
            self.new_objs.discard(obj)
            self.new_meshes.discard(obj.data)
    else:
        if job.minimize_bones:
            self.saved_deform_bone_names = [b.name for b in rig.data.bones if b.use_deform]

        # Finally export
        for filepath, items in groups.items():
            filename = bpy.path.basename(filepath)
            objs = [item.obj for item in items]

            if is_object_arp_humanoid(rig):
                log(f"Exporting {filename} via Auto-Rig export")
                exporter = export_autorig
            elif is_object_arp(rig):
                log(f"Exporting {filename} via Auto-Rig export (universal)")
                exporter = export_autorig_universal
            else:
                log(f"Exporting {filename}")
                exporter = export_fbx
            logger.indent += 1
            logd(f"{len(objs)} objects in group")

            options = {'minimize_bones': job.minimize_bones}
            result = exporter(filepath, context, rig, objects=objs, options=options)
            if result == {'FINISHED'}:
                self.exported_files.append(filepath)
            else:
                log("Failed to export!")
            logger.indent -= 1

def rig_export(self, context, job):
    assert job.what == 'RIG'
    rig = job.rig

    if not rig or rig.type != 'ARMATURE':
        self.report({'ERROR'}, "No armature selected.")
        return {'CANCELLED'}
    if job.to_collection and not job.export_collection:
        self.report({'ERROR'}, "No collection selected to export to.")
        return {'CANCELLED'}
    context.view_layer.objects.active = rig

    # Check addon availability and export path
    try:
        if not job.to_collection:
            field_names = ['rigfile', 'rig', 'object', 'collection']
            fail_if_invalid_export_path(job.rig_export_path, field_names)
    except Exception as e:
        self.report({'ERROR'}, str(e))
        return {'CANCELLED'}

    saved_selection = save_selection()
    viewport_reveal_all()
    assert rig.visible_get()
    saved_pose_position = rig.data.pose_position
    saved_use_global_undo = context.preferences.edit.use_global_undo
    context.preferences.edit.use_global_undo = False
    self.exported_files = []
    self.new_objs = set()
    self.new_meshes = set()
    self.saved_material_names = {}
    self.saved_deform_bone_names = []
    logger.start_logging()
    log(f"Beginning rig export job '{job.name}'")

    try:
        start_time = time.time()
        _rig_export(self, context, job, rig)
        # Finished without errors
        elapsed = time.time() - start_time
        self.report({'INFO'}, get_nice_export_report(self.exported_files, elapsed))
        log("Job complete")
        if prefs.jobs__beep_on_finish:
            beep(pitch=0)
    finally:
        # Clean up
        while self.new_objs:
            bpy.data.objects.remove(self.new_objs.pop())
        while self.new_meshes:
            bpy.data.meshes.remove(self.new_meshes.pop())
        for mat, name in self.saved_material_names.items():
            mat.name = name
        for bone_name in self.saved_deform_bone_names:
            rig.data.bones[bone_name].use_deform = True
        del self.saved_material_names
        del self.saved_deform_bone_names
        rig.data.pose_position = saved_pose_position
        context.preferences.edit.use_global_undo = saved_use_global_undo
        load_selection(saved_selection)
        logger.end_logging()

    if job.to_collection:
        # Crashes if undo is attempted right after a simulate export job
        # Pushing an undo step here seems to prevent that
        bpy.ops.ed.undo_push()

    return {'FINISHED'}
