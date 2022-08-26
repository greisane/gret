from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
from collections import namedtuple
from functools import wraps, lru_cache
from mathutils import Vector, Quaternion, Euler
import bpy
import io
import os
import re

from . import prefs

safediv = lambda x, y: x / y if y != 0.0 else 0.0
fmt_pct = lambda pct: f"{pct:.0f}%" if int(pct) == pct else f"{pct:.1f}%"
fmt_fraction = lambda n, count: fmt_pct(safediv(n, count) * 100.0)

def get_name_safe(obj):
    return getattr(obj, 'name', "Unknown") if obj else "None"

def get_object_filepath(obj):
    """Return source filepath of a proxy or library override, otherwise return the working filepath."""

    try:
        if bpy.app.version < (3, 2, 0):
            if obj.proxy and obj.proxy.library:
                return obj.proxy.library.filepath
        if obj.override_library and obj.override_library.reference:
            return obj.override_library.reference.library.filepath
    except:
        pass
    return bpy.data.filepath

def select_only(context, objs):
    """Ensures only the given object or objects are selected."""

    if not hasattr(objs, '__iter__'):
        objs = [objs]
    for obj in context.selected_objects:
        obj.select_set(False)
    for obj in objs:
        obj.hide_viewport = False
        obj.hide_select = False
        obj.select_set(True)
    context.view_layer.objects.active = next(iter(objs), None)

def show_only(context, objs):
    """Ensures only the given object or objects are visible in viewport or render."""

    if not hasattr(objs, '__iter__'):
        objs = [objs]
    for obj in context.scene.objects:
        obj.hide_viewport = True
        obj.hide_render = True
        obj.hide_select = True
    for obj in objs:
        obj.hide_viewport = False
        obj.hide_render = False
        obj.hide_select = False

def is_valid(data_block):
    """Returns whether a reference to a data-block is valid."""

    if not data_block:
        return False
    try:
        data_block.id_data
    except (ReferenceError, KeyError):
        return False
    return True

def get_context(active_obj=None, selected_objs=None):
    """Returns context for single object operators."""

    ctx = {}
    if active_obj and selected_objs:
        # Operate on all the objects, active object is specified. Selected should include active
        ctx['object'] = ctx['active_object'] = active_obj
        selected_objs = selected_objs if active_obj in selected_objs else list(selected_objs) + [active_obj]
        ctx['selected_objects'] = ctx['selected_editable_objects'] = selected_objs
    elif not active_obj and selected_objs:
        # Operate on all the objects, it isn't important which one is active
        ctx['object'] = ctx['active_object'] = next(iter(selected_objs))
        ctx['selected_objects'] = ctx['selected_editable_objects'] = [active_obj]
    elif active_obj and not selected_objs:
        # Operate on a single object
        ctx['object'] = ctx['active_object'] = active_obj
        ctx['selected_objects'] = ctx['selected_editable_objects'] = [active_obj]
    return ctx

def get_collection(context, name, allow_duplicate=False, clean=True):
    """Ensures that a collection with the given name exists in the scene."""

    # collection = bpy.data.collections.get(name)
    collection = None
    collections = [context.scene.collection]
    while collections:
        cl = collections.pop()
        if cl.name == name or allow_duplicate and re.match(rf"^{name}(?:\.\d\d\d)?$", cl.name):
            collection = cl
            break
        collections.extend(cl.children)
        cl = None

    if not collection:
        collection = bpy.data.collections.new(name)
    elif clean:
        for obj in collection.objects[:]:
            collection.objects.unlink(obj)
    if name not in context.scene.collection.children:
        context.scene.collection.children.link(collection)
    return collection

def get_vgroup(obj, name, clean=True):
    """Ensures that a vertex group with the given name exists."""

    vgroup = obj.vertex_groups.get(name)
    if not vgroup:
        vgroup = obj.vertex_groups.new(name=name)
    elif clean:
        vgroup.remove(range(len(obj.data.vertices)))
    return vgroup

