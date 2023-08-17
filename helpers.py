from bl_ui.space_toolsystem_common import ToolSelectPanelHelper
from bpy.ops import op_as_string
from collections import namedtuple
from functools import wraps, lru_cache
import bpy
import io
import os
import re

from . import prefs

safediv = lambda x, y: x / y if y != 0.0 else 0.0
fmt_pct = lambda pct: f"{pct:.0f}%" if int(pct) == pct else f"{pct:.1f}%"
fmt_fraction = lambda x, y: fmt_pct(safediv(x, y) * 100.0)

def get_name_safe(bid):
    return getattr(bid, 'name', "Unknown") if bid else "None"

def get_bid_filepath(bid):
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

def select_only(context, objs):
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

def show_only(context, objs):
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

def is_valid(bid):
    """Returns whether a reference to a data-block is valid."""

    if bid is None:
        return False
    try:
        bid.id_data
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

def get_modifier_mask(obj, key=None):
    """Return a modifier mask for use with gret.shape_key_apply_modifiers."""

    if callable(key):
        mask = [key(modifier) for modifier in obj.modifiers]
    elif hasattr(key, '__iter__'):
        mask = [bool(el) for el in key]
    else:
        mask = [True] * len(obj.modifiers)
    return mask[:32] + [False] * (32 - len(mask))

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

    def __exit__(self, exc_type, exc_value, traceback):
        ctx = get_context(self.obj)

        if self.obj.data.shape_keys and self.obj.data.shape_keys.key_blocks:
            bpy.ops.gret.shape_key_apply_modifiers(ctx,
                modifier_mask=get_modifier_mask(self.obj, key=lambda mod: mod == self.modifier))
        else:
            bpy.ops.object.modifier_apply(ctx, modifier=self.modifier.name)

        if self.saved_mode == 'EDIT_MESH':
            bpy.ops.object.editmode_toggle()

def swap_names(bid1, bid2):
    if bid1 == bid2:
        return
    name1, name2 = bid1.name, bid2.name
    bid1.name = name2
    bid2.name = name1
    bid1.name = name2

def get_layers_recursive(layer):
    yield layer
    for child in layer.children:
        yield from get_layers_recursive(child)

def viewport_reveal_all(context):
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

def save_property(struct, prop_name):
    """Returns a Python representation of a Blender property value."""

    prop = struct.bl_rna.properties[prop_name]
    if prop.type == 'COLLECTION':
        return [save_properties(el) for el in getattr(struct, prop_name)]
    elif getattr(prop, 'is_array', False):
        return getattr(struct, prop_name)[:]
    else:
        return getattr(struct, prop_name)

def save_properties(struct):
    """Returns a dictionary representing the properties of a Blender struct."""

    saved = {}
    for prop in struct.bl_rna.properties:
        if not prop.is_runtime:
            # Only save user properties
            continue
        saved[prop.identifier] = save_property(struct, prop.identifier)
    return saved

def load_property(struct, prop_name, value):
    """Sets the value of a property from its Python representation."""

    prop = struct.bl_rna.properties[prop_name]
    if prop.type == 'COLLECTION':
        collection = getattr(struct, prop_name)
        collection.clear()
        for saved_el in value:
            el = collection.add()
            load_properties(el, saved_el)
    elif not prop.is_readonly:
        setattr(struct, prop_name, value)

def load_properties(struct, saved):
    """Restores properties from a dictionary returned by save_properties()"""

    for prop_name, value in saved.items():
        load_property(struct, prop_name, value)

def get_topmost_parent(obj):
    while obj.parent:
        obj = obj.parent
    return obj

lr = {'l': 'r', 'L': 'R', 'r': 'l', 'R': 'L',
    'left': 'right', 'Left': 'Right', 'right': 'left', 'Right': 'Left'}

def flip_name(s, suffix_only=False):
    """Returns the given name with flipped side affixes, or None if not applicable."""

    if not suffix_only:
        # Prefix with no delimiter, case sensitive (lBone -> rBone)
        m = re.match(r"^([lr]|[lL]eft|[rR]ight)[A-Z]", s)
        if m:
            return lr[m[1]] + s[len(m[1]):]
        # Prefix with delimiter
        if re.match(r"^[LlRr][_.].", s):
            return lr[s[0]] + s[1:]
    # Suffix with delimiter
    if re.match(r".+[_.][LlRr]$", s):
        return s[:-1] + lr[s[-1]]
    return None

def flip_names(s):
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

def try_call(func, *args, **kwargs):
    try:
        func(*args, **kwargs)
        return True
    except RuntimeError:
        pass
    return False

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

def gret_operator_exists(bl_idname):
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

def ensure_starts_with(s, prefix):
    return s if s.startswith(prefix) else prefix + s

def snakecase(s):
    """Convert string into snake case."""

    s = re.sub(r"[\-\.\s]", '_', str(s))
    if not s:
        return s
    s = s[0] + re.sub(r"[^_][A-Z]+", lambda m: m.group(0)[0] + "_" + m.group(0)[1:], s[1:])
    return s.lower()

