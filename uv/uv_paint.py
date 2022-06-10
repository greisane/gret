from bpy_extras import view3d_utils
from collections import namedtuple
from math import inf, atan2, pi
from mathutils import Vector
from random import randrange
import bpy

from .. import prefs
from ..helpers import select_only
from ..material.helpers import Node, get_material, set_material
from ..math import SMALL_NUMBER, equals, calc_bounds_2d, calc_center_2d
from .uv_sheet import GRET_PT_uv_sheet_presets

# TODO:
# - paint flipped uvs?
# - gravity paint?
# - trim alignment
# - paint hold lmb to paint multiple faces? or to change rotation?
# - paint hold shift lmb to slide texture?

generative_modifier_types = {'MULTIRES', 'BEVEL', 'BOOLEAN', 'BUILD', 'DECIMATE', 'NODES', 'MASK',
    'REMESH', 'SCREW', 'SKIN', 'SOLIDIFY', 'SUBSURF', 'TRIANGULATE', 'WIREFRAME'}

simple_nodes = (Node('OutputMaterial')
.link('Surface', None,
    Node('BsdfDiffuse')
    .set('Roughness', 1.0)
    .link('Color', 0,
        Node('TexImage', image_eval='image', interpolation='Closest', show_texture=True)
    )
))

is_color_none = lambda c: c[0] == 0.0 and c[1] == 0.0 and c[2] == 0.0 and c[3] == 0.0

class Quad(namedtuple("Quad", ["uv_sheet", "x0", "y0", "x1", "y1", "rotation"])):
    @classmethod
    def from_uv_sheet(cls, uv_sheet, rotation=-1):
        if not uv_sheet:
            return cls.invalid
        if uv_sheet.use_custom_region:
            return cls(uv_sheet, *uv_sheet.custom_region.v0, *uv_sheet.custom_region.v1, rotation)
        elif uv_sheet.active_index >= 0 and uv_sheet.active_index < len(uv_sheet.regions):
            region = uv_sheet.regions[uv_sheet.active_index]
            if uv_sheet.use_palette_uv and not is_color_none(region.color):
                cx, cy = (region.v0[0] + region.v1[0]) * 0.5, (region.v0[1] + region.v1[1]) * 0.5
                return cls(uv_sheet, cx, cy, cx, cy, rotation)
            else:
                return cls(uv_sheet, *region.v0, *region.v1, rotation)
        return cls.invalid

    def to_uv_sheet(self, uv_sheet):
        eq = equals
        for region_idx, region in enumerate(uv_sheet.regions):
            x0, y0, x1, y1 = *region.v0, *region.v1
            # Check if corners match, or if they're all inside in case of a solid color region
            if ((eq(self.x0, x0) and eq(self.y0, y0) and eq(self.x1, x1) and eq(self.y1, y1)) or
                (uv_sheet.use_palette_uv and not is_color_none(region.color) and
                self.x0 >= x0 and self.y0 >= y0 and self.x1 <= x1 and self.y1 <= y1)):
                uv_sheet.use_custom_region = False
                uv_sheet.active_index = region_idx
                return
        uv_sheet.use_custom_region = True
        uv_sheet.custom_region.v0 = self.x0, self.y0
        uv_sheet.custom_region.v1 = self.x1, self.y1

    def region_equals(self, other):
        return (self.uv_sheet == other.uv_sheet
            and equals(self.x0, other.x0) and equals(self.y0, other.y0)
            and equals(self.x1, other.x1) and equals(self.y1, other.y1))

    def with_rotation(self, rotation):
        return Quad(self.uv_sheet, self.x0, self.y0, self.x1, self.y1, rotation)

    def __bool__(self):
        return self.uv_sheet is not None

Quad.invalid = Quad(None, 0.0, 0.0, 0.0, 0.0, -1)