def get_modifier(obj, type, name="", index=None):
    """Ensures that a modifier with the given name exists."""

    if name:
        modifier = obj.modifiers.get(name)
    else:
        modifier = next((m for m in obj.modifiers if m.type == type), None)
    if not modifier or modifier.type != type:
        modifier = obj.modifiers.new(type=type, name=name)
    if index is not None:
        index %= len(obj.modifiers)
        if index != obj.modifiers.find(modifier.name):
            ctx = get_context(obj)
            bpy.ops.object.modifier_move_to_index(ctx, modifier=modifier.name, index=index)
    return modifier

def get_node_group(name, type='GeometryNodeTree', clean=True):
    grp = bpy.data.node_groups.get(name)
    if not grp:
        grp = bpy.data.node_groups.new(name=name, type=type)
    elif clean:
        if grp.users > 0:
            # Re-adding the group instead of cleaning it because it will crash if it has users
            bpy.data.node_groups.remove(grp)
            grp = bpy.data.node_groups.new(name=name, type=type)
        else:
            grp.nodes.clear()
            grp.links.clear()
            grp.inputs.clear()
            grp.outputs.clear()
    return grp

def dump_node_group(name):
    """Return source code for a function recreating a node group."""

    grp = bpy.data.node_groups.get(name)
    if not grp:
        return ""

    text = ""
    text += f"def build_{snakecase(grp.name)}(grp):\n"
    text += f"\t# Generated by dump_node_group(\"{grp.name}\")\n"

    def dump_socket(socket):
        nonlocal text
        if hasattr(socket, 'default_value'):
            try:
                text += f"\tsocket.default_value = {repr(socket.default_value[:])}\n"
            except TypeError:
                text += f"\tsocket.default_value = {repr(socket.default_value)}\n"
        text += f"\tsocket.description = '{socket.description}'\n"

    text += "\t# Inputs\n"
    for socket in grp.inputs:
        text += f"\tsocket = grp.inputs.new('{socket.bl_socket_idname}', '{socket.name}')\n"
        dump_socket(socket)

    text += "\t# Outputs\n"
    for socket in grp.outputs:
        text += f"\tsocket = grp.outputs.new('{socket.bl_socket_idname}', '{socket.name}')\n"
        dump_socket(socket)

    text += "\t# Nodes\n"
    skip_prop_ids = {"color", "show_options", "select", "width", "width_hidden", "height"}
    for node in grp.nodes:
        text += f"\tnode = grp.nodes.new('{node.bl_idname}')\n"
        for prop in node.bl_rna.properties:
            prop_id = prop.identifier
            if prop.is_readonly or prop.type in {'COLLECTION', 'POINTER'}:
                continue  # Not supported
            if prop_id.startswith("bl_") or prop_id in skip_prop_ids:
                continue  # Skip cosmetic properties, too much text
            try:
                if getattr(prop, 'is_array', False):
                    value, default_value = getattr(node, prop_id)[:], prop.default_array[:]
                else:
                    value, default_value = getattr(node, prop_id), prop.default
                if value != default_value:
                    text += f"\tnode.{prop_id} = {repr(value)}\n"
            except:
                pass
        for socket_idx, socket in enumerate(node.inputs):
            if socket.bl_idname == 'NodeSocketVirtual' or not hasattr(socket, 'default_value'):
                continue
            text += f"\tnode.inputs[{socket_idx}].default_value = {repr(socket.default_value)}\n"

    def find_socket_index(sockets, socket_identifier):
        for socket_idx, socket in enumerate(sockets):
            if socket.identifier == socket_identifier:
                return socket_idx
        return -1

    text += "\t# Links\n"
    for link in grp.links:
        from_socket_index = find_socket_index(link.from_node.outputs, link.from_socket.identifier)
        to_socket_index = find_socket_index(link.to_node.inputs, link.to_socket.identifier)
        text += f"\tgrp.links.new(\n"
        text += f"\t\tgrp.nodes['{link.from_node.name}'].outputs[{from_socket_index}],\n"
        text += f"\t\tgrp.nodes['{link.to_node.name}'].inputs[{to_socket_index}])\n"

    # For testing
    # temp = ast.parse(text)
    # exec(compile(temp, filename="<ast>", mode="exec"))
    return text

