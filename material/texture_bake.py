from itertools import chain
from math import radians
from mathutils import Matrix
import bpy
import time

from gret import prefs
from gret.log import log, logger
from gret.helpers import (
    beep,
    fail_if_invalid_export_path,
    get_export_path,
    get_nice_export_report,
    load_selection,
    save_selection,
    select_only,
    show_only,
)
from gret.material.helpers import SolidPixels, Node

# TODO
# - AO floor
# - Make it a collection like vertex color mapping, add UVMap name to it
# - Allow Quick Unwrap from object mode

def remap_materials(objs, src_mat, dst_mat):
    for obj in objs:
        for mat_idx, mat in enumerate(obj.data.materials):
            obj.data.materials[mat_idx] = dst_mat if mat == src_mat else None

nodes_ao = (Node('OutputMaterial')
.link('Surface', None,
    Node('Emission')
    .link('Color', 0,
        Node('AmbientOcclusion', samples=16, only_local=True)
        .set('Distance', 2.0)
    )
))

nodes_bevel = (Node('OutputMaterial')
.link('Surface', None,
    Node('Emission')
    .link('Color', 0,
        Node('Math', operation='SMOOTH_MIN')
        .set(1, 0.6)  # Value2
        .set(2, 2.0)  # Distance
        .link(0, None,
            Node('VectorMath', operation='LENGTH')
            .link('Vector', None,
                Node('VectorMath', operation='CROSS_PRODUCT')
                .link(0, 'Normal',
                    Node('NewGeometry')
                )
                .link(1, 'Normal',
                    Node('Bevel', samples=2)
                    .set('Radius', 0.1)
                )
            )
        )
    )
))

nodes_curvature_cavity = (Node('Math', operation='SUBTRACT', use_clamp=True)
.set(0, 1.0)
.link(1, 'AO',
    Node('AmbientOcclusion', samples=16, only_local=True)
    .set('Distance', 0.05)
    .link('Normal', None,
        Node('Bevel', samples=8)
        .set('Radius', 0.2)
    )
))
nodes_curvature_edge = (Node('Math', operation='SMOOTH_MIN', use_clamp=True)
.set(1, 0.5)  # Value2
.set(2, 1.0)  # Distance
.link(0, None,
    Node('Math', operation='SUBTRACT', use_clamp=True)
    .set(0, 1.0)  # One minus AO
    .link(1, 'AO',
        Node('AmbientOcclusion', samples=16, inside=True, only_local=True)
        .set('Distance', 0.1)
        .link('Normal', None,
            Node('Bevel', samples=8)
            .set('Radius', 0.1)
        )
    )
))
nodes_curvature = (Node('OutputMaterial')
.link('Surface', None,
    Node('Emission')
    .link('Color', 0,
        Node('Math', operation='SUBTRACT', use_clamp=True)
        .link(1, None,
            Node('Math', operation='MULTIPLY', use_clamp=True)
            .set(1, 4.0)  # Value2
            .link(0, None,
                Node('Math', operation='MAXIMUM', use_clamp=True)
                .link(0, None,
                    Node('Math', operation='SUBTRACT', use_clamp=True)
                    .link(0, None, nodes_curvature_cavity)
                    .link(1, None, nodes_curvature_edge)
                )
                .link(1, None,
                    Node('Math', operation='MULTIPLY', use_clamp=True)
                    .link(0, None, nodes_curvature_cavity)
                    .link(1, None, nodes_curvature_edge)
                )
            )
        )
        .link(0, None,
            Node('Math', operation='ADD', use_clamp=True)
            .set(0, 0.5)  # Value1
            .link(1, None, nodes_curvature_edge)
        )
    )
))

def bake_ao(scene, node_tree):
    # scene.cycles.samples = 128
    # bpy.ops.object.bake(type='AO')
    # Ambient occlusion node seems to produce less artifacts
    nodes_ao.build(node_tree)
    scene.cycles.samples = 16
    bpy.ops.object.bake(type='EMIT')

def bake_bevel(scene, node_tree):
    nodes_bevel.build(node_tree)
    scene.cycles.samples = 16
    bpy.ops.object.bake(type='EMIT')