def get_uv_sheet_from_material(mat):
    if mat and mat.use_nodes:
        # Find the "active" image node that will be visible in viewport texture mode
        for node in mat.node_tree.nodes:
            if node.show_texture and node.type == 'TEX_IMAGE':
                return node.image.uv_sheet
    return None

def set_face_uvs(face, uvs, quad):
    if len(face.loop_indices) != 4:
        for loop_idx in face.loop_indices:
            uvs[loop_idx].uv[:] = (quad.x0, quad.y0)
    else:
        rotation = quad.rotation
        if rotation == -1:
            rotation = randrange(0, 4)
        uvs[face.loop_indices[(0 - rotation) % 4]].uv[:] = (quad.x0, quad.y0)
        uvs[face.loop_indices[(1 - rotation) % 4]].uv[:] = (quad.x1, quad.y0)
        uvs[face.loop_indices[(2 - rotation) % 4]].uv[:] = (quad.x1, quad.y1)
        uvs[face.loop_indices[(3 - rotation) % 4]].uv[:] = (quad.x0, quad.y1)

def get_quad(obj, face, uv_layer_name):
    mesh = obj.data

    if face.material_index >= len(obj.material_slots):
        # No such material
        return Quad.invalid

    uv_sheet = get_uv_sheet_from_material(get_material(obj, face.material_index))
    if not uv_sheet:
        # Not a uv_sheet material
        return Quad.invalid

    uv_layer = mesh.uv_layers.get(uv_layer_name) if uv_layer_name else mesh.uv_layers.active
    if not uv_layer:
        # Invalid UVs
        return Quad.invalid
    uvs = uv_layer.data

    points = [uvs[loop_idx].uv for loop_idx in face.loop_indices]
    uv_avg = calc_center_2d(points)
    uv_min, uv_max, axis = calc_bounds_2d(points)

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

    return Quad(uv_sheet, *uv_min, *uv_max, rotation)

def set_quad(obj, face, quad, uv_layer_name):
    if not quad:
        return
    uv_sheet = quad.uv_sheet
    mesh = obj.data

    # Ensure material and UV state
    mat = get_material(obj, face.material_index)
    if not mat:
        mat_name = uv_sheet.id_data.name
        mat = bpy.data.materials.get(mat_name) or bpy.data.materials.new(name=mat_name)
        set_material(obj, face.material_index, mat)

    do_fill = False
    if get_uv_sheet_from_material(mat) != uv_sheet:
        # Convert the material to use this UV sheet
        mat.use_nodes = True
        mat.node_tree.nodes.clear()
        simple_nodes.build(mat.node_tree, {'image': uv_sheet.id_data})
        do_fill = True

    uv_layer = mesh.uv_layers.get(uv_layer_name) if uv_layer_name else mesh.uv_layers.active
    if not uv_layer:
        uv_layer = mesh.uv_layers.new(name=uv_layer_name)
        do_fill = True
    uv_layer.active = True
    uv_layer.active_render = True
    uvs = uv_layer.data

    # Apply UVs
    if do_fill:
        for other_face in mesh.polygons:
            if other_face.material_index == face.material_index:
                set_face_uvs(other_face, uvs, quad)
    set_face_uvs(face, uvs, quad)

def get_ray_hit(context, mouse_x, mouse_y):
    coords2d = mouse_x, mouse_y
    view_vector = view3d_utils.region_2d_to_vector_3d(context.region, context.region_data, coords2d)
    ray_origin = view3d_utils.region_2d_to_origin_3d(context.region, context.region_data, coords2d)
    hit_dist = inf
    hit_obj = None

    for obj in context.scene.objects:
        if obj.type != 'MESH' or not obj.visible_get():
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
    quadrant = (round(atan2(y, -x) / (pi * 0.5)) + 1) % 4
    return hit_obj, face, quadrant

