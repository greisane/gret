from fnmatch import fnmatch
import bpy
from .helpers import (
    get_children_recursive,
    intercept,
    load_selection,
    log,
    logger,
    save_selection,
    select_only,
)

class ConstantCurve:
    """Mimics FCurve and always returns the same value on evaluation."""
    def __init__(self, value=0.0):
        self.value = value
    def evaluate(self, frame_index):
        return self.value

def get_nice_export_report(files, elapsed):
    if len(files) > 5:
        return f"{len(files)} files exported in {elapsed:.2f}s."
    if files:
        filenames = [bpy.path.basename(filepath) for filepath in files]
        return f"Exported {', '.join(filenames)} in {elapsed:.2f}s."
    return "Nothing exported."

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
def export_autorig(context, filepath, actions):
    scn = context.scene
    rig = context.active_object
    ik_bones_not_found = [s for s in ik_bone_names if
        s not in rig.pose.bones or 'custom_bone' not in rig.pose.bones[s]]
    if not ik_bones_not_found:
        # All IK bones accounted for
        add_ik_bones = False
    elif len(ik_bones_not_found) == len(ik_bone_names):
        # No IK bones present, let ARP create them
        add_ik_bones = True
    else:
        # Only some IK bones found. Probably a mistake
        raise Exception("Some IK bones are missing or not marked for export: "
            + ", ".join(ik_bones_not_found))

    # Configure Auto-Rig and then finally export
    scn.arp_engine_type = 'unreal'
    scn.arp_export_rig_type = 'humanoid'
    scn.arp_ge_sel_only = True

    # Rig Definition
    scn.arp_keep_bend_bones = False
    scn.arp_push_bend = False
    scn.arp_full_facial = True
    scn.arp_export_twist = True
    scn.arp_export_noparent = False

    # Units
    scn.arp_units_x100 = True

    # Unreal Options
    scn.arp_ue_root_motion = True
    scn.arp_rename_for_ue = True
    scn.arp_ue_ik = add_ik_bones
    scn.arp_mannequin_axes = True

    # Animation
    if not actions:
        scn.arp_bake_actions = False
    else:
        scn.arp_bake_actions = True
        scn.arp_export_name_actions = True
        scn.arp_export_name_string = ','.join(action.name for action in actions)
        scn.arp_simplify_fac = 0.0

    # Misc
    scn.arp_global_scale = 1.0
    scn.arp_mesh_smooth_type = 'EDGE'
    scn.arp_use_tspace = False
    scn.arp_fix_fbx_rot = True
    scn.arp_fix_fbx_matrix = True
    scn.arp_init_fbx_rot = False
    scn.arp_bone_axis_primary_export = 'Y'
    scn.arp_bone_axis_secondary_export = 'X'
    scn.arp_export_rig_name = 'root'

    return bpy.ops.id.arp_export_fbx_panel(filepath=filepath)

@intercept(error_result={'CANCELLED'})
def export_autorig_universal(context, filepath, actions):
    scn = context.scene
    rig = context.active_object

    # Configure Auto-Rig and then finally export
    scn.arp_engine_type = 'unreal'
    scn.arp_export_rig_type = 'mped'
    scn.arp_ge_sel_only = True

    # Rig Definition
    scn.arp_keep_bend_bones = False
    scn.arp_push_bend = False
    scn.arp_export_twist = True
    scn.arp_export_noparent = False

    # Units
    scn.arp_units_x100 = True

    # Unreal Options
    scn.arp_ue_root_motion = True

    # Animation
    if not actions:
        scn.arp_bake_actions = False
    else:
        scn.arp_bake_actions = True
        scn.arp_export_name_actions = True
        scn.arp_export_name_string = ','.join(action.name for action in actions)
        scn.arp_simplify_fac = 0.0

    # Misc
    scn.arp_global_scale = 1.0
    scn.arp_mesh_smooth_type = 'EDGE'
    scn.arp_use_tspace = False
    scn.arp_fix_fbx_rot = True
    scn.arp_fix_fbx_matrix = True
    scn.arp_init_fbx_rot = False
    scn.arp_bone_axis_primary_export = 'Y'
    scn.arp_bone_axis_secondary_export = 'X'
    scn.arp_export_rig_name = 'root'

    return bpy.ops.id.arp_export_fbx_panel(filepath=filepath)

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

