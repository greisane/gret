from collections import namedtuple
from itertools import chain
from math import pi
import bpy
import os
import re
import time

from gret.helpers import (
    beep,
    fail_if_invalid_export_path,
    fail_if_no_operator,
    get_children_recursive,
    get_context,
    get_export_path,
    get_nice_export_report,
    load_properties,
    load_selection,
    save_properties,
    save_selection,
    select_only,
)
from gret.mesh.helpers import (
    apply_modifiers,
    apply_shape_keys_with_vertex_groups,
    delete_faces_with_no_material,
    merge_basis_shape_keys,
    merge_freestyle_edges,
    mirror_shape_keys,
    unsubdivide_preserve_uvs,
)
from gret import prefs
from gret.log import logger, log, logd
from gret.rig.helpers import (
    clear_pose,
    export_autorig,
    export_autorig_universal,
    export_fbx,
    is_object_arp,
    is_object_arp_humanoid,
)

class GRET_OT_rig_export(bpy.types.Operator):
    bl_idname = 'gret.rig_export'
    bl_label = "Rig Export"
    bl_context = 'objectmode'
    bl_options = {'INTERNAL'}

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
                self.new_meshes.add(new_data)
            else:
                log(f"Copied data of object {obj.name} won't be released!")
            new_obj.data = new_data
        self.new_objs.add(new_obj)

        # New objects are moved to the scene collection, ensuring they're visible
        bpy.context.scene.collection.objects.link(new_obj)
        new_obj.hide_set(False)
        new_obj.hide_viewport = False
        new_obj.hide_select = False
        return new_obj

    def sanitize_mesh(self, obj):
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

    def _execute(self, context, job, rig):
        rig_filepath = (rig.proxy.library.filepath if rig.proxy and rig.proxy.library
            else bpy.data.filepath)
        path_fields = {
            'rigfile': os.path.splitext(bpy.path.basename(rig_filepath))[0],
            'rig': rig.name,
        }
        cloned_objs = []
        rig.data.pose_position = 'REST'

        if job.to_collection and job.clean_collection:
            # Clean the target collection first
            # Currently not checking whether the rig is in here, it will probably explode
            log(f"Cleaning target collection")
            for obj in job.export_collection.objects:
                bpy.data.objects.remove(obj, do_unlink=True)

        # Find and clone objects to be exported
        origin_objs, job_cls = job.get_export_objects(context, types={'MESH'}, armature=rig)
        cloned_objs = [self.copy_obj(oobj) for oobj in origin_objs]
        def remove_obj(obj_index):
            origin_objs.pop(obj_index)
            cloned_objs.pop(obj_index)
            job_cls.pop(obj_index)

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

        for obj_index, obj in enumerate(cloned_objs[:]):
            log(f"Processing {obj.name}")
            ctx = get_context(obj)
            logger.indent += 1

            # Simplify if specified in job collection
            levels = job_cls[obj_index].subdivision_levels
            if levels < 0:
                unsubdivide_preserve_uvs(obj, -levels)

            # Ensure mesh has custom normals so that they won't be recalculated on masking
            bpy.ops.mesh.customdata_custom_splitnormals_add(ctx)
            obj.data.use_auto_smooth = True
            obj.data.auto_smooth_angle = pi

            if job.merge_basis_shape_keys:
                merge_basis_shape_keys(obj)

            if job.mirror_shape_keys:
                mirror_shape_keys(obj, job.side_vgroup_name)

            # Only use modifiers enabled for render. Delete unused modifiers
            context.view_layer.objects.active = obj
            for mod_idx, mod in enumerate(obj.modifiers[:]):
                if should_enable_modifier(mod):
                    logd(f"Enabled {mod.type} modifier {mod.name}")
                    mod.show_viewport = True
                else:
                    logd(f"Removed {mod.type} modifier {mod.name}")
                    bpy.ops.object.modifier_remove(ctx, modifier=mod.name)

            if job.apply_modifiers:
                apply_modifiers(obj)

            # Remap materials, any objects or faces with no material won't be exported
            for mat_idx, mat in enumerate(obj.data.materials):
                for remap in job.remap_materials:
                    if mat and mat == remap.source:
                        logd(f"Remapped material {mat.name} to {remap.destination}")
                        obj.data.materials[mat_idx] = remap.destination
                        break
            if all(not mat for mat in obj.data.materials):
                log(f"Object has no materials and won't be exported")
                remove_obj(obj_index)
                logger.indent -= 1
                continue
            delete_faces_with_no_material(obj)
            if not obj.data.polygons:
                log(f"Object has no faces and won't be exported")
                remove_obj(obj_index)
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
            # While in Blender it's more intuitive to author masks starting from black, however
            # UE4 defaults to white. Materials should then use OneMinus to get the original value
            if not obj.data.vertex_colors and not obj.vertex_color_mapping:
                bpy.ops.mesh.vertex_color_mapping_add(ctx)
            bpy.ops.mesh.vertex_color_mapping_refresh(ctx, invert=True)
            bpy.ops.mesh.vertex_color_mapping_clear(ctx)

            # Ensure proper mesh state
            self.sanitize_mesh(obj)
            logger.indent -= 1

        # Create export groups. Meshes in each group are merged together
        ExportGroup = namedtuple('ExportGroup', ['suffix', 'objects'])
        groups = []
        if job.join_meshes:
            groups.append(ExportGroup(suffix="", objects=cloned_objs[:]))
        else:
            # Each mesh exports to a different file
            for oobj, cobj in zip(origin_objs, cloned_objs):
                groups.append(ExportGroup(suffix=f"_{oobj.name}", objects=[cobj]))

        merges = {}
        for group in groups:
            objs = group.objects
            if len(objs) <= 1:
                continue

            # Pick the densest object to receive all the others
            merged_obj = max(objs, key=lambda o: len(o.data.vertices))
            merges.update({obj.name: merged_obj for obj in objs})
            log(f"Merging {', '.join(obj.name for obj in objs if obj is not merged_obj)} " \
                f"into {merged_obj.name}")
            logger.indent += 1

            # TODO this sucks
            for obj in objs:
                if obj != merged_obj:
                    self.new_objs.discard(obj)
                    self.new_meshes.discard(obj.data)
            ctx = get_context(active_obj=merged_obj, selected_objs=objs)
            bpy.ops.object.join(ctx)
            group.objects[:] = [merged_obj]

            # Joining objects loses drivers, restore them
            for oobj in origin_objs:
                if oobj.data.shape_keys and oobj.data.shape_keys.animation_data:
                    for fc in oobj.data.shape_keys.animation_data.drivers:
                        if merged_obj.data.shape_keys.animation_data is None:
                            merged_obj.data.shape_keys.animation_data_create()
                        merged_obj.data.shape_keys.animation_data.drivers.from_existing(src_driver=fc)

            # Ensure proper mesh state
            self.sanitize_mesh(merged_obj)

            num_verts_merged = merge_freestyle_edges(merged_obj)
            if num_verts_merged > 0:
                log(f"Welded {num_verts_merged} verts (edges were marked freestyle)")
            logger.indent -= 1

        if job.to_collection:
            # Keep new objects in the target collection
            for group in groups:
                for obj in group.objects:
                    if len(group.objects) == 1:
                        # If producing a single object, rename it to match the collection
                        obj.name = job.export_collection.name
                        obj.data.name = job.export_collection.name
                    job.export_collection.objects.link(obj)
                    context.scene.collection.objects.unlink(obj)
                    # Disable features on output meshes for performance
                    obj.data.use_auto_smooth = False
                    obj.data.use_customdata_vertex_bevel = False
                    obj.data.use_customdata_edge_bevel = False
                    obj.data.use_customdata_edge_crease = False
                    # Don't delete this
                    self.new_objs.discard(obj)
                    self.new_meshes.discard(obj.data)
        else:
            # Finally export
            for group in groups:
                path_fields['suffix'] = group.suffix
                filepath = get_export_path(job.rig_export_path, path_fields)
                filename = bpy.path.basename(filepath)
                if filepath in self.exported_files:
                    log(f"Skipping {filename} as it would overwrite a file that was just exported")

                select_only(context, group.objects)
                rig.select_set(True)
                context.view_layer.objects.active = rig
                rig.data.pose_position = 'POSE'
                clear_pose(rig)

                if is_object_arp_humanoid(rig):
                    log(f"Exporting {filename} via Auto-Rig export")
                    logger.indent += 1
                    result = export_autorig(context, filepath, [])
                elif is_object_arp(rig):
                    log(f"Exporting {filename} via Auto-Rig export (universal)")
                    logger.indent += 1
                    result = export_autorig_universal(context, filepath, [])
                else:
                    # Temporarily rename the armature as it's the root bone itself
                    saved_rig_name = rig.name
                    rig.name = "root"
                    log(f"Exporting {filename}")
                    logger.indent += 1
                    result = export_fbx(context, filepath, [])
                    rig.name = saved_rig_name
                logger.indent -= 1

                if result == {'FINISHED'}:
                    self.exported_files.append(filepath)
                else:
                    log("Failed to export!")

    def execute(self, context):
        job = context.scene.gret.export_jobs[self.index]
        rig = job.rig
        assert job.what == 'RIG'

        if not rig or rig.type != 'ARMATURE':
            self.report({'ERROR'}, "No armature selected.")
            return {'CANCELLED'}
        if job.to_collection and not job.export_collection:
            self.report({'ERROR'}, "No collection selected to export to.")
            return {'CANCELLED'}
        if not rig.visible_get():
            self.report({'ERROR'}, "Currently the rig must be visible to export.")
            return {'CANCELLED'}
        context.view_layer.objects.active = rig

        # Check addon availability and export path
        try:
            fail_if_no_operator('shape_key_apply_modifiers')
            fail_if_no_operator('vertex_color_mapping_refresh', submodule=bpy.ops.mesh)
            if not job.to_collection:
                fail_if_invalid_export_path(job.rig_export_path, ['rigfile', 'rig'])
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        saved_selection = save_selection(all_objects=True)
        saved_pose_position = rig.data.pose_position
        saved_use_global_undo = context.preferences.edit.use_global_undo
        context.preferences.edit.use_global_undo = False
        self.exported_files = []
        self.new_objs = set()
        self.new_meshes = set()
        self.saved_material_names = {}
        self.saved_materials = []  # List of (obj, material_idx, material)
        self.saved_auto_smooth = {}
        logger.start_logging()
        log(f"Beginning rig export job '{job.name}'")

        try:
            start_time = time.time()
            self._execute(context, job, rig)
            # Finished without errors
            elapsed = time.time() - start_time
            self.report({'INFO'}, get_nice_export_report(self.exported_files, elapsed))
            log("Job complete")
            beep(pitch=0)
        finally:
            # Clean up
            while self.new_objs:
                bpy.data.objects.remove(self.new_objs.pop())
            while self.new_meshes:
                bpy.data.meshes.remove(self.new_meshes.pop())
            for mat, name in self.saved_material_names.items():
                mat.name = name
            for obj, material_idx, material in self.saved_materials:
                obj.data.materials[material_idx] = material
            for obj, (value, angle) in self.saved_auto_smooth.items():
                obj.data.use_auto_smooth = value
                obj.data.auto_smooth_angle = angle
            del self.saved_materials
            del self.saved_material_names
            del self.saved_auto_smooth
            rig.data.pose_position = saved_pose_position
            context.preferences.edit.use_global_undo = saved_use_global_undo
            load_selection(saved_selection)
            logger.end_logging()

        if job.to_collection:
            # Crashes if undo is attempted right after a simulate export job
            # Pushing an undo step here seems to prevent that
            bpy.ops.ed.undo_push()

        return {'FINISHED'}

def register(settings):
    bpy.utils.register_class(GRET_OT_rig_export)

def unregister():
    bpy.utils.unregister_class(GRET_OT_rig_export)
