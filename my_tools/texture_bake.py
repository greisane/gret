from itertools import chain
from math import radians
from mathutils import Matrix
import bpy
import time
from .helpers import (
    beep,
    fail_if_invalid_export_path,
    get_export_path,
    get_nice_export_report,
    load_selection,
    log,
    logger,
    save_selection,
    select_only,
    show_only,
)

class SolidPixels:
    """Mimics a pixels array, always returning the same value for all pixels."""

    def __init__(self, size, value=0.0):
        self.size = size
        self.value = value
    def __len__(self):
        return self.size * self.size * 4
    def __getitem__(self, key):
        if isinstance(key, slice):
            return [self.value] * len(range(*key.indices(len(self))))
        return self.value

class Node:
    """Fluent interface wrapper for nodes in a Blender node tree."""

    def __init__(self, type, **kwargs):
        self.type = 'ShaderNode' + type
        self.options = kwargs
        self.default_values = {}
        self.links = []  # List of (this_input, other_output, other)
        self._node = None

    def link(self, this_input, other_output, other):
        """Links the other's node output to this node's input.
        If other_output is None, uses any output socket that matches the type of the input socket."""
        self.links.append((this_input, other_output, other))
        return self

    def set(self, this_input, value):
        """Sets the default value of the input."""
        self.default_values[this_input] = value
        return self

    def find_input_socket(self, id_):
        """Find an input socket by its name, index or type."""
        if id_ in {'VALUE', 'VECTOR', 'RGBA', 'SHADER'}:
            return next(s for s in self._node.inputs if s.type == id_)
        return self._node.inputs[id_]

    def find_output_socket(self, id_):
        """Find an output socket by its name, index or type."""
        if id_ in {'VALUE', 'VECTOR', 'RGBA', 'SHADER'}:
            return next(s for s in self._node.outputs if s.type == id_)
        return self._node.outputs[id_]

    def build(self, tree, location=(0, 0)):
        if self._node:
            return

        self._node = tree.nodes.new(type=self.type)
        self._node.location[:] = location
        # Can't get actual node dimensions until the layout is updated, so make a guess
        node_height = max(len(self._node.inputs), len(self._node.outputs)) * 20.0 + 200.0
        self.branch_height = node_height + 20.0

        for k, v in self.options.items():
            try:
                setattr(self._node, k, v)
            except AttributeError, TypeError as e:
                log(f"Couldn't set option {k} for node {self._node.name}: {e}")
        for k, v in self.default_values.items():
            self.find_input_socket(k).default_value = v

        height = 0.0
        for link_idx, (this_input, other_output, other) in enumerate(self.links):
            # Rudimentary arrangement
            other_x = self._node.location.x - 200.0
            other_y = self._node.location.y - height
            other.build(tree, (other_x, other_y))
            height += other.branch_height

            this_input_socket = self.find_input_socket(this_input)
            other_output = this_input_socket.type if other_output is None else other_output
            other_output_socket = other.find_output_socket(other_output)
            tree.links.new(this_input_socket, other_output_socket)

    def __repr__(self):
        return f"{__class__.__name__}({repr(self.type)})"

def remap_materials(objs, src_mat, dst_mat):
    for obj in objs:
        for mat_idx, mat in enumerate(obj.data.materials):
            obj.data.materials[mat_idx] = dst_mat if mat == src_mat else None

def bake_ao(scene, node_tree):
    # scene.cycles.samples = 128
    # bpy.ops.object.bake(type='AO')
    # Ambient occlusion node seems to produce less artifacts
    main = (Node('OutputMaterial')
    .link('Surface', None,
        Node('Emission')
        .link('Color', 0,
            Node('AmbientOcclusion', samples=16, only_local=True)
            .set('Distance', 2.0)
        )
    ))
    main.build(node_tree)
    scene.cycles.samples = 16
    bpy.ops.object.bake(type='EMIT')

