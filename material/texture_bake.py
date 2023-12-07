from collections import namedtuple
from itertools import chain
from math import radians
from mathutils import Matrix
import bpy
import sys

from .. import prefs
from ..log import log, logd, logger
from ..math import reverse_morton3, zagzig
from .helpers import SolidPixels, Node
from ..helpers import (
    beep,
    fail_if_invalid_export_path,
    get_context,
    get_export_path,
    get_nice_export_report,
    select_only,
    show_only,
)
from ..operator import SaveContext, SaveState

# TODO
# - AO floor
# - Report progress, see io_scene_obj/export_obj.py and bpy_extras.wm_utils.progress_report

def xyz_from_index(i):
    x = zagzig(reverse_morton3(i >> 0))
    y = zagzig(reverse_morton3(i >> 1))
    z = zagzig(reverse_morton3(i >> 2))
    return x, y, z

def remap_materials(obj, src_mat, dst_mat):
    for mat_index, mat in enumerate(obj.data.materials):
        obj.data.materials[mat_index] = dst_mat if mat == src_mat else None

nodes_none = (Node('OutputMaterial')
.link('Surface', None,
    Node('Emission')
    .set('Color', 0)
))

nodes_ao = (Node('OutputMaterial')
.link('Surface', None,
    Node('Emission')
    .link('Color', 0,
        Node('AmbientOcclusion', samples=16, only_local=True)
        .set('Distance', "scale*1.0")
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
                    Node('Bevel', samples=4)
                    .set('Radius', "scale*0.05")
                )
            )
        )
    )
))

