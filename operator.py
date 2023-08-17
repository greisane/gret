from collections import namedtuple
from functools import partial
from itertools import count, groupby, zip_longest
import bpy
import numpy as np
import re

from .log import log, logd
from .helpers import (
    ensure_iterable,
    get_context,
    get_data_collection,
    get_layers_recursive,
    load_property,
    save_property,
    select_only,
    swap_names,
    titlecase,
)

logs = partial(log, category="SAVE")
custom_prop_pattern = re.compile(r'(.+)?\["([^"]+)"\]')
prop_pattern = re.compile(r'(?:(.+)\.)?([^"\.]+)')

class GRET_OT_property_warning(bpy.types.Operator):
    """Changes won't be saved"""

    bl_idname = 'gret.property_warning'
    bl_label = "Not Overridable"
    bl_options = {'INTERNAL'}

def draw_warning_if_not_overridable(layout, bid, data_path):
    """Adds a warning to a layout if the requested property is not available or not overridable."""

    if bid and bid.override_library:
        try:
            if not bid.is_property_overridable_library(data_path):
                layout.operator(GRET_OT_property_warning.bl_idname,
                    icon='ERROR', text="", emboss=False, depress=True)
                return True
        except TypeError:
            pass
    return False

class PropertyWrapper(namedtuple('PropertyWrapper', 'struct prop_name is_custom')):
    """Provides read/write access to a property given its data path."""

    __slots__ = ()

    @classmethod
    def from_path(cls, struct, data_path):
        # To set a property given a data path it's necessary to split the struct and attribute name.
        # `struct.path_resolve(path, False)` returns a bpy_prop, and bpy_prop.data holds the struct.
        # Unfortunately it knows but doesn't expose the attribute name (see `bpy_prop.__str__`)
        # It's also necessary to determine if it's a custom property, the interface is different.
        # Just parse the data path with a regular expression instead.
        try:
            prop_match = custom_prop_pattern.fullmatch(data_path)
            if prop_match:
                if prop_match[1]:
                    struct = struct.path_resolve(prop_match[1])
                prop_name = prop_match[2]
                if prop_name not in struct:
                    return None
                return cls(struct, prop_name, True)

            prop_match = prop_pattern.fullmatch(data_path)
            if prop_match:
                if prop_match[1]:
                    struct = struct.path_resolve(prop_match[1])
                prop_name = prop_match[2]
                if not hasattr(struct, prop_name):
                    return None
                return cls(struct, prop_name, False)
        except ValueError:
            return None

    @property
    def data_path(self):
        return f'["{self.prop_name}"]' if self.is_custom else self.prop_name

    @property
    def title(self):
        if self.is_custom:
            return titlecase(self.prop_name)  # Custom property name should be descriptive enough
        else:
            return f"{getattr(self.struct, 'name', self.struct.bl_rna.name)} {titlecase(self.prop_name)}"

    @property
    def default_value(self):
        if self.is_custom:
            return self.struct.id_properties_ui(self.prop_name).as_dict()['default']
        else:
            return getattr(self.struct.bl_rna.properties[self.prop_name], 'default', None)

    @property
    def value(self):
        if self.is_custom:
            return self.struct[self.prop_name]
        else:
            return save_property(self.struct, self.prop_name)

    @value.setter
    def value(self, new_value):
        if self.is_custom:
            self.struct[self.prop_name] = new_value
        else:
            load_property(self.struct, self.prop_name, new_value)

class PropOp(namedtuple('PropOp', 'prop_wrapper value')):
    __slots__ = ()

    def __new__(cls, struct, data_path, value=None):
        prop_wrapper = PropertyWrapper.from_path(struct, data_path)
        if not prop_wrapper:
            raise RuntimeError(f"Couldn't resolve {data_path}")

        saved_value = prop_wrapper.value
        if value is not None:
            prop_wrapper.value = value

        return super().__new__(cls, prop_wrapper, saved_value)

    def revert(self, context):
        self.prop_wrapper.value = self.value

