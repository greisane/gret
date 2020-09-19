from collections import namedtuple
from functools import wraps, lru_cache
from mathutils import Vector, Quaternion, Euler
import bpy
import io
import os
import re

def select_only(context, objs):
    """Ensures only the given object or objects are selected."""

    if not hasattr(objs, "__iter__"):
        objs = [objs]
    for obj in context.scene.objects:
        obj.select_set(False)
    for obj in objs:
        obj.select_set(True)
    context.view_layer.objects.active = next(iter(objs), None)

def is_valid(data_block):
    """Returns whether a reference to a data-block is valid."""

    if not data_block:
        return False
    try:
        data_block.id_data
    except (ReferenceError, KeyError):
        return False
    return True

SelectionState = namedtuple('SelectionState', [
    'selected',
    'active',
    'layers',
])

def save_selection():
    """Returns a SelectionState storing the current selection state."""

    return SelectionState(
        selected=bpy.context.selected_objects[:],
        active=bpy.context.scene.objects.active if not _280() else
            bpy.context.view_layer.objects.active,
        layers=bpy.context.scene.layers[:] if not _280() else
            [(c, c.hide_select, c.hide_viewport, c.hide_render) for c in bpy.data.collections],
    )

def load_selection(state):
    """Restores selection state from a SelectionState as returned by save_selection()."""

    if not _280():
        bpy.context.scene.layers[:] = state.layers
    else:
        for collection, hide_select, hide_viewport, hide_render in state.layers:
            if is_valid(collection):
                collection.hide_select = hide_select
                collection.hide_viewport = hide_viewport
                collection.hide_render = hide_render

    select_only(bpy.context, (obj for obj in state.selected if is_valid(obj)))

    if is_valid(state.active):
        bpy.context.view_layer.objects.active = state.active

def save_attrs(o):
    """Returns a dictionary storing simple attributes found in the object.
Doesn't rely on __dict__ so it can be used on Blender objects."""

    saved = {}
    for k in dir(o):
        if k.startswith("__"):
            continue
        try:
            val = getattr(o, k)
        except:
            continue
        if type(val) in {int, float, str}:
            saved[k] = val
    return saved

def load_attrs(o, saved):
    """Restores attributes from a dictionary returned by save_attrs()."""

    for k, v in saved.items():
        try:
            setattr(o, k, v)
        except:
            continue

def is_object_arp(obj):
    """Returns whether the object is an Auto-Rig Pro armature."""
    return obj and obj.type == 'ARMATURE' and obj.pose.bones.get("c_pos")

def clear_pose(obj, clear_armature_properties=True, clear_bone_properties=True):
    """Resets the given armature."""

    if not obj or obj.type != 'ARMATURE':
        return

    # Collect keys with `sorted(set(chain.from_iterable(pb.keys() for pb in C.object.pose.bones)))`
    default_values = {}
    is_arp = is_object_arp(obj)
    if is_arp:
        arp_default_values = {
            "arp_layer":None, # Internal thing
            "auto_eyelid":0.1,
            "auto_stretch":0.0,
            "autolips":None, # Different values, hard to tweak
            "bend_all":0.0,
            "elbow_pin":0.0,
            "eye_target":1.0,
            "fingers_grasp":0.0,
            "fix_roll":0.0,
            "head_free":0,
            "ik_fk_switch":0.0,
            "leg_pin":0.0,
            "lips_retain":0.0,
            "lips_stretch":1.0,
            "pole_parent":1,
            "stretch_length":1.0,
        }

    if clear_armature_properties:
        for prop_name, prop_value in obj.items():
            if isinstance(prop_value, float):
                obj[prop_name] = 0.0

    for pose_bone in obj.pose.bones:
        if clear_bone_properties:
            for prop_name, prop_value in pose_bone.items():
                if is_arp and prop_name in arp_default_values:
                    value = arp_default_values[prop_name]
                    if value is not None:
                        pose_bone[prop_name] = value
                elif prop_name in default_values:
                    value = default_values[prop_name]
                    if value is not None:
                        pose_bone[prop_name] = value
                elif prop_name.startswith("_"):
                    continue
                else:
                    try:
                        pose_bone[prop_name] = type(prop_value)()
                    except TypeError:
                        pass

        pose_bone.location = Vector()
        pose_bone.rotation_quaternion = Quaternion()
        pose_bone.rotation_euler = Euler()
        pose_bone.rotation_axis_angle = [0.0, 0.0, 1.0, 0.0]
        pose_bone.scale = Vector((1.0, 1.0, 1.0))