class TempModifier:
    """Convenient modifier wrapper to use in a `with` block to be automatically applied at the end."""

    def __init__(self, obj, type):
        self.obj = obj
        self.type = type

    def __enter__(self):
        self.saved_mode = bpy.context.mode
        if bpy.context.mode == 'EDIT_MESH':
            bpy.ops.object.editmode_toggle()

        self.modifier = self.obj.modifiers.new(type=self.type, name="")
        # Move first to avoid the warning on applying
        ctx = get_context(self.obj)
        bpy.ops.object.modifier_move_to_index(ctx, modifier=self.modifier.name, index=0)

        return self.modifier

    def __exit__(self, exc_type, exc_value, exc_traceback):
        ctx = get_context(self.obj)

        bpy.ops.object.modifier_apply(ctx, modifier=self.modifier.name)

        if self.saved_mode == 'EDIT_MESH':
            bpy.ops.object.editmode_toggle()

SelectionState = namedtuple('SelectionState', [
    'selected',
    'active',
    'collections',
    'layers',
    'objects',
])

def get_layers_recursive(layer):
    yield layer
    for child in layer.children:
        yield from get_layers_recursive(child)

def save_selection():
    """Returns a SelectionState storing the current selection state."""

    return SelectionState(
        selected=bpy.context.selected_objects[:],
        active=bpy.context.view_layer.objects.active,
        collections=[(c, c.hide_select, c.hide_viewport, c.hide_render) for c in bpy.data.collections],
        layers=[(l, l.hide_viewport, l.exclude) for l in
            get_layers_recursive(bpy.context.view_layer.layer_collection)],
        objects=[(o, o.hide_select, o.hide_viewport, o.hide_render) for o in bpy.data.objects],
    )

def load_selection(state):
    """Restores selection state from a SelectionState returned by save_selection()"""

    for collection, hide_select, hide_viewport, hide_render in state.collections:
        if is_valid(collection):
            collection.hide_select = hide_select
            collection.hide_viewport = hide_viewport
            collection.hide_render = hide_render
    for layer, hide_viewport, exclude in state.layers:
        if is_valid(layer):
            layer.hide_viewport = hide_viewport
            layer.exclude = exclude
    for obj, hide_select, hide_viewport, hide_render in state.objects:
        if is_valid(obj):
            obj.hide_select = hide_select
            obj.hide_viewport = hide_viewport
            obj.hide_render = hide_render

    select_only(bpy.context, (obj for obj in state.selected if is_valid(obj)))

    if is_valid(state.active):
        bpy.context.view_layer.objects.active = state.active

def viewport_reveal_all():
    for collection in bpy.data.collections:
        collection.hide_select = False
        collection.hide_viewport = False
    for layer in get_layers_recursive(bpy.context.view_layer.layer_collection):
        layer.hide_viewport = False
        layer.exclude = False

def save_properties(obj):
    """Returns a dictionary storing the properties of a Blender object."""

    saved = {}
    for prop in obj.bl_rna.properties:
        if not prop.is_runtime:
            # Only save user properties
            continue
        prop_id = prop.identifier
        try:
            if prop.type == 'COLLECTION':
                saved[prop_id] = [save_properties(el) for el in getattr(obj, prop_id)]
            elif getattr(prop, 'is_array', False):
                saved[prop_id] = getattr(obj, prop_id)[:]
            else:
                saved[prop_id] = getattr(obj, prop_id)
        except:
            continue
    return saved

def load_properties(obj, saved):
    """Restores properties from a dictionary returned by save_properties()"""

    for prop_id, value in saved.items():
        try:
            prop = obj.bl_rna.properties[prop_id]
            if prop.type == 'COLLECTION':
                collection = getattr(obj, prop_id)
                collection.clear()
                for saved_el in value:
                    el = collection.add()
                    load_properties(el, saved_el)
            elif not prop.is_readonly:
                setattr(obj, prop_id, value)
        except:
            continue