def bake_curvature(scene, node_tree):
    nodes_curvature.build(node_tree)
    scene.cycles.samples = 16
    bpy.ops.object.bake(type='EMIT')

bakers = {
    'AO': bake_ao,
    'BEVEL': bake_bevel,
    'CURVATURE': bake_curvature,
}

node_trees = {
    'AO': nodes_ao,
    'BEVEL': nodes_bevel,
    'CURVATURE': nodes_curvature,
}

bake_items = [
    ('NONE', "None", "Nothing"),
    ('AO', "AO", "Ambient occlusion"),
    ('BEVEL', "Bevel", "Bevel mask, similar to curvature"),
    ('CURVATURE', "Curvature", "Curvature, centered on gray"),
]

class GRET_OT_bake(bpy.types.Operator):
    #tooltip
    """Bake and export the texture.
All faces from all objects assigned to the active material are assumed to contribute"""

    bl_idname = 'gret.bake'
    bl_label = "Bake"
    bl_options = {'INTERNAL'}

    uv_layer_name: bpy.props.StringProperty(
        name="UV Layer",
        description="""Name of the target UV layer.
Defaults to the setting found in addon preferences if not specified""",
        default="",
    )
    debug: bpy.props.BoolProperty(
        name="Debug",
        description="Debug mode with verbose output. Keeps intermediate materials and textures",
        default=False,
    )

    def new_image(self, name, size):
        # For debugging purposes, try to reuse an image if it exists
        image = bpy.data.images.get(name)
        if image and image.size[:] != (size, size):
            bpy.data.images.remove(image)
            image = None
        if not image:
            image = bpy.data.images.new(name=name, width=size, height=size)
        self.new_images.append(image)

        image.colorspace_settings.name = 'Linear'
        image.alpha_mode = 'NONE'
        return image

    def new_bake_material(self, image):
        # For debugging purposes, try to reuse a material if it exists
        name = f"_bake{image.name}"
        mat = bpy.data.materials.get(name)
        if not mat:
            mat = bpy.data.materials.new(name=name)
        self.new_materials.append(mat)

        mat.use_nodes = True
        mat.node_tree.nodes.clear()
        image_node = mat.node_tree.nodes.new(type='ShaderNodeTexImage')
        image_node.image = image
        return mat

    @classmethod
    def poll(cls, context):
        return context.object and context.object.active_material and context.mode == 'OBJECT'

    def _execute(self, context):
        # External baking is broken in Blender
        # See https://developer.blender.org/T57143 and https://developer.blender.org/D4162

        mat = context.object.active_material
        bake = mat.texture_bake
        size = bake.size
        self.uv_layer_name = self.uv_layer_name or prefs.quick_unwrap_uv_layer_name

        # Collect all the objects that share this material
        objs = [o for o in context.scene.objects if
            o.type == 'MESH' and o.data.uv_layers.active and mat.name in o.data.materials]
        show_only(context, objs)
        select_only(context, objs)

        log(f"Baking {mat.name} with {len(objs)} contributing objects")
        logger.indent += 1

        # Explode objects. Not strictly necessary anymore since AO node has only_local flag
        for obj_idx, obj in enumerate(objs):
            self.saved_transforms[obj] = obj.matrix_world.copy()
            obj.matrix_world = Matrix.Translation((100.0 * obj_idx, 0.0, 0.0))

            uv = obj.data.uv_layers.get(self.uv_layer_name)
            if not uv:
                log(f"UV layer {self.uv_layer_name} not found in {obj.name}")
                return {'CANCELLED'}
            uv.active = True

        # Setup common to all bakers
        # Note that dilation happens before the bake results from multiple objects are merged
        # Margin should be kept at a minimum to prevent bakes from overlapping
        context.scene.render.engine = 'CYCLES'
        context.scene.render.bake.margin = size // 128
        context.scene.render.bake.use_selected_to_active = False

        bake_pixels = [SolidPixels(size, k) for k in (0.0, 0.0, 0.0, 1.0)]
        bake_srcs = [bake.r, bake.g, bake.b]
        for bake_src in bake_srcs:
            if bake_src != 'NONE':
                # Avoid doing extra work and bake only once for all channels with the same baker
                channel_idxs = [idx for idx, src in enumerate(bake_srcs) if src == bake_src]
                channel_names = ""
                for channel_idx in channel_idxs:
                    bake_srcs[channel_idx] = 'NONE'
                    channel_names += ("R", "G", "B")[channel_idx]
                log(f"Baking {bake_src} for channel {channel_names}")
                bake_img = self.new_image(f"_{mat.name}_{bake_src}", size)
                bake_mat = self.new_bake_material(bake_img)

                # Switch to the bake material, bake then restore
                saved_materials = {obj: obj.data.materials[:] for obj in objs}
                remap_materials(objs, mat, bake_mat)
                bakers[bake_src](context.scene, bake_mat.node_tree)
                for obj, saved_mats in saved_materials.items():
                    for mat_idx, saved_mat in enumerate(saved_mats):
                        obj.data.materials[mat_idx] = saved_mat

                # Store the result
                pixels = bake_img.pixels[:]
                for channel_idx in channel_idxs:
                    bake_pixels[channel_idx] = pixels

        # Composite and write file to disk
        path_fields = {
            'material': mat.name,
        }
        filepath = get_export_path(bake.export_path, path_fields)
        filename = bpy.path.basename(filepath)

        log(f"Exporting {filename}")
        pack_img = self.new_image(f"_{mat.name}", size)
        pack_img.pixels[:] = chain.from_iterable(
            zip(*(pixels[channel_idx::4] for channel_idx, pixels in enumerate(bake_pixels))))
        pack_img.filepath_raw = filepath
        pack_img.file_format = 'PNG'  # TODO detect format from extension
        pack_img.save()
        self.exported_files.append(filepath)

        logger.indent -= 1

    def execute(self, context):
        bake = context.object.active_material.texture_bake

        try:
            fail_if_invalid_export_path(bake.export_path, ['material'])
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        saved_selection = save_selection()
        saved_render_engine = context.scene.render.engine
        saved_render_bake_margin = context.scene.render.bake.margin  # Don't mistake for bake_margin
        saved_render_use_selected_to_active = context.scene.render.bake.use_selected_to_active
        saved_cycles_samples = context.scene.cycles.samples
        saved_use_global_undo = context.preferences.edit.use_global_undo
        context.preferences.edit.use_global_undo = False
        self.exported_files = []
        self.new_materials = []
        self.new_images = []
        self.saved_transforms = {}
        logger.start_logging()

        try:
            start_time = time.time()
            self._execute(context)
            # Finished without errors
            elapsed = time.time() - start_time
            self.report({'INFO'}, get_nice_export_report(self.exported_files, elapsed))
            beep(pitch=3, num=1)
        finally:
            # Clean up
            if not self.debug:
                while self.new_materials:
                    bpy.data.materials.remove(self.new_materials.pop())
                while self.new_images:
                    bpy.data.images.remove(self.new_images.pop())
            for obj, matrix_world in self.saved_transforms.items():
                obj.matrix_world = matrix_world
            del self.saved_transforms

            load_selection(saved_selection)
            context.scene.render.engine = saved_render_engine
            context.scene.render.bake.margin = saved_render_bake_margin
            context.scene.render.bake.use_selected_to_active = saved_render_use_selected_to_active
            context.scene.cycles.samples = saved_cycles_samples
            context.preferences.edit.use_global_undo = saved_use_global_undo
            logger.end_logging()

        if self.debug:
            bpy.ops.ed.undo_push()

        return {'FINISHED'}