def merge_vertex_groups(obj, src_name, dst_name, remove_src=True):
    """Merges the source vertex group into the destination vertex group."""

    src = obj.vertex_groups[src_name]
    dst = obj.vertex_groups.get(dst_name)
    if not dst:
        dst = obj.vertex_groups.new(name=dst_name)

    for vert_idx, vert in enumerate(obj.data.vertices):
        try:
            dst.add([vert_idx], src.weight(vert_idx), 'ADD')
        except RuntimeError:
            pass

    if remove_src:
        obj.vertex_groups.remove(src)

def subdivide_vertex_group(obj, src_name, dst_names, bone_head, bone_tail, remove_src=True):
    """Subdivides a vertex group along a line."""

    src = obj.vertex_groups[src_name]
    dsts = [obj.vertex_groups.new(name=name) for name in dst_names]
    bone_dir = bone_tail - bone_head
    bone_length = bone_dir.length
    bone_dir /= bone_length

    for vert in obj.data.vertices:
        for vgrp in vert.groups:
            if vgrp.group == src.index:
                x = bone_dir.dot(vert.co - bone_head) / bone_length * len(dsts)
                for n, dst in enumerate(dsts):
                    t = 1.0
                    if n > 0:
                        t = min(t, x + 0.5 - n)
                    if n < len(dsts) - 1:
                        t = min(t, (n + 1.5) - x)
                    t = max(0.0, min(1.0, t))
                    dst.add([vert.index], vgrp.weight * t, 'REPLACE')

    if remove_src:
        obj.vertex_groups.remove(src)

def is_object_defaulted(obj, recursive=False):
    """Returns whether the properties of an object are set to their default values."""

    for prop in obj.bl_rna.properties:
        if prop.name == 'Name':
            # Skip this one
            continue

        try:
            if getattr(prop, "is_array", False):
                # Handle arrays
                current = [p for p in getattr(obj, prop.identifier)]
                default = [p for p in prop.default_array]
            else:
                current = getattr(obj, prop.identifier)
                default = getattr(prop, "default", type(current)())

            if current != default:
                return False
        except TypeError:
            # The value type is not trivially initializable, omit it
            # Could be a PointerProperty but checking recursively is not currently supported
            continue

    return True

def get_children_recursive(obj):
    for child in obj.children:
        yield child
        yield from get_children_recursive(child)

def get_flipped_name(name):
    """Returns the given name with flipped L/R affixes, or None if not applicable."""

    def flip_LR(s):
        if "L" in s.upper():
            return s.replace("l", "r").replace("L", "R")
        else:
            return s.replace("r", "l").replace("R", "L")

    match = re.match(r'(.+)([_\.][LlRr])$', name) # Suffix
    if match:
        return match[1] + flip_LR(match[2])

    match = re.match(r'^([LlRr][_\.])(.+)', name) # Prefix
    if match:
        return flip_LR(match[1]) + match[2]

    return None

def beep(pitch=0):
    try:
        import winsound
        freq = 800 + 100 * pitch
        winsound.Beep(freq, 50)
        winsound.Beep(freq, 50)
    except:
        pass

def intercept(_func=None, error_result=None):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Redirect output
            stdout = io.StringIO()
            try:
                from contextlib import redirect_stdout
                with redirect_stdout(stdout):
                    result = func(*args, **kwargs)
            except Exception as err:
                # import traceback
                # traceback.print_exc()
                result = error_result
            return result
        return wrapper

    if _func is None:
        return decorator
    else:
        return decorator(_func)

