from math import floor
from mathutils import Vector, Matrix
import bpy
import gpu
import traceback

from ..drawing import (
    draw_box,
    draw_box_fill,
    draw_grid,
    draw_help_box,
    draw_image,
    draw_point,
    UVSheetTheme,
)
from ..math import Rect, saturate, SMALL_NUMBER
from ..operator import StateMachineBaseState, StateMachineMixin, DrawHooksMixin

theme = UVSheetTheme()

def is_event_single_press(event, types={}):
    return not event.is_repeat and event.value == 'PRESS' and event.type in types

def draw_region_rect(rect, color, emboss=False):
    draw_box(*rect, color, width=4.0 if emboss else 2.0)
    draw_point(*rect.center, color, size=theme.point_size)

class Region:
    def __init__(self, x0, y0, x1, y1, solid=False):
        self.rect = Rect(x0, y0, x1, y1)
        self.solid = solid

    @classmethod
    def from_property_group(cls, pg):
        return cls(*pg.v0, *pg.v1, pg.solid)

    def fill_property_group(self, pg):
        pg.v0 = self.rect.x0, self.rect.y0
        pg.v1 = self.rect.x1, self.rect.y1
        pg.solid = self.solid

class UVSheetBaseState(StateMachineBaseState):
    def on_event(self, context, event):
        return False  # Return True to consume event

    def on_draw_post_pixel(self, context):
        pass

class UVSheetCreateRegionState(UVSheetBaseState):
    start_mouse_pos = (0, 0)
    rect = None

    def update(self):
        cols, rows = self.owner.grid_cols, self.owner.grid_rows
        x0 = floor(saturate(self.start_mouse_pos[0]) * cols) / cols
        y0 = floor(saturate(self.start_mouse_pos[1]) * rows) / rows
        x1 = floor(saturate(self.owner.mouse_pos[0]) * cols) / cols
        y1 = floor(saturate(self.owner.mouse_pos[1]) * rows) / rows
        self.rect = Rect(min(x0, x1), min(y0, y1), max(x0, x1) + 1 / cols, max(y0, y1) + 1 / rows)
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
            region = self.owner.regions.append(Region(*self.rect))
            self.owner.pop_state()

            if not num_removed:
                self.owner.report({'INFO'}, "Created new region.")
            else:
                self.owner.report({'INFO'}, f"Created new region, {num_removed} overlaps removed.")
            return True

        elif is_event_single_press(event, {'ESC', 'RIGHTMOUSE'}):
            self.owner.pop_state()
            return True

    def on_enter(self):
        self.start_mouse_pos = self.owner.mouse_pos
        self.update()
        self.owner.help_title = "Create Region"
        self.owner.help_texts = ["(Escape/RMB) Cancel."]

    def on_draw_post_pixel(self, context):
        for region in self.owner.regions:
            draw_region_rect(region.rect, theme.unselectable)

        # Draw the region being created
        color = theme.selected if not self.is_intersecting else theme.bad
        draw_region_rect(self.rect, color, emboss=True)

        draw_box(*self.start_mouse_pos, *self.owner.mouse_pos, theme.marquee)

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
            self.owner.push_state(UVSheetCreateRegionState)
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
                self.region_index = -1
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
        for region in self.owner.regions:
            draw_region_rect(region.rect, theme.unselected)

        if self.region_index != -1 and not self.deleting:
            region = self.owner.regions[self.region_index]
            draw_region_rect(region.rect, theme.selected, emboss=False)

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

    def commit(self):
        image = bpy.data.images.get(self.image)
        if not image:
            return
        uv_sheet = image.uv_sheet
        uv_sheet.grid_rows = self.grid_rows
        uv_sheet.grid_cols = self.grid_cols
        uv_sheet.regions.clear()
        for region in self.regions:
            region.fill_property_group(uv_sheet.regions.add())
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
                    self.commit()
                    self.wants_quit = True
                elif event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
                    # Allow navigation
                    return {'PASS_THROUGH'}

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
        self.matrix_view = Matrix()
        self.matrix_view[0][3] = view_rect.x0 - region_rect.x0
        self.matrix_view[1][3] = view_rect.y0 - region_rect.y0
        self.matrix_view[0][0] = view_rect.width
        self.matrix_view[1][1] = view_rect.height
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
    solid: bpy.props.BoolProperty(
        name="Solid Region",
        description="Allows UVs to be collapsed to the center of the region",
    )

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

classes = (
    GRET_OT_uv_sheet_edit,
    GRET_PG_uv_region,
    GRET_PG_uv_sheet,
)

def register(settings):
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Image.uv_sheet = bpy.props.PointerProperty(
        type=GRET_PG_uv_sheet,
    )

def unregister():
    del bpy.types.Image.uv_sheet

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
