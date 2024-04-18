from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
from bpy.ops import op_as_string
from contextlib import contextmanager
from functools import wraps, lru_cache
from itertools import islice
from mathutils import Matrix
from typing import Sequence
import bpy
import io
import os
import re

from . import prefs

safediv = lambda x, y: x / y if y != 0.0 else 0.0
fmt_pct = lambda pct: f"{pct:.0f}%" if int(pct) == pct else f"{pct:.1f}%"
fmt_fraction = lambda x, y: fmt_pct(safediv(x, y) * 100.0)

class ConstantCurve:
    """Mimics FCurve and always returns the same value on evaluation."""
    def __init__(self, value=0.0):
        self.value = value
    def evaluate(self, frame_index):
        return self.value

class namedtupleish:
    """Functions similarly to a mutable namedtuple. Not terribly tested."""

    def __new__(cls, typename, field_names):
        class _namedtupleish(Sequence):
            __slots__ = ()
            def __init__(self, *args):
                for slot, arg in zip(self.__slots__, args):
                    setattr(self, slot, arg)
            def __repr__(self):
                return f'{self.__class__.__name__}({", ".join(f"{s}={getattr(self, s)}" for s in self.__slots__)})'
            def __getitem__(self, index):
                return getattr(self, self.__slots__[index])
            def __len__(self):
                return len(self.__slots__)
        return type(typename, (_namedtupleish,), {'__slots__': field_names.split()})

def get_name_safe(bid, /):
    return getattr(bid, 'name', "Unknown") if bid else "None"

def get_bid_filepath(bid, /):
    """Return source filepath of a proxy or library override, otherwise return the working filepath."""

    try:
        if bpy.app.version < (3, 2, 0):
            if bid.proxy and bid.proxy.library:
                return bid.proxy.library.filepath
        if bid.override_library and bid.override_library.reference:
            return bid.override_library.reference.library.filepath
    except:
        pass
    return bpy.data.filepath

def select_only(context, objs, /):
    """Ensures only the given object or objects are selected."""

    for obj in context.selected_objects:
        obj.select_set(False)
    for obj in ensure_iterable(objs):
        try:
            obj.hide_viewport = False
            obj.hide_select = False
            obj.select_set(True)
            context.view_layer.objects.active = obj
        except ReferenceError:
            pass

def show_only(context, objs, /):
    """Ensures only the given object or objects are visible in viewport or render."""

    for obj in context.scene.objects:
        obj.hide_viewport = True
        obj.hide_render = True
        obj.hide_select = True
    for obj in ensure_iterable(objs):
        try:
            obj.hide_viewport = False
            obj.hide_render = False
            obj.hide_select = False
        except ReferenceError:
            pass

def is_valid(bid, /):
    """Returns whether a reference to a data-block is valid."""

    if bid is None:
        return False
    try:
        bid.id_data
    except (ReferenceError, KeyError):
        return False
    return True

def get_object_context_override(active_obj, selected_objs=[]):
    selected_objs = list(selected_objs)
    if active_obj not in selected_objs:
        selected_objs.append(active_obj)
    return {
        'object': active_obj,
        'active_object': active_obj,
        'selected_objs': selected_objs,
        'selected_editable_objects': selected_objs,
    }

def with_object(operator, active_obj, selected_objs=[], /, *args, **kwargs):
    with bpy.context.temp_override(**get_object_context_override(active_obj, selected_objs)):
        operator(*args, **kwargs)

def try_with_object(operator, active_obj, selected_objs=[], /, *args, **kwargs):
    try:
        return with_object(operator, active_obj, selected_objs, *args, **kwargs)
    except RuntimeError:
        pass

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

def get_vgroup(obj, name="", clean=True):
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
            with_object(bpy.ops.object.modifier_move_to_index, obj, modifier=modifier.name, index=index)
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
            grp.interface.clear()
    return grp

