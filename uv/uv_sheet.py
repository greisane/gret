from bl_operators.presets import AddPresetBase
from bl_ui.utils import PresetPanel
from math import floor
from mathutils import Vector, Matrix
import bpy
import gpu
import traceback

from ..color import rgb2lab
from ..drawing import *
from ..math import Rect, saturate, saturate2, SMALL_NUMBER
from ..operator import StateMachineBaseState, StateMachineMixin, DrawHooksMixin
from ..log import log, logd

theme = UVSheetTheme()
color_none = (0.0, 0.0, 0.0, 0.0)

def is_event_single_press(event, types={}):
    return not event.is_repeat and event.value == 'PRESS' and event.type in types

def draw_region_rect(rect, color, emboss=False):
    draw_box(*rect, color, width=4.0 if emboss else 2.0)
    draw_point(*rect.center, color, size=theme.point_size)

def _MT(tx, ty):
    m = Matrix()
    m[0][3], m[1][3] = tx, ty
    return m

def _MS(sx, sy):
    m = Matrix()
    m[0][0], m[1][1] = sx, sy
    return m

def _MTS(tx, ty, sx, sy):
    m = Matrix()
    m[0][3], m[1][3] = tx, ty
    m[0][0], m[1][1] = sx, sy
    return m

class Region:
    def __init__(self, x0, y0, x1, y1, color=color_none):
        self.rect = Rect(x0, y0, x1, y1)
        self.color = color

    @classmethod
    def from_property_group(cls, pg):
        return cls(*pg.v0, *pg.v1, pg.color)

    def fill_property_group(self, pg):
        pg.v0 = self.rect.x0, self.rect.y0
        pg.v1 = self.rect.x1, self.rect.y1
        pg.color = self.color

class UVSheetBaseState(StateMachineBaseState):
    def on_event(self, context, event):
        return False  # Return True to consume event

    def on_draw_post_pixel(self, context):
        pass

class UVSheetCreateRegionState(UVSheetBaseState):
    start_mouse_pos = (0, 0)
    rect = None
    grid_snap = True

    def update(self):
        if self.grid_snap:
            cols, rows = self.owner.grid_cols, self.owner.grid_rows
            x0 = floor(saturate2(self.start_mouse_pos[0]) * cols) / cols
            y0 = floor(saturate2(self.start_mouse_pos[1]) * rows) / rows
            x1 = floor(saturate2(self.owner.mouse_pos[0]) * cols) / cols
            y1 = floor(saturate2(self.owner.mouse_pos[1]) * rows) / rows
            self.rect = Rect(min(x0, x1), min(y0, y1), max(x0, x1) + 1 / cols, max(y0, y1) + 1 / rows)
        else:
            x0 = saturate(self.start_mouse_pos[0])
            y0 = saturate(self.start_mouse_pos[1])
            x1 = saturate(self.owner.mouse_pos[0])
            y1 = saturate(self.owner.mouse_pos[1])
            self.rect = Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        test_rect = self.rect.expand(-SMALL_NUMBER)

        self.is_intersecting = False
        for region in self.owner.regions:
            if test_rect.intersects(region.rect):
                self.is_intersecting = True
                break

    def on_event(self, context, event):
        self.update()

        if event.type == 'LEFTMOUSE' and event.value == 'RELEASE':
            # Remove overlapping regions, backwards from the end for efficiency
            num_removed = 0
            region_idx = len(self.owner.regions) - 1
            test_rect = self.rect.expand(-SMALL_NUMBER)
            while region_idx >= 0:
                region = self.owner.regions[region_idx]
                if test_rect.intersects(region.rect):
                    self.owner.regions.pop(region_idx)
                    region_idx = min(region_idx, len(self.owner.regions) - 1)
                    num_removed += 1
                    continue
                region_idx -= 1

            # Add new region
            self.owner.regions.append(Region(*self.rect))
            self.owner.regions_updated()
            self.owner.pop_state()

            if not num_removed:
                self.owner.report({'INFO'}, "Created new region.")
            else:
                self.owner.report({'INFO'}, f"Created new region, {num_removed} overlaps removed.")
            return True

        elif is_event_single_press(event, {'ESC', 'RIGHTMOUSE'}):
            self.owner.pop_state()
            return True

    def on_enter(self, grid_snap=True):
        self.start_mouse_pos = self.owner.mouse_pos
        self.grid_snap = grid_snap
        self.update()
        self.owner.help_title = "Create Region"
        self.owner.help_texts = ["(Escape/RMB) Cancel."]

    def on_draw_post_pixel(self, context):
        self.owner.draw_region_rects(theme.unselectable)

        # Draw the region being created
        color = theme.selected if not self.is_intersecting else theme.bad
        draw_region_rect(self.rect, color, emboss=True)

        # draw_box(*self.start_mouse_pos, *self.owner.mouse_pos, theme.marquee)

