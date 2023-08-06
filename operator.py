import bpy

class GRET_OT_property_warning(bpy.types.Operator):
    """Changes won't be saved"""

    bl_idname = 'gret.property_warning'
    bl_label = "Not Overridable"
    bl_options = {'INTERNAL'}

def draw_warning_if_not_overridable(layout, obj, data_path):
    if obj and obj.override_library:
        try:
            if not obj.is_property_overridable_library(data_path):
                layout.operator(GRET_OT_property_warning.bl_idname,
                    icon='ERROR', text="", emboss=False, depress=True)
                return True
        except TypeError:
            pass
    return False

class ScopedRestore:
    """Saves attributes of an object and restores them when exiting scope."""

    def __init__(self, obj, field_names):
        self.obj = obj
        if isinstance(field_names, str):
            field_names = field_names.replace(',', ' ').split()
        self.field_names = field_names

    def __enter__(self):
        self.saved_values = {name: getattr(self.obj, name) for name in self.field_names}

    def __exit__(self, exc_type, exc_value, exc_traceback):
        for name, value in self.saved_values.items():
            setattr(self.obj, name, value)

class StateMachineBaseState:
    def __init__(self, owner):
        self.owner = owner

    def on_enter(self):
        pass

    def on_exit(self):
        pass

    # def exit(self):
    #     self.owner.pop_state()

class StateMachineMixin:
    """Simple state machine."""
    state_stack = None
    state_events_on_reentry = True

    @property
    def state(self):
        return self.state_stack[-1] if self.state_stack else None

    def pop_state(self, *args, **kwargs):
        if self.state:
            self.state_stack.pop().on_exit(*args, **kwargs)
            if self.state_events_on_reentry and self.state:
                self.state.on_enter()

    def push_state(self, state_class, *args, **kwargs):
        assert state_class
        new_state = state_class(self)

        if self.state_events_on_reentry and self.state:
            self.state.on_exit()
        if self.state_stack is None:
            self.state_stack = []
        self.state_stack.append(new_state)

        if new_state:
            new_state.on_enter(*args, **kwargs)

class DrawHooksMixin:
    space_type = bpy.types.SpaceView3D
    draw_post_pixel_handler = None
    draw_post_view_handler = None

    def hook(self, context):
        if not self.draw_post_pixel_handler and hasattr(self, "on_draw_post_pixel"):
            self.draw_post_pixel_handler = self.space_type.draw_handler_add(self.on_draw_post_pixel,
                (context,), 'WINDOW', 'POST_PIXEL')

        if not self.draw_post_view_handler and hasattr(self, "on_draw_post_view"):
            self.draw_post_pixel_handler = self.space_type.draw_handler_add(self.on_draw_post_view,
                (context,), 'WINDOW', 'POST_VIEW')

    def unhook(self):
        if self.draw_post_pixel_handler:
            self.space_type.draw_handler_remove(self.draw_post_pixel_handler, 'WINDOW')
            self.draw_post_pixel_handler = None

        if self.draw_post_view_handler:
            self.space_type.draw_handler_remove(self.draw_post_view_handler, 'WINDOW')
            self.draw_post_view_handler = None

def register(settings, prefs):
    bpy.utils.register_class(GRET_OT_property_warning)

def unregister():
    bpy.utils.unregister_class(GRET_OT_property_warning)