def dump_node_group(node_group_or_name, abbreviated=True):
    """Return source code for a function that recreates a node group."""

    grp = (bpy.data.node_groups.get(node_group_or_name)
        if isinstance(node_group_or_name, str)
        else node_group_or_name)
    if not grp:
        return ""

    text = f"def build_{snakecase(grp.name)}_node_group(grp=None):\n" \
        f"\t# Generated by dump_node_group({grp.name!r}, abbreviated={abbreviated})\n" \
        f"\tgrp = grp or bpy.data.node_groups.new(name={grp.name!r}, type='GeometryNodeTree')\n" \
        f"\tgrp.is_modifier = {grp.is_modifier}\n" \
        f"\tgrp.is_tool = {grp.is_tool}\n" \
        f"\tinterface, nodes, links = grp.interface, grp.nodes, grp.links\n"

    def dump_prop_if_not_default(struct, struct_name, prop_or_id):
        nonlocal text
        prop = (struct.bl_rna.properties.get(prop_or_id, None)
            if isinstance(prop_or_id, str)
            else prop_or_id)
        if not prop:
            return  # Missing property
        prop_id = prop.identifier
        if prop.is_readonly or prop_id.startswith('bl_') or prop.type in {'COLLECTION', 'POINTER'}:
            return  # Not supported

        try:
            if getattr(prop, 'is_array', False):
                value, default_value = getattr(struct, prop_id)[:], prop.default_array[:]
            elif prop_id in {'min_value', 'max_value'}:
                # Silly exception due to inf, not sure how to handle it
                value, default_value = getattr(struct, prop_id), None
            else:
                value, default_value = getattr(struct, prop_id), prop.default
            if value != default_value:
                text += f"\t{struct_name}.{prop_id} = {value!r}\n"
        except AttributeError:
             pass

    if not abbreviated:
        skip_socket_prop_ids = set()
        skip_node_types = set()
        skip_node_prop_ids = {'select'}
    else:
        skip_socket_prop_ids = {'description', 'force_non_field', 'hide_in_modifier', 'hide_value',
            'max_value', 'min_value', 'single_value', 'subtype'}
        skip_node_types = {'FRAME'}
        skip_node_prop_ids = {'color', 'height', 'hide', 'name', 'location', 'show_options',
            'show_preview', 'show_texture', 'use_custom_color', 'width', 'width_hidden'}

    if grp.interface.items_tree:
        text += "\t# Sockets\n"
        for item in grp.interface.items_tree:
            text += f"\tsocket = interface.new_socket(name={item.name!r}, " \
                f"in_out={item.in_out!r}, socket_type={item.socket_type!r})\n"
            for prop in item.bl_rna.properties:
                if prop.identifier not in skip_socket_prop_ids:
                    dump_prop_if_not_default(item, 'socket', prop)

    if grp.nodes:
        text += "\t# Nodes\n"
        node_names = {}
        node_name_digits = len(str(len(grp.nodes)))

        for node_index, node in enumerate(grp.nodes):
            if node.type in skip_node_types:
                continue

            node_names[node] = node_name = f'n{node_index:0{node_name_digits}}'
            text += f"\tnode = {node_name} = nodes.new({node.bl_idname!r})\n"
            if not abbreviated and node.parent:
                # Nodes are in hierarchical order so that saves the hassle of dumping ancestors first
                text += f"\tnode.parent = {node_names[node.parent]}\n"

            for prop in node.bl_rna.properties:
                if prop.identifier not in skip_node_prop_ids:
                    dump_prop_if_not_default(node, 'node', prop)

            for socket_idx, socket in enumerate(node.inputs):
                socket_name = f'node.inputs[{socket_idx}]'
                dump_prop_if_not_default(socket, socket_name, 'default_value')
                if not abbreviated:
                    dump_prop_if_not_default(socket, socket_name, 'hide')

            for socket_idx, socket in enumerate(node.outputs):
                socket_name = f'node.outputs[{socket_idx}]'
                if not abbreviated:
                    dump_prop_if_not_default(socket, socket_name, 'hide')

    if grp.links:
        def find_socket_index(sockets, socket_identifier):
            for socket_idx, socket in enumerate(sockets):
                if socket.identifier == socket_identifier:
                    return socket_idx
            return -1

        text += "\t# Links\n"
        line = "\tfor from_node, from_index, to_node, to_index in ["
        chunk = ""

        for link in grp.links:
            from_socket_index = find_socket_index(link.from_node.outputs, link.from_socket.identifier)
            to_socket_index = find_socket_index(link.to_node.inputs, link.to_socket.identifier)
            chunk += f"({node_names[link.from_node]}, {from_socket_index}, " \
                f"{node_names[link.to_node]}, {to_socket_index})"
            if len(line) + len(chunk) >= 100:
                text += line + ",\n"
                line = "\t\t"
                chunk = chunk.removeprefix(", ")
            line += chunk
            chunk = ", "

        text += line + "]:\n"
        text += "\t\tlinks.new(from_node.outputs[from_index], to_node.inputs[to_index])\n"

    text += "\treturn grp\n"

    # Test that it compiles
    import ast
    temp = ast.parse(text)
    exec(compile(temp, filename="<ast>", mode='exec'))

    return text

