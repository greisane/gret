from fnmatch import fnmatch
import bpy

from gret.helpers import (
    get_children_recursive,
    intercept,
    load_selection,
    save_selection,
    select_only,
)
from gret.log import log, logger

class ConstantCurve:
    """Mimics FCurve and always returns the same value on evaluation."""
    def __init__(self, value=0.0):
        self.value = value
    def evaluate(self, frame_index):
        return self.value

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

class GRET_OT_export_job_add(bpy.types.Operator):
    #tooltip
    """Add a new export job"""

    bl_idname = 'gret.export_job_add'
    bl_label = "Add Export Job"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        scn = context.scene
        job = scn.gret.export_jobs.add()
        job_index = len(scn.gret.export_jobs) - 1
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
    for job_idx, job in enumerate(context.scene.gret.export_jobs):
        for coll in job.collections:
            coll.job_index = job_idx
        for action in job.actions:
            action.job_index = job_idx
        for copy_property in job.copy_properties:
            copy_property.job_index = job_idx
        for remap_material in job.remap_materials:
            remap_material.job_index = job_idx

class GRET_OT_export_job_remove(bpy.types.Operator):
    #tooltip
    """Removes an export job"""

    bl_idname = 'gret.export_job_remove'
    bl_label = "Remove Export Job"
    bl_options = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty(options={'HIDDEN'})

    def execute(self, context):
        context.scene.gret.export_jobs.remove(self.index)
        refresh_job_list(context)

        return {'FINISHED'}

class GRET_OT_export_job_move_up(bpy.types.Operator):
    #tooltip
    """Moves the export job up"""

    bl_idname = 'gret.export_job_move_up'
    bl_label = "Move Export Job Up"
    bl_options = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty(options={'HIDDEN'})

    def execute(self, context):
        context.scene.gret.export_jobs.move(self.index, self.index - 1)
        refresh_job_list(context)

        return {'FINISHED'}

class GRET_OT_export_job_move_down(bpy.types.Operator):
    #tooltip
    """Moves the export job down"""

    bl_idname = 'gret.export_job_move_down'
    bl_label = "Move Export Job Down"
    bl_options = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty(options={'HIDDEN'})

    def execute(self, context):
        context.scene.gret.export_jobs.move(self.index, self.index + 1)
        refresh_job_list(context)

        return {'FINISHED'}