def is_defaulted(obj):
    """Returns whether the properties of an object are set to their default values."""
    # This is not extensively tested, it should work for most things

    for prop in obj.bl_rna.properties:
        if not prop.is_runtime:
            # Only consider user properties
            continue
        prop_id = prop.identifier
        try:
            if prop.type == 'COLLECTION':
                # Consider that if the collection has any elements, then it's not default
                current = len(getattr(obj, prop_id))
                default = 0
            elif prop.type == 'POINTER':
                current = getattr(obj, prop_id)
                default = None
            elif getattr(prop, 'is_array', False):
                current = getattr(obj, prop_id)[:]
                default = prop.default_array[:]
            else:
                current = getattr(obj, prop_id)
                default = getattr(prop, 'default', type(current)())

            if current != default:
                return False
        except TypeError:
            # The value type is not trivially initializable, omit it
            continue

    return True

def get_topmost_parent(obj):
    while obj.parent:
        obj = obj.parent
    return obj

def get_flipped_name(name):
    """Returns the given name with flipped L/R affixes, or None if not applicable."""

    def flip_LR(s):
        if "L" in s.upper():
            return s.replace("l", "r").replace("L", "R")
        else:
            return s.replace("r", "l").replace("R", "L")

    match = re.match(r'(.+)([_\.][LlRr])$', name)  # Suffix
    if match:
        return match[1] + flip_LR(match[2])

    match = re.match(r'^([LlRr][_\.])(.+)', name)  # Prefix
    if match:
        return flip_LR(match[1]) + match[2]

    return None

def swap_object_names(obj1, obj2):
    name1, name2 = obj1.name, obj2.name
    obj1.name = name2
    obj2.name = name1
    obj1.name = name2

def beep(pitch=0, num=2):
    if not prefs.use_beeps:
        return
    try:
        import winsound
        freq = 800 + 100 * pitch
        for _ in range(num):
            winsound.Beep(freq, 50)
    except:
        pass

