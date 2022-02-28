from math import floor
import bpy
import gpu

from ..drawing import *
from ..math import Rect, saturate, saturate2
from ..operator import StateMachineMixin, StateMachineBaseState
from .uv_paint import GRET_TT_uv_paint, GRET_OT_uv_paint

theme = UVSheetTheme()
quad_xy = [(0, 1), (1, 1), (0, 0), (1, 0)]

class UVPickerBaseState(StateMachineBaseState):
    def on_enter(self, context, event, control):
        pass

    def on_exit(self, context, cancel):
        pass

    # def on_draw(self, context):
    #     pass

    def on_modal(self, context, event, tweak):
        pass

class UVPickerSelectState(UVPickerBaseState):
    def update(self, context, mx, my):
        image, uv_sheet = self.owner.get_active_image_info(context)
        if self.control.test_select(context, image, mx, my):
            uv_sheet.use_custom_region = False
            if uv_sheet.active_index != self.control.region_index:
                uv_sheet.active_index = self.control.region_index
                context.area.tag_redraw()

    def on_enter(self, context, event, control):
        self.control = control
        self.update(context, event.mouse_region_x, event.mouse_region_y)

    def on_modal(self, context, event, tweak):
        if event.type == 'MOUSEMOVE' and event.value == 'PRESS':
            self.update(context, event.mouse_region_x, event.mouse_region_y)

class UVPickerCustomRegionState(UVPickerBaseState):
    start_mouse_pos = (0, 0)
    grid_snap = False

    def update(self, context, mx, my):
        image, uv_sheet = self.owner.get_active_image_info(context)
        picker_rect = self.control.get_rect(image)
        start_x, start_y = picker_rect.inverse_transform_point(*self.start_mouse_pos)
        x, y = picker_rect.inverse_transform_point(mx, my)

        if self.grid_snap:
            cols, rows = uv_sheet.grid_cols, uv_sheet.grid_rows
            x0 = floor(saturate2(start_x) * cols) / cols
            y0 = floor(saturate2(start_y) * rows) / rows
            x1 = floor(saturate2(x) * cols) / cols
            y1 = floor(saturate2(y) * rows) / rows
            x0, y0, x1, y1 = min(x0, x1), min(y0, y1), max(x0, x1) + 1 / cols, max(y0, y1) + 1 / rows
        else:
            x0 = saturate(start_x)
            y0 = saturate(start_y)
            x1 = saturate(x)
            y1 = saturate(y)
            x0, y0, x1, y1 = min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)

        uv_sheet.use_custom_region = True
        uv_sheet.custom_region.v0 = x0, y0
        uv_sheet.custom_region.v1 = x1, y1

        context.area.tag_redraw()

    def on_enter(self, context, event, control):
        self.control = control
        self.owner.region_index = -1
        self.start_mouse_pos = event.mouse_region_x, event.mouse_region_y
        self.update(context, *self.start_mouse_pos)

    def on_modal(self, context, event, tweak):
        if event.type == 'MOUSEMOVE' and event.value == 'PRESS':
            self.grid_snap = True
            self.update(context, event.mouse_region_x, event.mouse_region_y)

class UVPickerResizeState(UVPickerBaseState):
    def update(self, context, mx, my):
        delta_size = max(mx - self.start_mouse_pos[0], my - self.start_mouse_pos[1])
        max_size = min(context.region.width, context.region.height) - 100.0
        new_size = min(max_size, self.start_size + delta_size)
        self.owner.size = new_size

    def on_enter(self, context, event, control):
        self.start_mouse_pos = event.mouse_region_x, event.mouse_region_y
        self.start_size = self.owner.matrix_basis[0][0]
        self.control = control

    def on_exit(self, context, cancel):
        context.scene.gret.uv_picker_size = self.owner.size

    def on_modal(self, context, event, tweak):
        if event.type == 'MOUSEMOVE' and event.value == 'PRESS':
            self.update(context, event.mouse_region_x, event.mouse_region_y)