def get_modifier_mask(obj, key=None):
    """Return a modifier mask for use with gret.shape_key_apply_modifiers."""

    if callable(key):
        mask = [key(modifier) for modifier in obj.modifiers]
    elif hasattr(key, '__iter__'):
        mask = [bool(el) for el in key]
    else:
        mask = [True] * len(obj.modifiers)
    return mask[:32] + [False] * (32 - len(mask))

@contextmanager
def instant_modifier(obj, type):
    """Create a modifier and automatically apply it when leaving scope."""

    saved_mode = bpy.context.mode
    if bpy.context.mode == 'EDIT_MESH':
        bpy.ops.object.editmode_toggle()

    try:
        modifier = obj.modifiers.new(type=type, name="")
        # Move first to avoid the warning on applying
        with_object(bpy.ops.object.modifier_move_to_index, obj, modifier=modifier.name, index=0)

        yield modifier

        if obj.data.shape_keys and obj.data.shape_keys.key_blocks:
            with_object(bpy.ops.gret.shape_key_apply_modifiers, obj,
                modifier_mask=get_modifier_mask(obj, key=lambda mod: mod == modifier))
        else:
            with_object(bpy.ops.object.modifier_apply, obj, modifier=modifier.name)
    finally:
        if saved_mode == 'EDIT_MESH':
            bpy.ops.object.editmode_toggle()

def swap_names(bid1, bid2, /):
    if bid1 == bid2:
        return
    name1, name2 = bid1.name, bid2.name
    bid1.name = name2
    bid2.name = name1
    bid1.name = name2

def get_layers_recursive(layer, /):
    yield layer
    for child in layer.children:
        yield from get_layers_recursive(child)

def viewport_reveal_all(context, /):
    for collection in bpy.data.collections:
        collection.hide_select = False
        collection.hide_viewport = False
    for layer in get_layers_recursive(context.view_layer.layer_collection):
        layer.hide_viewport = False
        layer.exclude = False
    # Not sure if this is necessary, it's not really reliable. Does object.visible_get() care?
    space_data = context.space_data
    if space_data and getattr(space_data, 'local_view', False):
        bpy.ops.view3d.localview()

def save_property(struct, prop_name, /):
    """Returns a Python representation of a Blender property value."""

    prop = struct.bl_rna.properties[prop_name]
    if prop.type == 'COLLECTION':
        return [save_properties(el) for el in getattr(struct, prop_name)]
    elif getattr(prop, 'is_array', False):
        return reshape(ravel(getattr(struct, prop_name)), prop.array_dimensions)
    else:
        return getattr(struct, prop_name)

def save_properties(struct, /):
    """Returns a dictionary representing the properties of a Blender struct."""

    saved = {}
    for prop in struct.bl_rna.properties:
        if not prop.is_runtime:
            # Only save user properties
            continue
        saved[prop.identifier] = save_property(struct, prop.identifier)
    return saved

def load_property(struct, prop_name, value, /):
    """Sets the value of a property from its Python representation."""

    prop = struct.bl_rna.properties[prop_name]
    if prop.type == 'COLLECTION':
        collection = getattr(struct, prop_name)
        collection.clear()
        for saved_el in value:
            el = collection.add()
            load_properties(el, saved_el)
    elif not prop.is_readonly:
        if prop.subtype == 'MATRIX' and not isinstance(value, Matrix):
            # Setting a matrix property like matrix_world has some side effects that don't happen
            # when passing in Python types. It worked as expected for the vector properties I tried,
            # so I guess this workaround for matrices only is sufficient for now.
            value = Matrix(value)
        setattr(struct, prop_name, value)

