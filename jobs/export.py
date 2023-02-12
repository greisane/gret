import bpy
import re
import shlex

from ..log import log, logger
from ..helpers import gret_operator_exists
from ..rig.helpers import is_object_arp
from .scene_export import scene_export
from .rig_export import rig_export
from .anim_export import anim_export

class GRET_OT_export_job_preset(bpy.types.Operator):
    #tooltip
    """Add preset jobs and automatically create collections"""

    bl_idname = 'gret.export_job_preset'
    bl_label = "Add Job Preset"
    bl_options = {'INTERNAL', 'UNDO'}

    preset: bpy.props.EnumProperty(
        items=[
            ('BAKE', "Bake Jobs", "Add bake export jobs and collections"),
            ('CHARACTER', "Character Jobs", "Add character export jobs and collections"),
        ],
        name="Preset",
        description="Job Presets",
    )

    def execute(self, context):
        def ensure_collection(collection_name, color_tag='NONE'):
            collection = bpy.data.collections.get(collection_name)
            if not collection:
                collection = bpy.data.collections.new(collection_name)
                context.scene.collection.children.link(collection)
            collection.color_tag = color_tag
            return collection

        if self.preset == 'BAKE':
            job = add_job(context, name="low", collections=[ensure_collection("low", 'COLOR_04')])
            job.what = 'SCENE'
            job.merge_basis_shape_keys = False
            job.selection_only = False
            job.export_collision = False
            job.export_sockets = False
            job.keep_transforms = True
            job.material_name_prefix = ""
            job.scene_export_path = "//{file}_low.fbx"

            job = add_job(context,
                name="high",
                collections=[ensure_collection("high", 'COLOR_02')],
                remap_materials=[(None, bpy.data.materials.new("high"))],
            )
            job.what = 'SCENE'
            job.merge_basis_shape_keys = False
            job.selection_only = False
            job.export_collision = False
            job.export_sockets = False
            job.keep_transforms = True
            job.ensure_uv_layers = False
            job.material_name_prefix = ""
            job.scene_export_path = "//{file}_high.fbx"

        elif self.preset == 'CHARACTER':
            rig = next((o for o in bpy.data.objects if o.type == 'ARMATURE'), None)

            job = add_job(context, name="preview", collections=[ensure_collection("body")])
            job.what = 'RIG'
            job.rig = rig
            job.modifier_tags = "preview"
            job.material_name_prefix = ""
            job.to_collection = True
            job.clean_collection = True
            job.export_collection = ensure_collection("preview")

            job = add_job(context, name="rig", collections=[ensure_collection("body")])
            job.what = 'RIG'
            job.rig = rig
            job.encode_shape_keys = True
            job.rig_export_path = "//export/SK_{rigfile}.fbx"

        return {'FINISHED'}

class GRET_OT_export_job_add(bpy.types.Operator):
    #tooltip
    """Add a new export job"""

    bl_idname = 'gret.export_job_add'
    bl_label = "Add Export Job"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        add_job(context)

        return {'FINISHED'}

def add_job(context, name="", collections=[], remap_materials=[]):
    jobs = context.scene.gret.export_jobs
    job = jobs.add()
    job_index = len(jobs) - 1
    job.name = name or ("Job #%d" % (job_index + 1))
    if collections:
        for collection in collections:
            job_cl = job.collections.add()
            job_cl.job_index = job_index
            job_cl.collection = collection
    else:
        job_cl = job.collections.add()
        job_cl.job_index = job_index
    action = job.actions.add()
    action.job_index = job_index
    copy_property = job.copy_properties.add()
    copy_property.job_index = job_index
    if remap_materials:
        for source, destination in remap_materials:
            remap_material = job.remap_materials.add()
            remap_material.job_index = job_index
            remap_material.source = source
            remap_material.destination = destination
    else:
        remap_material = job.remap_materials.add()
        remap_material.job_index = job_index
    return job