class GRET_OT_quick_unwrap(bpy.types.Operator):
    #tooltip
    """Smart unwrap and pack UVs for all objects that have the active material assigned"""

    bl_idname = 'gret.quick_unwrap'
    bl_label = "Quick Unwrap"
    bl_options = {'REGISTER', 'UNDO'}

    uv_layer_name: bpy.props.StringProperty(
        name="UV Layer",
        description="""Name of the target UV layer.
Defaults to the setting found in addon preferences if not specified""",
        default="",
    )
    angle_limit: bpy.props.FloatProperty(
        name="Angle Limit",
        description="Lower for more projection groups, higher for less distortion",
        subtype='ANGLE',
        default=radians(66.0),
        min=radians(0.0),
        max=radians(89.0),
    )
    area_weight: bpy.props.FloatProperty(
        name="Area Weight",
        description="Weight projection vectors by faces with larger areas",
        default=0.0,
        min=0.0,
        max=1.0,
    )
    align_with_world: bpy.props.BoolProperty(
        name="Align With World",
        description="Rotate UV islands to flow in the direction of gravity. Requires TexTools addon",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return context.object and context.object.active_material and context.mode == 'EDIT_MESH'

    def execute(self, context):
        mat = context.object.active_material
        saved_area_ui_type = context.area.ui_type
        saved_use_uv_select_sync = context.scene.tool_settings.use_uv_select_sync
        saved_selection = save_selection()
        saved_active_uv_layers = {}  # Object to UV layer
        margin = 1.0 / 128 * 2
        self.uv_layer_name = self.uv_layer_name or prefs.quick_unwrap_uv_layer_name

        try:
            # Select all faces of all objects that share the material
            bpy.ops.object.editmode_toggle()
            context.scene.tool_settings.use_uv_select_sync = True
            objs = [o for o in context.scene.objects if mat.name in o.data.materials]
            select_only(context, objs)
            bpy.ops.object.editmode_toggle()
            bpy.ops.mesh.reveal()
            bpy.ops.mesh.select_mode(type='FACE')
            bpy.ops.object.editmode_toggle()
            for obj in objs:
                saved_active_uv_layers[obj] = obj.data.uv_layers.active
                uv = obj.data.uv_layers.get(self.uv_layer_name)
                if not uv:
                    uv = obj.data.uv_layers.new(name=self.uv_layer_name)
                uv.active = True
                for face in obj.data.polygons:
                    face.select = obj.data.materials[face.material_index] == mat
            bpy.ops.object.editmode_toggle()

            # Unwrap
            bpy.ops.uv.smart_project(
                angle_limit=self.angle_limit,
                island_margin=margin,
                area_weight=self.area_weight,
                correct_aspect=True,
                scale_to_bounds=False)

            # If set and TexTools is available, rotate islands
            if self.align_with_world:
                try:
                    context.area.ui_type = 'UV'
                    context.scene.tool_settings.use_uv_select_sync = False
                    bpy.ops.uv.textools_island_align_world(steps=2)
                except AttributeError:
                    pass

            # If available, pack using an addon
            try:
                context.scene.uvp2_props.margin = margin
                context.scene.uvp2_props.rot_enable = not self.align_with_world
                bpy.ops.uvpackmaster2.uv_pack()
            except AttributeError:
                pass
        finally:
            for obj, uv_layer in saved_active_uv_layers.items():
                obj.data.uv_layers.active = uv_layer
            load_selection(saved_selection)
            context.scene.tool_settings.use_uv_select_sync = saved_use_uv_select_sync
            context.area.ui_type = saved_area_ui_type
            # Exiting edit mode here causes uvpackmaster2 to break, it's doing some weird modal stuff
            # bpy.ops.object.mode_set(mode='OBJECT')

        return {'FINISHED'}

class GRET_OT_bake_preview(bpy.types.Operator):
    #tooltip
    """Preview bake result"""
    # This is a modal operator because it would be far too messy to revert the changes otherwise

    bl_idname = 'gret.bake_preview'
    bl_label = "Preview Bake"
    bl_options = {'INTERNAL'}

    baker: bpy.props.EnumProperty(
        name="Source",
        description="Mask type to preview",
        items=bake_items,
    )

    @classmethod
    def poll(cls, context):
        return context.object and context.object.active_material

    def modal(self, context, event):
        if event.type in {'LEFTMOUSE', 'RIGHTMOUSE', 'ESC', 'RET', 'SPACE'}:
            # Revert changes
            obj = context.object
            preview_mat = next((mat for mat in obj.data.materials if mat), None)
            for mat_idx, mat in enumerate(self.saved_materials):
                obj.data.materials[mat_idx] = mat
            if preview_mat:
                bpy.data.materials.remove(preview_mat)
            del self.saved_materials

            context.area.header_text_set(None)
            context.scene.render.engine = self.saved_render_engine
            context.scene.cycles.preview_samples = self.saved_cycles_samples
            return {'CANCELLED'}

        elif event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'MIDDLEMOUSE', 'WHEELDOWNMOUSE',
            'WHEELUPMOUSE', 'LEFT_CTRL', 'LEFT_SHIFT', 'LEFT_ALT'}:
            # Only allow navigation keys. Kind of sucks, see https://developer.blender.org/T37427
            return {'PASS_THROUGH'}

        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        scn = context.scene
        obj = context.object
        node_tree = node_trees.get(self.baker)
        if not node_tree:
            self.report({'ERROR'}, "Invalid baker type.")
            return {'CANCELLED'}

        self.saved_materials = obj.data.materials[:]
        preview_mat = bpy.data.materials.new(name=f"_preview_{self.baker}")
        preview_mat.use_nodes = True
        preview_mat.node_tree.nodes.clear()
        node_tree.build(preview_mat.node_tree)
        remap_materials([obj], context.object.active_material, preview_mat)

        self.saved_render_engine, scn.render.engine = scn.render.engine, 'CYCLES'
        self.saved_cycles_samples, scn.cycles.preview_samples = scn.cycles.preview_samples, 8

        context.area.header_text_set(f"Previewing {self.baker} baker")
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