def bake_bevel(scene, node_tree):
    main = (Node('OutputMaterial')
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
    main.build(node_tree)
    scene.cycles.samples = 16
    bpy.ops.object.bake(type='EMIT')

def bake_curvature(scene, node_tree):
    cavity = (Node('Math', operation='SUBTRACT', use_clamp=True)
    .set(0, 1.0) \
    .link(1, 'AO',
        Node('AmbientOcclusion', samples=16, only_local=True)
        .set('Distance', 0.05)
        .link('Normal', None,
            Node('Bevel', samples=8) \
            .set('Radius', 0.2)
        )
    ))
    edge = (Node('Math', operation='SMOOTH_MIN', use_clamp=True)
    .set(1, 0.5)  # Value2
    .set(2, 1.0)  # Distance
    .link(0, None,
        Node('Math', operation='SUBTRACT', use_clamp=True)
        .set(0, 1.0)  # One minus AO
        .link(1, 'AO',
            Node('AmbientOcclusion', samples=16, inside=True, only_local=True)
            .set('Distance', 0.1)
            .link('Normal', None,
                Node('Bevel', samples=8) \
                .set('Radius', 0.1)
            )
        )
    ))
    main = (Node('OutputMaterial')
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
                        .link(0, None, cavity)
                        .link(1, None, edge)
                    )
                    .link(1, None,
                        Node('Math', operation='MULTIPLY', use_clamp=True)
                        .link(0, None, cavity)
                        .link(1, None, edge)
                    )
                )
            )
            .link(0, None,
                Node('Math', operation='ADD', use_clamp=True)
                .set(0, 0.5)  # Value1
                .link(1, None, edge)
            )
        )
    ))
    main.build(node_tree)
    scene.cycles.samples = 16
    bpy.ops.object.bake(type='EMIT')

bakers = {
    'AO': bake_ao,
    'BEVEL': bake_bevel,
    'CURVATURE': bake_curvature,
}

bake_items = [
    ('NONE', "None", "Nothing."),
    ('AO', "AO", "Ambient occlusion."),
    ('BEVEL', "Bevel", "Bevel mask, similar to curvature."),
    ('CURVATURE', "Curvature", "Curvature, centered on gray."),
]

class MY_OT_bake(bpy.types.Operator):
    #tooltip
    """Bake and export the texture.
All faces from all objects assigned to this material are assumed to contribute"""

    bl_idname = 'my_tools.bake'
    bl_label = "Bake"
    bl_options = {'INTERNAL'}

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

        # Collect all the objects that share this material
        objs = [o for o in context.scene.objects if mat.name in o.data.materials]
        show_only(context, objs)
        select_only(context, objs)

        log(f"Baking {mat.name} with {len(objs)} contributing objects")
        logger.log_indent += 1

        # Explode objects
        for obj_idx, obj in enumerate(objs):
            self.saved_transforms[obj] = obj.matrix_world.copy()
            obj.matrix_world = Matrix.Translation((100.0 * obj_idx, 0.0, 0.0))

        # Setup common to all bakers
        # Note that dilation happens before the bake results from multiple objects are merged
        # Margin should be kept at a minimum to prevent bakes from overlapping
        context.scene.render.engine = 'CYCLES'
        context.scene.render.bake.margin = size // 128

        bake_pixels = [SolidPixels(size, k) for k in (0.0, 0.0, 0.0, 1.0)]
        bake_srcs = [bake.r, bake.g, bake.b]
        for bake_src in bake_srcs:
            if bake_src != 'NONE':
                # Avoid doing extra work and bake only once for all channels with the same source
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

        logger.log_indent -= 1

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
            context.scene.cycles.samples = saved_cycles_samples
            context.preferences.edit.use_global_undo = saved_use_global_undo
            logger.end_logging()

        if self.debug:
            bpy.ops.ed.undo_push()

        return {'FINISHED'}