def load_properties(struct, saved, /):
    """Restores properties from a dictionary returned by save_properties()"""

    for prop_name, value in saved.items():
        load_property(struct, prop_name, value)

def get_topmost_parent(obj, /):
    while obj.parent:
        obj = obj.parent
    return obj

lr = {'l': 'r', 'L': 'R', 'r': 'l', 'R': 'L',
    'left': 'right', 'Left': 'Right', 'right': 'left', 'Right': 'Left'}

def flip_name(s, /, suffix_only=False):
    """Returns the given name with flipped side affixes, or None if not applicable."""

    if not suffix_only:
        # Prefix with no delimiter, case sensitive (lBone -> rBone)
        if match := re.match(r"^([lr]|[lL]eft|[rR]ight)[A-Z]", s):
            return lr[match[1]] + s[len(match[1]):]
        # Prefix with delimiter
        if re.match(r"^[LlRr][_.].", s):
            return lr[s[0]] + s[1:]
    # Suffix with delimiter
    if re.match(r".+[_.][LlRr]$", s):
        return s[:-1] + lr[s[-1]]
    return None

def flip_names(s, /):
    """Flips all names with side affixes found in the string."""

    # Prefix with no delimiter, case sensitive (lBone -> rBone)
    s = re.sub(r"\b([lr]|[lL]eft|[rR]ight)[A-Z]", lambda m: lr[m[1]] + m[0][len(m[1]):], s)
    # Prefix with delimiter
    s = re.sub(r"\b[LlRr][_.].", lambda m: lr[m[0][0]] + m[0][1:], s)
    # Suffix with delimiter
    s = re.sub(r".[_.][LlRr]\b", lambda m: m[0][:-1] + lr[m[0][-1]], s)
    return s

def beep(pitch=0, num=2):
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

def fail_if_invalid_export_path(path, allowed_field_names):
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
        fields = {s: "" for s in allowed_field_names}
        dirpath = os.path.dirname(get_export_path(path, fields))
    except Exception as e:
        raise Exception(f"Invalid export path: {e}")

    try:
        os.makedirs(dirpath)
    except PermissionError:
        raise Exception("Invalid export path.")
    except OSError:
        pass  # Directory already exists

def gret_operator_exists(bl_idname, /):
    """Returns whether the operator is available."""

    return hasattr(bpy.types, "GRET_OT_" + bl_idname.removeprefix("gret."))

def get_nice_export_report(filepaths, elapsed):
    """Returns text informing the user of the files that were exported."""

    if len(filepaths) > 5:
        return f"{len(filepaths)} files exported in {elapsed:.2f}s."
    if filepaths:
        filenames = [bpy.path.basename(filepath) for filepath in filepaths]
        return f"Exported {', '.join(filenames)} in {elapsed:.2f}s."
    return "Nothing exported."

def ensure_starts_with(s, prefix, /):
    return s if s.startswith(prefix) else prefix + s

def snakecase(s, /):
    """Convert string into snake case."""

    s = re.sub(r"[\-\.\s]", '_', str(s))
    if not s:
        return s
    s = s[0] + re.sub(r"[^_][A-Z]+", lambda m: m.group(0)[0] + "_" + m.group(0)[1:], s[1:])
    return s.lower()

two_letter_words = frozenset(("an", "as", "at", "be", "bi", "by", "ex", "go", "he", "hi", "if",
    "in", "is", "it", "mu", "my", "no", "of", "on", "or", "ox", "pi", "re", "to", "up", "us", "we"))
titlecase_word = lambda s: s[0].upper() + s[1:] if s and len(s) != 2 or s in two_letter_words else s.upper()

def titlecase(s, /):
    """Convert string into sentence case."""

    if not s:
        return s
    return " ".join(titlecase_word(word) for word in snakecase(s).split("_"))