class UVSheetScrollState(UVSheetBaseState):
    def on_event(self, context, event):
        if event.type == 'MOUSEMOVE':
            mt = _MT(event.mouse_x - event.mouse_prev_x, event.mouse_y - event.mouse_prev_y)
            self.owner.matrix_view = mt @ self.owner.matrix_view

        elif (event.type == 'MIDDLEMOUSE' and event.value == 'RELEASE' or
            is_event_single_press(event, {'ESC', 'RIGHTMOUSE'})):
            self.owner.pop_state()
            return True

class UVSheetEditRegionState(UVSheetBaseState):
    region_index = -1
    deleting = False

    def update(self):
        self.region_index = -1
        if self.owner.is_mouse_pos_in_bounds:
            # Find index of hovered region
            for region_idx, region in enumerate(self.owner.regions):
                if region.rect.contains(*self.owner.mouse_pos):
                    self.region_index = region_idx
                    break

    def on_event(self, context, event):
        self.update()
        is_valid_region = self.region_index >= 0 and self.region_index < len(self.owner.regions)

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            # Begin creating new region
            self.owner.push_state(UVSheetCreateRegionState, grid_snap=not event.ctrl)
            return True

        elif event.type == 'MIDDLEMOUSE' and event.value == 'PRESS':
            # Begin scrolling
            self.owner.push_state(UVSheetScrollState)
            return True

        elif (event.type == 'WHEELUPMOUSE' or event.type == 'WHEELDOWNMOUSE') and event.ctrl:
            # Adjust grid
            x, y = self.owner.mouse_pos[0] - 0.5, self.owner.mouse_pos[1] - 0.5
            change = 1 if event.type == 'WHEELUPMOUSE' else -1
            verb = "increased" if change > 0 else "decreased"
            if (x < y) != (-x < y):
                new_grid_rows = max(1, self.owner.grid_rows + change)
                if new_grid_rows != self.owner.grid_rows:
                    self.owner.grid_rows = new_grid_rows
                    self.owner.report({'INFO'}, f"Grid rows {verb} to {new_grid_rows}.")
            else:
                new_grid_cols = max(1, self.owner.grid_cols + change)
                if new_grid_cols != self.owner.grid_cols:
                    self.owner.grid_cols = new_grid_cols
                    self.owner.report({'INFO'}, f"Grid columns {verb} to {new_grid_cols}.")
            return True

        elif (event.type == 'WHEELUPMOUSE' or event.type == 'WHEELDOWNMOUSE'):
            # Zoom
            change = 1.25 if event.type == 'WHEELUPMOUSE' else 0.8
            mt, ms = _MT(event.mouse_x, event.mouse_y), _MS(change, change)
            self.owner.matrix_view = mt @ ms @ mt.inverted() @ self.owner.matrix_view
            return True

        elif event.type in {'X', 'DEL'}:
            # Some extra logic to avoid triggering reset while deleting and vice versa
            if not event.is_repeat and event.value == 'PRESS':
                if event.shift:
                    # Clear all regions
                    self.do_reset(intialize_grid=True)
                    self.owner.report({'INFO'}, "Reset all regions.")
                    return True
                else:
                    self.deleting = True
            elif event.value == 'RELEASE':
                self.deleting = False
            if self.deleting and is_valid_region:
                # Remove hovered region
                self.owner.regions.pop(self.region_index)
                self.owner.regions_updated()
                self.owner.report({'INFO'}, "Deleted region.")
                return True

        return False

    def do_reset(self, intialize_grid=False):
        self.owner.regions.clear()
        if intialize_grid:
            gw, gh = 1.0 / self.owner.grid_cols, 1.0 / self.owner.grid_rows
            for y in range(self.owner.grid_rows):
                for x in range(self.owner.grid_cols):
                    self.owner.regions.append(Region(x * gw, y * gh, (x + 1) * gw, (y + 1) * gh))
        self.owner.regions_updated()
        self.update()

    def on_enter(self):
        self.owner.help_title = "Edit Tile/Trim UV Sheet"
        self.owner.help_texts = [
            "\u2022 Click and drag to create a region.",
            "\u2022 Ctrl+Wheel to adjust grid divisions.",
            "\u2022 Hold X or Del to remove regions.",
            # "\u2022 Shift+X resets regions to a grid.",
            "",
            "(ENTER) Finish -- (Escape/RMB) Cancel.",
        ]

    def on_draw_post_pixel(self, context):
        self.owner.draw_region_rects(theme.unselected)

        # Draw hovered region
        if self.region_index != -1 and not self.deleting:
            region = self.owner.regions[self.region_index]
            draw_region_rect(region.rect, theme.selected)