class GRET_PT_texture_bake(bpy.types.Panel):
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'material'
    bl_label = "Texture Bake"

    @classmethod
    def poll(cls, context):
        return context.object and context.object.active_material

    def draw(self, context):
        layout = self.layout
        mat = context.object.active_material
        bake = mat.texture_bake

        row = layout.row(align=True)
        row.prop(bake, 'r', icon='COLOR_RED', text="")
        op = row.operator('gret.bake_preview', icon='HIDE_OFF', text="")
        op.baker = bake.r
        row.prop(bake, 'g', icon='COLOR_GREEN', text="")
        op = row.operator('gret.bake_preview', icon='HIDE_OFF', text="")
        op.baker = bake.g
        row.prop(bake, 'b', icon='COLOR_BLUE', text="")
        op = row.operator('gret.bake_preview', icon='HIDE_OFF', text="")
        op.baker = bake.b
        row.prop(bake, 'size', text="")

        col = layout.column(align=True)
        col.prop(bake, 'export_path', text="")
        row = col.row(align=True)
        row.operator('gret.quick_unwrap', icon='UV')
        op = row.operator('gret.bake', icon='INDIRECT_ONLY_ON', text="Bake")
        op.debug = False
        op = row.operator('gret.bake', icon='INDIRECT_ONLY_OFF', text="")
        op.debug = True

