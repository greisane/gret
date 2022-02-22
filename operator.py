import bpy

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