class MY_OT_quick_unwrap(bpy.types.Operator):
    #tooltip
    """Smart unwrap and pack UVs for all objects that have this material assigned"""

    bl_idname = 'my_tools.quick_unwrap'
    bl_label = "Quick Unwrap"
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.active_material and context.mode == 'OBJECT'

    def execute(self, context):
        mat = context.object.active_material
        uv_layer_name = mat.texture_bake.uv_layer_name or "UVMap"
        saved_use_uv_select_sync = context.scene.tool_settings.use_uv_select_sync
        saved_selection = save_selection()
        margin = 1.0 / 128 * 2

        try:
            # Select all faces of all objects that share the material
            context.scene.tool_settings.use_uv_select_sync = True
            objs = [o for o in context.scene.objects if mat.name in o.data.materials]
            select_only(context, objs)
            bpy.ops.object.editmode_toggle()
            bpy.ops.mesh.reveal()
            bpy.ops.mesh.select_mode(type='FACE')
            bpy.ops.object.editmode_toggle()
            for obj in objs:
                uv_map = obj.data.uv_layers.get(uv_layer_name)
                if not uv_map:
                    uv_map = obj.data.uv_layers.new(name=uv_layer_name)
                uv_map.active = True
                for face in obj.data.polygons:
                    face.select = obj.data.materials[face.material_index] == mat
            bpy.ops.object.editmode_toggle()

            # Unwrap
            bpy.ops.uv.smart_project(
                angle_limit=radians(66.0),
                island_margin=margin,
                area_weight=0.0,
                correct_aspect=True,
                scale_to_bounds=False)
            try:
                # Pack using an addon if available
                context.scene.uvp2_props.margin = margin
                bpy.ops.uvpackmaster2.uv_pack()
            except AttributeError:
                pass
        finally:
            load_selection(saved_selection)
            context.scene.tool_settings.use_uv_select_sync = saved_use_uv_select_sync
            # Exiting edit mode here causes uvpackmaster2 to break, it's doing some weird modal stuff
            # bpy.ops.object.mode_set(mode='OBJECT')

        return {'FINISHED'}

class MY_PT_material_tools(bpy.types.Panel):
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
        row.prop(bake, 'uv_layer_name', text="")
        row.operator('my_tools.quick_unwrap', icon='UV')

        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(bake, 'r', icon='COLOR_RED', text="")
        row.prop(bake, 'g', icon='COLOR_GREEN', text="")
        row.prop(bake, 'b', icon='COLOR_BLUE', text="")
        row.prop(bake, 'size', text="")
        col.prop(bake, 'export_path', text="")
        row = col.row(align=True)
        op = row.operator('my_tools.bake', icon='INDIRECT_ONLY_ON', text="Bake")
        op.debug = False
        op = row.operator('my_tools.bake', icon='INDIRECT_ONLY_OFF', text="")
        op.debug = True

class MY_PG_texture_bake(bpy.types.PropertyGroup):
    uv_layer_name: bpy.props.StringProperty(
        name="UV Layer",
        description="Name of the target UV layer",
        default="UVMap",
    )
    size: bpy.props.IntProperty(
        name="Texture Size",
        description="Size of the exported texture",
        default=256,
        min=8,
    )
    r: bpy.props.EnumProperty(
        name="Texture R Source",
        description="Mask to bake into the texture's red channel",
        items=bake_items,
        default='AO',
    )
    g: bpy.props.EnumProperty(
        name="Texture G Source",
        description="Mask to bake into the texture's green channel",
        items=bake_items,
        default='CURVATURE',  # Curvature in green for RGB565
    )
    b: bpy.props.EnumProperty(
        name="Texture B Source",
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
    MY_OT_bake,
    MY_OT_quick_unwrap,
    MY_PG_texture_bake,
    MY_PT_material_tools,
)

def register(settings):
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Material.texture_bake = bpy.props.PointerProperty(type=MY_PG_texture_bake)

def unregister():
    del bpy.types.Material.texture_bake

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