class UVPickerBaseControl:
    def __init__(self, owner):
        self.owner = owner

    @property
    def is_active(self):
        return self.owner.active_control is self

    def get_rect(self, image):
        return None

    def test_select(self, context, image, mx, my):
        rect = self.get_rect(image)
        return rect is not None and rect.contains(mx, my)

    def draw(self, context, image):
        pass

    def invoke(self, context, event):
        pass

    def on_enter(self):
        pass

    def on_exit(self):
        pass

class UVPickerSelectorControl(UVPickerBaseControl):
    def __init__(self, owner):
        super().__init__(owner)
        self.region_index = -1

    def get_rect(self, image):
        if image:
            scale_x, scale_y = image.size[0] / max(image.size), image.size[1] / max(image.size)
        else:
            scale_x, scale_y = 1.0, 1.0
        size = self.owner.size
        return Rect.from_size(*self.owner.position, size * scale_x, size * scale_y)

    def get_target_rect(self):
        quad = bpy.context.scene.gret.uv_picker_quad
        if quad >= 0 and quad < 4:
            x0, y0 = quad_xy[quad][0] * 0.5, quad_xy[quad][1] * 0.5
            return Rect(x0, y0, x0 + 0.5, y0 + 0.5)
        return Rect(0.0, 0.0, 1.0, 1.0)

    def test_select(self, context, image, mx, my):
        self.region_index = -1
        if not image:
            return
        uv_sheet = image.uv_sheet

        rect = self.get_rect(image)
        if rect.contains(mx, my):
            # Update index of hovered region
            x, y = rect.inverse_transform_point(mx, my)
            for region_idx, region in enumerate(uv_sheet.regions):
                if region.v0[0] < x < region.v1[0] and region.v0[1] < y < region.v1[1]:
                    self.region_index = region_idx
                    break
            return True
        return False

    def draw(self, context, image):
        rect = self.get_rect(image)
        image_rect = self.get_target_rect()
        def transform_region(region):
            return (rect.transform_point(*image_rect.inverse_transform_point(*region.v0))
                + rect.transform_point(*image_rect.inverse_transform_point(*region.v1)))

        # Draw bordered image, or help text if no image
        draw_box(*rect, theme.border)
        if not image:
            draw_box_fill(*rect, theme.background)
            draw_text(*rect.center, "Select an image in the Tool tab.", theme.border, rect)
            return

        uv_sheet = image.uv_sheet
        draw_image(*rect, image, (0.7, 0.7, 0.7, 1.0), self.get_target_rect().corners, nearest=True)

        # Draw region rectangles
        if context.scene.gret.uv_picker_show_grid:
            # TODO Not actually caching since it's difficult to figure out when regions have changed,
            # and bpy.msgbus isn't doing the trick. Drawing all rects together is still an improvement
            if True or not self.batch_rects:
                rects = [transform_region(region) for region in uv_sheet.regions]
                self.batch_rects = batch_rects(rects)
            draw_solid_batch(self.batch_rects, theme.unselected, line_width=1.0, use_clip=True)

        # Draw hovered region
        if self.region_index >= 0 and self.region_index < len(uv_sheet.regions):
            region = uv_sheet.regions[self.region_index]
            draw_box(*transform_region(region), theme.hovered)

        # Draw active region
        if not uv_sheet.use_custom_region:
            if uv_sheet.active_index >= 0 and uv_sheet.active_index < len(uv_sheet.regions):
                region = uv_sheet.regions[uv_sheet.active_index]
                draw_box(*transform_region(region), theme.selected, width=2.0)
        else:
            region = uv_sheet.custom_region
            x0, y0, x1, y1 = transform_region(region)
            if abs(x0 - x1) < 4.0 and abs(y0 - y1) < 4.0:
                # Custom region is too small and won't be visible with draw_box
                draw_point(x0, y0, theme.bad, size=theme.point_size)
            else:
                draw_box(x0, y0, x1, y1, theme.bad, width=2.0)

    def invoke(self, context, event):
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if event.ctrl:
                self.owner.push_state(UVPickerCustomRegionState, context, event, self)
            else:
                self.owner.push_state(UVPickerSelectState, context, event, self)

