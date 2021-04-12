from collections import namedtuple, defaultdict
import bpy
import math
import os
import re
import time

from gret.helpers import (
    beep,
    fail_if_invalid_export_path,
    fail_if_no_operator,
    get_children_recursive,
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
    subdivide_verts_with_bevel_weight,
)
from gret import prefs
from gret.log import logger, log, logd
from gret.rig.helpers import (
    clear_pose,
    export_autorig,
    export_autorig_universal,
    is_object_arp,
    is_object_arp_humanoid,
)

ik_bone_names = [
    "ik_foot_root",
    "ik_foot.l",
    "ik_foot.r",
    "ik_hand_root",
    "ik_hand_gun",
    "ik_hand.l",
    "ik_hand.r"
]

@intercept(error_result={'CANCELLED'})
def export_fbx(context, filepath, actions):
    if actions:
        # Needs to slap action strips in the NLA
        raise NotImplementedError
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
        , object_types={'ARMATURE', 'MESH'}
        , use_mesh_modifiers=True
        , use_mesh_modifiers_render=False
        , mesh_smooth_type='EDGE'
        , bake_space_transform=True
        , use_subsurf=False
        , use_mesh_edges=False
        , use_tspace=False
        , use_custom_props=False
        , add_leaf_bones=False
        , primary_bone_axis='Y'
        , secondary_bone_axis='X'
        , use_armature_deform_only=True
        , armature_nodetype='NULL'
        , bake_anim=len(actions) > 0
        , bake_anim_use_all_bones=False
        , bake_anim_use_nla_strips=False
        , bake_anim_use_all_actions=True
        , bake_anim_force_startend_keying=True
        , bake_anim_step=1.0
        , bake_anim_simplify_factor=1.0
        , path_mode='STRIP'
        , embed_textures=False
        , batch_mode='OFF'
        , use_batch_own_dir=False
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

    def copy_obj_clone_normals(self, obj):
        new_obj = self.copy_obj(obj, copy_data=True)
        new_obj.data.use_auto_smooth = True  # Enable custom normals
        new_obj.data.auto_smooth_angle = math.pi

        # I don't see a way to check if topology mapping is working or not, so clone normals twice
        data_transfer = new_obj.modifiers.new("_Clone Normals", 'DATA_TRANSFER')
        data_transfer.object = obj
        data_transfer.use_object_transform = False
        data_transfer.use_loop_data = True
        data_transfer.loop_mapping = 'NEAREST_POLYNOR'  # 'NEAREST_POLY' fails on sharp edges
        data_transfer.data_types_loops = {'CUSTOM_NORMAL'}
        data_transfer.max_distance = 1e-5
        data_transfer.use_max_distance = True

        data_transfer = new_obj.modifiers.new("_Clone Normals Topology", 'DATA_TRANSFER')
        data_transfer.object = obj
        data_transfer.use_object_transform = False
        data_transfer.use_loop_data = True
        data_transfer.loop_mapping = 'TOPOLOGY'
        data_transfer.data_types_loops = {'CUSTOM_NORMAL'}
        data_transfer.max_distance = 1e-5
        data_transfer.use_max_distance = True
        return new_obj

    def sanitize_mesh(self, obj):
        # Enable autosmooth to allow custom normals
        obj.data.use_auto_smooth = True
        obj.data.auto_smooth_angle = math.pi

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
        mesh_objs = []
        original_objs = []
        rig.data.pose_position = 'REST'

        # Find all unique objects that should be considered for export
        def should_export(job_coll, what):
            if job_coll is None or what is None:
                return False
            return (job_coll.export_viewport and not what.hide_viewport
                or job_coll.export_render and not what.hide_render)
        all_objs = set()
        for job_coll in job.collections:
            coll = job_coll.collection
            if not coll and all(not jc.collection for jc in job.collections):
                # When no collections are set use the scene collection
                coll = scn.collection
            if should_export(job_coll, coll):
                all_objs.update(obj for obj in coll.objects if should_export(job_coll, obj))

        # Mark the objects that should be exported as render so they will be picked up
        objs = set()
        for obj in all_objs:
            if obj.type == 'MESH':
                saved_materials = []
                for mat_idx, mat in enumerate(obj.data.materials):
                    for remap_material in job.remap_materials:
                        if mat and mat is remap_material.source:
                            saved_materials.append((obj, mat_idx, mat))
                            obj.data.materials[mat_idx] = remap_material.destination
                            break
                if all(not mat for mat in obj.data.materials):
                    log(f"Not exporting '{obj.name}' because it has no materials")
                    # Undo any remaps
                    for obj, material_idx, material in saved_materials:
                        obj.data.materials[material_idx] = material
                    continue
                self.saved_materials.extend(saved_materials)
            obj.hide_select = False
            obj.hide_render = False
            objs.add(obj)

        # Hide all objects that shouldn't be exported
        for obj in get_children_recursive(job.rig):
            obj.hide_render = obj not in objs

        if job.to_collection and job.clean_collection:
            # Clean the target collection first
            # Currently not checking whether the rig is in here, it will probably explode
            log(f"Cleaning target collection")
            for obj in job.export_collection.objects:
                bpy.data.objects.remove(obj, do_unlink=True)

        def has_custom_normals(obj):
            return obj.data.has_custom_normals or any(m.type == 'DATA_TRANSFER' and 'CUSTOM_NORMAL'
                in m.data_types_loops for m in obj.modifiers)

        for obj in get_children_recursive(rig):
            # Enable all render modifiers in the originals, except masks
            for modifier in obj.modifiers:
                if modifier.type != 'MASK' and modifier.show_render and not modifier.show_viewport:
                    modifier.show_viewport = True
                    self.saved_disabled_modifiers.add(modifier)
            if obj.type == 'MESH':
                self.saved_auto_smooth[obj] = (obj.data.use_auto_smooth, obj.data.auto_smooth_angle)
                obj.data.use_auto_smooth = True
                obj.data.auto_smooth_angle = math.pi
                if not obj.hide_render and obj.find_armature() == rig:
                    original_objs.append(obj)
                    if not has_custom_normals(obj):
                        mesh_objs.append(self.copy_obj_clone_normals(obj))
                    else:
                        mesh_objs.append(self.copy_obj(obj))

        ExportGroup = namedtuple('ExportGroup', ['suffix', 'objects'])
        export_groups = []
        if mesh_objs:
            if job.join_meshes:
                export_groups.append(ExportGroup(suffix="", objects=mesh_objs[:]))
            else:
                # Each mesh exports to a different file
                for obj, mesh_obj in zip(original_objs, mesh_objs):
                    export_groups.append(ExportGroup(suffix=f"_{obj.name}", objects=[mesh_obj]))

        modifier_tags = job.modifier_tags.split(',')
        kept_modifiers = []  # List of (object name, modifier index, modifier properties)
        wants_subsurf = {}  # Object name to subsurf level
        def should_enable_modifier(mo):
            no_tags = re.findall(r"g:!(\S+)", mo.name)
            if no_tags and any(s in no_tags for s in modifier_tags):
                return False
            yes_tags = re.findall(r"g:(\S+)", mo.name)
            if yes_tags and any(s in yes_tags for s in modifier_tags):
                return True
            return mo.show_render

        # Process individual meshes
        for export_group in export_groups:
            num_objects = len(export_group.objects)
            for obj in export_group.objects:
                log(f"Processing {obj.name}")
                logger.indent += 1

                delete_faces_with_no_material(obj)

                if job.merge_basis_shape_keys:
                    merge_basis_shape_keys(obj)

                if job.mirror_shape_keys:
                    mirror_shape_keys(obj, job.side_vgroup_name)

                # Only use modifiers enabled for render. Delete unused modifiers
                context.view_layer.objects.active = obj
                for modifier_idx, modifier in enumerate(obj.modifiers[:]):
                    if should_enable_modifier(modifier):
                        if modifier.type == 'SUBSURF' and modifier.levels > 0 and num_objects > 1:
                            # Subsurf will be applied after merge, otherwise boundaries won't match up
                            logd(f"Removed {modifier.type} modifier {modifier.name}")
                            wants_subsurf[obj.name] = modifier.levels
                            bpy.ops.object.modifier_remove(modifier=modifier.name)
                        else:
                            logd(f"Enabled {modifier.type} modifier {modifier.name}")
                            modifier.show_viewport = True
                    else:
                        if "!keep" in modifier.name:
                            # Store the modifier to recreate it later
                            kept_modifiers.append((obj.name, modifier_idx, save_properties(modifier)))
                        logd(f"Removed {modifier.type} modifier {modifier.name}")
                        bpy.ops.object.modifier_remove(modifier=modifier.name)

                if job.apply_modifiers:
                    apply_modifiers(obj)

                # If set, ensure prefix for any exported materials
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
                    bpy.ops.mesh.vertex_color_mapping_add()
                bpy.ops.mesh.vertex_color_mapping_refresh(invert=True)
                bpy.ops.mesh.vertex_color_mapping_clear()

                # Ensure proper mesh state
                self.sanitize_mesh(obj)

                logger.indent -= 1

        # Join meshes
        merges = {}
        for export_group in export_groups:
            objs = export_group.objects
            if len(objs) <= 1:
                continue

            # Pick the densest object to receive all the others
            merged_obj = max(objs, key=lambda ob: len(ob.data.vertices))
            merges.update({obj.name: merged_obj for obj in objs})
            log(f"Merging {', '.join(obj.name for obj in objs if obj is not merged_obj)} " \
                f"into {merged_obj.name}")
            logger.indent += 1

            subsurf_levels = max(wants_subsurf.get(obj.name, 0) for obj in objs)
            for obj in objs:
                if subsurf_levels:
                    # Mark vertices that belong to a subsurf mesh
                    obj.data.use_customdata_vertex_bevel = True
                    for vert in obj.data.vertices:
                        vert.bevel_weight = obj.name in wants_subsurf
                if obj is not merged_obj:
                    self.new_objs.discard(obj)
                    self.new_meshes.discard(obj.data)

            ctx = {}
            ctx['object'] = ctx['active_object'] = merged_obj
            ctx['selected_objects'] = ctx['selected_editable_objects'] = objs
            bpy.ops.object.join(ctx)
            objs[:] = [merged_obj]

            # Joining objects won't copy drivers, so do that now
            for original_obj in original_objs:
                if original_obj.data.shape_keys and original_obj.data.shape_keys.animation_data:
                    for fc in original_obj.data.shape_keys.animation_data.drivers:
                        if merged_obj.data.shape_keys.animation_data is None:
                            merged_obj.data.shape_keys.animation_data_create()
                        merged_obj.data.shape_keys.animation_data.drivers.from_existing(src_driver=fc)

            # Ensure proper mesh state
            self.sanitize_mesh(merged_obj)

            num_verts_merged = merge_freestyle_edges(merged_obj)
            if num_verts_merged > 0:
                log(f"Welded {num_verts_merged} verts (edges were marked freestyle)")

            if subsurf_levels:
                subdivide_verts_with_bevel_weight(merged_obj, levels=subsurf_levels)
                merged_obj.data.use_customdata_vertex_bevel = False

            logger.indent -= 1

        if job.to_collection:
            # Keep new objects in the target collection
            for obj in self.new_objs:
                if len(self.new_objs) == 1:
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
            if kept_modifiers:
                # Recreate modifiers that were stored
                log(f"Restoring {len(kept_modifiers)} modifiers")
                for obj_name, index, properties in kept_modifiers:
                    obj = bpy.data.objects.get(obj_name) or merges.get(obj_name)
                    if obj:
                        mod = obj.modifiers.new(name=properties['name'], type=properties['type'])
                        load_properties(mod, properties)

                        new_index = min(index, len(obj.modifiers) - 1)
                        ctx = {'object': obj}
                        bpy.ops.object.modifier_move_to_index(ctx, modifier=mod.name, index=new_index)
            # Don't delete the new stuff
            self.new_objs.clear()
            self.new_meshes.clear()
        else:
            # Finally export
            for export_group in export_groups:
                path_fields['suffix'] = export_group.suffix
                filepath = get_export_path(job.rig_export_path, path_fields)
                filename = bpy.path.basename(filepath)
                if filepath in self.exported_files:
                    log(f"Skipping {filename} as it would overwrite a file that was just exported")

                for obj in context.scene.objects:
                    obj.select_set(False)
                for obj in export_group.objects:
                    obj.select_set(True)
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
        self.saved_disabled_modifiers = set()
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
            beep(pitch=0)
        finally:
            # Clean up
            while self.new_objs:
                bpy.data.objects.remove(self.new_objs.pop())
            while self.new_meshes:
                bpy.data.meshes.remove(self.new_meshes.pop())
            while self.saved_disabled_modifiers:
                self.saved_disabled_modifiers.pop().show_viewport = False
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

        log("Job complete")
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