class GRET_OT_uv_sheet_edit(bpy.types.Operator, StateMachineMixin, DrawHooksMixin):
    #tooltip
    """Edit UV sheet regions associated with this image"""

    bl_idname = "gret.uv_sheet_edit"
    bl_label = "Edit UV Sheet"
    bl_options = {'INTERNAL', 'UNDO'}

    image: bpy.props.StringProperty(options={'HIDDEN'})

    # Current state
    matrix_view = None
    mouse_pos = (0, 0)
    mouse_region_pos = (0, 0)
    is_mouse_pos_in_bounds = False
    help_title = ""
    help_texts = []
    wants_quit = False
    committed = False
    batch_rects = None
    batch_points = None

    # UV sheet data being edited
    grid_rows, grid_cols = 1, 1
    regions = []

    def on_draw_post_pixel(self, context):
        region = context.region
        image = bpy.data.images.get(self.image)

        # Cover the viewport
        rect = Rect.from_size(region.x, region.y, region.width, region.height)
        draw_box_fill(0, 0, region.width, region.height, theme.background)

        with gpu.matrix.push_pop():
            gpu.matrix.multiply_matrix(self.matrix_view)

            num_rows, num_cols = self.grid_rows, self.grid_cols
            draw_image(0, 0, 1, 1, image, (0.75, 0.75, 0.75, 1.0), nearest=True)
            draw_box(0, 0, 1, 1, theme.border)
            draw_grid(0, 0, 1 / num_cols, 1 / num_rows, num_cols, num_rows, theme.grid)

            try:
                if self.state:
                    self.state.on_draw_post_pixel(context)
            except Exception as e:
                traceback.print_exc()
                self.wants_quit = True  # Avoid flooding the console
                self.report({'ERROR'}, f"An exception ocurred: {e}")

        draw_help_box(30.0, 30.0, self.help_texts, self.help_title, width=280.0)

    def draw_region_rects(self, color):
        if not self.batch_rects:
            self.batch_rects = batch_rects([region.rect for region in self.regions])
        if not self.batch_points:
            self.batch_points = batch_points([region.rect.center for region in self.regions])
        draw_solid_batch(self.batch_rects, color, line_width=2.0)
        draw_solid_batch(self.batch_points, color, point_size=theme.point_size)

    def regions_updated(self):
        self.batch_rects = self.batch_points = None
        self.region_index = -1

    def commit(self, context):
        wm = context.window_manager
        image = bpy.data.images.get(self.image)
        if not image:
            return
        uv_sheet = image.uv_sheet
        uv_sheet.grid_rows = self.grid_rows
        uv_sheet.grid_cols = self.grid_cols
        uv_sheet.regions.clear()

        # Accessing Image.pixels is really slow, do color stuff here instead of when regions are added
        pixels = image.pixels[:]
        w, h = image.size
        def get_pixel_color(x, y):
            offset = (int(x) + int(y) * w) * 4
            return pixels[offset:offset + 4]

        logd(f"Committing {len(self.regions)} regions")
        wm.progress_begin(0, len(self.regions))
        for region_idx, region in enumerate(self.regions):
            wm.progress_update(region_idx)

            color = color_none
            px0, py0 = int(region.rect.x0 * w), int(region.rect.y0 * h)
            px1, py1 = int(region.rect.x1 * w), int(region.rect.y1 * h)
            xstep, ystep = 1, 1
            color = get_pixel_color(px0, py0)
            for py in range(py0, py1, ystep):
                for px in range(px0, px1, xstep):
                    if get_pixel_color(px, py) != color:
                        color = color_none
                        break

            pg_region = uv_sheet.regions.add()
            region.fill_property_group(pg_region)
            pg_region.color = color
            pg_region.color_lab = rgb2lab(color)
        wm.progress_end()

        if uv_sheet.active_index > len(uv_sheet.regions):
            uv_sheet.active_index = -1

        self.committed = True

    def modal(self, context, event):
        context.area.tag_redraw()
        region = context.region
        obj = context.active_object

        if not self.wants_quit:
            handled = False
            if event.type == 'MOUSEMOVE':
                self.mouse_region_pos = (event.mouse_region_x, event.mouse_region_y)
                self.mouse_pos = (self.matrix_view.inverted() @ Vector((*self.mouse_region_pos, 0))).xy
                self.is_mouse_pos_in_bounds = (0.0 <= self.mouse_pos[0] <= 1.0
                    and 0.0 <= self.mouse_pos[1] <= 1.0)

            # Pass event to current state
            try:
                if self.state:
                    handled = bool(self.state.on_event(context, event))
            except Exception as e:
                traceback.print_exc()
                handled = True
                self.pop_state()  # Exit the offending state
                if not self.state:
                    self.wants_quit = True
                self.report({'ERROR'}, f"An exception ocurred: {e}")

            if not handled:
                if is_event_single_press(event, {'ESC', 'RIGHTMOUSE'}):
                    self.wants_quit = True
                elif is_event_single_press(event, {'RET', 'NUMPAD_ENTER'}):
                    self.commit(context)
                    self.wants_quit = True

        if self.wants_quit:
            self.unhook()
            if self.committed:
                self.report({'INFO'}, "Finished UV sheet editing.")
                return {'FINISHED'}
            else:
                self.report({'INFO'}, "Cancelled UV sheet editing.")
                return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        region = context.region
        image = bpy.data.images.get(self.image)
        if not image or image.size[0] == 0 and image.size[1] == 0:
            self.report({'ERROR'}, "Image doesn't exist or is invalid.")
            return {'CANCELLED'}

        # Reset state
        max_size = min(region.width * 0.6, region.height * 0.8)
        scale = image.size[0] / max(image.size), image.size[1] / max(image.size)
        region_rect = Rect.from_size(region.x, region.y, region.width, region.height)
        view_rect = region_rect.resize(max_size * scale[0], max_size * scale[1])
        self.matrix_view = _MTS(view_rect.x0 - region_rect.x0, view_rect.y0 - region_rect.y0,
            view_rect.width, view_rect.height)
        self.mouse_pos = (0, 0)
        self.mouse_region_pos = (0, 0)
        self.is_mouse_pos_in_bounds = False
        self.wants_quit = False
        self.committed = False

        # Grab copy of UV sheet data
        uv_sheet = image.uv_sheet
        self.grid_rows, self.grid_cols = uv_sheet.grid_rows, uv_sheet.grid_cols
        self.regions = [Region.from_property_group(pg) for pg in uv_sheet.regions]

        self.hook(context)
        self.push_state(UVSheetEditRegionState)

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