class UVPickerQuadControl(UVPickerBaseControl):
    def get_rect(self, image):
        picker_rect = self.owner.controls[-1].get_rect(image)
        return Rect.from_size(picker_rect.x0, picker_rect.y1 + 4.0, *icon_size)

    def draw(self, context, image):
        rect = self.get_rect(image)
        w, h = rect.width, rect.height
        rect = rect.resize(w * 0.5, h * 0.5).expand(-1)
        quad = context.scene.gret.uv_picker_quad
        draw_box_fill(*rect.move(w * -0.25, h * +0.25), theme.hovered if quad == 0 else theme.border)
        draw_box_fill(*rect.move(w * +0.25, h * +0.25), theme.hovered if quad == 1 else theme.border)
        draw_box_fill(*rect.move(w * -0.25, h * -0.25), theme.hovered if quad == 2 else theme.border)
        draw_box_fill(*rect.move(w * +0.25, h * -0.25), theme.hovered if quad == 3 else theme.border)

    def invoke(self, context, event):
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS' and not event.is_repeat:
            context.scene.gret.uv_picker_quad = ((context.scene.gret.uv_picker_quad + 2) % 5) - 1
            context.area.tag_redraw()

class UVPickerGridControl(UVPickerBaseControl):
    def get_rect(self, image):
        picker_rect = self.owner.controls[-1].get_rect(image)
        return Rect.from_size(picker_rect.x1 - icon_size[0], picker_rect.y1 + 4.0, *icon_size)

    def draw(self, context, image):
        draw_icon(*self.get_rect(image), 'GRID', theme.hovered if self.is_active else theme.border)

    def invoke(self, context, event):
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS' and not event.is_repeat:
            context.scene.gret.uv_picker_show_grid = not context.scene.gret.uv_picker_show_grid
            context.area.tag_redraw()

class UVPickerResizeControl(UVPickerBaseControl):
    def get_rect(self, image):
        picker_rect = self.owner.controls[-1].get_rect(image)
        return Rect.from_size(picker_rect.x1 + 4.0, picker_rect.y1 + 4.0, *icon_size)

    def draw(self, context, image):
        draw_icon(*self.get_rect(image), 'RESIZE', theme.hovered if self.is_active else theme.border)

    def invoke(self, context, event):
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.owner.push_state(UVPickerResizeState, context, event, self)

class UVPickerHelpControl(UVPickerBaseControl):
    help_boxes = [
        {
            "title": "1. Select UVs",
            "texts": [
                "\u2022 Click on the picker to select a region.",
                "\u2022 Ctrl+Click and drag to use a custom region.",
            ],
        },
        {
            "title": "2. Paint in the viewport",
            "texts": [
                "\u2022 Click on mesh faces or Ctrl+Click to sample UVs.",
                "\u2022 Shift+Click to fill.",
                "\u2022 Shift+Ctrl+Click to replace similar.",
                "",
                "Brush options can be found in the Tool tab on the right.",
            ],
        },
    ]

    def get_rect(self, image):
        picker_rect = self.owner.controls[-1].get_rect(image)
        return Rect.from_size(picker_rect.x1 + 4.0, picker_rect.y1 - icon_size[1], *icon_size)

    def draw(self, context, image):
        rect = self.get_rect(image)
        draw_icon(*rect, 'HELP', theme.hovered if self.is_active else theme.border)

        if self.is_active:
            x, y = rect.x1 + 8.0, rect.y1
            for box in reversed(self.help_boxes):
                width, height = draw_help_box(x, y, box["texts"], box["title"], width=320.0)
                y += height + 8.0