def resolve_copy_property_source(cp):
    try:
        value = float(cp.source)
        return ConstantCurve(value)
    except ValueError:
        pass
    return next((fc for fc in action.fcurves if fc.data_path == cp.source), None)

class GRET_OT_animation_export(bpy.types.Operator):
    bl_idname = 'gret.animation_export'
    bl_label = "Animation Export"
    bl_context = "objectmode"
    bl_options = {'INTERNAL'}

    index: bpy.props.IntProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def _execute(self, context, job, rig):
        start_time = time.time()
        rig_filepath = (rig.proxy.library.filepath if rig.proxy and rig.proxy.library
            else bpy.data.filepath)
        path_fields = {
            'rigfile': os.path.splitext(bpy.path.basename(rig_filepath))[0],
            'rig': rig.name,
        }

        # Select actions to export
        actions = set()
        for job_action in job.actions:
            if job_action:
                if job_action.use_pattern:
                    for action in bpy.data.actions:
                        if not action.library and fnmatch(action.name, job_action.action):
                            actions.add(action)
                else:
                    action = bpy.data.actions.get(job_action.action)
                    if action and not action.library:
                        actions.add(action)

        # One export group for each action
        ExportGroup = namedtuple('ExportGroup', ('suffix', 'action'))
        export_groups = [ExportGroup(suffix="", action=action) for action in actions]

        # Process individual actions
        for action in actions:
            log(f"Processing {action.name}")
            logger.indent += 1

            for cp in job.copy_properties:
                if not cp.source and not cp.destination:
                    continue
                desc = f"{cp.source}->{cp.destination}"

                def resolve_source(source):
                    try:
                        value = float(source)
                        return ConstantCurve(value)
                    except ValueError:
                        pass
                    return next((fc for fc in action.fcurves if fc.data_path == cp.source), None)
                src_fc = resolve_source(cp.source)
                if not src_fc:
                    log(f"Couldn't bake {desc}, invalid source")
                    continue

                dst_fc = next((fc for fc in action.fcurves if fc.data_path == cp.destination), None)
                if dst_fc:
                    # Currently baking to existing curves is not allowed
                    # Would need to duplicate strips, although ARP already does that
                    log(f"Couldn't bake {desc}, destination already exists")
                    continue

                dst_fc = action.fcurves.new(cp.destination)
                self.new_fcurves.append((action, dst_fc))

                log(f"Baking {desc}")
                for frame_idx in range(0, int(action.frame_range[1]) + 1):
                    val = src_fc.evaluate(frame_idx)
                    dst_fc.keyframe_points.insert(frame_idx, val)

            logger.indent -= 1

        if job.disable_auto_eyelid:
            for bone_name in ('c_eyelid_base.l', 'c_eyelid_base.r'):
                pb = rig.pose.bones.get('c_eyelid_base.l')
                if pb:
                    for constraint in (con for con in pb.constraints if not con.mute):
                        constraint.mute = True
                        self.saved_unmuted_constraints.append(constraint)

        # Don't want shape keys animated as I'm using armature custom props to drive them
        # export_fbx_bin.py will skip over absolute shape keys so use that to disable them
        # TODO this should be configurable
        no_shape_keys = True
        if no_shape_keys:
            for mesh in bpy.data.meshes:
                if mesh.shape_keys and mesh.shape_keys.use_relative:
                    self.saved_meshes_with_relative_shape_keys.append(mesh)
                    mesh.shape_keys.use_relative = False

        # Finally export
        for export_group in export_groups:
            path_fields['action'] = export_group.action.name
            path_fields['suffix'] = export_group.suffix
            filepath = get_export_path(job.animation_export_path, path_fields)
            filename = bpy.path.basename(filepath)
            if filepath in self.exported_files:
                log(f"Skipping {filename} as it would overwrite a file that was just exported")
                continue

            rig.select_set(True)
            context.view_layer.objects.active = rig
            rig.data.pose_position = 'POSE'
            clear_pose(rig)

            rig.animation_data.action = export_group.action
            context.scene.frame_preview_start = export_group.action.frame_range[0]
            context.scene.frame_preview_end = export_group.action.frame_range[1]
            context.scene.use_preview_range = True
            context.scene.frame_current = export_group.action.frame_range[0]
            bpy.context.evaluated_depsgraph_get().update()

            markers = export_group.action.pose_markers
            if markers and job.export_markers:
                # Export action markers as a comma separated list
                csv_filepath = get_export_path(job.markers_export_path, path_fields)
                csv_filename = bpy.path.basename(csv_filepath)
                csv_separator = ','
                fps = float(context.scene.render.fps)
                if csv_filepath not in self.exported_files:
                    log(f"Writing markers to {csv_filename}")
                    with open(csv_filepath, 'w') as fout:
                        field_headers = ["Name", "Frame", "Time"]
                        print(csv_separator.join(field_headers), file=fout)
                        for marker in markers:
                            fields = [marker.name, marker.frame, marker.frame / fps]
                            print(csv_separator.join(str(field) for field in fields), file=fout)
                else:
                    log(f"Skipping {csv_filename} as it would overwrite a file that was " \
                        "just exported")

            if is_object_arp_humanoid(rig):
                log(f"Exporting {filename} via Auto-Rig export")
                result = export_autorig(context, filepath, [export_group.action])
            elif is_object_arp(rig):
                log(f"Exporting {filename} via Auto-Rig export (universal)")
                result = export_autorig_universal(context, filepath, [export_group.action])
            else:
                log(f"Exporting {filename}")
                result = export_fbx(context, filepath, [export_group.action])

            if result == {'FINISHED'}:
                self.exported_files.append(filepath)
            else:
                log("Failed to export!")

    def execute(self, context):
        job = context.scene.gret.export_jobs[self.index]
        rig = job.rig
        assert job.what == 'ANIMATION'

        if not rig or rig.type != 'ARMATURE':
            self.report({'ERROR'}, "No armature selected.")
            return {'CANCELLED'}
        if not rig.visible_get():
            self.report({'ERROR'}, "Currently the rig must be visible to export.")
            return {'CANCELLED'}
        context.view_layer.objects.active = rig

        # Check addon availability and export path
        try:
            fail_if_invalid_export_path(job.animation_export_path, ['action', 'rigfile', 'rig'])
            if job.export_markers:
                fail_if_invalid_export_path(job.markers_export_path, ['action', 'rigfile', 'rig'])
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        saved_selection = save_selection(all_objects=True)
        saved_pose_position = rig.data.pose_position
        saved_action = rig.animation_data.action
        saved_use_global_undo = context.preferences.edit.use_global_undo
        saved_scene_object_names = [o.name for o in context.scene.objects]
        context.preferences.edit.use_global_undo = False
        self.exported_files = []
        self.saved_unmuted_constraints = []
        self.saved_meshes_with_relative_shape_keys = []
        self.new_fcurves = []  # List of (action, fcurve)
        logger.start_logging()
        log(f"Beginning animation export job '{job.name}'")

        try:
            start_time = time.time()
            self._execute(context, job, rig)
            # Finished without errors
            elapsed = time.time() - start_time
            self.report({'INFO'}, get_nice_export_report(self.exported_files, elapsed))
            beep(pitch=1)
        finally:
            # ARP has started leaving behind objects and it breaks subsequent exports
            for obj in context.scene.objects[:]:
                if obj.name not in saved_scene_object_names:
                    log(f"Removing object '{obj.name}' that was left behind")
                    bpy.data.objects.remove(obj, do_unlink=True)
            # Clean up
            for mesh in self.saved_meshes_with_relative_shape_keys:
                mesh.shape_keys.use_relative = True
            for modifier in self.saved_unmuted_constraints:
                modifier.mute = False
            for action, fcurve in self.new_fcurves:
                action.fcurves.remove(fcurve)
            del self.saved_meshes_with_relative_shape_keys
            del self.saved_unmuted_constraints
            del self.new_fcurves
            rig.data.pose_position = saved_pose_position
            rig.animation_data.action = saved_action
            context.preferences.edit.use_global_undo = saved_use_global_undo
            load_selection(saved_selection)
            logger.end_logging()

        log("Job complete")
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

classes = (
    GRET_OT_animation_export,
    GRET_OT_rig_export,
)

def register(settings):
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