class GRET_PG_uv_region(bpy.types.PropertyGroup):
    v0: bpy.props.FloatVectorProperty(
        size=2,
        default=(0.0, 0.0),
    )
    v1: bpy.props.FloatVectorProperty(
        size=2,
        default=(0.0, 0.0),
    )
    color: bpy.props.FloatVectorProperty(
        name="Uniform Color",
        description="Color of every pixel in this region. Allows UVs to be collapsed",
        size=4,
        subtype='COLOR',
        default=color_none,
    )
    color_lab: bpy.props.FloatVectorProperty(size=3)

class GRET_PG_uv_sheet(bpy.types.PropertyGroup):
    grid_rows: bpy.props.IntProperty(
        name="Grid Rows",
        description="Number of rows in the UV sheet",
        default=8,
        min=1,
    )
    grid_cols: bpy.props.IntProperty(
        name="Grid Columns",
        description="Number of columns in the UV sheet",
        default=8,
        min=1,
    )
    regions: bpy.props.CollectionProperty(
        type=GRET_PG_uv_region,
    )
    active_index: bpy.props.IntProperty()
    custom_region: bpy.props.PointerProperty(
        type=GRET_PG_uv_region,
    )
    use_custom_region: bpy.props.BoolProperty()
    use_palette_uv: bpy.props.BoolProperty(
        name="Use Palette UVs",
        description="Collapse UVs to a point when the region is an uniform color",
        default=False,
    )

