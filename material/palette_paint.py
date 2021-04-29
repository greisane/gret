from bl_keymap_utils.io import keyconfig_init_from_data
from bpy.utils.toolsystem import ToolDef
from bpy_extras import view3d_utils
from collections import namedtuple
from math import ceil, inf, atan2, pi
from mathutils import Color, Vector
from random import randrange
import bpy
import re

from gret.helpers import (
    get_tools_from_space_and_mode,
    keymap_view3d_empty,
    remove_subsequence,
    select_only,
)
from gret.material.helpers import Node
from gret import prefs

tool_id = 'gret.palette_paint'
km_tool_paint_name = "3D View Tool: Palette Paint"
generative_modifier_types = {'MULTIRES', 'BEVEL', 'BOOLEAN', 'BUILD', 'DECIMATE', 'NODES', 'MASK',
    'REMESH', 'SCREW', 'SKIN', 'SOLIDIFY', 'SUBSURF', 'TRIANGULATE', 'WIREFRAME'}
palettes = {}  # Image name to Palette

nodes_palette = (Node('OutputMaterial')
.link('Surface', None,
    Node('BsdfDiffuse')
    .set('Roughness', 1.0)
    .link('Color', 0,
        Node('TexImage', image_eval='image', interpolation='Closest')
    )
))

Tile = namedtuple('Tile', ['palette', 'index', 'rotation'])
Tile.__bool__ = lambda self: self.palette is not None
Tile.invalid = Tile(None, -1, 0)

class Palette(namedtuple('Palette', ['name', 'dims', 'solid', 'pcoll', 'icon_ids'])):
    @classmethod
    def from_image(cls, image):
        if not image:
            return None

        # Read tile dimensions from image name
        name = image.name
        w, h = image.size
        if w <= 0 or h <= 0:
            return None
        match = re.search(r"(\d+)x(\d+)", name)
        if not match:
            return None
        tile_w, tile_h = int(match[1]), int(match[2])
        tiles_x, tiles_y = image.size[0] // tile_w, image.size[1] // tile_h
        max_tiles = 1024
        if tiles_x <= 0 or tiles_y <= 0 or tiles_x * tiles_y > max_tiles:
            return None

        # Generate preview collection icons
        # It doesn't seem icon size in the UI can be changed easily
        # Real icon size is 16x16 so that works as supersampling for large tilesets
        pixels = image.pixels[:]
        tile_w, tile_h = w // tiles_x, h // tiles_y
        step_x, step_y = ceil(tile_w / 32), ceil(tile_h / 32)
        icon_w, icon_h = tile_w // step_x, tile_h // step_y
        def get_pixel(x, y):
            offset = (int(x) + int(y) * w) * 4
            r, g, b, a = pixels[offset:offset + 4]
            return (r * a, g * a, b * a, a)  # Premultiplied alpha

        is_solid = True
        icon_ids = []
        pcoll = bpy.utils.previews.new()
        for tile_y in range(tiles_y - 1, -1, -1):
            for tile_x in range(tiles_x):
                x1 = int(tile_x * tile_w)
                y1 = int(tile_y * tile_h)
                x2 = x1 + tile_w
                y2 = y1 + tile_h
                first_pixel = get_pixel(x1, y1)
                icon_index = tile_y * tiles_x + tile_x
                icon_pixels = []

                for src_y in range(y1, y2, step_y):
                    for src_x in range(x1, x2, step_x):
                        pixel = get_pixel(src_x, src_y)
                        icon_pixels += pixel
                        if pixel != first_pixel:
                            is_solid = False

                # Size must be set before changing pixels, and passed as a tuple
                icon = pcoll.new(str(icon_index))
                icon.icon_size = (icon_w, icon_h)
                icon.icon_pixels_float = icon_pixels
                icon_ids.append(icon.icon_id)

        return Palette(name, (tiles_x, tiles_y), is_solid, pcoll, icon_ids)

def get_palette_from_material(mat):
    if mat and mat.use_nodes:
        image = next((node.image for node in mat.node_tree.nodes if node.type == 'TEX_IMAGE'), None)
        if image:
            return palettes.get(image.name)
    return None