class GRET_PG_texture_bake(bpy.types.PropertyGroup):
    size: bpy.props.IntProperty(
        name="Texture Size",
        description="Size of the exported texture",
        default=256,
        min=8,
    )
    r: bpy.props.EnumProperty(
        name="Texture R Baker",
        description="Mask to bake into the texture's red channel",
        items=bake_items,
        default='AO',
    )
    g: bpy.props.EnumProperty(
        name="Texture G Baker",
        description="Mask to bake into the texture's green channel",
        items=bake_items,
        default='CURVATURE',  # Curvature in green for RGB565
    )
    b: bpy.props.EnumProperty(
        name="Texture B Baker",
        description="Mask to bake into the texture's blue channel",
        items=bake_items,
        default='BEVEL',
    )
    export_path: bpy.props.StringProperty(
        name="Export Path",
        description="""Export path for the baked texture.
{file} = Name of this .blend file without extension.
{material} = Name of the material being baked.""",
        default="//export/T_{material}.png",
        subtype='FILE_PATH',
    )

classes = (
    GRET_OT_bake,
    GRET_OT_bake_preview,
    GRET_OT_quick_unwrap,
    GRET_PG_texture_bake,
    GRET_PT_texture_bake,
)

def register(settings):
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Material.texture_bake = bpy.props.PointerProperty(type=GRET_PG_texture_bake)

def unregister():
    del bpy.types.Material.texture_bake

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
