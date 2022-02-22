import bpy
import gpu

from ..drawing import (
    draw_box,
    draw_box_fill,
    draw_help_box,
    draw_icon,
    draw_image,
    draw_point,
    icon_size,
    UVSheetTheme,
)
from ..math import Rect, saturate, SMALL_NUMBER
from ..operator import StateMachineMixin, StateMachineBaseState

theme = UVSheetTheme()

class UVPickerGizmoBaseState(StateMachineBaseState):
    def on_enter(self, context, event, control):
        pass

    def on_exit(self, context, cancel):
        pass

    # def on_draw(self, context):
    #     pass

    def on_modal(self, context, event, tweak):
        pass

class UVPickerGizmoSelectState(UVPickerGizmoBaseState):
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

class UVPickerGizmoResizeState(UVPickerGizmoBaseState):
    def update(self, context, mx, my):
        delta_size = max(mx - self.start_mouse_pos[0], my - self.start_mouse_pos[1])
        max_size = min(context.region.width, context.region.height) - 100.0
        new_size = min(max_size, self.start_size + delta_size)
        self.owner.size = new_size
        context.scene.gret.uv_picker_picker_size = self.owner.size

    def on_enter(self, context, event, control):
        self.start_mouse_pos = event.mouse_region_x, event.mouse_region_y
        self.start_size = self.owner.matrix_basis[0][0]
        self.control = control

    def on_modal(self, context, event, tweak):
        if event.type == 'MOUSEMOVE' and event.value == 'PRESS':
            self.update(context, event.mouse_region_x, event.mouse_region_y)

class UVPickerGizmoBaseControl:
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

class UVPickerGizmoPickerControl(UVPickerGizmoBaseControl):
    def __init__(self, owner):
        super().__init__(owner)
        self.region_index = -1

    def get_rect(self, image):
        scale_x, scale_y = image.size[0] / max(image.size), image.size[1] / max(image.size)
        size = self.owner.size
        return Rect.from_size(*self.owner.position, size * scale_x, size * scale_y)

    def test_select(self, context, image, mx, my):
        self.region_index = -1
        if not image:
            return
        uv_sheet = image.uv_sheet

        rect = self.get_rect(image)
        if rect.contains(mx, my):
            # Update index of hovered region
            x, y = rect.inverse_transform(mx, my)
            for region_idx, region in enumerate(uv_sheet.regions):
                if region.v0[0] < x < region.v1[0] and region.v0[1] < y < region.v1[1]:
                    self.region_index = region_idx
                    break
            return True
        return False

    def draw(self, context, image):
        uv_sheet = image.uv_sheet

        with gpu.matrix.push_pop():
            gpu.matrix.multiply_matrix(self.get_rect(image).to_trs_matrix())

            draw_box(0, 0, 1, 1, theme.border)
            draw_image(0, 0, 1, 1, image, nearest=True)
            draw_box_fill(0, 0, 1, 1, (0, 0, 0, 0.3))  # Darken (image shader doesn't support tint)

            # Draw region rectangles
            for region in uv_sheet.regions:
                draw_box(*region.v0, *region.v1, theme.unselected)

            # Draw hovered region
            if self.region_index >= 0 and self.region_index < len(uv_sheet.regions):
                region = uv_sheet.regions[self.region_index]
                draw_box(*region.v0, *region.v1, theme.hovered)

            # Draw active region
            if not uv_sheet.use_custom_region:
                if uv_sheet.active_index >= 0 and uv_sheet.active_index < len(uv_sheet.regions):
                    region = uv_sheet.regions[uv_sheet.active_index]
                    draw_box(*region.v0, *region.v1, theme.selected, width=2.0)
            else:
                region = uv_sheet.custom_region
                x0, y0, x1, y1 = *region.v0, *region.v1
                if abs(x0 - x1) < SMALL_NUMBER and abs(y0 - y1) < SMALL_NUMBER:
                    # Custom region is too small and won't be visible with draw_box
                    draw_point(x0, y0, theme.bad, size=theme.point_size)
                else:
                    draw_box(x0, y0, x1, y1, theme.bad, width=2.0)

    def invoke(self, context, event):
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if event.ctrl:
                raise NotImplementedError
            else:
                self.owner.push_state(UVPickerGizmoSelectState, context, event, self)