def set_face_tile_uvs(face, uvs, tile):
    palette = tile.palette
    tiles_x, tiles_y = palette.dims
    tile_x = tile.index % tiles_x
    tile_y = tiles_y - tile.index // tiles_x - 1
    if palette.solid:
        x1 = x2 = (tile_x + 0.5) / tiles_x
        y1 = y2 = (tile_y + 0.5) / tiles_y
    else:
        x1 = (tile_x + 0.0) / tiles_x
        y1 = (tile_y + 0.0) / tiles_y
        x2 = (tile_x + 1.0) / tiles_x
        y2 = (tile_y + 1.0) / tiles_y

    if palette.solid or len(face.loop_indices) != 4:
        for loop_idx in face.loop_indices:
            uvs[loop_idx].uv[:] = (x1, y1)
    else:
        rotation = tile.rotation
        if rotation == -1:
            rotation = randrange(0, 4)
        uvs[face.loop_indices[(0 - rotation) % 4]].uv[:] = (x1, y1)
        uvs[face.loop_indices[(1 - rotation) % 4]].uv[:] = (x2, y1)
        uvs[face.loop_indices[(2 - rotation) % 4]].uv[:] = (x2, y2)
        uvs[face.loop_indices[(3 - rotation) % 4]].uv[:] = (x1, y2)

def get_tile(mesh, face, uv_layer_name):
    if face.material_index >= len(mesh.materials):
        # No such material
        return Tile.invalid

    palette = get_palette_from_material(mesh.materials[face.material_index])
    if not palette:
        # Not a palette material
        return Tile.invalid

    uv_layer = mesh.uv_layers.get(uv_layer_name)
    if not uv_layer:
        # Invalid UVs
        return Tile.invalid
    uvs = uv_layer.data

    tiles_x, tiles_y = palette.dims
    uv_avg = sum((uvs[loop_idx].uv for loop_idx in face.loop_indices), Vector((0.0, 0.0)))
    uv_avg /= len(face.loop_indices)
    tile_x = int(uv_avg.x * tiles_x)
    tile_y = int((1.0 - uv_avg.y) * tiles_y)
    tile_idx = tile_y * tiles_x + tile_x

    if len(face.loop_indices) == 4:
        uv0 = uvs[face.loop_indices[0]].uv
        if uv0.x < uv_avg.x and uv0.y < uv_avg.y:
            rotation = 0
        elif uv0.x > uv_avg.x and uv0.y < uv_avg.y:
            rotation = 1
        elif uv0.x > uv_avg.x and uv0.y > uv_avg.y:
            rotation = 2
        else:
            rotation = 3
    else:
        rotation = 0

    return Tile(palette, tile_idx, rotation)

def set_tile(mesh, face, tile, uv_layer_name):
    if not tile:
        return
    palette = tile.palette

    # Ensure material and UV state
    if not mesh.materials:
        mat = bpy.data.materials.new(name=palette.name)
        mesh.materials.append(mat)
    else:
        mat = mesh.materials[face.material_index]

    do_fill = False
    if get_palette_from_material(mat) != palette:
        # Convert the material to use this palette
        mat.use_nodes = True
        mat.node_tree.nodes.clear()
        nodes_palette.build(mat.node_tree, {'image': bpy.data.images.get(palette.name)})
        do_fill = True

    uv_layer = mesh.uv_layers.get(uv_layer_name)
    if not uv_layer:
        uv_layer = mesh.uv_layers.new(name=uv_layer_name)
        do_fill = True
    uv_layer.active = True
    uvs = uv_layer.data

    # Apply UVs
    if do_fill:
        for other_face in mesh.polygons:
            if other_face.material_index == face.material_index:
                set_face_tile_uvs(other_face, uvs, tile)
    set_face_tile_uvs(face, uvs, tile)