class GRET_GT_uv_picker_gizmo(bpy.types.Gizmo, StateMachineMixin):
    __slots__ = (
        "controls",
        "active_control",
    )

    state_events_on_reentry = False
    active_area = None

    @staticmethod
    def get_active_image_info(context):
        tool = context.workspace.tools.get(GRET_TT_uv_paint.bl_idname)
        if tool:
            props = tool.operator_properties(GRET_OT_uv_paint.bl_idname)
            image = bpy.data.images.get(props.image)
            if image:
                return image, image.uv_sheet
        return None, None

    @property
    def position(self):
        return self.matrix_basis[0][3], self.matrix_basis[1][3]

    @position.setter
    def position(self, new_position):
        self.matrix_basis[0][3], self.matrix_basis[1][3] = new_position

    @property
    def size(self):
        return self.matrix_basis[0][0]

    @size.setter
    def size(self, new_size):
        new_size = min(1024.0, max(64.0, new_size))
        self.matrix_basis[0][0], self.matrix_basis[1][1] = new_size, new_size

    def draw(self, context):
        cls = __class__
        if not cls.active_area or context.area != cls.active_area:
            # Only draw in the active area
            return

        image, _ = self.get_active_image_info(context)
        for control in reversed(self.controls):
            control.draw(context, image)

    def test_select(self, context, location):
        # This is only called for the currently hovered viewport while not running modal
        cls = __class__
        if cls.active_area:
            # Force a redraw on the area we're leaving so the picker disappears
            cls.active_area.tag_redraw()
        cls.active_area = context.area

        image, _ = self.get_active_image_info(context)
        for control_idx, control in enumerate(self.controls):
            if control.test_select(context, image, *location):
                if control is not self.active_control:
                    if self.active_control:
                        self.active_control.on_exit()
                    self.active_control = control
                    self.active_control.on_enter()
                return control_idx
        # No control hovered
        if self.active_control:
            self.active_control.on_exit()
            self.active_control = None

        return -1

    def setup(self):
        self.controls = [
            UVPickerHelpControl(self),
            UVPickerResizeControl(self),
            UVPickerGridControl(self),
            # UVPickerQuadControl(self),
            UVPickerSelectorControl(self),
        ]
        self.active_control = None

    def invoke(self, context, event):
        if self.active_control:
            self.active_control.invoke(context, event)
        return {'RUNNING_MODAL'}

    def exit(self, context, cancel):
        # while self.state:
        self.pop_state(context, cancel)

    def modal(self, context, event, tweak):
        if self.state:
            self.state.on_modal(context, event, tweak)
        return {'RUNNING_MODAL'}

class GRET_GGT_uv_picker_gizmo_group(bpy.types.GizmoGroup):
    bl_label = "Gizmo Group"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'WINDOW'
    bl_options = {'PERSISTENT'}  # No 'SCALE', matrix_basis is only being used to store

    @classmethod
    def poll(cls, context):
        current_tool = context.workspace.tools.from_space_view3d_mode(context.mode, create=False)
        return context.mode == 'OBJECT' and current_tool.idname == GRET_TT_uv_paint.bl_idname

    def setup(self, context):
        settings = context.scene.gret
        self.gizmo = self.gizmos.new(GRET_GT_uv_picker_gizmo.__name__)
        self.gizmo.use_draw_modal = True  # Keep drawing gizmo while clicking
        self.gizmo.use_draw_scale = False
        # self.gizmo.use_draw_hover = True  # Only draw while cursor is on the picker
        # self.gizmo.use_operator_tool_properties = True  # ?
        # self.gizmo.use_event_handle_all = True  # Swallow all events while hovered
        self.gizmo.position = settings.uv_picker_pos
        self.gizmo.size = settings.uv_picker_size

classes = (
    GRET_GT_uv_picker_gizmo,
    GRET_GGT_uv_picker_gizmo_group,
)

def register(settings):
    for cls in classes:
        bpy.utils.register_class(cls)

    settings.add_property('uv_picker_pos', bpy.props.FloatVectorProperty(
        name="UV Picker Position",
        description="Offset of the UV picker from the lower left corner of the viewport",
        size=2,
        default=(30.0, 30.0),
    ))
    settings.add_property('uv_picker_size', bpy.props.FloatProperty(
        name="UV Picker Size",
        description="Size in pixels of the UV picker",
        default=256.0,
        min=64.0,
        max=1024.0,
    ))
    settings.add_property('uv_picker_quad', bpy.props.IntProperty(
        name="UV Picker Quad",
        description="Portion of the UV sheet image shown",
        default=-1,
        min=-1,
        max=3,
    ))
    settings.add_property('uv_picker_show_grid', bpy.props.BoolProperty(
        name="UV Picker Show Grid",
        description="Display the UV picker grid",
        default=True,
    ))

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