def set_collection_viewport_visibility(context, collection_name, visibility=True):
    # Based on https://blenderartists.org/t/1141768
    # This is dumb as hell and hopefully it'll change in the future

    def get_viewport_ordered_collections(context):
        def add_child_collections(collection, out_list, add_self=True):
            if add_self:
                out_list.append(collection)
            for child in collection.children:
                out_list.append(child)
            for child in collection.children:
                add_child_collections(child, out_list, False)
        result = []
        add_child_collections(context.scene.collection, result)
        return result

    def get_area_from_context(context, area_type):
        for area in context.screen.areas:
            if area.type == area_type:
                return area
        return None

    # Find outliner index for the given collection name
    try:
        collections = get_viewport_ordered_collections(context)
        index, collection = next(((n, coll) for n, coll in enumerate(collections)
            if coll.name == collection_name))
    except StopIteration:
        return

    first_object = None
    if len(collection.objects) > 0:
        first_object = collection.objects[0]

    try:
        bpy.ops.object.hide_collection(context, collection_index=index, toggle=True)

        if first_object.visible_get() != visibility:
            bpy.ops.object.hide_collection(context, collection_index=index, toggle=True)
    except:
        context_override = context.copy()
        context_override['area'] = get_area_from_context(context, 'VIEW_3D')

        bpy.ops.object.hide_collection(context_override, collection_index=index, toggle=True)

        if first_object.visible_get() != visibility:
            bpy.ops.object.hide_collection(context_override, collection_index=index, toggle=True)

    return collection

def get_export_path(path, **kwargs):
    """Returns an absolute path from an export path."""

    kwargs.update({
        "basename": os.path.splitext(bpy.path.basename(bpy.data.filepath))[0],
    })
    path = path.format(**kwargs)

    if "suffix" in kwargs:
        path, ext = os.path.splitext(path)
        path = path + kwargs["suffix"] + ext

    return bpy.path.abspath(path)

def check_invalid_export_path(path, **kwargs):
    """Validates an export path and returns the reason it isn't valid."""

    if path.startswith("//") and not bpy.data.filepath:
        # While not technically wrong the file will likely end up at blender working directory
        return "Can't use a relative export path before the file is saved."
    if os.path.isdir(path):
        return "Export path must be a file path."

    # Check that the export path is valid
    try:
        dirpath = os.path.dirname(get_export_path(path, **kwargs))
    except Exception as err:
        return "Invalid export path: %s" % err

    try:
        os.makedirs(dirpath)
    except PermissionError:
        return "Invalid export path."
    except OSError:
        pass # Directory already exists

    return ""

def path_split_all(path):
    """Returns a path split into a list of its parts"""

    all_parts = []
    while True:
        parts = os.path.split(path)
        if parts[0] == path: # Sentinel for absolute paths
            all_parts.insert(0, parts[0])
            break
        elif parts[1] == path: # Sentinel for relative paths
            all_parts.insert(0, parts[1])
            break
        else:
            path = parts[0]
            all_parts.insert(0, parts[1])
    return all_parts

def try_key(obj, prop_path, frame=0):
    try:
        return obj.keyframe_insert(prop_path, frame=frame)
    except TypeError:
        return False

@lru_cache(maxsize=4095)
def levenshtein_distance(s, t):
    if not s: return len(t)
    if not t: return len(s)
    if s[0] == t[0]: return levenshtein_distance(s[1:], t[1:])
    l1 = levenshtein_distance(s, t[1:])
    l2 = levenshtein_distance(s[1:], t)
    l3 = levenshtein_distance(s[1:], t[1:])
    return 1 + min(l1, l2, l3)

def remove_extra_data(obj):
    """Removes all data from a mesh object, except for the mesh itself"""

    obj.vertex_groups.clear()
    obj.shape_key_clear()
    if obj.type == 'MESH':
        mesh = obj.data
        mesh.use_customdata_vertex_bevel = False
        mesh.use_customdata_edge_bevel = False
        mesh.use_customdata_edge_crease = False
        # mesh.materials.clear() seems to crash
        while mesh.materials:
            mesh.materials.pop()
        while mesh.vertex_colors.active:
            mesh.vertex_colors.remove(mesh.vertex_colors.active)
        while mesh.uv_layers.active:
            mesh.uv_layers.remove(mesh.uv_layers.active)

def make_annotations(cls):
    """Converts class fields to annotations if running Blender 2.8."""

    def is_property(o):
        try:
            return o[0].__module__ == 'bpy.props'
        except:
            return False

    if bpy.app.version < (2, 80):
        return
    bl_props = {k: v for k, v in cls.__dict__.items() if is_property(v)}
    if bl_props:
        if '__annotations__' not in cls.__dict__:
            setattr(cls, '__annotations__', {})
        annotations = cls.__dict__['__annotations__']
        for k, v in bl_props.items():
            annotations[k] = v
            delattr(cls, k)
    return

def _280(true=True, false=False):
    return true if bpy.app.version >= (2, 80) else false