def get_ray_hit(context, mouse_x, mouse_y):
    coords2d = mouse_x, mouse_y
    view_vector = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, coords2d)
    ray_origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, coords2d)
    hit_dist = inf
    hit_obj = None

    for obj in context.scene.objects:
        if obj.type != 'MESH':
            continue
        # Move ray to object local space
        obj_to_world = obj.matrix_world
        world_to_obj = obj_to_world.inverted()
        ray_origin_obj = world_to_obj @ ray_origin
        view_vector_obj = world_to_obj.to_3x3() @ view_vector

        success, hit, normal, face_index = obj.ray_cast(ray_origin_obj, view_vector_obj)
        if success:
            # It's been determined that the object was hit, however face_index comes from the
            # evaluated object and it may not match up with the original mesh
            # Some modifiers like mirror are allowed since it's easy to find the original face
            disabled_modifiers = []
            for mod in obj.modifiers:
                if mod.show_viewport and mod.type in generative_modifier_types:
                    mod.show_viewport = False
                    disabled_modifiers.append(mod)
            if disabled_modifiers:
                # Generative modifiers found, raycast again while they're disabled
                success, hit, normal, face_index = obj.ray_cast(ray_origin_obj, view_vector_obj)
                for mod in disabled_modifiers:
                    mod.show_viewport = True
        if success:
            hit_world = obj_to_world @ hit
            dist = (hit_world - ray_origin).length_squared
            if dist < hit_dist:
                hit_dist = dist
                hit_obj = obj
                hit_face_idx = face_index
                hit_local = hit
    if not hit_obj:
        return None, -1, 0

    hit_obj = hit_obj.original
    mesh = hit_obj.data
    hit_face_idx %= len(mesh.polygons)  # Mirrors and arrays (without caps) multiply the polycount

    # Find out which quadrant of the face was hit
    face = mesh.polygons[hit_face_idx]
    v0 = mesh.vertices[mesh.loops[face.loop_indices[0]].vertex_index].co
    v1 = mesh.vertices[mesh.loops[face.loop_indices[1]].vertex_index].co
    v_north = face.center - ((v1 - v0) * 0.5 + v0)
    v_north.normalize()
    v_east = v_north.cross(face.normal)
    v = hit_local - face.center
    x = v.dot(v_east)
    y = v.dot(v_north)
    quadrant = round(atan2(y, -x) / pi * 0.5) - 1
    return hit_obj, face, quadrant

def palette_items(self, context):
    items = []
    for image in bpy.data.images:
        if re.search(r"(\d+)x(\d+)", image.name):
            items.append((image.name, image.name, "", image.preview.icon_id, len(items)))
    if not items:
        items.append(("NONE", "None", ""))
    return items

class GRET_OT_palette_draw(bpy.types.Operator):
    bl_idname = 'gret.palette_draw'
    bl_label = "Paint Face"
    bl_options = {'INTERNAL', 'UNDO'}

    palette: bpy.props.EnumProperty(
        name="Palette",
        description="Selects the palette used to paint",
        items=palette_items,
    )
    uv_layer_name: bpy.props.StringProperty(
        name="UV Layer",
        description="Name of the target UV layer. Can change the default in addon preferences",
        default="UVMap",
    )
    index: bpy.props.IntProperty(
        options={'HIDDEN'},
    )
    mode: bpy.props.EnumProperty(
        name="Mode",
        description="Tool mode",
        items = (
            ('DRAW', "Paint", "Paint face"),
            ('SAMPLE', "Sample", "Sample tile"),
            ('FILL', "Fill", "Paint floodfill"),
            ('REPLACE', "Replace", "Replace faces with the same paint"),
        ),
        default='DRAW',
    )

    def do_draw(self, context, mesh, face, rotation=0):
        new_tile = Tile(palettes.get(self.palette), self.index, rotation)
        if not new_tile:
            return
        set_tile(mesh, face, new_tile, self.uv_layer_name)

    def do_sample(self, context, mesh, face):
        tile = get_tile(mesh, face, self.uv_layer_name)
        if not tile:
            return

        tool = context.workspace.tools.get(tool_id)
        if tool:
            props = tool.operator_properties(GRET_OT_palette_draw.bl_idname)
            props.palette = tile.palette.name
            props.index = tile.index

    def do_fill(self, context, mesh, face):
        new_tile = Tile(palettes.get(self.palette), self.index, -1)
        if not new_tile:
            return

        delimit = ({'UV', 'MATERIAL', 'SEAM', 'SHARP'} if new_tile.palette.solid
            else {'MATERIAL', 'SEAM', 'SHARP'})
        bpy.ops.object.editmode_toggle()
        bpy.ops.mesh.select_mode(type='FACE')
        bpy.ops.mesh.select_all(action='DESELECT')
        index = len(mesh.vertices) + len(mesh.edges) + face.index
        bpy.ops.mesh.select_linked_pick(deselect=False, delimit=delimit, index=index, object_index=0)
        bpy.ops.object.editmode_toggle()
        for face in mesh.polygons:
            if face.select:
                set_tile(mesh, face, new_tile, self.uv_layer_name)

    def do_replace(self, context, mesh, face):
        tile = get_tile(mesh, face, self.uv_layer_name)
        new_tile = Tile(palettes.get(self.palette), self.index, -1)
        if not tile or not new_tile:
            return

        for other_face in mesh.polygons:
            other_tile = get_tile(mesh, other_face, self.uv_layer_name)
            if tile.palette == other_tile.palette and tile.index == other_tile.index:
                set_tile(mesh, other_face, new_tile, self.uv_layer_name)

    def invoke(self, context, event):
        # Make sure user can see the result
        shading = context.space_data.shading
        if shading.type == 'SOLID':
            shading.color_type = 'TEXTURE'

        obj, hit_face, quadrant = get_ray_hit(context, event.mouse_region_x, event.mouse_region_y)
        if not obj:
            return {'CANCELLED'}
        select_only(context, obj)

        if self.mode == 'DRAW':
            self.do_draw(context, obj.data, hit_face, quadrant)
        elif self.mode == 'SAMPLE':
            self.do_sample(context, obj.data, hit_face)
        elif self.mode == 'FILL':
            self.do_fill(context, obj.data, hit_face)
        elif self.mode == 'REPLACE':
            self.do_replace(context, obj.data, hit_face)
        return {'FINISHED'}