nodes_curvature_cavity = (Node('Math', operation='SUBTRACT', use_clamp=True)
.set(0, 1.0)
.link(1, 'AO',
    Node('AmbientOcclusion', samples=16, only_local=True)
    .set('Distance', "scale*0.025")
    .link('Normal', None,
        Node('Bevel', samples=8)
        .set('Radius', "scale*0.1")
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
        .set('Distance', "scale*0.05")
        .link('Normal', None,
            Node('Bevel', samples=8)
            .set('Radius', "scale*0.05")
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

Baker = namedtuple('Baker', 'enum name description neutral_value node_builder')
bakers = [
    Baker('NONE', "None", "Nothing", 0.0, nodes_none),
    Baker('AO', "AO", "Ambient occlusion", 1.0, nodes_ao),
    Baker('BEVEL', "Bevel", "Bevel mask", 0.0, nodes_bevel),
    Baker('CURVATURE', "Curvature", "Curvature", 0.5, nodes_curvature),
]
baker_items = [(baker.enum, baker.name, baker.description) for baker in bakers]
bakers = {baker.enum: baker for baker in bakers}
channel_prefixes = ('r', 'g', 'b')
channel_icons = ('COLOR_RED', 'COLOR_GREEN', 'COLOR_BLUE')

def new_image(name, size):
    image = bpy.data.images.new(name=name, width=size, height=size)
    image.colorspace_settings.name = 'Linear'
    image.alpha_mode = 'NONE'
    return image

def _texture_bake(context, texture_bake, save, results):
    mat = context.object.active_material
    size = texture_bake.size
    render = context.scene.render

    save.selection()
    # Setup common to all bakers. Note that dilation happens before the bake results from multiple
    # objects are merged. Margin should be kept at a minimum to prevent bakes from overlapping.
    # Don't mistake bake.margin for bake_margin!
    save.prop(render, 'engine', 'CYCLES')
    save.prop(render.bake, 'margin use_selected_to_active use_clear', [size // 128, False, False])
    save.prop(context.scene, 'cycles.samples', 16)

    # Clone all the objects that contribute to the bake
    objs = [save.clone_obj(obj, to_mesh=True, reset_origin=True)
        for obj in texture_bake.get_bake_objects(context)]

    log(f"Baking {mat.name} with {len(objs)} contributing objects")
    logger.indent += 1
    show_only(context, objs)
    select_only(context, objs)

    # Explode objects. Not strictly necessary anymore since AO node has only_local flag
    explode_dist = max(max(obj.dimensions) for obj in objs) + 10.0
    for obj_index, obj in enumerate(objs):
        if prefs.texture_bake__explode_objects:
            # Spread out in every direction
            x, y, z = xyz_from_index(obj_index)
            explode_loc = (x * explode_dist, y * explode_dist, z * explode_dist)
            logd(f"Moving {obj.name} to {explode_loc}")
            obj.matrix_world = Matrix.Translation(explode_loc)
        obj.data.uv_layers[texture_bake.uv_layer_name].active = True

    bake_pixels = [SolidPixels(size, k) for k in (0.0, 0.0, 0.0, 1.0)]
    bakes = [
        (bakers[texture_bake.r], {'scale': texture_bake.r_scale}),
        (bakers[texture_bake.g], {'scale': texture_bake.g_scale}),
        (bakers[texture_bake.b], {'scale': texture_bake.b_scale}),
    ]
    fill_color = [bake[0].neutral_value for bake in bakes] + [0.0]

    for bake in bakes:
        if not bake:
            continue

        # Avoid redundant work, bake only once for all channels sharing the same settings
        target_channel_indices = []
        for channel_index, other_bake in enumerate(bakes):
            if bake == other_bake:
                bakes[channel_index] = None
                target_channel_indices.append(channel_index)

        baker, bake_params = bake
        channel_names = ''.join(channel_prefixes[idx] for idx in target_channel_indices).upper()
        log(f"Baking {baker.enum} for channel {channel_names}")
        bake_img = new_image(f"_{mat.name}_{baker.enum}", size)
        bake_img.generated_color = fill_color
        bake_mat = bpy.data.materials.new(name=f"_bake{bake_img.name}")
        save.temporary_bids([bake_img, bake_mat])
        bake_mat.use_nodes = True
        bake_mat.node_tree.nodes.clear()
        image_node = bake_mat.node_tree.nodes.new(type='ShaderNodeTexImage')
        image_node.image = bake_img
        baker.node_builder.build(bake_mat.node_tree, bake_params)

        # Switch to the bake material temporarily and bake
        with SaveContext(context, "_texture_bake") as save2:
            for obj in objs:
                save2.collection(obj.data.materials)
                remap_materials(obj, mat, bake_mat)

            # Fixes a bug where bake fails because it polls for context.object being visible
            # Why the hell is 'object' not in sync with 'active_object'?
            ctx = bpy.context.copy()
            ctx['object'] = ctx['active_object']
            bpy.ops.object.bake(ctx, type='EMIT')

        # Store the result
        pixels = bake_img.pixels[:]
        for channel_index in target_channel_indices:
            bake_pixels[channel_index] = pixels

    # Composite and write file to disk since external baking is broken in Blender
    # See https://developer.blender.org/T57143 and https://developer.blender.org/D4162
    path_fields = {
        'material': mat.name,
    }
    filepath = get_export_path(texture_bake.export_path, path_fields)
    filename = bpy.path.basename(filepath)

    log(f"Exporting {filename}")
    pack_img = new_image(f"_{mat.name}", size)
    save.temporary_bids(pack_img)
    pack_img.pixels[:] = chain.from_iterable(  # TODO numpy
        zip(*(pixels[channel_index::4] for channel_index, pixels in enumerate(bake_pixels))))
    pack_img.filepath_raw = filepath
    pack_img.file_format = 'PNG'  # TODO detect format from extension
    pack_img.save()
    results.append(filepath)

    logger.indent -= 1

class GRET_OT_texture_bake(bpy.types.Operator):
    """Bake and export the texture. All faces from all objects assigned to the active material \
are assumed to contribute"""

    bl_idname = 'gret.texture_bake'
    bl_label = "Bake Textures"
    bl_options = {'INTERNAL'}

    index: bpy.props.IntProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.object and context.object.active_material and context.mode == 'OBJECT'

    def execute(self, context):
        texture_bake = context.object.active_material.texture_bakes[self.index]

        # Validate settings
        try:
            fail_if_invalid_export_path(texture_bake.export_path, ['material'])
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        results = []
        logger.start_logging()
        log(f"Beginning texture bake")

        try:
            with SaveContext(context, "gret.texture_bake") as save:
                _texture_bake(context, texture_bake, save, results)

            # Finished without errors
            log("Bake complete")
            if prefs.texture_bake__beep_on_finish:
                beep(pitch=3, num=1)
            self.report({'INFO'}, get_nice_export_report(results, logger.time_elapsed))
        finally:
            logger.end_logging()

        return {'FINISHED'}

class GRET_OT_texture_bake_preview(bpy.types.Operator):
    """Preview this baker in the viewport. Click anywhere to stop previewing"""
    # This is a modal operator because it would be far too messy to revert the changes otherwise

    bl_idname = 'gret.texture_bake_preview'
    bl_label = "Preview Bake"
    bl_options = {'INTERNAL'}

    index: bpy.props.IntProperty(options={'HIDDEN'})
    channel_index: bpy.props.IntProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        # return context.object and context.object.active_material and context.mode == 'OBJECT'
        return context.object and context.object.active_material

    def modal(self, context, event):
        if event.type in {'LEFTMOUSE', 'RIGHTMOUSE', 'ESC', 'RET', 'SPACE'}:
            self.save.revert()
            del self.save
            return {'CANCELLED'}

        elif event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE', 'MIDDLEMOUSE', 'WHEELDOWNMOUSE',
            'WHEELUPMOUSE', 'LEFT_CTRL', 'LEFT_SHIFT', 'LEFT_ALT'}:
            # Only allow navigation keys. Kind of sucks, see https://developer.blender.org/T37427
            return {'PASS_THROUGH'}

        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        mat = context.object.active_material
        texture_bake = mat.texture_bakes[self.index]
        channel_prefix = channel_prefixes[self.channel_index]
        baker = bakers.get(getattr(texture_bake, channel_prefix))
        scale = getattr(texture_bake, f'{channel_prefix}_scale')
        if not baker:
            self.report({'ERROR'}, "Invalid baker type.")
            return {'CANCELLED'}

        logger.start_logging(timestamps=False)
        log(f"Previewing {baker.enum} baker")
        logger.indent += 1

        try:
            self.save = save = SaveState(context, "gret.texture_bake_preview")
            save.selection()
            save.prop(context.scene.render, 'engine', 'CYCLES')
            save.prop(context.scene, 'cycles.preview_samples', 8)

            # Clone all the objects that contribute to the bake
            objs = [save.clone_obj(obj, to_mesh=True, reset_origin=True)
                for obj in texture_bake.get_bake_objects(context)]

            preview_mat = bpy.data.materials.new(name=f"_preview_{baker.enum}")
            save.temporary_bids(preview_mat)
            preview_mat.use_nodes = True
            preview_mat.node_tree.nodes.clear()
            baker.node_builder.build(preview_mat.node_tree, {'scale': scale})
            for obj in objs:
                remap_materials(obj, mat, preview_mat)

            show_only(context, objs)
            save.viewports(header_text=f"Previewing {baker.enum} baker",
                show_overlays=False,
                type='RENDERED')
        except:
            self.save.revert()
            del self.save
            raise
        finally:
            logger.end_logging()

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

class GRET_OT_texture_bake_add(bpy.types.Operator):
    """Add vertex color mapping"""

    bl_idname = 'gret.texture_bake_add'
    bl_label = "Add Texture Bake"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.active_material

    def execute(self, context):
        mat = context.active_object.active_material
        bake = mat.texture_bakes.add()
        bake.uv_layer_name = prefs.texture_bake__uv_layer_name

        return {'FINISHED'}

class GRET_OT_texture_bake_clear(bpy.types.Operator):
    """Clear vertex color mapping"""

    bl_idname = 'gret.texture_bake_clear'
    bl_label = "Clear Texture Bake"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.active_material

    def execute(self, context):
        mat = context.active_object.active_material
        mat.texture_bakes.clear()

        return {'FINISHED'}

class GRET_PT_texture_bake(bpy.types.Panel):
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'material'
    bl_label = "Texture Bake"

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.active_material

    def draw(self, context):
        layout = self.layout
        mat = context.object.active_material

        if not mat.texture_bakes:
            layout.operator('gret.texture_bake_add', icon='ADD')
        else:
            layout.operator('gret.texture_bake_clear', icon='X')

        def draw_channel_layout(layout, bake_index, bake, channel_index):
            channel_prefix = channel_prefixes[channel_index]
            row = layout.row(align=True)
            row.prop(bake, channel_prefix, icon=channel_icons[channel_index], text="")
            sub = row.split(align=True)
            sub.prop(bake, f'{channel_prefix}_scale', text="")
            sub.scale_x = 0.4
            op = row.operator('gret.texture_bake_preview', icon='HIDE_OFF', text="")
            op.index = bake_index
            op.channel_index = channel_index

        for bake_index, bake in enumerate(mat.texture_bakes):
            box = layout
            col = box.column(align=True)

            draw_channel_layout(col, bake_index, bake, 0)
            draw_channel_layout(col, bake_index, bake, 1)
            draw_channel_layout(col, bake_index, bake, 2)
            col.separator()
            row = col.row(align=True)
            row.prop(bake, 'uv_layer_name', icon='UV', text="")
            row.prop(bake, 'size')
            col.prop(bake, 'export_path', text="")
            op = col.operator('gret.texture_bake', icon='INDIRECT_ONLY_ON', text="Bake")
            op.index = bake_index

class GRET_PG_texture_bake(bpy.types.PropertyGroup):
    uv_layer_name: bpy.props.StringProperty(
        name="UV Layer",
        description="UV layer to use for baking. Defaults can be changed in addon preferences",
        default="UVMap",
    )
    size: bpy.props.IntProperty(
        name="Texture Size",
        description="Size of the exported texture",
        default=256,
        min=8,
    )
    r: bpy.props.EnumProperty(
        name="Texture R Baker",
        description="Mask to bake into the texture's red channel",
        items=baker_items,
        default='AO',
    )
    r_scale: bpy.props.FloatProperty(
        name="Texture R Baker Scale",
        description="Baker-specific scaling factor",
        default=1.0,
        min=0.0,
    )
    g: bpy.props.EnumProperty(
        name="Texture G Baker",
        description="Mask to bake into the texture's green channel",
        items=baker_items,
        default='CURVATURE',  # Curvature in green for RGB565
    )
    g_scale: bpy.props.FloatProperty(
        name="Texture G Baker Scale",
        description="Baker-specific scaling factor",
        default=1.0,
        min=0.0,
    )
    b: bpy.props.EnumProperty(
        name="Texture B Baker",
        description="Mask to bake into the texture's blue channel",
        items=baker_items,
        default='BEVEL',
    )
    b_scale: bpy.props.FloatProperty(
        name="Texture B Baker Scale",
        description="Baker-specific scaling factor",
        default=1.0,
        min=0.0,
    )
    export_path: bpy.props.StringProperty(
        name="Export Path",
        description="""Export path for the baked texture.
{file} = Name of this .blend file without extension.
{material} = Name of the material being baked.""",
        default="//export/T_{material}.png",
        subtype='FILE_PATH',
    )

    def get_bake_objects(self, context):
        objs = []
        for obj in context.scene.objects:
            if obj.type != 'MESH' or obj.hide_render:
                continue  # Not a mesh or filtered by visibility
            if self.id_data.name not in obj.material_slots:
                continue  # Doesn't contribute
            if self.uv_layer_name not in obj.data.uv_layers:
                continue  # No UV layer
            if not obj.data.polygons:
                continue  # Empty meshes cause bake to fail
            objs.append(obj)
        return objs

classes = (
    GRET_OT_texture_bake,
    GRET_OT_texture_bake_add,
    GRET_OT_texture_bake_clear,
    GRET_OT_texture_bake_preview,
    GRET_PG_texture_bake,
    GRET_PT_texture_bake,
)

def register(settings, prefs):
    if not prefs.texture_bake__enable:
        return False

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Material.texture_bakes = bpy.props.CollectionProperty(
        type=GRET_PG_texture_bake,
    )

def unregister():
    del bpy.types.Material.texture_bakes

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
