from bpy_extras import view3d_utils
from collections import namedtuple
from gpu_extras.batch import batch_for_shader
from math import inf, atan2, pi
from mathutils import Vector, Matrix
from random import randrange
import bpy
import gpu

from .. import prefs
from ..drawing import UVSheetTheme, draw_image, draw_box, draw_box_fill
from ..helpers import select_only
from ..material.helpers import Node
from ..math import Rect, SMALL_NUMBER

generative_modifier_types = {'MULTIRES', 'BEVEL', 'BOOLEAN', 'BUILD', 'DECIMATE', 'NODES', 'MASK',
    'REMESH', 'SCREW', 'SKIN', 'SOLIDIFY', 'SUBSURF', 'TRIANGULATE', 'WIREFRAME'}
rect_texcoords = (0, 0), (1, 0), (1, 1), (0, 1)
theme = UVSheetTheme()

simple_nodes = (Node('OutputMaterial')
.link('Surface', None,
    Node('BsdfDiffuse')
    .set('Roughness', 1.0)
    .link('Color', 0,
        Node('TexImage', image_eval='image', interpolation='Closest')
    )
))

Quad = namedtuple('Quad', ['uv_sheet', 'region_index', 'rotation'])
Quad.__bool__ = lambda self: self.uv_sheet is not None
Quad.invalid = Quad(None, -1, 0)

def get_uv_sheet_from_material(mat):
    if mat and mat.use_nodes:
        # Find the "active" image node that is also displayed in viewport texture mode
        for node in mat.node_tree.nodes:
            if node.show_texture and node.type == 'TEX_IMAGE':
                return node.image.uv_sheet
    return None

def set_face_uvs(face, uvs, quad):
    uv_sheet = quad.uv_sheet

    region = uv_sheet.regions[quad.region_index]
    x0, y0, x1, y1 = *region.v0, *region.v1

    if region.solid or len(face.loop_indices) != 4:
        for loop_idx in face.loop_indices:
            uvs[loop_idx].uv[:] = (x0, y0)
    else:
        rotation = quad.rotation
        if rotation == -1:
            rotation = randrange(0, 4)
        uvs[face.loop_indices[(0 - rotation) % 4]].uv[:] = (x0, y0)
        uvs[face.loop_indices[(1 - rotation) % 4]].uv[:] = (x1, y0)
        uvs[face.loop_indices[(2 - rotation) % 4]].uv[:] = (x1, y1)
        uvs[face.loop_indices[(3 - rotation) % 4]].uv[:] = (x0, y1)

def get_quad(mesh, face, uv_layer_name):
    # TODO respect object materials
    if face.material_index >= len(mesh.materials):
        # No such material
        return Quad.invalid

    uv_sheet = get_uv_sheet_from_material(mesh.materials[face.material_index])
    if not uv_sheet:
        # Not a uv_sheet material
        return Quad.invalid

    uv_layer = mesh.uv_layers.get(uv_layer_name)
    if not uv_layer:
        # Invalid UVs
        return Quad.invalid
    uvs = uv_layer.data

    uv_avg = sum((uvs[loop_idx].uv for loop_idx in face.loop_indices), Vector((0.0, 0.0)))
    uv_avg /= len(face.loop_indices)
    region_index = -1
    for region_idx, region in enumerate(uv_sheet.regions):
        cx = region.v0[0] + (region.v1[0] - region.v0[0]) * 0.5
        cy = region.v0[1] + (region.v1[1] - region.v0[1]) * 0.5
        if abs(cx - uv_avg[0]) <= SMALL_NUMBER and abs(cy - uv_avg[1]) <= SMALL_NUMBER:
            region_index = region_idx
            break

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

    return Quad(uv_sheet, region_index, rotation)