def intercept(_func=None, error_result=None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if not prefs.debug:
                # Redirect output
                stdout = io.StringIO()
                try:
                    from contextlib import redirect_stdout
                    with redirect_stdout(stdout):
                        result = func(*args, **kwargs)
                except Exception as e:
                    # import traceback
                    # traceback.print_exc()
                    result = error_result
            else:
                result = func(*args, **kwargs)
            return result
        return wrapper

    if _func is None:
        return decorator
    else:
        return decorator(_func)

def get_export_path(path, fields):
    """Returns an absolute path from an export path."""

    fields.update({
        'file': os.path.splitext(bpy.path.basename(bpy.data.filepath))[0],
    })
    path = path.format(**fields)

    if 'suffix' in fields:
        path, ext = os.path.splitext(path)
        path = path + fields['suffix'] + ext

    path = bpy.path.abspath(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path

def fail_if_invalid_export_path(path, field_names):
    """Raises an exception if the export path is not valid."""

    if not path:
        raise Exception("Invalid export path.")

    if path.startswith("//") and not bpy.data.filepath:
        # While not technically wrong the file will likely end up at blender working directory
        raise Exception("Can't use a relative export path before the file is saved.")
    if os.path.isdir(path):
        raise Exception("Export path must be a file path.")

    # Check that the export path is valid
    try:
        fields = {s: "" for s in field_names}
        dirpath = os.path.dirname(get_export_path(path, fields))
    except Exception as e:
        raise Exception(f"Invalid export path: {e}")

    try:
        os.makedirs(dirpath)
    except PermissionError:
        raise Exception("Invalid export path.")
    except OSError:
        pass  # Directory already exists

def fail_if_no_operator(bl_idname, submodule=bpy.ops.object):
    """Raises an exception if the operator is not available."""

    try:
        # Use getattr, hasattr seems to always return True
        getattr(submodule, bl_idname)
    except AttributeError:
        raise Exception(f"Operator {bl_idname} is required and couldn't be found.")

def get_nice_export_report(filepaths, elapsed):
    """Returns text informing the user of the files that were exported."""

    if len(filepaths) > 5:
        return f"{len(filepaths)} files exported in {elapsed:.2f}s."
    if filepaths:
        filenames = [bpy.path.basename(filepath) for filepath in filepaths]
        return f"Exported {', '.join(filenames)} in {elapsed:.2f}s."
    return "Nothing exported."

def snakecase(s):
    """Convert string into snake case."""

    s = re.sub(r"[\-\.\s]", '_', str(s))
    if not s:
        return s
    s = s[0] + re.sub(r"[^_][A-Z]+", lambda m: m.group(0)[0] + "_" + m.group(0)[1:], s[1:])
    return s.lower()

two_letter_words = frozenset(("an", "as", "at", "be", "bi", "by", "ex", "go", "he", "hi", "if",
    "in", "is", "it", "mu", "my", "no", "of", "on", "or", "ox", "pi", "re", "to", "up", "us", "we"))
titlecase_word = lambda s: s[0].upper() + s[1:] if len(s) != 2 or s in two_letter_words else s.upper()
def titlecase(s):
    """Convert string into sentence case."""

    if not s:
        return s
    return " ".join(titlecase_word(word) for word in snakecase(s).split("_"))

def path_split_all(path):
    """Returns a path split into a list of its parts."""

    all_parts = []
    while True:
        parts = os.path.split(path)
        if parts[0] == path:  # Sentinel for absolute paths
            all_parts.insert(0, parts[0])
            break
        elif parts[1] == path:  # Sentinel for relative paths
            all_parts.insert(0, parts[1])
            break
        else:
            path = parts[0]
            all_parts.insert(0, parts[1])
    return all_parts

@lru_cache(maxsize=4095)
def levenshtein_distance(string1, string2):
    """Returns the minimum number of operations required to transform one string into the other."""

    if not string1:
        return len(string2)
    if not string2:
        return len(string1)
    if string1[0] == string2[0]:
        return levenshtein_distance(string1[1:], string2[1:])
    l1 = levenshtein_distance(string1, string2[1:])
    l2 = levenshtein_distance(string1[1:], string2)
    l3 = levenshtein_distance(string1[1:], string2[1:])
    return 1 + min(l1, l2, l3)

def link_properties(from_obj, from_data_path, to_obj, to_data_path, invert=False):
    """Creates a simple driver linking properties between two objects."""

    if not to_obj.animation_data:
        to_obj.animation_data_create()
    fc = to_obj.driver_add(to_data_path)
    fc.driver.expression = '1 - var' if invert else 'var'
    fc.driver.type = 'SCRIPTED'
    fc.driver.use_self = True
    var = fc.driver.variables.new()
    var.name = 'var'
    var.type = 'SINGLE_PROP'
    tgt = var.targets[0]
    tgt.data_path = from_data_path
    tgt.id = from_obj

def remove_subsequence(seq, subseq):
    """Removes the first instance of a subsequence from another sequence."""

    for i in range(0, len(seq) - len(subseq) + 1):
        j = -1
        for j, el in enumerate(subseq):
            if seq[i+j] != el:
                j = -1
                break
        if j == len(subseq) - 1:
            del seq[i:i+len(subseq)]
            break
    return seq

def get_visible_objects_and_duplis(context):
    """Loop over (object, matrix) pairs."""

    dg = context.evaluated_depsgraph_get()
    for dup in dg.object_instances:
        if dup.is_instance:
            obj = dup.instance_object
            yield obj, dup.matrix_world
        else:
            obj = dup.object
            yield obj, obj.matrix_world

def get_tools_from_space_and_mode(space_type, context_mode):
    return ToolSelectPanelHelper._tool_class_from_space_type(space_type)._tools[context_mode]