two_letter_words = frozenset(("an", "as", "at", "be", "bi", "by", "ex", "go", "he", "hi", "if",
    "in", "is", "it", "mu", "my", "no", "of", "on", "or", "ox", "pi", "re", "to", "up", "us", "we"))
titlecase_word = lambda s: s[0].upper() + s[1:] if s and len(s) != 2 or s in two_letter_words else s.upper()
def titlecase(s):
    """Convert string into sentence case."""

    if not s:
        return s
    return " ".join(titlecase_word(word) for word in snakecase(s).split("_"))

def sentence_join(seq, ignore_empty=True):
    """Concatenate a sequence with commas. Last element is concatenated with 'and' instead."""

    seq = list(str(el) for el in seq if not ignore_empty or str(el))
    if len(seq) >= 2:
        return " and ".join((", ".join(seq[:-1]), seq[-1]))
    elif len(seq) == 1:
        return seq[0]
    return ""

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
    bpy.types.Action: 'actions',                # BlendDataActions
    bpy.types.Armature: 'armatures',            # BlendDataArmatures
    bpy.types.Brush: 'brushes',                 # BlendDataBrushes
    bpy.types.CacheFile: 'cache_files',         # BlendDataCacheFiles
    bpy.types.Camera: 'cameras',                # BlendDataCameras
    bpy.types.Collection: 'collections',        # BlendDataCollections
    bpy.types.Curve: 'curves',                  # BlendDataCurves
    # bpy.types.Font: 'fonts',                  # BlendDataFonts -- no such type
    bpy.types.GreasePencil: 'grease_pencils',   # BlendDataGreasePencils
    # bpy.types.HairCurve: 'hair_curves',       # BlendDataHairCurves -- no such type
    bpy.types.Image: 'images',                  # BlendDataImages
    bpy.types.Lattice: 'lattices',              # BlendDataLattices
    bpy.types.Library: 'libraries',             # BlendDataLibraries
    bpy.types.LightProbe: 'lightprobes',        # BlendDataProbes
    bpy.types.Light: 'lights',                  # BlendDataLights
    # bpy.types.LineStyle: 'linestyles',          # BlendDataLineStyles -- no such type
    bpy.types.Mask: 'masks',                    # BlendDataMasks
    bpy.types.Material: 'materials',            # BlendDataMaterials
    bpy.types.Mesh: 'meshes',                   # BlendDataMeshes
    bpy.types.MetaBall: 'metaballs',            # BlendDataMetaBalls
    bpy.types.MovieClip: 'movieclips',          # BlendDataMovieClips
    bpy.types.NodeGroup: 'node_groups',         # BlendDataNodeTrees
    bpy.types.Object: 'objects',                # BlendDataObjects
    bpy.types.PaintCurve: 'paint_curves',       # BlendDataPaintCurves
    bpy.types.Palette: 'palettes',              # BlendDataPalettes
    bpy.types.Particle: 'particles',            # BlendDataParticles
    bpy.types.PointCloud: 'pointclouds',        # BlendDataPointClouds
    bpy.types.Scene: 'scenes',                  # BlendDataScenes
    bpy.types.Screen: 'screens',                # BlendDataScreens
    # bpy.types.ShapeKey: 'shape_keys',         # BlendData.shape_keys?
    bpy.types.Sound: 'sounds',                  # BlendDataSounds
    bpy.types.Speaker: 'speakers',              # BlendDataSpeakers
    bpy.types.Text: 'texts',                    # BlendDataTexts
    bpy.types.Texture: 'textures',              # BlendDataTextures
    bpy.types.Volume: 'volumes',                # BlendDataVolumes
    bpy.types.WindowManager: 'window_managers', # BlendDataWindowManagers
    bpy.types.WorkSpace: 'workspaces',          # BlendDataWorkSpaces
    bpy.types.World: 'worlds',                  # BlendDataWorlds
}

def get_data_collection(bid_or_type):
    """Return the bpy.data collection that the ID belongs to, or None if not applicable."""

    if isinstance(bid_or_type, bpy.types.ID):
        bid_or_type = type(bid_or_type)
    return getattr(bpy.data, bpy_type_to_data_collection_name.get(bid_or_type, ''), None)

def link_properties(from_bid, from_data_path, to_bid, to_data_path, invert=False):
    """Creates a simple driver linking properties between two IDs."""

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

def ensure_iterable(seq_or_el):
    return seq_or_el if hasattr(seq_or_el, '__iter__') and not isinstance(seq_or_el, str) else (seq_or_el,)

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

def split_sequence(seq, key=None):
    """Returns two lists containing items for which key(item) returns true or false respectively.
    If key is None, bool(element) is tested instead."""

    a, b = [], []
    for el in seq:
        if key is None:
            (a if bool(el) else b).append(el)
        else:
            (a if key(el) else b).append(el)
    return a, b

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