def sentence_join(seq, /, ignore_empty=True):
    """Concatenate a sequence with commas. Last element is concatenated with 'and' instead."""

    seq = list(str(el) for el in seq if not ignore_empty or str(el))
    if len(seq) >= 2:
        return " and ".join((", ".join(seq[:-1]), seq[-1]))
    elif len(seq) == 1:
        return seq[0]
    return ""

def path_split_all(path, /):
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

# There might be a builtin map or property in bl_rna that returns this, I couldn't find it
# https://docs.blender.org/api/3.5/bpy_types_enum_items/id_type_items.html
bpy_type_to_id_type = {
    bpy.types.Action: 'ACTION',
    bpy.types.Armature: 'ARMATURE',
    bpy.types.Brush: 'BRUSH',
    bpy.types.CacheFile: 'CACHEFILE',
    bpy.types.Camera: 'CAMERA',
    bpy.types.Collection: 'COLLECTION',
    bpy.types.Curve: 'CURVE',
    bpy.types.Curves: 'CURVES',
    # bpy.types.Font: 'FONT',  # No such type
    bpy.types.GreasePencil: 'GREASEPENCIL',
    bpy.types.Image: 'IMAGE',
    bpy.types.Key: 'KEY',
    bpy.types.Lattice: 'LATTICE',
    bpy.types.Library: 'LIBRARY',
    bpy.types.Light: 'LIGHT',
    bpy.types.LightProbe: 'LIGHT_PROBE',
    # bpy.types.LineStyle: 'LINESTYLE',  # No such type
    bpy.types.Mask: 'MASK',
    bpy.types.Material: 'MATERIAL',
    bpy.types.Mesh: 'MESH',
    bpy.types.MetaBall: 'META',
    bpy.types.MovieClip: 'MOVIECLIP',
    bpy.types.NodeTree: 'NODETREE',
    bpy.types.Object: 'OBJECT',
    bpy.types.PaintCurve: 'PAINTCURVE',
    bpy.types.Palette: 'PALETTE',
    bpy.types.Particle: 'PARTICLE',
    bpy.types.PointCloud: 'POINTCLOUD',
    bpy.types.Scene: 'SCENE',
    # bpy.types.Simulation: 'SIMULATION',  # No such type
    bpy.types.Sound: 'SOUND',
    bpy.types.Speaker: 'SPEAKER',
    bpy.types.Text: 'TEXT',
    bpy.types.Texture: 'TEXTURE',
    bpy.types.Volume: 'VOLUME',
    bpy.types.WindowManager: 'WINDOWMANAGER',
    bpy.types.WorkSpace: 'WORKSPACE',
    bpy.types.World: 'WORLD',
}