def set_quad(mesh, face, quad, uv_layer_name):
    if not quad:
        return
    uv_sheet = quad.uv_sheet

    # Ensure material and UV state
    if not mesh.materials:
        mat = bpy.data.materials.new(name=uv_sheet.id_data.name)
        mesh.materials.append(mat)
    elif mesh.materials:
        mat = mesh.materials[face.material_index]

    do_fill = False
    if get_uv_sheet_from_material(mat) != uv_sheet:
        # Convert the material to use this uv_sheet
        mat.use_nodes = True
        mat.node_tree.nodes.clear()
        simple_nodes.build(mat.node_tree, {'image': uv_sheet.id_data})
        do_fill = True

    uv_layer = mesh.uv_layers.get(uv_layer_name)
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
    bl_idname = 'gret.uv_sheet_draw'
    bl_label = "Paint Face"
    bl_options = {'INTERNAL', 'UNDO'}

    image: bpy.props.StringProperty(
        name="Image",
        description="Select tileset or trim sheet image",
    )
    uv_layer_name: bpy.props.StringProperty(
        name="UV Layer",
        description="Target UV layer name. Defaults can be changed in addon preferences",
        default="",
    )
    mode: bpy.props.EnumProperty(
        name="Mode",
        description="Tool mode",
        items = (
            ('DRAW', "Paint", "Paint face"),
            ('SAMPLE', "Sample", "Sample quad"),
            ('FILL', "Fill", "Paint floodfill"),
            ('REPLACE', "Replace", "Replace faces with the same quad"),
        ),
        default='DRAW',
    )
    delimit: bpy.props.EnumProperty(
        name="Fill Mode",
        description="Delimit fill region",
        items = (
            ('NORMAL', "Normal", "Delimit by face directions"),
            ('MATERIAL', "Material", "Delimit by material"),
            ('SEAM', "Seam", "Delimit by edge seams"),
            ('SHARP', "Sharp", "Delimit by sharp edges"),
            ('UV', "UVs", "Delimit by UV coordinates"),
        ),
        options={'ENUM_FLAG'},
        default={'MATERIAL', 'SEAM', 'SHARP', 'UV'},
    )

    @property
    def uv_sheet(self):
        image = bpy.data.images.get(self.image)
        return image.uv_sheet if image else None

    def do_draw(self, context, mesh, face, rotation=0):
        new_quad = Quad(self.uv_sheet, self.uv_sheet.active_index, rotation)
        if not new_quad:
            return
        set_quad(mesh, face, new_quad, self.uv_layer_name)

    def do_sample(self, context, mesh, face):
        quad = get_quad(mesh, face, self.uv_layer_name)
        if not quad:
            return

        tool = context.workspace.tools.get(GRET_TT_uv_paint.bl_idname)
        if tool:
            props = tool.operator_properties(GRET_OT_uv_paint.bl_idname)
            props.image = quad.uv_sheet.id_data.name
            quad.uv_sheet.active_index = quad.region_index

    def do_fill(self, context, mesh, face):
        new_quad = Quad(self.uv_sheet, self.uv_sheet.active_index, -1)
        if not new_quad:
            return

        bpy.ops.object.editmode_toggle()
        bpy.ops.mesh.select_mode(type='FACE')
        bpy.ops.mesh.select_all(action='DESELECT')
        index = len(mesh.vertices) + len(mesh.edges) + face.index
        bpy.ops.mesh.select_linked_pick(deselect=False, delimit=self.delimit,
            index=index, object_index=0)
        bpy.ops.object.editmode_toggle()
        for face in mesh.polygons:
            if face.select:
                set_quad(mesh, face, new_quad, self.uv_layer_name)

    def do_replace(self, context, mesh, face):
        quad = get_quad(mesh, face, self.uv_layer_name)
        new_quad = Quad(self.uv_sheet, self.uv_sheet.active_index, -1)
        if not quad or not new_quad:
            return

        for other_face in mesh.polygons:
            other_quad = get_quad(mesh, other_face, self.uv_layer_name)
            if quad.uv_sheet == other_quad.uv_sheet and quad.region_index == other_quad.region_index:
                set_quad(mesh, other_face, new_quad, self.uv_layer_name)

    def invoke(self, context, event):
        # Make sure user can see the result
        if context.space_data.shading.type == 'SOLID':
            context.space_data.shading.color_type = 'TEXTURE'

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