class GRET_OT_uv_paint(bpy.types.Operator):
    bl_idname = 'gret.uv_paint'
    bl_label = "UV Paint"
    bl_options = {'INTERNAL', 'UNDO'}

    image: bpy.props.StringProperty(
        name="Image",
        description="Select tileset or trim sheet image",
    )
    uv_layer_name: bpy.props.StringProperty(
        name="UV Layer",
        description="""UV layer to paint to. Leave empty to use the active UV layer.
Defaults can be changed in addon preferences""",
        default="",
    )
    mode: bpy.props.EnumProperty(
        name="Mode",
        description="Tool mode",
        items = (
            ('DRAW', "Paint", "Paint face"),
            ('SAMPLE', "Sample", "Sample UVs"),
            ('FILL', "Fill", "Paint floodfill"),
            ('REPLACE', "Replace", "Replace faces with the same UVs"),
        ),
        default='DRAW',
    )
    delimit: bpy.props.EnumProperty(
        name="Fill Mode",
        description="Delimit fill region",
        items = (
            # ('NORMAL', "Normal", "Delimit by face directions"),
            ('MATERIAL', "Material", "Delimit by material"),
            ('SEAM', "Seam", "Delimit by edge seams"),
            ('SHARP', "Sharp", "Delimit by sharp edges"),
            ('UV', "UVs", "Delimit by UV coordinates"),
        ),
        options={'ENUM_FLAG'},
        default={'MATERIAL', 'SEAM', 'SHARP'},
    )
    random: bpy.props.BoolProperty(
        name="Random Fill",
        description="Select a random direction while filling",
        default=False,
    )

    @property
    def uv_sheet(self):
        image = bpy.data.images.get(self.image)
        return image.uv_sheet if image else None

    def do_draw(self, context, obj, face, rotation):
        new_quad = Quad.from_uv_sheet(self.uv_sheet, rotation)
        if not new_quad:
            return
        set_quad(obj, face, new_quad, self.uv_layer_name)

    def do_sample(self, context, obj, face):
        quad = get_quad(obj, face, self.uv_layer_name)
        if not quad:
            return

        tool = context.workspace.tools.get(GRET_TT_uv_paint.bl_idname)
        if tool:
            props = tool.operator_properties(GRET_OT_uv_paint.bl_idname)
            props.image = quad.uv_sheet.id_data.name
            quad.to_uv_sheet(quad.uv_sheet)

    def do_fill(self, context, obj, face, rotation):
        new_quad = Quad.from_uv_sheet(self.uv_sheet, rotation)
        if not new_quad:
            return
        mesh = obj.data

        bpy.ops.object.editmode_toggle()
        bpy.ops.mesh.select_mode(type='FACE')
        bpy.ops.mesh.select_all(action='DESELECT')
        index = len(mesh.vertices) + len(mesh.edges) + face.index
        bpy.ops.mesh.select_linked_pick(deselect=False, delimit=self.delimit,
            index=index, object_index=0)
        bpy.ops.object.editmode_toggle()
        for face in mesh.polygons:
            if face.select:
                set_quad(obj, face, new_quad, self.uv_layer_name)

    def do_replace(self, context, obj, face):
        quad = get_quad(obj, face, self.uv_layer_name)
        new_quad = Quad.from_uv_sheet(self.uv_sheet, -1)
        if not quad or not new_quad:
            return

        for other_face in obj.data.polygons:
            other_quad = get_quad(obj, other_face, self.uv_layer_name)
            if quad.region_equals(other_quad):
                new_other_quad = new_quad.with_rotation(other_quad.rotation)
                set_quad(obj, other_face, new_other_quad, self.uv_layer_name)

    def invoke(self, context, event):
        image = bpy.data.images.get(self.image)
        if not image and self.mode != 'SAMPLE':
            self.report({'WARNING'}, "No image to paint with, select one in the Tool tab.")
            return {'CANCELLED'}

        # Make sure user can see the result
        if context.space_data.shading.type == 'SOLID':
            context.space_data.shading.color_type = 'TEXTURE'

        obj, hit_face, quadrant = get_ray_hit(context, event.mouse_region_x, event.mouse_region_y)
        if not obj:
            return {'CANCELLED'}

        select_only(context, obj)

        if self.mode == 'DRAW':
            self.do_draw(context, obj, hit_face, quadrant)
        elif self.mode == 'SAMPLE':
            self.do_sample(context, obj, hit_face)
        elif self.mode == 'FILL':
            self.do_fill(context, obj, hit_face, -1 if self.random else quadrant)
        elif self.mode == 'REPLACE':
            self.do_replace(context, obj, hit_face)

        return {'FINISHED'}