bpy_type_to_data_collection_name = {
    bpy.types.Action: 'actions',                    # BlendDataActions
    bpy.types.Armature: 'armatures',                # BlendDataArmatures
    bpy.types.Brush: 'brushes',                     # BlendDataBrushes
    bpy.types.CacheFile: 'cache_files',             # BlendDataCacheFiles
    bpy.types.Camera: 'cameras',                    # BlendDataCameras
    bpy.types.Collection: 'collections',            # BlendDataCollections
    bpy.types.CompositorNodeTree: 'node_groups',    # BlendDataNodeTrees
    bpy.types.Curve: 'curves',                      # BlendDataCurves
    # bpy.types.Font: 'fonts',                      # BlendDataFonts -- no such type
    bpy.types.GeometryNodeTree: 'node_groups',      # BlendDataNodeTrees
    bpy.types.GreasePencil: 'grease_pencils',       # BlendDataGreasePencils
    # bpy.types.HairCurve: 'hair_curves',           # BlendDataHairCurves -- no such type
    bpy.types.Image: 'images',                      # BlendDataImages
    bpy.types.Lattice: 'lattices',                  # BlendDataLattices
    bpy.types.Library: 'libraries',                 # BlendDataLibraries
    bpy.types.LightProbe: 'lightprobes',            # BlendDataProbes
    bpy.types.Light: 'lights',                      # BlendDataLights
    # bpy.types.LineStyle: 'linestyles',            # BlendDataLineStyles -- no such type
    bpy.types.Mask: 'masks',                        # BlendDataMasks
    bpy.types.Material: 'materials',                # BlendDataMaterials
    bpy.types.Mesh: 'meshes',                       # BlendDataMeshes
    bpy.types.MetaBall: 'metaballs',                # BlendDataMetaBalls
    bpy.types.MovieClip: 'movieclips',              # BlendDataMovieClips
    bpy.types.NodeGroup: 'node_groups',             # BlendDataNodeTrees
    bpy.types.Object: 'objects',                    # BlendDataObjects
    bpy.types.PaintCurve: 'paint_curves',           # BlendDataPaintCurves
    bpy.types.Palette: 'palettes',                  # BlendDataPalettes
    bpy.types.Particle: 'particles',                # BlendDataParticles
    bpy.types.PointCloud: 'pointclouds',            # BlendDataPointClouds
    bpy.types.Scene: 'scenes',                      # BlendDataScenes
    bpy.types.Screen: 'screens',                    # BlendDataScreens
    bpy.types.ShaderNodeTree: 'node_groups',        # BlendDataNodeTrees
    # bpy.types.ShapeKey: 'shape_keys',             # BlendData.shape_keys?
    bpy.types.Sound: 'sounds',                      # BlendDataSounds
    bpy.types.Speaker: 'speakers',                  # BlendDataSpeakers
    bpy.types.Text: 'texts',                        # BlendDataTexts
    bpy.types.Texture: 'textures',                  # BlendDataTextures
    bpy.types.TextureNodeTree: 'node_groups',       # BlendDataNodeTrees
    bpy.types.Volume: 'volumes',                    # BlendDataVolumes
    bpy.types.WindowManager: 'window_managers',     # BlendDataWindowManagers
    bpy.types.WorkSpace: 'workspaces',              # BlendDataWorkSpaces
    bpy.types.World: 'worlds',                      # BlendDataWorlds
}

def get_data_collection(bid_or_type, /):
    """Return the bpy.data collection that the ID belongs to, or None if not applicable."""

    if isinstance(bid_or_type, bpy.types.ID):
        bid_or_type = type(bid_or_type)
    return getattr(bpy.data, bpy_type_to_data_collection_name.get(bid_or_type, ''), None)

def link_properties(from_bid, from_data_path, to_bid, to_data_path, invert=False):
    """Create a simple driver linking properties between two IDs."""

    if not to_bid.animation_data:
        to_bid.animation_data_create()
    fc = to_bid.driver_add(to_data_path)
    fc.driver.expression = '1 - var' if invert else 'var'
    fc.driver.type = 'SCRIPTED'
    fc.driver.use_self = True
    var = fc.driver.variables.new()
    var.name = 'var'
    var.type = 'SINGLE_PROP'
    tgt = var.targets[0]
    tgt.data_path = from_data_path
    tgt.id_type = bpy_type_to_id_type.get(type(from_bid))
    tgt.id = from_bid

def ensure_iterable(seq_or_el, /):
    return seq_or_el if hasattr(seq_or_el, '__iter__') and not isinstance(seq_or_el, str) else (seq_or_el,)

def remove_subsequence(seq, subseq, /):
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

def partition(iterable, /, key=lambda item: item):
    """Returns two lists containing items for which key(item) returns True or False respectively."""

    a, b = [], []
    for el in iterable:
        (a if key(el) else b).append(el)
    return a, b

def first_index(iterable, /, key=lambda item: item):
    """Return the index of the first item for which key(item) returns True, or -1 otherwise."""

    return next((n for n, el in enumerate(iterable) if key(el)), -1)

def ravel(iterable, /):
    """Return a contiguous flattened iterable."""

    for el in iterable:
        if not isinstance(el, str):
            try:
                yield from ravel(el)
                continue
            except TypeError:
                pass
        yield el

def reshape(iterable, /, shape):
    """Clumsy Python implementation of numpy.reshape. Accepts zeros in the shape tuple."""

    it = iter(iterable)
    if len(shape) > 1 and shape[1] != 0:
        return tuple(reshape(it, shape[1:]) for _ in range(shape[0]))
    else:
        return tuple(islice(it, shape[0]))

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