class GRET_OT_palette_new(bpy.types.Operator):
    #tooltip
    """Creates a new palette material. Tile size is taken from the filename, e.g. Palette8x8.png"""

    bl_idname = 'gret.palette_new'
    bl_label = "New Palette"
    bl_options = {'INTERNAL', 'UNDO'}

    filter_glob: bpy.props.StringProperty(
        default="*.png",
        options={'HIDDEN'}
    )
    filepath: bpy.props.StringProperty(
        name="Image Path",
        description="Path to the palette image",
        subtype='FILE_PATH',
    )
    filename: bpy.props.StringProperty(
        options={'HIDDEN'},
    )

    def execute(self, context):
        try:
            image = bpy.data.images.load(self.filepath, check_existing=True)
            image.reload()
        except RuntimeError:
            self.report({'ERROR'}, "Couldn't load image.")
            return {'CANCELLED'}

        palette = Palette.from_image(image)
        if not palette:
            self.report({'ERROR'}, "Filename must specify the tile size, for example Palette8x8.png")
            return {'CANCELLED'}

        palettes[image.name] = palette
        return {'FINISHED'}

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

class GRET_OT_palette_reload(bpy.types.Operator):
    #tooltip
    """Reload palette images from disk"""

    bl_idname = 'gret.palette_reload'
    bl_label = "Reload Palettes"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        for name in palettes.keys():
            image = bpy.data.images.get(name)
            if image:
                image.reload()
        clear_palettes()
        return {'FINISHED'}

class GRET_OT_palette_select(bpy.types.Operator):
    #tooltip
    """Selects the current tile"""

    bl_idname = 'gret.palette_select'
    bl_label = "Select Tile"
    bl_options = {'INTERNAL'}

    index: bpy.props.IntProperty()

    def execute(self, context):
        tool = context.workspace.tools.get(tool_id)
        if not tool:
            return {'CANCELLED'}

        props = tool.operator_properties(GRET_OT_palette_draw.bl_idname)
        props.index = self.index
        return {'FINISHED'}