class MY_OT_export_job_add(bpy.types.Operator):
    #tooltip
    """Add a new export job"""

    bl_idname = 'my_tools.export_job_add'
    bl_label = "Add Export Job"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        scn = context.scene
        job = scn.my_tools.export_jobs.add()
        job_index = len(scn.my_tools.export_jobs) - 1
        job.name = "Job #%d" % (job_index + 1)
        collection = job.collections.add()
        collection.job_index = job_index
        action = job.actions.add()
        action.job_index = job_index
        copy_property = job.copy_properties.add()
        copy_property.job_index = job_index
        remap_material = job.remap_materials.add()
        remap_material.job_index = job_index

        return {'FINISHED'}

def refresh_job_list(context):
    """Call after changing the job list, keeps job indices up to date"""
    for job_idx, job in enumerate(context.scene.my_tools.export_jobs):
        for coll in job.collections:
            coll.job_index = job_idx
        for action in job.actions:
            action.job_index = job_idx
        for copy_property in job.copy_properties:
            copy_property.job_index = job_idx
        for remap_material in job.remap_materials:
            remap_material.job_index = job_idx

class MY_OT_export_job_remove(bpy.types.Operator):
    #tooltip
    """Removes an export job"""

    bl_idname = 'my_tools.export_job_remove'
    bl_label = "Remove Export Job"
    bl_options = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty(options={'HIDDEN'})

    def execute(self, context):
        context.scene.my_tools.export_jobs.remove(self.index)
        refresh_job_list(context)

        return {'FINISHED'}

class MY_OT_export_job_move_up(bpy.types.Operator):
    #tooltip
    """Moves the export job up"""

    bl_idname = 'my_tools.export_job_move_up'
    bl_label = "Move Export Job Up"
    bl_options = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty(options={'HIDDEN'})

    def execute(self, context):
        context.scene.my_tools.export_jobs.move(self.index, self.index - 1)
        refresh_job_list(context)

        return {'FINISHED'}

class MY_OT_export_job_move_down(bpy.types.Operator):
    #tooltip
    """Moves the export job down"""

    bl_idname = 'my_tools.export_job_move_down'
    bl_label = "Move Export Job Down"
    bl_options = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty(options={'HIDDEN'})

    def execute(self, context):
        context.scene.my_tools.export_jobs.move(self.index, self.index + 1)
        refresh_job_list(context)

        return {'FINISHED'}