class UVPickerGizmoResizeControl(UVPickerGizmoBaseControl):
    def get_rect(self, image):
        picker_rect = self.owner.controls[-1].get_rect(image)
        return Rect.from_size(picker_rect.x1 + 4.0, picker_rect.y1 + 4.0, *icon_size)

    def draw(self, context, image):
        draw_icon(*self.get_rect(image), 'RESIZE', theme.hovered if self.is_active else theme.border)

    def invoke(self, context, event):
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self.owner.push_state(UVPickerGizmoResizeState, context, event, self)

class UVPickerGizmoHelpControl(UVPickerGizmoBaseControl):
    help_title = "Tile/Trim UV Picker"
    help_texts = [
        "\u2022 Click to select before painting.",
        "\u2022 Ctrl+Click and drag to use a custom region.",
        # "\u2022 Hold X or Del to remove regions.",
    ]

    def get_rect(self, image):
        picker_rect = self.owner.controls[-1].get_rect(image)
        return Rect.from_size(picker_rect.x1 + 4.0, picker_rect.y1 - icon_size[1], *icon_size)

    def draw(self, context, image):
        rect = self.get_rect(image)
        draw_icon(*rect, 'HELP', theme.hovered if self.is_active else theme.border)

        if self.is_active:
            draw_help_box(rect.x1 + 8.0, rect.y1, self.help_texts, self.help_title)

class GRET_GT_uv_picker_gizmo(bpy.types.Gizmo, StateMachineMixin):
    __slots__ = (
        "controls",
        "active_control",
    )

    state_events_on_reentry = False
    active_area = None

    @staticmethod
    def get_active_image_info(context):
        tool = context.workspace.tools.get("gret.uv_paint")  # GRET_TT_uv_paint.bl_idname
        if tool:
            props = tool.operator_properties("gret.uv_paint")  # GRET_OT_uv_paint.bl_idname
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

        image, uv_sheet = self.get_active_image_info(context)
        if image and image.size[0] > 0 and image.size[1] > 0:
            for control in reversed(self.controls):
                control.draw(context, image)

    def test_select(self, context, location):
        # This is only called for the currently hovered viewport while not running modal
        cls = __class__
        if cls.active_area:
            # Force a redraw on the area we're leaving so the picker disappears
            cls.active_area.tag_redraw()
        cls.active_area = context.area

        image, uv_sheet = self.get_active_image_info(context)
        if image and image.size[0] > 0 and image.size[1] > 0:
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
            UVPickerGizmoHelpControl(self),
            UVPickerGizmoResizeControl(self),
            UVPickerGizmoPickerControl(self),
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
        return context.mode == 'OBJECT'

    def setup(self, context):
        settings = context.scene.gret
        self.gizmo = self.gizmos.new(GRET_GT_uv_picker_gizmo.__name__)
        self.gizmo.use_draw_modal = True  # Keep drawing gizmo while clicking
        self.gizmo.use_draw_scale = False
        # self.gizmo.use_draw_hover = True  # Only draw while cursor is on the picker
        # self.gizmo.use_operator_tool_properties = True  # ?
        # self.gizmo.use_event_handle_all = True  # Swallow all events while hovered
        self.gizmo.position = settings.uv_picker_picker_pos
        self.gizmo.size = settings.uv_picker_picker_size

classes = (
    GRET_GT_uv_picker_gizmo,
    GRET_GGT_uv_picker_gizmo_group,
)

def register(settings):
    for cls in classes:
        bpy.utils.register_class(cls)

    settings.add_property('uv_picker_picker_pos', bpy.props.FloatVectorProperty(
        name="UV Paint Picker Position",
        description="Offset of the UV picker gizmo from the lower left corner of the viewport",
        size=2,
        default=(30.0, 30.0),
    ))
    settings.add_property('uv_picker_picker_size', bpy.props.FloatProperty(
        name="UV Paint Picker Size",
        description="Size in pixels of the UV picker gizmo",
        default=256.0,
        min=64.0,
        max=1024.0,
    ))

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