@ToolDef.from_fn
def tool_paint():
    def draw_settings(context, layout, tool):
        props = tool.operator_properties(GRET_OT_palette_draw.bl_idname)
        name = props.palette
        image = bpy.data.images.get(name)
        palette = palettes.get(name)
        if not palette:
            palette = Palette.from_image(image)
            if palette:
                palettes[name] = palette

        col = layout.column(align=False)
        row = col.row(align=True)
        sub = row.split(align=True)
        sub.prop(props, 'palette', text="")
        sub.enabled = image is not None
        row.operator('gret.palette_reload', icon='FILE_REFRESH', text="")
        row.operator('gret.palette_new', icon='ADD', text="")
        col.prop(props, 'uv_layer_name', icon='UV', text="")
        if not palette:
            return

        # Draw tile grid
        tiles_x, tiles_y = palette.dims
        layout.separator()
        col = layout.column(align=True)
        scale = 1.2
        col.scale_x = scale
        for tile_idx, icon_id in enumerate(palette.icon_ids):
            if tile_idx % tiles_x == 0:
                row = col.row(align=True)
                row.scale_y = scale
                row.alignment = 'CENTER'
            selected = (tile_idx == props.index)
            op = row.operator('gret.palette_select', text="", depress=selected, icon_value=icon_id)
            op.index = tile_idx

    return dict(
        idname=tool_id,
        label="Palette Paint",
        description="""Paint faces using the palette selected in the Active Tool panel.
\u2022 Left click to paint.
\u2022 Ctrl+Left to sample.
\u2022 Shift+Left to fill.
\u2022 Shift+Ctrl+Left to replace similar""",
        icon="brush.paint_texture.draw",
        widget=None,
        cursor='PAINT_BRUSH',
        keymap=km_tool_paint_name,
        draw_settings=draw_settings,
    )

km_tool_paint = (
    km_tool_paint_name,
    {"space_type": 'VIEW_3D', "region_type": 'WINDOW'},
    {"items": [
        (
            GRET_OT_palette_draw.bl_idname,
            {"type": 'LEFTMOUSE', "value": 'PRESS'},
            None
        ),
        (
            GRET_OT_palette_draw.bl_idname,
            {"type": 'LEFTMOUSE', "value": 'PRESS', "ctrl": True},
            {"properties": [("mode", 'SAMPLE')]}
        ),
        (
            GRET_OT_palette_draw.bl_idname,
            {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True},
            {"properties": [("mode", 'FILL')]}
        ),
        (
            GRET_OT_palette_draw.bl_idname,
            {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True, "ctrl": True},
            {"properties": [("mode", 'REPLACE')]}
        ),
    ]},
)
km_tool_paint_empty = keymap_view3d_empty(km_tool_paint_name)

def register_keymaps():
    keyconfigs = bpy.context.window_manager.keyconfigs
    kc_defaultconf = keyconfigs.default
    kc_addonconf = keyconfigs.addon

    keyconfig_init_from_data(kc_defaultconf, [km_tool_paint_empty])
    keyconfig_init_from_data(kc_addonconf, [km_tool_paint])

def unregister_keymaps():
    keyconfigs = bpy.context.window_manager.keyconfigs
    defaultmap = keyconfigs.get('blender').keymaps
    addonmap = keyconfigs.get('blender addon').keymaps

    for km_name, km_args, km_content in [km_tool_paint]:
        keymap = addonmap.find(km_name, **km_args)
        keymap_items = keymap.keymap_items
        for item in km_content['items']:
            item_id = keymap_items.find(item[0])
            if item_id != -1:
                keymap_items.remove(keymap_items[item_id])
        addonmap.remove(keymap)
        defaultmap.remove(defaultmap.find(km_name, **km_args))

def clear_palettes():
    for palette in palettes.values():
        bpy.utils.previews.remove(palette.pcoll)
    palettes.clear()

classes = (
    GRET_OT_palette_draw,
    GRET_OT_palette_new,
    GRET_OT_palette_reload,
    GRET_OT_palette_select,
)

def register(settings):
    # Don't know a better way of changing the default
    GRET_OT_palette_draw.__annotations__['uv_layer_name'][1]['default'] = prefs.palette_uv_layer_name

    for cls in classes:
        bpy.utils.register_class(cls)

    get_tools_from_space_and_mode('VIEW_3D', 'OBJECT').extend((None, tool_paint))
    register_keymaps()
    clear_palettes()

def unregister():
    clear_palettes()
    unregister_keymaps()
    remove_subsequence(get_tools_from_space_and_mode('VIEW_3D', 'OBJECT'), (None, tool_paint))

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