class GRET_OT_export_job_run(bpy.types.Operator):
    #tooltip
    """Execute export job"""

    bl_idname = 'gret.export_job_run'
    bl_label = "Execute Export Job"

    index: bpy.props.IntProperty(options={'HIDDEN'})
    debug: bpy.props.BoolProperty(options={'HIDDEN'})
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def _execute(self, context):
        scn = context.scene
        job = scn.gret.export_jobs[self.index]

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

            bpy.ops.gret.rig_export(
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

            bpy.ops.gret.animation_export(
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

class GRET_PT_export_jobs(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Jobs"
    bl_label = "Export Jobs"

    def draw(self, context):
        layout = self.layout
        scn = context.scene

        layout.operator("gret.export_job_add", text="Add")

        for job_idx, job in enumerate(scn.gret.export_jobs):
            col_job = layout.column(align=True)
            box = col_job.box()
            row = box.row()
            icon = 'DISCLOSURE_TRI_DOWN' if job.show_expanded else 'DISCLOSURE_TRI_RIGHT'
            row.prop(job, 'show_expanded', icon=icon, text="", emboss=False)
            row.prop(job, 'what', text="", expand=True)
            row.prop(job, 'name', text="")
            row = row.row(align=True)
            split = row.split()
            op = split.operator('gret.export_job_move_up', icon='TRIA_UP', text="", emboss=False)
            op.index = job_idx
            split.enabled = job_idx > 0
            split = row.split()
            op = split.operator('gret.export_job_move_down', icon='TRIA_DOWN', text="", emboss=False)
            op.index = job_idx
            split.enabled = job_idx < len(scn.gret.export_jobs) - 1
            op = row.operator('gret.export_job_remove', icon='X', text="", emboss=False)
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

                    col.prop(job, 'export_markers')
                    split = col.split(align=True)
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
            op = row.operator('gret.export_job_run', icon='INDIRECT_ONLY_ON', text="Execute")
            op.index = job_idx
            op.debug = False
            op = row.operator('gret.export_job_run', icon='INDIRECT_ONLY_OFF', text="")
            op.index = job_idx
            op.debug = True


def on_collection_updated(self, context):
    scn = context.scene
    job = scn.gret.export_jobs[self.job_index]
    index = job.collections.values().index(self)

    empty = not self.collection

    if empty and index < len(job.collections) - 1:
        # Remove it unless it's the last item
        job.collections.remove(index)
    elif not empty and index == len(job.collections) - 1:
        # Make sure there's always an empty item at the end
        coll = job.collections.add()
        coll.job_index = self.job_index

class GRET_PG_export_collection(bpy.types.PropertyGroup):
    job_index: bpy.props.IntProperty()
    collection: bpy.props.PointerProperty(
        name="Collection",
        description="Collection to include",
        type=bpy.types.Collection,
        update=on_collection_updated,
    )
    export_viewport: bpy.props.BoolProperty(
        name="Export Viewport",
        description="Include collections and objects that are visible in viewport",
        default=False,
    )
    export_render: bpy.props.BoolProperty(
        name="Export Render",
        description="Include collections and objects that are visible in render",
        default=True,
    )

def on_action_updated(self, context):
    scn = context.scene
    job = scn.gret.export_jobs[self.job_index]
    index = job.actions.values().index(self)

    empty = not self.action and not self.use_pattern

    if empty and index < len(job.actions) - 1:
        # Remove it unless it's the last item
        job.actions.remove(index)
    elif not empty and index == len(job.actions) - 1:
        # Make sure there's always an empty item at the end
        action = job.actions.add()
        action.job_index = self.job_index

class GRET_PG_export_action(bpy.types.PropertyGroup):
    job_index: bpy.props.IntProperty()
    action: bpy.props.StringProperty(
        name="Action",
        description="Action or actions to export",
        default="",
        update=on_action_updated,
    )
    use_pattern: bpy.props.BoolProperty(
        name="Use Pattern",
        description="Adds all actions that match a pattern (.?* allowed)",
        default=False,
        update=on_action_updated,
    )

def on_copy_property_updated(self, context):
    scn = context.scene
    job = scn.gret.export_jobs[self.job_index]
    index = job.copy_properties.values().index(self)

    empty = not self.source and not self.destination

    if empty and index < len(job.copy_properties) - 1:
        # Remove it unless it's the last item
        job.copy_properties.remove(index)
    elif not empty and index == len(job.copy_properties) - 1:
        # Make sure there's always an empty item at the end
        copy_property = job.copy_properties.add()
        copy_property.job_index = self.job_index

class GRET_PG_copy_property(bpy.types.PropertyGroup):
    job_index: bpy.props.IntProperty()
    source: bpy.props.StringProperty(
        name="Source",
        description="""Path of the source property to bake.
e.g.: pose.bones["c_eye_target.x"]["eye_target"]""",
        default="",
        update=on_copy_property_updated,
    )
    destination: bpy.props.StringProperty(
        name="Destination",
        description="""Path of the destination property.
e.g.: ["eye_target"]""",
        default="",
        update=on_copy_property_updated,
    )

def on_remap_material_updated(self, context):
    scn = context.scene
    job = scn.gret.export_jobs[self.job_index]
    index = job.remap_materials.values().index(self)

    empty = not self.source and not self.destination

    if empty and index < len(job.remap_materials) - 1:
        # Remove it unless it's the last item
        job.remap_materials.remove(index)
    elif not empty and index == len(job.remap_materials) - 1:
        # Make sure there's always an empty item at the end
        remap_material = job.remap_materials.add()
        remap_material.job_index = self.job_index

class GRET_PG_remap_material(bpy.types.PropertyGroup):
    job_index: bpy.props.IntProperty()
    source: bpy.props.PointerProperty(
        name="Source",
        description="Source material",
        type=bpy.types.Material,
        update=on_remap_material_updated,
    )
    destination: bpy.props.PointerProperty(
        name="Destination",
        description="Destination material",
        type=bpy.types.Material,
        update=on_remap_material_updated,
    )

def on_what_updated(self, context):
    # Ensure collections are valid
    if not self.collections:
        job_index = context.scene.gret.export_jobs.values().index(self)
        collection = self.collections.add()
        collection.job_index = job_index
    if not self.actions:
        job_index = context.scene.gret.export_jobs.values().index(self)
        action = self.actions.add()
        action.job_index = job_index
    if not self.copy_properties:
        job_index = context.scene.gret.export_jobs.values().index(self)
        copy_property = self.copy_properties.add()
        copy_property.job_index = job_index
    if not self.remap_materials:
        job_index = context.scene.gret.export_jobs.values().index(self)
        remap_material = self.remap_materials.add()
        remap_material.job_index = job_index

class GRET_PG_export_job(bpy.types.PropertyGroup):
    show_expanded: bpy.props.BoolProperty(
        name="Show Expanded",
        description="Set export job expanded in the user interface",
        default=True,
    )
    name: bpy.props.StringProperty(
        name="Name",
        description="Export job name",
        default="Job",
    )
    rig: bpy.props.PointerProperty(
        name="Rig",
        description="Armature to operate on",
        type=bpy.types.Object,
        poll=lambda self, obj: obj and obj.type == 'ARMATURE',
    )
    what: bpy.props.EnumProperty(
        items=[
            ('SCENE', "Scene", "Scene objects", 'SCENE_DATA', 0),
            ('RIG', "Rig", "Armature and meshes", 'ARMATURE_DATA', 1),
            ('ANIMATION', "Animation", "Armature animation only", 'ANIM', 2),
        ],
        name="Export Type",
        description="What to export",
        update=on_what_updated,
    )
    export_collection: bpy.props.PointerProperty(
        name="Export Collection",
        description="Collection where to place export products",
        type=bpy.types.Collection,
    )
    selection_only: bpy.props.BoolProperty(
        name="Selection Only",
        description="Exports the current selection",
        default=True,
    )
    collections: bpy.props.CollectionProperty(
        type=GRET_PG_export_collection,
    )
    material_name_prefix: bpy.props.StringProperty(
        name="Material Prefix",
        description="Ensures that exported material names begin with a prefix",
        default="MI_",
    )

    # Scene export options
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
    scene_export_path: bpy.props.StringProperty(
        name="Export Path",
        description="""Export path relative to the current folder.
{file} = Name of this .blend file without extension.
{object} = Name of the object being exported.
{collection} = Name of the first collection the object belongs to""",
        default="//export/S_{object}.fbx",
        subtype='FILE_PATH',
    )

    # Rig export options
    merge_basis_shape_keys: bpy.props.BoolProperty(
        name="Merge Basis Shape Keys",
        description="Blends 'Key' and 'b_' shapekeys into the basis shape",
        default=True,
    )
    mirror_shape_keys: bpy.props.BoolProperty(
        name="Mirror Shape Keys",
        description="""Creates mirrored versions of shape keys that have side suffixes.
Requires a mirror modifier""",
        default=True,
    )
    side_vgroup_name: bpy.props.StringProperty(
        name="Side Vertex Group Name",
        description="Name of the vertex groups that will be created on mirroring shape keys",
        default="_side.l",
    )
    apply_modifiers: bpy.props.BoolProperty(
        name="Apply Modifiers",
        description="Allows exporting of shape keys even if the meshes have generative modifiers",
        default=True,
    )
    modifier_tags: bpy.props.StringProperty(
        name="Modifier Tags",
        description="""Tagged modifiers are only applied if the tag is found in this list.
Separate tags with commas. Tag modifiers with 'g:tag'""",
        default="",
    )
    join_meshes: bpy.props.BoolProperty(
        name="Join Meshes",
        description="Joins meshes before exporting",
        default=True,
    )
    split_masks: bpy.props.BoolProperty(
        name="Split Masks",
        description="""Splits mask modifiers into extra meshes that are exported separately.
Normals are preserved""",
        default=False,
    )
    remap_materials: bpy.props.CollectionProperty(
        type=GRET_PG_remap_material,
    )
    to_collection: bpy.props.BoolProperty(
        name="To Collection",
        description="""Produced meshes are put in a collection instead of being exported.
Tag modifiers with '!keep' to preserve them in the new meshes""",
        default=False,
    )
    clean_collection: bpy.props.BoolProperty(
        name="Clean Collection",
        description="Clean the target collection",
        default=False,
    )
    rig_export_path: bpy.props.StringProperty(
        name="Export Path",
        description="""Export path relative to the current folder.
{file} = Name of this .blend file without extension.
{rigfile} = Name of the .blend file the rig is linked from, without extension.
{rig} = Name of the rig being exported""",
        default="//export/SK_{rigfile}.fbx",
        subtype='FILE_PATH',
    )

    # Animation export options
    actions: bpy.props.CollectionProperty(
        type=GRET_PG_export_action,
    )
    disable_auto_eyelid: bpy.props.BoolProperty(
        name="Disable Auto-Eyelid",
        description="Disables Auto-Eyelid (ARP only)",
        default=True,
    )
    export_markers: bpy.props.BoolProperty(
        name="Export Markers",
        description="Export markers names and frame times as a list of comma-separated values",
        default=False,
    )
    markers_export_path: bpy.props.StringProperty(
        name="Markers Export Path",
        description="""Export path for markers relative to the current folder.
{file} = Name of this .blend file without extension.
{rigfile} = Name of the .blend file the rig is linked from, without extension.
{rig} = Name of the rig being exported.
{action} = Name of the action being exported""",
        default="//export/DT_{rigfile}_{action}.csv",
        subtype='FILE_PATH',
    )
    copy_properties: bpy.props.CollectionProperty(
        type=GRET_PG_copy_property,
    )
    animation_export_path: bpy.props.StringProperty(
        name="Export Path",
        description="""Export path relative to the current folder.
{file} = Name of this .blend file without extension.
{rigfile} = Name of the .blend file the rig is linked from, without extension.
{rig} = Name of the rig being exported.
{action} = Name of the action being exported, if exporting animation""",
        default="//export/A_{rigfile}_{action}.fbx",
        subtype='FILE_PATH',
    )

classes = (
    GRET_OT_export_job_add,
    GRET_OT_export_job_move_down,
    GRET_OT_export_job_move_up,
    GRET_OT_export_job_remove,
    GRET_OT_export_job_run,
    GRET_PG_copy_property,
    GRET_PG_export_action,
    GRET_PG_export_collection,
    GRET_PG_remap_material,
    GRET_PG_export_job,
    GRET_PT_export_jobs,
)

def register(settings):
    for cls in classes:
        bpy.utils.register_class(cls)

    settings.add_property('export_jobs', bpy.props.CollectionProperty(
        type=GRET_PG_export_job,
    ))

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