class MY_OT_export_job_run(bpy.types.Operator):
    #tooltip
    """Execute export job"""

    bl_idname = 'my_tools.export_job_run'
    bl_label = "Execute Export Job"

    index: bpy.props.IntProperty(options={'HIDDEN'})
    debug: bpy.props.BoolProperty(options={'HIDDEN'})
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def _execute(self, context):
        scn = context.scene
        job = scn.my_tools.export_jobs[self.index]

        def should_export(job_coll, what):
            if job_coll is None or what is None:
                return False
            return (job_coll.export_viewport and not what.hide_viewport
                or job_coll.export_render and not what.hide_render)

        if job.what == 'SCENE':
            if not job.selection_only:
                objs = set()
                for job_coll in job.collections:
                    coll = job_coll.collection
                    if not coll and all(not jc.collection for jc in job.collections):
                        # When no collections are set use the scene collection
                        coll = scn.collection
                    if should_export(job_coll, coll):
                        for obj in coll.objects:
                            if obj not in objs and should_export(job_coll, obj):
                                obj.hide_select = False
                                obj.hide_viewport = False
                                obj.hide_render = False
                                objs.add(obj)
                select_only(context, objs)
            elif not context.selected_objects:
                self.report({'ERROR'}, "Nothing to export.")
                return {'CANCELLED'}

            log(f"Beginning scene export job '{job.name}'")

            bpy.ops.export_scene.my_fbx(
                export_path=job.scene_export_path,
                export_collision=job.export_collision,
                keep_transforms=job.keep_transforms,
                material_name_prefix=job.material_name_prefix,
                debug=self.debug,
            )

        elif job.what == 'RIG':
            if not job.rig:
                self.report({'ERROR'}, "No armature selected.")
                return {'CANCELLED'}
            if not job.rig.visible_get():
                self.report({'ERROR'}, "Currently the rig must be visible to export.")
                return {'CANCELLED'}
            if job.to_collection and not job.export_collection:
                self.report({'ERROR'}, "No collection selected to export to.")
                return {'CANCELLED'}
            context.view_layer.objects.active = job.rig
            bpy.ops.object.mode_set(mode='OBJECT')

            log(f"Beginning rig export job '{job.name}'")

            # Find all unique objects that should be considered for export
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

            export_coll = job.export_collection
            if job.to_collection and job.clean_collection:
                # Clean the target collection first
                # Currently not checking whether the rig is in here, it will probably explode
                log(f"Cleaning target collection")
                for obj in export_coll.objects:
                    bpy.data.objects.remove(obj, do_unlink=True)

            bpy.ops.my_tools.rig_export(
                export_path=job.rig_export_path if not job.to_collection else "",
                export_collection=export_coll.name if job.to_collection and export_coll else "",
                merge_basis_shape_keys=job.merge_basis_shape_keys,
                mirror_shape_keys=job.mirror_shape_keys,
                side_vgroup_name=job.side_vgroup_name,
                apply_modifiers=job.apply_modifiers,
                modifier_tags=job.modifier_tags,
                join_meshes=job.join_meshes,
                split_masks=job.split_masks,
                material_name_prefix=job.material_name_prefix,
                debug=self.debug,
            )

        elif job.what == 'ANIMATION':
            if not job.rig:
                self.report({'ERROR'}, "No armature selected.")
                return {'CANCELLED'}
            if not job.rig.visible_get():
                self.report({'ERROR'}, "Currently the rig must be visible to export.")
                return {'CANCELLED'}
            context.view_layer.objects.active = job.rig
            bpy.ops.object.mode_set(mode='OBJECT')

            log(f"Beginning animation export job '{job.name}'")

            action_names = set()
            for job_action in job.actions:
                if job_action:
                    if not job_action.use_pattern:
                        action_names.add(job_action.action)
                    else:
                        action_names.update(action.name for action in bpy.data.actions
                            if not action.library and fnmatch(action.name, job_action.action))

            for cp in job.copy_properties:
                if not cp.source and not cp.destination:
                    # Empty row
                    continue
                for action_name in action_names:
                    action = bpy.data.actions.get(action_name)
                    if not action:
                        continue
                    if action.library:
                        # Never export linked actions
                        continue

                    try:
                        fcurve_src = next(fc for fc in action.fcurves if fc.data_path == cp.source)
                    except StopIteration:
                        try:
                            value = float(cp.source)
                            fcurve_src = ConstantCurve(value)
                        except ValueError:
                            self.report({'ERROR'}, f"Couldn't bake {cp.source} -> {cp.destination} " \
                                f"in '{action_name}', source doesn't exist")
                            return {'CANCELLED'}

                    try:
                        fcurve_dst = next(fc for fc in action.fcurves if fc.data_path == cp.destination)
                        if fcurve_dst:
                            # Currently baking to existing curves is not allowed
                            # Would need to duplicate strips, although ARP already does that
                            log(f"Couldn't bake {cp.source} -> {cp.destination}, " \
                                "destination already exists")
                            self.report({'ERROR'}, f"Couldn't bake {cp.source} -> {cp.destination} " \
                                f"in '{action_name}', destination already exists")
                            return {'CANCELLED'}
                    except StopIteration:
                        fcurve_dst = action.fcurves.new(cp.destination)
                        self.new_fcurves.append((action, fcurve_dst))

                    log(f"Baking {cp.source} -> {cp.destination} in '{action_name}'")
                    for frame_idx in range(0, int(action.frame_range[1]) + 1):
                        val = fcurve_src.evaluate(frame_idx)
                        fcurve_dst.keyframe_points.insert(frame_idx, val)

            bpy.ops.my_tools.animation_export(
                export_path=job.animation_export_path,
                markers_export_path=job.markers_export_path if job.export_markers else "",
                actions=','.join(action_names),
                disable_auto_eyelid=job.disable_auto_eyelid,
                debug=self.debug,
            )

        log("Job complete")

    def execute(self, context):
        saved_selection = save_selection(all_objects=True)
        self.new_fcurves = []  # List of (action, fcurve)
        self.saved_materials = []  # List of (obj, material_idx, material)
        logger.start_logging()

        try:
            self._execute(context)
        finally:
            # Clean up
            for action, fcurve in self.new_fcurves:
                action.fcurves.remove(fcurve)
            for obj, material_idx, material in self.saved_materials:
                obj.data.materials[material_idx] = material
            del self.new_fcurves
            del self.saved_materials
            load_selection(saved_selection)
            logger.end_logging()

        return {'FINISHED'}