class PropForeachOp(namedtuple('PropForeachOp', 'collection prop_name values')):
    __slots__ = ()

    def __new__(cls, collection, prop_name, value=None):
        assert isinstance(collection, bpy.types.bpy_prop_collection)

        if len(collection) == 0:
            # Can't investigate array type if there are no elements (would do nothing anyway)
            return super().__new__(cls, collection, prop_name, np.empty(0))

        prop = collection[0].bl_rna.properties[prop_name]
        element_type = type(prop.default)
        num_elements = len(collection) * prop.array_length
        saved_values = np.empty(num_elements, dtype=element_type)
        collection.foreach_get(prop_name, saved_values)
        if value is not None:
            values = np.full(num_elements, value, dtype=element_type)
            collection.foreach_set(prop_name, values)

        return super().__new__(cls, collection, prop_name, saved_values)

    def revert(self, context):
        if self.values.size > 0:
            self.collection.foreach_set(self.prop_name, self.values)

class CallOp(namedtuple('CallOp', 'func args kwargs')):
    __slots__ = ()

    def __new__(cls, func, *args, **kwargs):
        assert callable(func)
        return super().__new__(cls, func, args, kwargs)

    def revert(self, context):
        self.func(*self.args, **self.kwargs)

class SelectionOp(namedtuple('SelectionOp', 'selected_objects active_object collection_hide '
    'layer_hide object_hide')):
    __slots__ = ()

    def __new__(cls, context):
        return super().__new__(cls,
            selected_objects=context.selected_objects[:],
            active_object=context.view_layer.objects.active,
            collection_hide=[(cl, cl.hide_select, cl.hide_viewport, cl.hide_render)
                for cl in bpy.data.collections],
            layer_hide=[(layer, layer.hide_viewport, layer.exclude)
                for layer in get_layers_recursive(context.view_layer.layer_collection)],
            object_hide=[(obj, obj.hide_select, obj.hide_viewport, obj.hide_render)
                for obj in bpy.data.objects])

    def revert(self, context):
        for collection, hide_select, hide_viewport, hide_render in self.collection_hide:
            try:
                collection.hide_select = hide_select
                collection.hide_viewport = hide_viewport
                collection.hide_render = hide_render
            except ReferenceError:
                pass
        for layer, hide_viewport, exclude in self.layer_hide:
            try:
                layer.hide_viewport = hide_viewport
                layer.exclude = exclude
            except ReferenceError:
                pass
        for obj, hide_select, hide_viewport, hide_render in self.object_hide:
            try:
                obj.hide_select = hide_select
                obj.hide_viewport = hide_viewport
                obj.hide_render = hide_render
            except ReferenceError:
                pass
        select_only(context, self.selected_objects)
        try:
            context.view_layer.objects.active = self.active_object
        except ReferenceError:
            pass

class CollectionOp(namedtuple('CollectionOp', 'collection remove_func_name items is_whitelist')):
    __slots__ = ()

    def __new__(cls, collection, items=None):
        assert isinstance(collection, bpy.types.bpy_prop_collection)

        # Find out if there's a remove-like function available
        for func_name in ('remove', 'unlink', ''):
            func = collection.bl_rna.functions.get(func_name)
            if (func is not None
                and sum(param.is_required for param in func.parameters) == 1
                and func.parameters[0].type == 'POINTER'):
                break
        if not func_name:
            raise RuntimeError(f"'{collection.bl_rna.name}' is not supported")

        if items is None:
            # On reverting, remove all but the current items
            return super().__new__(cls, collection, func_name, set(collection), True)
        else:
            # On reverting, remove the specified items
            return super().__new__(cls, collection, func_name, set(items), False)

    def revert(self, context):
        # Allow passing in object names instead of object references
        # Compare types, don't use `isinstance` as that will throw on removed objects
        items = set(self.collection.get(el) if type(el) == str else el for el in self.items)
        items.discard(None)

        remove_func = getattr(self.collection, self.remove_func_name)
        if self.is_whitelist:
            # Remove items not in the set
            for item in set(self.collection) - items:
                logs("Removing", item)
                remove_func(item)
        else:
            # Remove items in the set
            for item in items:
                try:
                    logs("Removing", item)
                    remove_func(item)
                except ReferenceError:
                    pass