class GRET_TT_uv_paint(bpy.types.WorkSpaceTool):
    bl_space_type = 'VIEW_3D'
    bl_context_mode = 'OBJECT'

    bl_idname = "gret.uv_paint"
    bl_label = "UV Paint"
    bl_description = """Assign UVs from a previously configured tileset or trim sheet.
\u2022 Click on mesh faces to paint.
\u2022 Ctrl+Click to sample.
\u2022 Shift+Click to fill.
\u2022 Shift+Ctrl+Click to replace similar"""
    bl_icon = "brush.paint_texture.draw"
    bl_widget = "GRET_GGT_uv_picker_gizmo_group"
    bl_cursor = 'PAINT_BRUSH'
    bl_keymap = (
        (
            GRET_OT_uv_paint.bl_idname,
            {"type": 'LEFTMOUSE', "value": 'PRESS'},
            None,
        ),
        (
            GRET_OT_uv_paint.bl_idname,
            {"type": 'LEFTMOUSE', "value": 'PRESS', "ctrl": True},
            {"properties": [("mode", 'SAMPLE')]},
        ),
        (
            GRET_OT_uv_paint.bl_idname,
            {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True},
            {"properties": [("mode", 'FILL')]},
        ),
        (
            GRET_OT_uv_paint.bl_idname,
            {"type": 'LEFTMOUSE', "value": 'PRESS', "shift": True, "ctrl": True},
            {"properties": [("mode", 'REPLACE')]},
        ),
    )

    def draw_settings(context, layout, tool):
        props = tool.operator_properties(GRET_OT_uv_paint.bl_idname)
        if not props.uv_layer_name and prefs.uv_paint__layer_name:
            props.uv_layer_name = prefs.uv_paint__layer_name
        image = bpy.data.images.get(props.image)

        col = layout.column(align=False)
        col.use_property_split = True
        col.use_property_decorate = False
        row = col.row(align=True)
        row.prop_search(props, "image", bpy.data, "images", text="")
        row.operator('image.reload', icon='FILE_REFRESH', text="")  # TODO this reloads what?
        row.operator('image.open', icon='ADD', text="")

        col.separator()
        if not image:
            col.label(text="No image selected.")
            return
        has_uv_sheet = bool(image.uv_sheet.regions)
        if not has_uv_sheet:
            col.label(text="No UV sheet defined.")
        else:
            col.prop(props, 'uv_layer_name', icon='UV')
            col.prop(props, 'delimit')
            col.prop(props, 'random', icon='FORCE_VORTEX', text="Random")
        col.separator()

        col = layout.column(align=False)
        col.alert = not has_uv_sheet
        text = "Edit UV Sheet" if image.uv_sheet.regions else "Create UV Sheet"
        row = col.row(align=True)
        op = row.operator('gret.uv_sheet_edit', icon='MESH_GRID', text=text)
        op.image = image.name
        row.popover(panel=GRET_PT_uv_sheet_presets.__name__, icon='PRESET', text="")
        if has_uv_sheet:
            col.prop(image.uv_sheet, 'use_palette_uv')

classes = (
    GRET_OT_uv_paint,
)

def register(settings):
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.utils.register_tool(GRET_TT_uv_paint, separator=True)

def unregister():
    bpy.utils.unregister_tool(GRET_TT_uv_paint)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