class MY_PT_export_jobs(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "My Tools"
    bl_label = "Export Jobs"

    def draw(self, context):
        layout = self.layout
        scn = context.scene

        layout.operator("my_tools.export_job_add", text="Add")

        for job_idx, job in enumerate(scn.my_tools.export_jobs):
            col_job = layout.column(align=True)
            box = col_job.box()
            row = box.row()
            icon = 'DISCLOSURE_TRI_DOWN' if job.show_expanded else 'DISCLOSURE_TRI_RIGHT'
            row.prop(job, 'show_expanded', icon=icon, text="", emboss=False)
            row.prop(job, 'what', text="", expand=True)
            row.prop(job, 'name', text="")
            row = row.row(align=True)
            split = row.split()
            op = split.operator('my_tools.export_job_move_up', icon='TRIA_UP', text="", emboss=False)
            op.index = job_idx
            split.enabled = job_idx > 0
            split = row.split()
            op = split.operator('my_tools.export_job_move_down', icon='TRIA_DOWN', text="", emboss=False)
            op.index = job_idx
            split.enabled = job_idx < len(scn.my_tools.export_jobs) - 1
            op = row.operator('my_tools.export_job_remove', icon='X', text="", emboss=False)
            op.index = job_idx
            box = col_job.box()
            col = box

            if job.show_expanded:
                def add_collection_layout():
                    col = box.column(align=True)
                    for coll in job.collections:
                        row = col.row(align=True)
                        row.prop(coll, 'collection', text="")
                        row.prop(coll, 'export_viewport', icon='RESTRICT_VIEW_OFF', text="")
                        row.prop(coll, 'export_render', icon='RESTRICT_RENDER_OFF', text="")
                    return col

                if job.what == 'SCENE':
                    col.prop(job, 'selection_only')
                    add_collection_layout().enabled = not job.selection_only

                    col = box.column()
                    col.prop(job, 'export_collision')
                    col.prop(job, 'keep_transforms')
                    col.prop(job, 'material_name_prefix', text="M. Prefix")

                    col = box.column(align=True)
                    col.prop(job, 'scene_export_path', text="")

                elif job.what == 'RIG' or job.what == 'MESH':  # 'MESH' for backwards compat
                    box.prop(job, 'rig')
                    add_collection_layout()

                    col = box.column()
                    col.prop(job, 'merge_basis_shape_keys')

                    row = col.row(align=True)
                    row.prop(job, 'mirror_shape_keys')
                    split = row.split(align=True)
                    split.prop(job, 'side_vgroup_name', text="")
                    split.enabled = job.mirror_shape_keys

                    row = col.row(align=True)
                    row.prop(job, 'apply_modifiers')
                    split = row.split(align=True)
                    split.prop(job, 'modifier_tags', text="")
                    split.enabled = job.apply_modifiers

                    col.prop(job, 'join_meshes')
                    # Don't have an use for Split Masks currently and too many options gets confusing
                    # col.prop(job, 'split_masks')

                    col = box.column(align=True)
                    col.label(text="Remap Materials:")
                    for remap_material in job.remap_materials:
                        row = col.row(align=True)
                        row.prop(remap_material, 'source', text="")
                        row.label(text="", icon='FORWARD')
                        row.prop(remap_material, 'destination', text="")
                    col.prop(job, 'material_name_prefix', text="M. Prefix")

                    col = box.column(align=True)
                    col.prop(job, 'to_collection')
                    if job.to_collection:
                        row = col.row(align=True)
                        row.prop(job, 'export_collection', text="")
                        row.prop(job, 'clean_collection', icon='TRASH', text="")
                    else:
                        col.prop(job, 'rig_export_path', text="")

                elif job.what == 'ANIMATION':
                    box.prop(job, 'rig')

                    col = box.column(align=True)
                    for action in job.actions:
                        row = col.row(align=True)
                        if not action.use_pattern:
                            row.prop_search(action, 'action', bpy.data, "actions", text="")
                        else:
                            row.prop(action, 'action', text="")
                        row.prop(action, 'use_pattern', icon='SELECT_SET', text="")

                    col = box.column()
                    col.prop(job, 'disable_auto_eyelid')

                    row = col.row(align=True)
                    row.prop(job, 'export_markers')
                    split = row.split(align=True)
                    split.prop(job, 'markers_export_path', text="")
                    split.enabled = job.export_markers

                    col = box.column(align=True)
                    col.label(text="Bake Properties:")
                    for copy_property in job.copy_properties:
                        row = col.row(align=True)
                        row.prop(copy_property, 'source', text="")
                        row.label(text="", icon='FORWARD')
                        row.prop(copy_property, 'destination', text="")

                    col = box.column(align=True)
                    col.prop(job, 'animation_export_path', text="")

            row = col.row(align=True)
            op = row.operator('my_tools.export_job_run', icon='INDIRECT_ONLY_ON', text="Execute")
            op.index = job_idx
            op.debug = False
            op = row.operator('my_tools.export_job_run', icon='INDIRECT_ONLY_OFF', text="")
            op.index = job_idx
            op.debug = True

classes = (
    MY_OT_export_job_add,
    MY_OT_export_job_move_down,
    MY_OT_export_job_move_up,
    MY_OT_export_job_remove,
    MY_OT_export_job_run,
    MY_PT_export_jobs,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