class GRET_GT_uv_sheet_gizmo(bpy.types.Gizmo):
    active_area = None
    __slots__ = (
        "region_index",
    )

    @staticmethod
    def get_active_image_info(context):
        tool = context.workspace.tools.get(GRET_TT_uv_paint.bl_idname)
        if tool:
            props = tool.operator_properties(GRET_OT_uv_paint.bl_idname)
            im = bpy.data.images.get(props.image)
            if im:
                return im, im.uv_sheet, im.size[0]/ max(im.size), im.size[1] / max(im.size)
        return None, None, 0, 0

    def get_region_index_at(self, context, x, y):
        image, uv_sheet, w, h = self.get_active_image_info(context)
        if not image:
            return None, None

        x, y, _ = self.matrix_world.inverted() @ Vector((x, y, 0))
        x /= w
        y /= h

        if x >= 0.0 and y >= 0.0 and x < 1.0 and y < 1.0:
            # Find index of hovered region
            for region_idx, region in enumerate(uv_sheet.regions):
                if region.v0[0] < x < region.v1[0] and region.v0[1] < y < region.v1[1]:
                    return uv_sheet, region_idx
            return uv_sheet, -1
        return uv_sheet, None

    def draw(self, context):
        cls = __class__
        if not cls.active_area or context.area != cls.active_area:
            # Only draw the picker in the active area
            return
        image, uv_sheet, w, h = self.get_active_image_info(context)
        if not image:
            return
        texture = gpu.texture.from_image(image)

        with gpu.matrix.push_pop():
            gpu.matrix.multiply_matrix(self.matrix_world)
            gpu.matrix.multiply_matrix(Matrix.Diagonal((w, h, 1.0, 1.0)))

            draw_box(0, 0, 1, 1, theme.border)
            draw_image(0, 0, 1, 1, image, nearest=True)
            draw_box_fill(0, 0, 1, 1, (0, 0, 0, 0.3))  # Darken (image shader doesn't support tint)

            for region in uv_sheet.regions:
                draw_box(*region.v0, *region.v1, theme.unselected)

            if self.region_index >= 0 and self.region_index < len(uv_sheet.regions):
                region = uv_sheet.regions[self.region_index]
                draw_box(*region.v0, *region.v1, theme.hovered)

            if uv_sheet.active_index >= 0 and uv_sheet.active_index < len(uv_sheet.regions):
                region = uv_sheet.regions[uv_sheet.active_index]
                draw_box(*region.v0, *region.v1, theme.selected, width=2.0)

    def test_select(self, context, location):
        # Only called for the currently hovered viewport
        cls = __class__
        if cls.active_area:
            # Force a redraw on the area we're leaving so the picker disappears
            cls.active_area.tag_redraw()
        cls.active_area = context.area

        uv_sheet, result = self.get_region_index_at(context, *location)
        if result is not None:
            self.region_index = result
            return 0

        self.region_index = -1
        return -1

    def setup(self):
        self.region_index = -1

    def invoke(self, context, event):
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            uv_sheet, result = self.get_region_index_at(context, event.mouse_region_x, event.mouse_region_y)
            if result is not None:
                uv_sheet.active_index = result
                return {'RUNNING_MODAL'}
        return {'FINISHED'}

    def exit(self, context, cancel):
        pass

    def modal(self, context, event, tweak):
        return {'RUNNING_MODAL'}

class GRET_GGT_uv_sheet_gizmo_group(bpy.types.GizmoGroup):
    bl_label = "Gizmo Group"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'PERSISTENT', 'SCALE'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def set_position_and_size(self, x, y, width, height):
        self.gizmo.matrix_basis[0][3], self.gizmo.matrix_basis[1][3] = x, y
        self.gizmo.matrix_basis[0][0], self.gizmo.matrix_basis[1][1] = width, height

    def setup(self, context):
        self.gizmo = self.gizmos.new(GRET_GT_uv_sheet_gizmo.__name__)
        # self.gizmo.use_event_handle_all = True  # Swallow all events while hovered
        self.gizmo.use_draw_modal = True  # Keep drawing gizmo while clicking
        # self.gizmo.use_draw_hover = True  # Only draw while cursor is on the picker
        # self.gizmo.use_operator_tool_properties = True  # ?
        self.set_position_and_size(30, 30, 256, 256)

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
    bl_widget = GRET_GGT_uv_sheet_gizmo_group.__name__
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
        if not props.uv_layer_name and prefs.uv_paint_layer_name:
            props.uv_layer_name = prefs.uv_paint_layer_name
        image = bpy.data.images.get(props.image)

        layout.use_property_split = True
        col = layout.column(align=False)
        row = col.row(align=True)
        row.prop_search(props, "image", bpy.data, "images", text="")
        # row.operator('gret.uv_sheet_reload', icon='FILE_REFRESH', text="")
        row.operator('image.reload', icon='FILE_REFRESH', text="")
        row.operator('image.open', icon='ADD', text="")

        col.separator()
        if not image:
            col.label(text="No image selected.")
            return
        elif not image.uv_sheet.regions:
            col.label(text="No UV sheet defined.")
        else:
            col.prop(props, 'uv_layer_name', icon='UV')
            col.prop(props, 'delimit')

        col.separator()
        col = layout.column(align=True)
        col.alert = not image.uv_sheet.regions
        text = "Edit UV Sheet" if image.uv_sheet.regions else "Create UV Sheet"
        op = col.operator('gret.uv_sheet_edit', icon='MESH_GRID', text=text)
        op.image = image.name

classes = (
    GRET_GT_uv_sheet_gizmo,
    GRET_GGT_uv_sheet_gizmo_group,
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