class RenameOp(namedtuple('RenameOp', 'bid name other_bid')):
    __slots__ = ()

    def __new__(cls, bid, name, start_num=0, name_format="{name}{num}"):
        data_collection = get_data_collection(bid)
        if data_collection is None:
            raise RuntimeError(f"Type {type(bid).__name__} is not supported")

        saved_name = bid.name
        bid.tag = True  # Not strictly necessary, tagging allows custom naming format to work
        for num in count(start=start_num):
            new_name = name if (num == start_num) else name_format.format(name=name, num=num)
            other_bid = data_collection.get(new_name)
            if not other_bid or bid == other_bid:
                bid.name = new_name
                return super().__new__(cls, bid, saved_name, None)
            elif other_bid and not other_bid.tag:
                swap_names(bid, other_bid)
                return super().__new__(cls, bid, saved_name, other_bid)

    def revert(self, context):
        if self.other_bid:
            try:
                swap_names(self.bid, self.other_bid)
            except ReferenceError:
                pass
        self.bid.name = self.name  # Ensure the name is reverted if swap_names failed
        self.bid.tag = False

class SaveState:
    """Similar to an undo stack. See SaveContext for example usage."""

    def __init__(self, context, name, refresh=False):
        self.context = context
        self.name = name
        self.refresh = refresh
        self.operations = []

    def revert(self):
        while self.operations:
            self._pop_op()
        if self.refresh:
            # Might be necessary in some cases where context.scene.view_layers.update() is not enough
            self.context.scene.frame_set(self.context.scene.frame_current)

    def _push_op(self, op_cls, *args, **kwargs):
        try:
            self.operations.append(op_cls(*args, **kwargs))
            logs("Push", self.operations[-1], max_len=90)
        except Exception as e:
            logs(f"Error pushing {op_cls.__name__}: {e}")

    def _pop_op(self):
        op = self.operations.pop()
        try:
            logs("Pop", op, max_len=90)
            op.revert(self.context)
        except Exception as e:
            logs(f"Error reverting {op.__class__.__name__}: {e}")

    def prop(self, struct, data_paths, values=[None]):
        """Save the specified properties and optionally assign new values."""

        if isinstance(data_paths, str):
            data_paths = data_paths.split()
        if not isinstance(values, list):
            values = [values]
        if len(values) != 1 and len(values) != len(data_paths):
            raise ValueError("Expected either a single value or as many values as data paths")
        for data_path, value in zip_longest(data_paths, values, fillvalue=values[0]):
            self._push_op(PropOp, struct, data_path, value)

    def prop_foreach(self, collection, prop_name, value=None):
        """Save the specified property for all elements in the collection."""

        self._push_op(PropForeachOp, collection, prop_name, value)

    def selection(self):
        """Save the current object selection."""

        self._push_op(SelectionOp, self.context)

    def temporary(self, collection, items):
        """Mark one or more items for deletion."""

        self._push_op(CollectionOp, collection, ensure_iterable(items))

    def temporary_bids(self, bids):
        """Mark one or more IDs for deletion."""

        for bid_type, bids in groupby(ensure_iterable(bids), key=lambda bid: type(bid)):
            if bid_type is not type(None):
                self._push_op(CollectionOp, get_data_collection(bid_type), bids)

    def keep_temporary_bids(self, bids):
        """Keep IDs that were previously marked for deletion."""

        bids = set(ensure_iterable(bids))
        for op in reversed(self.operations):
            if isinstance(op, CollectionOp) and not op.is_whitelist:
                op.items.difference_update(bids)

    def collection(self, collection):
        """Remember the current contents of a collection. Any items created later will be removed."""

        self._push_op(CollectionOp, collection)

    def viewports(self, header_text=None, show_overlays=None, **kwargs):
        """Save and override 3D viewport settings."""

        for area in self.context.screen.areas:
            if area.type == 'VIEW_3D':
                # Don't think there's a way to find out the current header text, reset on reverting
                self._push_op(CallOp, area.header_text_set, None)
                area.header_text_set(header_text)
            for space in area.spaces:
                if space.type == 'VIEW_3D':
                    if show_overlays is not None:
                        self._push_op(PropOp, space.overlay, 'show_overlays', show_overlays)
                    for field_name, field_value in kwargs.items():
                        self._push_op(PropOp, space.shading, field_name, field_value)

    def rename(self, bid, name):
        """Save the IDs current name and give it a new name."""

        self._push_op(RenameOp, bid, name)

    def clone_obj(self, obj, to_mesh=False, parent=None, reset_origin=False):
        """Clones or converts an object. Returns a new, visible scene object with unique data."""

        if to_mesh:
            dg = self.context.evaluated_depsgraph_get()
            new_data = bpy.data.meshes.new_from_object(obj, preserve_all_data_layers=True, depsgraph=dg)
            self.temporary_bids(new_data)
            new_obj = bpy.data.objects.new(obj.name + "_", new_data)
            self.temporary_bids(new_obj)
        else:
            new_data = obj.data.copy()
            self.temporary_bids(new_data)
            new_obj = obj.copy()
            self.temporary_bids(new_obj)
            new_obj.name = obj.name + "_"
            new_obj.data = new_data
        assert new_data.users == 1

        if obj.type == 'MESH':
            # Move object materials to mesh
            for mat_index, mat_slot in enumerate(obj.material_slots):
                if mat_slot.link == 'OBJECT':
                    new_data.materials[mat_index] = mat_slot.material
                    new_obj.material_slots[mat_index].link = 'DATA'

        # New objects are moved to the scene collection, ensuring they're visible
        self.context.scene.collection.objects.link(new_obj)
        new_obj.hide_set(False)
        new_obj.hide_viewport = False
        new_obj.hide_render = False
        new_obj.hide_select = False
        new_obj.parent = parent

        if reset_origin:
            new_data.transform(new_obj.matrix_world)
            bpy.ops.object.origin_set(get_context(new_obj), type='ORIGIN_GEOMETRY', center='MEDIAN')
        else:
            new_obj.matrix_world = obj.matrix_world

        return new_obj