def refresh_job_list(context):
    """Call after changing the job list, keeps job indices up to date"""
    for job_index, job in enumerate(context.scene.gret.export_jobs):
        for job_cl in job.collections:
            job_cl.job_index = job_index
        for action in job.actions:
            action.job_index = job_index
        for copy_property in job.copy_properties:
            copy_property.job_index = job_index
        for remap_material in job.remap_materials:
            remap_material.job_index = job_index

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

class GRET_OT_search_modifier_tags(bpy.types.Operator):
    #tooltip
    """Select job objects that use these modifier tags"""

    bl_idname = 'gret.search_modifier_tags'
    bl_label = "Search Modifier Tags"
    bl_options = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty(options={'HIDDEN'})

    def execute(self, context):
        try:
            job = context.scene.gret.export_jobs[self.index]
        except IndexError:
            self.report({'ERROR'}, "Invalid export job index.")
            return {'CANCELLED'}

        for obj in context.selected_objects:
            obj.select_set(False)

        tags = shlex.split(job.modifier_tags)
        export_objs, _ = job.get_export_objects(context)
        obj_names = []
        for obj in export_objs:
            for modifier in obj.modifiers:
                for tag in tags:
                    if tag.startswith("!") and len(tag) > 1:
                        tag = tag[1:]
                    if tag in modifier.name:
                        obj_names.append(obj.name)
                        try:
                            obj.select_set(True)
                        except RuntimeError:
                            pass
                        break

        if obj_names:
            s = "objects match" if len(obj_names) > 1 else "object matches"
            self.report({'INFO'}, f"{len(obj_names)} {s}: {', '.join(obj_names)}.")
            context.view_layer.objects.active = context.selected_objects[0]
        else:
            self.report({'INFO'}, f"No objects match these tags.")

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

