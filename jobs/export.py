import bpy

from .. import prefs
from ..log import log, logger
from ..rig.helpers import is_object_arp

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
    bl_idname = 'gret.export'
    bl_label = "Export"
    bl_context = 'objectmode'
    bl_options = {'REGISTER'}

    index: bpy.props.IntProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        if not context.scene.gret.export_jobs:
            self.report({'ERROR'}, "No export jobs created!")
            return {'CANCELLED'}
        if self.index < 0 or self.index >= len(context.scene.gret.export_jobs):
            self.report({'ERROR'}, "Invalid export job index.")
            return {'CANCELLED'}

        job = context.scene.gret.export_jobs[self.index]
        if job.what == 'SCENE':
            bpy.ops.gret.scene_export(self.index)
        elif job.what == 'RIG':
            bpy.ops.gret.rig_export(self.index)
        elif job.what == 'ANIMATION':
            bpy.ops.gret.animation_export(self.index)

        return {'FINISHED'}

def draw_job(layout, jobs, job_index):
    job = jobs[job_index]

    col_job = layout.column(align=True)
    box = col_job.box()
    row = box.row()
    icon = 'DISCLOSURE_TRI_DOWN' if job.show_expanded else 'DISCLOSURE_TRI_RIGHT'
    row.prop(job, 'show_expanded', icon=icon, text="", emboss=False)
    row.prop(job, 'what', text="", expand=True)
    row.prop(job, 'name', text="")
    row = row.row(align=True)
    sub = row.split()
    op = sub.operator('gret.export_job_move_up', icon='TRIA_UP', text="", emboss=False)
    op.index = job_index
    sub.enabled = job_index > 0
    sub = row.split()
    op = sub.operator('gret.export_job_move_down', icon='TRIA_DOWN', text="", emboss=False)
    op.index = job_index
    sub.enabled = job_index < len(jobs) - 1
    op = row.operator('gret.export_job_remove', icon='X', text="", emboss=False)
    op.index = job_index
    box = col_job.box()
    col = box

    def add_collection_layout():
        col = box.column(align=True)
        for job_cl in job.collections:
            row = col.row(align=True)
            row.prop(job_cl, 'collection', text="")
            sub = row.split(align=True)
            sub.prop(job_cl, 'subdivision_levels', text="")
            sub.ui_units_x = 1.8
            row.prop(job_cl, 'export_viewport', icon='RESTRICT_VIEW_OFF', text="")
            row.prop(job_cl, 'export_render', icon='RESTRICT_RENDER_OFF', text="")
        return col

    if job.what == 'SCENE':
        if job.show_expanded:
            col.prop(job, 'selection_only')
            add_collection_layout().enabled = not job.selection_only

            col = box.column()
            row = col.row(align=True)
            row.prop(job, 'apply_modifiers')
            sub = row.split(align=True)
            sub.prop(job, 'modifier_tags', text="")
            sub.enabled = job.apply_modifiers

            row = col.row(align=True)
            row.prop(job, 'merge_basis_shape_keys')
            sub = row.split(align=True)
            sub.prop(job, 'basis_shape_key_pattern', text="")
            sub.enabled = job.merge_basis_shape_keys

            col.prop(job, 'encode_shape_keys')

            col.prop(job, 'export_collision')
            col.prop(job, 'export_sockets')
            col.prop(job, 'keep_transforms')
            col.prop(job, 'ensure_uv_layers')

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

        op = col.operator('gret.export', icon='INDIRECT_ONLY_ON', text="Execute")
        op.index = job_index

    elif job.what == 'RIG':
        if job.show_expanded:
            box.prop(job, 'rig')
            add_collection_layout()

            col = box.column()
            row = col.row(align=True)
            row.prop(job, 'apply_modifiers')
            sub = row.split(align=True)
            sub.prop(job, 'modifier_tags', text="")
            sub.enabled = job.apply_modifiers

            row = col.row(align=True)
            row.prop(job, 'merge_basis_shape_keys')
            sub = row.split(align=True)
            sub.prop(job, 'basis_shape_key_pattern', text="")
            sub.enabled = job.merge_basis_shape_keys

            col.prop(job, 'encode_shape_keys')

            row = col.row(align=True)
            row.prop(job, 'mirror_shape_keys')
            sub = row.split(align=True)
            sub.prop(job, 'side_vgroup_name', text="")
            sub.enabled = job.mirror_shape_keys

            col.prop(job, 'minimize_bones')

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

        op = col.operator('gret.export', icon='INDIRECT_ONLY_ON', text="Execute")
        op.index = job_index

    elif job.what == 'ANIMATION':
        if job.show_expanded:
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
            sub = col.split(align=True)
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

        op = col.operator('gret.export', icon='INDIRECT_ONLY_ON', text="Execute")
        op.index = job_index