class SaveContext:
    """
    Saves state of various things and keeps track of temporary objects.
    When leaving scope, operations are reverted in the order they were applied.
    Example usage:

    with SaveContext(bpy.context, "test") as save:
        save.prop_foreach(bpy.context.scene.objects, 'location')
        bpy.context.active_object.location = (1, 1, 1)
    """

    def __init__(self, *args, **kwargs):
        self.save = SaveState(*args, **kwargs)

    def __enter__(self):
        return self.save

    def __exit__(self, exc_type, exc_value, traceback):
        self.save.revert()

class StateMachineBaseState:
    def __init__(self, owner):
        self.owner = owner

    def on_enter(self):
        pass

    def on_exit(self):
        pass

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

def show_window(width=0.5, height=0.5):
    """Open a window at the cursor. Size can be pixels or a fraction of the main window size."""

    # Hack from https://blender.stackexchange.com/questions/81974
    with SaveContext(bpy.context, "show_window") as save:
        render = bpy.context.scene.render
        prefs = bpy.context.preferences
        main_window = bpy.context.window_manager.windows[0]
        save.prop(prefs, 'is_dirty view.render_display_type')
        save.prop(render, 'resolution_x resolution_y resolution_percentage')

        render.resolution_x = int(main_window.width * width) if width <= 1.0 else int(width)
        render.resolution_y = int(main_window.height * height) if height <= 1.0 else int(height)
        render.resolution_percentage = 100
        prefs.view.render_display_type = 'WINDOW'
        bpy.ops.render.view_show('INVOKE_DEFAULT')
        return bpy.context.window_manager.windows[-1]

def show_text_window(text, title, width=0.5, height=0.5, font_size=16):
    """Open a window at the cursor displaying the given text."""

    # Open a render preview window, then modify it to show a text editor instead
    window = show_window(width, height)
    area = window.screen.areas[0]
    area.type = 'TEXT_EDITOR'
    space = area.spaces[0]
    assert isinstance(space, bpy.types.SpaceTextEditor)

    # Make a temporary text
    string = text
    text = bpy.data.texts.get(title) or bpy.data.texts.new(name=title)
    text.use_fake_user = False
    text.from_string(string)
    text.cursor_set(0)

    # Minimal interface
    if font_size is not None:
        space.font_size = font_size
    space.show_line_highlight = True
    space.show_line_numbers = False
    space.show_margin = False
    space.show_region_footer = False
    space.show_region_header = False
    space.show_region_ui = False
    space.show_syntax_highlight = False
    space.show_word_wrap = True
    space.text = text

def register(settings, prefs):
    bpy.utils.register_class(GRET_OT_property_warning)

def unregister():
    bpy.utils.unregister_class(GRET_OT_property_warning)