class GRET_OT_export(bpy.types.Operator):
    #tooltip
    """Execute the export job"""

    bl_idname = 'gret.export'
    bl_label = "Export"
    bl_context = 'objectmode'
    bl_options = {'REGISTER'}

    index: bpy.props.IntProperty(
        name="Index",
        description="Index of the job to execute",
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        if not context.scene.gret.export_jobs:
            self.report({'ERROR'}, "No export jobs created!")
            return {'CANCELLED'}
        try:
            job = context.scene.gret.export_jobs[self.index]
        except IndexError:
            self.report({'ERROR'}, "Invalid export job index.")
            return {'CANCELLED'}

        if job.what == 'SCENE':
            scene_export(self, context, job)
        elif job.what == 'RIG':
            rig_export(self, context, job)
        elif job.what == 'ANIMATION':
            anim_export(self, context, job)

        return {'FINISHED'}


class GRET_OT_export_by_name(bpy.types.Operator):
    #tooltip
    """Execute the export job"""

    bl_idname = 'gret.export_by_name'
    bl_label = "Export (by name)"
    bl_context = 'objectmode'
    bl_options = {'REGISTER'}

    name: bpy.props.IntProperty(
        name="Job Name",
        description="Name of the job to execute",
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        if not context.scene.gret.export_jobs:
            self.report({'ERROR'}, "No export jobs created!")
            return {'CANCELLED'}
        job = context.scene.gret.export_jobs.get(self.name)
        if not job:
            self.report({'ERROR'}, f"No export jobs named '{self.name}'")
            return {'CANCELLED'}

        if job.what == 'SCENE':
            scene_export(self, context, job)
        elif job.what == 'RIG':
            rig_export(self, context, job)
        elif job.what == 'ANIMATION':
            anim_export(self, context, job)

        return {'FINISHED'}

def draw_job(layout, jobs, job_index):
    job = jobs[job_index]

    col_job = layout.column(align=True)
    box = col_job.box()
    row = box.row()
    icon = 'DISCLOSURE_TRI_DOWN' if job.show_expanded else 'DISCLOSURE_TRI_RIGHT'
    row.prop(job, 'show_expanded', icon=icon, text="", emboss=False)
    row.prop(job, 'what', text="", expand=True)
    if job.show_expanded:
        row.prop(job, 'name', text="")  # Editable name while expanded
    else:
        row.label(text=job.name)
    row2 = row.row(align=True)
    row2.scale_x = 0.75
    sub = row2.column(align=True)
    op = sub.operator('gret.export_job_move_up', icon='TRIA_UP', text="", emboss=False)
    op.index = job_index
    sub.enabled = job_index > 0
    sub = row2.column(align=True)
    op = sub.operator('gret.export_job_move_down', icon='TRIA_DOWN', text="", emboss=False)
    op.index = job_index
    sub.enabled = job_index < len(jobs) - 1
    row2.separator()
    op = row2.operator('gret.export_job_remove', icon='X', text="", emboss=False)
    op.index = job_index
    op = row.operator('gret.export', icon='PLAY', text="")
    op.index = job_index

    if not job.show_expanded:
        return

    def add_collection_layout():
        col = box.column(align=True)
        for job_cl in job.collections:
            row = col.row(align=True)
            row.prop(job_cl, 'collection', text="")
            sub = row.row(align=True)
            sub.prop(job_cl, 'subdivision_levels', text="")
            sub.ui_units_x = 1.8
            row.prop(job_cl, 'export_viewport', icon='RESTRICT_VIEW_OFF', text="")
            row.prop(job_cl, 'export_render', icon='RESTRICT_RENDER_OFF', text="")
        return col

    box = col_job.box()
    col = box

    if job.what == 'SCENE':
        col.prop(job, 'selection_only')
        add_collection_layout().enabled = not job.selection_only

        col = box.column()
        row = col.row(align=True)
        row.prop(job, 'use_modifier_tags')
        sub = row.row(align=True)
        sub.prop(job, 'modifier_tags', text="")
        op = sub.operator('gret.search_modifier_tags', icon='VIEWZOOM', text="")
        op.index = job_index
        sub.enabled = job.use_modifier_tags

        row = col.row(align=True)
        row.prop(job, 'merge_basis_shape_keys')
        sub = row.row(align=True)
        sub.prop(job, 'basis_shape_key_pattern', text="")
        sub.enabled = job.merge_basis_shape_keys

        col.prop(job, 'encode_shape_keys')

        col.prop(job, 'export_collision')
        col.prop(job, 'export_sockets')
        col.prop(job, 'keep_transforms')
        col.prop(job, 'ensure_uv_layers')

        row = col.row(align=True)
        row.prop(job, 'ensure_vertex_color')
        sub = row.row(align=True)
        sub.prop(job, 'default_vertex_color', text="")
        sub.enabled = job.ensure_vertex_color
        # if gret_operator_exists("gret.vertex_color_mapping_add"):
            # col.prop(job, 'invert_vertex_color_mappings')

        row = col.row(align=True)
        row.prop(job, 'use_postprocess_script', text="Post Process")
        sub = row.row(align=True)
        sub.prop(job, 'postprocess_script', text="")
        sub.enabled = job.use_postprocess_script

        col = box.column(align=True)
        col.label(text="Remap Materials:")
        for remap_material in job.remap_materials:
            row = col.row(align=True)
            row.prop(remap_material, 'source', text="")
            row.label(text="", icon='FORWARD')
            row.prop(remap_material, 'destination', text="")
        col.prop(job, 'material_name_prefix', text="M. Prefix")

        col = box.column(align=True)
        col.prop(job, 'scene_export_path', text="")

    elif job.what == 'RIG':
        box.prop(job, 'rig')
        add_collection_layout()

        col = box.column()

        row = col.row(align=True)
        row.prop(job, 'weld_mode', text="Weld")
        sub = row.row(align=True)
        sub.prop(job, 'weld_distance', text="")
        sub.enabled = job.weld_mode != 'NEVER'

        row = col.row(align=True)
        row.prop(job, 'use_modifier_tags')
        sub = row.row(align=True)
        sub.prop(job, 'modifier_tags', text="")
        op = sub.operator('gret.search_modifier_tags', icon='VIEWZOOM', text="")
        op.index = job_index
        sub.enabled = job.use_modifier_tags

        row = col.row(align=True)
        row.prop(job, 'merge_basis_shape_keys')
        sub = row.row(align=True)
        sub.prop(job, 'basis_shape_key_pattern', text="")
        sub.enabled = job.merge_basis_shape_keys

        col.prop(job, 'encode_shape_keys')

        row = col.row(align=True)
        row.prop(job, 'mirror_shape_keys')
        # sub = row.row(align=True)
        # sub.prop(job, 'side_vgroup_name', text="")
        # sub.enabled = job.mirror_shape_keys

        row = col.row(align=True)
        row.prop(job, 'ensure_vertex_color')
        sub = row.row(align=True)
        sub.prop(job, 'default_vertex_color', text="")
        sub.enabled = job.ensure_vertex_color
        # if gret_operator_exists("gret.vertex_color_mapping_add"):
        #     col.prop(job, 'invert_vertex_color_mappings')

        row = col.row(align=True)
        row.prop(job, 'subdivide_faces')
        sub = row.row(align=True)
        sub.prop(job, 'subdivide_face_map_names', text="")
        sub.enabled = job.subdivide_faces

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
            col.prop(job, 'minimize_bones')

            row = col.row(align=True)
            row.prop(job, 'remove_bones')
            sub = row.row(align=True)
            sub.prop(job, 'remove_bone_names', text="")
            sub.enabled = job.remove_bones

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
        if is_object_arp(job.rig):
            col.prop(job, 'disable_auto_eyelid')
            col.prop(job, 'disable_twist_bones')

        col.prop(job, 'export_markers')
        sub = col.row(align=True)
        sub.prop(job, 'markers_export_path', text="")
        sub.enabled = job.export_markers

        col = box.column(align=True)
        col.label(text="Bake Properties:")
        for copy_property in job.copy_properties:
            row = col.row(align=True)
            row.prop(copy_property, 'source', text="")
            row.label(text="", icon='FORWARD')
            row.prop(copy_property, 'destination', text="")

        col = box.column(align=True)
        col.prop(job, 'animation_export_path', text="")

    op = col.operator('gret.export', icon='PLAY', text="Execute")
    op.index = job_index

class GRET_PT_export_jobs(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Jobs"
    bl_label = "Export Jobs"

    def draw(self, context):
        layout = self.layout

        row = layout.row(align=True)
        row.operator('gret.export_job_add', text="Add")
        row.operator_menu_enum('gret.export_job_preset', 'preset', text="", icon='DOWNARROW_HLT')

        jobs = context.scene.gret.export_jobs
        for job_index, job in enumerate(jobs):
            draw_job(layout, jobs, job_index)

def on_collection_updated(self, context):
    jobs = context.scene.gret.export_jobs
    job = jobs[self.job_index]
    index = job.collections.values().index(self)

    is_empty = not self.collection
    if is_empty and index < len(job.collections) - 1:
        # Remove it unless it's the last item
        job.collections.remove(index)
    elif not is_empty and index == len(job.collections) - 1:
        # Make sure there's always an empty item at the end
        new_item = job.collections.add()
        new_item.job_index = self.job_index

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
    subdivision_levels: bpy.props.IntProperty(
        name="Subdivision Levels",
        description="""Subdivide collection if positive, simplify if negative.
Performed after merging so that welded seams are smoothed correctly""",
        default=0,
        soft_min=-2,
        soft_max=2,
    )

    def get_collection(self, context):
        job = context.scene.gret.export_jobs[self.job_index]
        if all(not job_cl.collection for job_cl in job.collections):
            # When no collections are set for this job, use the scene collection
            return context.scene.collection
        else:
            return self.collection

    def get_child_collections(self, context, include_self=True):
        collection = self.get_collection(context)
        if not collection:
            return []
        elif include_self:
            return [collection] + collection.children_recursive
        else:
            return collection.children_recursive

def on_action_updated(self, context):
    jobs = context.scene.gret.export_jobs
    job = jobs[self.job_index]
    index = job.actions.values().index(self)

    is_empty = not self.action and not self.use_pattern
    if is_empty and index < len(job.actions) - 1:
        # Remove it unless it's the last item
        job.actions.remove(index)
    elif not is_empty and index == len(job.actions) - 1:
        # Make sure there's always an empty item at the end
        new_item = job.actions.add()
        new_item.job_index = self.job_index

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
    jobs = context.scene.gret.export_jobs
    job = jobs[self.job_index]
    index = job.copy_properties.values().index(self)

    is_empty = not self.source and not self.destination
    if is_empty and index < len(job.copy_properties) - 1:
        # Remove it unless it's the last item
        job.copy_properties.remove(index)
    elif not is_empty and index == len(job.copy_properties) - 1:
        # Make sure there's always an empty item at the end
        new_item = job.copy_properties.add()
        new_item.job_index = self.job_index

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
    jobs = context.scene.gret.export_jobs
    job = jobs[self.job_index]
    index = job.remap_materials.values().index(self)

    is_empty = not self.source and not self.destination
    if is_empty and index < len(job.remap_materials) - 1:
        # Remove it unless it's the last item
        job.remap_materials.remove(index)
    elif not is_empty and index == len(job.remap_materials) - 1:
        # Make sure there's always an empty item at the end
        new_item = job.remap_materials.add()
        new_item.job_index = self.job_index

class GRET_PG_remap_material(bpy.types.PropertyGroup):
    job_index: bpy.props.IntProperty()
    source: bpy.props.PointerProperty(
        name="Source",
        description="Source material. If left empty, adds a material when there are none",
        type=bpy.types.Material,
        update=on_remap_material_updated,
    )
    destination: bpy.props.PointerProperty(
        name="Destination",
        description="Destination material. Faces with no material will be deleted from the mesh",
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
        options=set(),
    )
    name: bpy.props.StringProperty(
        name="Name",
        description="Export job name",
        default="Job",
        options=set(),
    )
    rig: bpy.props.PointerProperty(
        name="Rig",
        description="Armature to operate on",
        type=bpy.types.Object,
        poll=lambda self, obj: obj and obj.type == 'ARMATURE',
        options=set(),
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
        options=set(),
    )
    export_collection: bpy.props.PointerProperty(
        name="Export Collection",
        description="Collection where to place export products",
        type=bpy.types.Collection,
        options=set(),
    )
    selection_only: bpy.props.BoolProperty(
        name="Selection Only",
        description="Exports the current selection",
        default=True,
        options=set(),
    )
    collections: bpy.props.CollectionProperty(
        type=GRET_PG_export_collection,
        options=set(),
    )
    material_name_prefix: bpy.props.StringProperty(
        name="Material Prefix",
        description="Ensures that exported material names begin with a prefix",
        default="MI_",
        options=set(),
    )

    # Shared scene and export rig options
    invert_vertex_color_mappings: bpy.props.BoolProperty(
        name="Invert Vertex Color Mappings",
        description="""Invert vertex colors generated from mappings.
Meshes with existing vertex color layers won't be affected""",
        default=False,
        options=set(),
    )
    ensure_vertex_color: bpy.props.BoolProperty(
        name="Ensure Vertex Color",
        description="Create a vertex color layer for meshes that have none",
        default=True,
        options=set(),
    )
    default_vertex_color: bpy.props.FloatVectorProperty(
        name="Default Vertex Color",
        description="Default vertex color values",
        size=4,
        default=(1.0, 1.0, 1.0, 1.0),
        min=0.0,
        max=1.0,
        subtype='COLOR',
        options=set(),
    )
    use_modifier_tags: bpy.props.BoolProperty(
        name="Use Modifier Tags",
        description="Allows enabling or disabling modifiers based on their name",
        default=False,
        options=set(),
    )
    modifier_tags: bpy.props.StringProperty(
        name="Modifier Tags",
        description="Substrings to search for in the modifier names. Separate with spaces",
        default="",
        options=set(),
    )
    merge_basis_shape_keys: bpy.props.BoolProperty(
        name="Merge Shape Keys",
        description="Merge shape keys by name, into the basis or another shape key",
        default=True,
        options=set(),
    )
    basis_shape_key_pattern: bpy.props.StringProperty(
        name="Shape Key Merge Patterns",
        description="""Patterns for shape keys to be merged into basis, wildcards are allowed.
Can also specify the target shape key with an arrow, e.g. 'SmileTweak->Smile'""",
        default='"Key [0-9]*" b_*',
        options=set(),
    )
    encode_shape_keys: bpy.props.BoolProperty(
        name="Encode Shape Keys",
        description="""Shape keys suffixed '_UV' are encoded in UV channels instead of being exported.
UVn+1: deltaXY, UVn+2: deltaZnormalX, UVn+3: normalYZ.
All values are remapped to a [0..1] UV range""",
        default=False,
        options=set(),
    )
    use_postprocess_script: bpy.props.BoolProperty(
        name="Use Post Process Script",
        description="Run script on each processed mesh, after modifiers are applied",
        default=False,
        options=set(),
    )
    postprocess_script: bpy.props.PointerProperty(
        name="Post Process Script",
        description="Script to run. `obj` is the object to modify and `ctx` is its context",
        type=bpy.types.Text,
        options=set(),
    )
    remap_materials: bpy.props.CollectionProperty(
        type=GRET_PG_remap_material,
        options=set(),
    )

    # Scene export options
    export_collision: bpy.props.BoolProperty(
        name="Export Collision",
        description="Exports collision objects that follow the UE4 naming pattern",
        default=True,
        options=set(),
    )
    export_sockets: bpy.props.BoolProperty(
        name="Export Sockets",
        description="Export any Empty parented to an object as a UE4 static mesh socket",
        default=True,
        options=set(),
    )
    keep_transforms: bpy.props.BoolProperty(
        name="Keep Transforms",
        description="Keep the position and rotation of objects relative to world center",
        default=False,
        options=set(),
    )
    ensure_uv_layers: bpy.props.BoolProperty(
        name="Ensure UV Layer",
        description="Create an empty UV layer for objects that have none",
        default=True,
        options=set(),
    )
    scene_export_path: bpy.props.StringProperty(
        name="Export Path",
        description="""Export path relative to the current folder.
{file} = Name of this .blend file without extension.
{object} = Name of the object being exported.
{topobject} = Name of the top-most parent of the object being exported.
{collection} = Name of the collection the object belongs to""",
        default="//export/S_{object}.fbx",
        subtype='FILE_PATH',
        options=set(),
    )

    # Rig export options
    mirror_shape_keys: bpy.props.BoolProperty(
        name="Mirror Shape Keys",
        description="""Creates mirrored versions of shape keys that have side suffixes.
Requires a mirror modifier""",
        default=True,
        options=set(),
    )
    side_vgroup_name: bpy.props.StringProperty(
        name="Side Vertex Group Name",
        description="Name of the vertex group that will be created on mirroring shape keys",
        default="_side.l",
        options=set(),
    )
    weld_mode: bpy.props.EnumProperty(
        items=[
            ('NEVER', "Never", "No welding step"),
            ('ALWAYS', "Always", "All vertices are considered for welding"),
            ('BOUNDARY', "Boundary", "Boundary vertices are considered for welding"),
            ('TAGGED', "Freestyle", "Only edges marked freestyle are considered for welding"),
        ],
        name="Weld",
        description="Allows welding merged parts to eliminate shading discontinuities",
        default='NEVER',
        options=set(),
    )
    weld_distance: bpy.props.FloatProperty(
        name="Weld Distance",
        description="Limit below which to merge vertices",
        subtype='DISTANCE',
        default=1e-3,
        min=0.0,
        options=set(),
    )
    subdivide_faces: bpy.props.BoolProperty(
        name="Partial Subdivide",
        description="Partially subdivide meshes using face maps",
        default=False,
        options=set(),
    )
    subdivide_face_map_names: bpy.props.StringProperty(
        name="Subdivide Face Maps",
        description="Names of face maps to subdivide. Separate with spaces",
        default="",
        options=set(),
    )
    minimize_bones: bpy.props.BoolProperty(
        name="Minimize Bone Hierarchy",
        description="Remove bones not affecting deformation of the meshes being exported",
        default=False,
        options=set(),
    )
    remove_bones: bpy.props.BoolProperty(
        name="Remove Bones",
        description="Remove additional bones",
        default=False,
        options=set(),
    )
    remove_bone_names: bpy.props.StringProperty(
        name="Remove Bone Names",
        description="Names of bones to remove, including children. Separate with spaces",
        default="",
        options=set(),
    )
    to_collection: bpy.props.BoolProperty(
        name="To Collection",
        description="Produced meshes are put in a collection instead of being exported",
        default=False,
        options=set(),
    )
    clean_collection: bpy.props.BoolProperty(
        name="Clean Collection",
        description="Clean the target collection",
        default=False,
        options=set(),
    )
    rig_export_path: bpy.props.StringProperty(
        name="Export Path",
        description="""Export path relative to the current folder.
{file} = Name of this .blend file without extension.
{rigfile} = Name of the .blend file the rig is linked from, without extension.
{rig} = Name of the rig being exported.
{object} = Name of the object being exported.
{collection} = Name of the collection the object belongs to""",
        default="//export/SK_{rigfile}.fbx",
        subtype='FILE_PATH',
        options=set(),
    )

    # Animation export options
    actions: bpy.props.CollectionProperty(
        type=GRET_PG_export_action,
        options=set(),
    )
    disable_auto_eyelid: bpy.props.BoolProperty(
        name="Disable Auto-Eyelid",
        description="Disables Auto-Eyelid. ARP only",
        default=True,
        options=set(),
    )
    disable_twist_bones: bpy.props.BoolProperty(
        name="Disable Twist Bones",
        description="Don't export twist bone animation. ARP only",
        default=True,
        options=set(),
    )
    export_markers: bpy.props.BoolProperty(
        name="Export Markers",
        description="Export markers names and frame times as a list of comma-separated values",
        default=False,
        options=set(),
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
        options=set(),
    )
    copy_properties: bpy.props.CollectionProperty(
        type=GRET_PG_copy_property,
        options=set(),
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
        options=set(),
    )

    def get_export_objects(self, context):
        if self.what == 'SCENE':
            return self._get_export_objects(context, types={'MESH', 'CURVE'})
        elif self.what == 'RIG':
            return self._get_export_objects(context, types={'MESH'}, armature=self.rig)
        return [], []

    def _get_export_objects(self, context, types=set(), armature=None):
        objs, objs_job_cl = [], []
        for job_cl in self.collections:
            for cl in job_cl.get_child_collections(context):
                if not (not cl.hide_viewport and job_cl.export_viewport
                    or not cl.hide_render and job_cl.export_render):
                    continue  # Collection filtered by visibility
                for obj in cl.objects:
                    if types and obj.type not in types:
                        continue  # Not in the requested object types
                    if armature and obj.find_armature() != armature:
                        continue  # Wrong armature
                    if not (not obj.hide_viewport and job_cl.export_viewport
                        or not obj.hide_render and job_cl.export_render):
                        continue  # Object filtered by visibility
                    if obj not in objs:
                        # Check object not already added
                        objs.append(obj)
                        objs_job_cl.append(job_cl)
        return objs, objs_job_cl

    def should_apply_modifier(self, modifier, ignore_block=False):
        if job.use_modifier_tags:
            tags = shlex.split(self.modifier_tags)
            for tag in tags:
                required = True
                if tag.startswith("!") and len(tag) > 1:
                    tag = tag[1:]
                    required = ignore_block
                # if bool(re.search(modifier.name, rf"\b{tag}\b")) == required:
                if (tag in modifier.name) == required:
                    return required
        return modifier.show_render

classes = (
    GRET_OT_export,
    GRET_OT_export_job_add,
    GRET_OT_export_job_move_down,
    GRET_OT_export_job_move_up,
    GRET_OT_export_job_preset,
    GRET_OT_export_job_remove,
    GRET_OT_search_modifier_tags,
    GRET_PG_copy_property,
    GRET_PG_export_action,
    GRET_PG_export_collection,
    GRET_PG_remap_material,
    GRET_PG_export_job,
    GRET_PT_export_jobs,
)

def register(settings, prefs):
    if not prefs.jobs__enable:
        return False

    for cls in classes:
        bpy.utils.register_class(cls)

    settings.add_property('export_jobs', bpy.props.CollectionProperty(
        type=GRET_PG_export_job,
    ))

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