class GRET_MT_uv_sheet_presets(bpy.types.Menu):
    bl_label = "UV Paint Presets"
    preset_subdir = "gret/uv_paint"
    preset_operator = 'script.execute_preset'
    draw = bpy.types.Menu.draw_preset

class GRET_OT_uv_sheet_add_preset(AddPresetBase, bpy.types.Operator):
    bl_idname = 'gret.uv_paint_add_preset'
    bl_label = "Add UV Paint Preset"
    preset_menu = GRET_MT_uv_sheet_presets.__name__
    preset_subdir = GRET_MT_uv_sheet_presets.preset_subdir
    preset_defines = [
        "from gret.uv.uv_paint import GRET_TT_uv_paint, GRET_OT_uv_paint",
        "tool = bpy.context.workspace.tools.get(GRET_TT_uv_paint.bl_idname)",
        "props = tool.operator_properties(GRET_OT_uv_paint.bl_idname)",
        "image = bpy.data.images.get(props.image)",
    ]
    preset_values = [
        # "props.uv_layer_name",
        # "props.delimit",
        # "props.random",
        "image.uv_sheet.regions",
    ]

class GRET_PT_uv_sheet_presets(PresetPanel, bpy.types.Panel):
    bl_label = "UV Paint Presets"
    preset_subdir = GRET_MT_uv_sheet_presets.preset_subdir
    preset_add_operator = GRET_OT_uv_sheet_add_preset.bl_idname
    preset_operator = 'script.execute_preset'

classes = (
    GRET_MT_uv_sheet_presets,
    GRET_OT_uv_sheet_add_preset,
    GRET_OT_uv_sheet_edit,
    GRET_PG_uv_region,
    GRET_PG_uv_sheet,
    GRET_PT_uv_sheet_presets,
)

def register(settings, prefs):
    if not prefs.uv_paint__enable:
        return False

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Image.uv_sheet = bpy.props.PointerProperty(
        type=GRET_PG_uv_sheet,
    )

def unregister():
    del bpy.types.Image.uv_sheet

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