class GRET_PT_export_jobs(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Jobs"
    bl_label = "Export Jobs"

    @classmethod
    def poll(cls, context):
        return prefs.jobs_panel_enable

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
        description="Subdivision levels to apply to the collection. Negative values will simplify",
        default=0,
        soft_min=-6,
        soft_max=6,
    )

    def get_collection(self, context):
        job = context.scene.gret.export_jobs[self.job_index]
        if all(not job_cl.collection for job_cl in job.collections):
            # When no collections are set for this job, use the scene collection
            return context.scene.collection
        else:
            return self.collection

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

    # Shared scene and export rig options
    apply_modifiers: bpy.props.BoolProperty(
        name="Apply Modifiers",
        description="Apply render modifiers",
        default=True,
    )
    modifier_tags: bpy.props.StringProperty(
        name="Modifier Tags",
        description="""Tagged modifiers are only applied if the tag is found in this list.
Separate tags with a space. Tag modifiers with 'g:tag'""",
        default="",
    )
    merge_basis_shape_keys: bpy.props.BoolProperty(
        name="Merge Basis Keys",
        description="Blends matching shape keys into the basis shape",
        default=True,
    )
    basis_shape_key_pattern: bpy.props.StringProperty(
        name="Basis Shape Key Pattern",
        description="Pattern for shape keys to be merged into basis (wildcards allowed)",
        default='"Key [0-9]*" b_*',
    )
    encode_shape_keys: bpy.props.BoolProperty(
        name="Encode Shape Keys",
        description="""Shape keys suffixed '_UV' are encoded in UV channels instead of being exported.
UVn+1: deltaXY, UVn+2: deltaZnormalX, UVn+3: normalYZ. All values are remapped to a [0..1] UV range""",
        default=False,
    )
    remap_materials: bpy.props.CollectionProperty(
        type=GRET_PG_remap_material,
    )

    # Scene export options
    export_collision: bpy.props.BoolProperty(
        name="Export Collision",
        description="Exports collision objects that follow the UE4 naming pattern",
        default=True,
    )
    export_sockets: bpy.props.BoolProperty(
        name="Export Sockets",
        description="Export any Empty parented to an object as a UE4 static mesh socket",
        default=True,
    )
    keep_transforms: bpy.props.BoolProperty(
        name="Keep Transforms",
        description="Keep the position and rotation of objects relative to world center",
        default=False,
    )
    ensure_uv_layers: bpy.props.BoolProperty(
        name="Ensure UV Layers",
        description="Create an empty UV layer for objects that have none",
        default=True,
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
    )

    # Rig export options
    mirror_shape_keys: bpy.props.BoolProperty(
        name="Mirror Shape Keys",
        description="""Creates mirrored versions of shape keys that have side suffixes.
Requires a mirror modifier""",
        default=True,
    )
    side_vgroup_name: bpy.props.StringProperty(
        name="Side Vertex Group Name",
        description="Name of the vertex group that will be created on mirroring shape keys",
        default="_side.l",
    )
    minimize_bones: bpy.props.BoolProperty(
        name="Minimize Bone Hierarchy",
        description="Only export bones that the meshes are weighted to",
        default=False,
    )
    to_collection: bpy.props.BoolProperty(
        name="To Collection",
        description="Produced meshes are put in a collection instead of being exported",
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
{rig} = Name of the rig being exported.
{object} = Name of the object being exported.
{collection} = Name of the collection the object belongs to""",
        default="//export/SK_{rigfile}.fbx",
        subtype='FILE_PATH',
    )

    # Animation export options
    actions: bpy.props.CollectionProperty(
        type=GRET_PG_export_action,
    )
    disable_auto_eyelid: bpy.props.BoolProperty(
        name="Disable Auto-Eyelid",
        description="Disables Auto-Eyelid. ARP only",
        default=True,
    )
    disable_twist_bones: bpy.props.BoolProperty(
        name="Disable Twist Bones",
        description="Don't export twist bone animation. ARP only",
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

    def get_export_objects(self, context, types=set(), armature=None, unique=False):
        objs, objs_job_cl = [], []
        for job_cl in self.collections:
            cl = job_cl.get_collection(context)
            if not cl:
                continue
            if not (not cl.hide_viewport and job_cl.export_viewport
                or not cl.hide_render and job_cl.export_render):
                continue  # Collection filtered by visibility
            for obj in cl.objects:
                if types and obj.type not in types:
                    continue  # Not in the requested object types
                if (armature and obj.find_armature() != armature and
                    (not armature.proxy or obj.find_armature() != armature.proxy)):
                    continue  # Wrong armature
                if not (not obj.hide_viewport and job_cl.export_viewport
                    or not obj.hide_render and job_cl.export_render):
                    continue  # Object filtered by visibility
                if not unique or obj not in objs:
                    objs.append(obj)
                    objs_job_cl.append(job_cl)
        return objs, objs_job_cl

classes = (
    GRET_OT_export_job_add,
    GRET_OT_export_job_move_down,
    GRET_OT_export_job_move_up,
    GRET_OT_export_job_preset,
    GRET_OT_export_job_remove,
    GRET_OT_export,
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
