from fnmatch import fnmatch
from math import cos
from mathutils import Vector
import bmesh
import bpy
import numpy as np
import re

from .. import prefs
from ..heapdict import heapdict
from ..helpers import (
    flip_name,
    fmt_fraction,
    get_modifier_mask,
    get_vgroup,
    select_only,
    try_with_object,
    with_object,
)
from ..log import logger, log, logd
from ..math import lerp, one_vector, half_vector

fmt_shape_key = lambda sk: (sk.name if sk.value == 1.0 else f"{sk.name} ({fmt_fraction(sk.value, 1.0)})")

def _select_mesh_elements(collection, select=True, indices=None, key=None):
    values = np.zeros(len(collection), dtype=bool)
    collection.foreach_set('hide', values)

    if select:
        if key is None and indices is None:
            values.fill(True)
        elif key is None:
            values[indices] = True
        elif indices is None:
            values = [bool(key(el)) for el in collection]
        else:
            for index in indices:
                values[index] = bool(key(collection[index]))

    collection.foreach_set('select', values)
    return np.sum(values)

def edit_mesh_elements(obj, type='VERT', indices=None, key=None):
    """
    Enters edit mode and selects elements of a mesh to be operated on.

    indices: Iterable with the indices of the elements to select. If None, all elements are selected.
    key: A function can be supplied to determine which elements should be selected.

    Returns the number of elements selected.
    """

    assert obj.type == 'MESH' and obj.mode == 'OBJECT'
    mesh = obj.data

    select_only(bpy.context, obj)
    num_verts_selected = _select_mesh_elements(mesh.vertices, type == 'VERT', indices, key)
    num_edges_selected = _select_mesh_elements(mesh.edges, type == 'EDGE', indices, key)
    num_faces_selected = _select_mesh_elements(mesh.polygons, type == 'FACE', indices, key)

    bpy.ops.object.editmode_toggle()
    bpy.ops.mesh.select_mode(type=type)

    return (num_verts_selected if type == 'VERT'
        else num_edges_selected if type == 'EDGE'
        else num_faces_selected)

def edit_face_map_elements(obj, face_map_name):
    """
    Enters edit mode and selects elements of a face map to be operated on.

    Returns the number of elements selected.
    """

    assert obj.type == 'MESH' and obj.mode == 'OBJECT'
    mesh = obj.data
    attr = mesh.attributes.get(face_map_name)

    values = np.zeros(len(mesh.polygons), dtype=bool)
    if attr and attr.domain == 'FACE' and attr.data_type == 'BOOLEAN':
        attr.data.foreach_get('value', values)

    falses = np.zeros(len(mesh.vertices), dtype=bool)
    mesh.vertices.foreach_set('hide', falses)
    mesh.vertices.foreach_set('select', falses)
    falses = np.zeros(len(mesh.edges), dtype=bool)
    mesh.edges.foreach_set('hide', falses)
    mesh.edges.foreach_set('select', falses)
    falses = np.zeros(len(mesh.polygons), dtype=bool)
    mesh.polygons.foreach_set('hide', falses)
    mesh.polygons.foreach_set('select', values)

    bpy.ops.object.editmode_toggle()
    bpy.ops.mesh.select_mode(type='FACE')

    return sum(values)

def get_vcolor(obj, name="", domain=None, data_type=None):
    """Ensures that a vertex color layer with the given name and format exists."""

    assert obj.type == 'MESH'
    if name:
        vcol = obj.data.color_attributes.get(name)
    else:
        vcol = obj.data.color_attributes.active_color
    if vcol and (domain and domain != vcol.domain or data_type and data_type != vcol.data_type):
        obj.data.color_attributes.remove(vcol)
        vcol = None
    if not vcol:
        vcol = obj.data.color_attributes.new(name=name,
            domain=domain or 'CORNER', type=data_type or 'FLOAT_COLOR')
    return vcol

def refresh_active_color_attribute(mesh):
    if mesh.color_attributes.active_color_index < 0:
        mesh.color_attributes.active_color_index = 0
    if mesh.color_attributes.render_color_index < 0:
        mesh.color_attributes.render_color_index = 0

def get_face_map_attribute(obj, name):
    """Ensures that a boolean face attribute with the given name exists."""

    assert obj.type == 'MESH'
    attr = obj.data.attributes.get(name)
    if not attr or attr.domain != 'FACE' or attr.data_type != 'BOOLEAN':
        attr = obj.data.attributes.new(name, type='BOOLEAN', domain='FACE')
    return attr

def clear_object_data(obj, /, *, vertex_groups=True, shape_keys=True, materials=True,
    constraints=True, custom_properties=True, bevel_weight_edge=True, bevel_weight_vert=True,
    crease_edge=True, crease_vert=True, sharp_edge=True, sharp_face=True, face_maps=True,
    uv_layers=True, vertex_colors=True, attributes=True, sculpt_mask_data=True, skin_data=True,
    custom_split_normals=True):

    # Clear object data
    if vertex_groups and hasattr(obj, 'vertex_groups'):
        try:
            obj.vertex_groups.clear()
        except RuntimeError:
            pass  # Most objects have vertex groups but only a few support them

    if shape_keys:
        obj.shape_key_clear()

    if constraints:
        try_with_object(bpy.ops.rigidbody.constraint_remove, obj)
        try_with_object(bpy.ops.rigidbody.object_remove, obj)
        # bpy.ops.object.forcefield_toggle(obj)  # Didn't bother

    if custom_properties:
        for key in list(obj.keys()):
            del obj[key]

    # Clear data data
    data = obj.data
    if materials and hasattr(data, 'materials'):
        data.materials.clear()  # This should also clear object material slots

    if hasattr(data, 'attributes'):
        # Iterating backwards seems to fix an issue where color layers just refuse to be removed
        for attribute in reversed(data.attributes):
            if attribute.name == 'bevel_weight_edge':
                should_delete = bevel_weight_edge
            elif attribute.name == 'bevel_weight_vert':
                should_delete = bevel_weight_vert
            elif attribute.name == 'crease_edge':
                should_delete = crease_edge
            elif attribute.name == 'crease_vert':
                should_delete = crease_vert
            elif attribute.name == 'sharp_edge':
                should_delete = sharp_edge
            elif attribute.name == 'sharp_face':
                should_delete = sharp_face
            elif attribute.name == 'face_maps':
                should_delete = face_maps
            elif attribute.domain == 'FACE' and attribute.data_type == 'BOOLEAN':
                should_delete = face_maps
            elif attribute.domain == 'CORNER' and attribute.data_type == 'FLOAT2':
                should_delete = uv_layers
            elif (attribute.domain in {'POINT', 'CORNER'}
                and attribute.data_type in {'BYTE_COLOR', 'FLOAT_COLOR'}):
                should_delete = vertex_colors
            else:
                should_delete = attributes
            if should_delete:
                try:
                    data.attributes.remove(attribute)
                except RuntimeError:
                    pass

    if custom_properties:
        for key in list(data.keys()):
            del data[key]

    if sculpt_mask_data:
        try_with_object(bpy.ops.mesh.customdata_mask_clear, obj)

    if skin_data:
        try_with_object(bpy.ops.mesh.customdata_skin_clear, obj)

    if custom_split_normals:
        try_with_object(bpy.ops.mesh.customdata_custom_splitnormals_clear, obj)

def merge_vertex_groups(obj, src_name, dst_name, remove_src=True):
    """Merges the source vertex group into the destination vertex group."""

    src = obj.vertex_groups[src_name]
    dst = obj.vertex_groups.get(dst_name)
    if not dst:
        dst = obj.vertex_groups.new(name=dst_name)

    for vert_idx in range(len(obj.data.vertices)):
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

def duplicate_shape_key(obj, name, new_name):
    shape_key = obj.data.shape_keys.key_blocks[name]

    # Store state
    saved_show_only_shape_key = obj.show_only_shape_key
    saved_active_shape_key_index = obj.active_shape_key_index
    saved_value = shape_key.value

    # Duplicate by muting all (with show_only_shape_key)
    shape_key_index = obj.data.shape_keys.key_blocks.find(name)
    obj.active_shape_key_index = shape_key_index
    obj.active_shape_key.value = obj.active_shape_key.slider_max
    obj.show_only_shape_key = True
    new_shape_key = obj.shape_key_add(name=new_name, from_mix=True)
    new_shape_key.slider_max = obj.active_shape_key.slider_max
    new_shape_key.value = saved_value

    # Restore state
    obj.show_only_shape_key = saved_show_only_shape_key
    obj.active_shape_key_index = saved_active_shape_key_index
    shape_key.value = saved_value

    return new_shape_key

def merge_shape_keys_pattern(obj, shape_key_pattern):
    def parse_target_shape_key(s):
        """Parses A->B format which specifies the target shape key instead of basis."""
        try:
            a, b = s.split("->")
            return a, b
        except ValueError:
            return s, None
    def parse_override_value(s):
        """Parses A=1.0 format which specifies shape key values as a percentage."""
        try:
            s, floats = s.split("=")
            return s, float(floats)
        except ValueError:
            return s, None

    shape_key_pattern, target_shape_key_name = parse_target_shape_key(shape_key_pattern)
    shape_key_pattern, override_value = parse_override_value(shape_key_pattern)
    if target_shape_key_name == "" or target_shape_key_name == "_":
        remove_shape_keys(obj, shape_key_pattern)
    else:
        merge_shape_keys(obj, shape_key_pattern, target_shape_key_name, override_value)

def merge_shape_keys(obj, shape_key_name="*", target_shape_key_name="", override_value=None):
    """Merges one or more shape keys into the basis, or target shape key if specified."""

    mesh = obj.data
    if not mesh.shape_keys or not mesh.shape_keys.key_blocks:
        # No shape keys
        return

    rig = obj.find_armature()
    def is_rig_driven(fcurve):
        if rig and fcurve and fcurve.driver:
            for var in fcurve.driver.variables:
                for tgt in var.targets:
                    if tgt.id == rig:
                        return True
        return False

    basis_shape_key_name = mesh.shape_keys.key_blocks[0].name
    if not target_shape_key_name:
        target_shape_key_name = basis_shape_key_name
    elif target_shape_key_name not in mesh.shape_keys.key_blocks:
        obj.shape_key_add(name=target_shape_key_name)

    # Store state
    saved_unmuted_shape_key_names = [sk.name for sk in mesh.shape_keys.key_blocks if not sk.mute]
    saved_unmuted_shape_key_drivers = []

    # Mute all but the ones to be merged
    for sk in mesh.shape_keys.key_blocks[1:]:
        if sk.name != target_shape_key_name and fnmatch(sk.name, shape_key_name):
            # Remove any drivers related to shape keys that will be deleted
            if mesh.shape_keys.animation_data:
                sk_data_path = f'key_blocks["{sk.name}"]'
                for fc in mesh.shape_keys.animation_data.drivers:
                    if fc.data_path.startswith(sk_data_path):
                        if fc.data_path.endswith('.value') and is_rig_driven(fc):
                            # Influence was being driven, assume user would want to merge it fully
                            logd(f"Maxed value of shape key {sk.name} (it was rig driven)")
                            sk.value = sk.slider_max
                        if sk.name == target_shape_key_name:
                            # Don't remove, mute temporarily
                            if not fc.mute:
                                fc.mute = True
                                saved_unmuted_shape_key_drivers.append(fc)
                        else:
                            logd(f"Remove shape key driver {fc.data_path} (will be merged)")
                            mesh.shape_keys.animation_data.drivers.remove(fc)
            if override_value is not None:
                sk.mute = False
                sk.value = lerp(sk.slider_min, sk.slider_max, override_value)
            if sk.mute or sk.value == 0.0:
                # Muted candidates are handled as if merged at 0% and deleted
                # Do it now to ensure shape keys don't unexpectedly return when objects are merged
                obj.shape_key_remove(sk)
        else:
            sk.mute = True

    source_shape_keys = [sk for sk in mesh.shape_keys.key_blocks[1:] if not sk.mute]
    if source_shape_keys:
        log(f"Merging {len(source_shape_keys)} shape keys to {target_shape_key_name}: " +
            ", ".join(fmt_shape_key(sk) for sk in source_shape_keys))

        # Add mix to target shape key. While the basis layer *does* exist in bmesh, changing it
        # doesn't seem to have any effect, hence the split code path.
        merged_sk = obj.shape_key_add(name="__merged", from_mix=True)
        bm = bmesh.new()
        bm.from_mesh(mesh)
        merged_layer = bm.verts.layers.shape[merged_sk.name]
        if target_shape_key_name != basis_shape_key_name:
            target_layer = bm.verts.layers.shape[target_shape_key_name]
            for vert in bm.verts:
                vert[target_layer] += vert[merged_layer] - vert.co
        else:
            for vert in bm.verts:
                vert.co = vert[merged_layer]
        bm.to_mesh(mesh)
        bm.free()
        obj.shape_key_remove(merged_sk)

        # Remove the merged shapekeys
        for sk in source_shape_keys:
            obj.shape_key_remove(sk)

    # Restore state
    for sk_name in saved_unmuted_shape_key_names:
        sk = mesh.shape_keys.key_blocks.get(sk_name)
        if sk:
            sk.mute = False
    for fc in saved_unmuted_shape_key_drivers:
        fc.mute = False

    # Only basis left? Remove it so applying modifiers has less issues
    if mesh.shape_keys and len(mesh.shape_keys.key_blocks) == 1:
        obj.shape_key_clear()

def remove_shape_keys(obj, shape_key_name="*"):
    mesh = obj.data
    if not mesh.shape_keys or len(mesh.shape_keys.key_blocks) <= 1:
        # No shape keys
        return

    removed_shape_key_names = []
    for sk in mesh.shape_keys.key_blocks[1:]:
        if fnmatch(sk.name, shape_key_name):
            removed_shape_key_names.append(sk.name)
            obj.shape_key_remove(sk)

    if len(mesh.shape_keys.key_blocks) <= 1:
        log(f"Removing all shape keys")
    elif removed_shape_key_names:
        log(f"Removing {len(removed_shape_key_names)} shape keys: " +
            ", ".join(removed_shape_key_names))

def mirror_shape_keys(obj, side_vgroup_name):
    if not obj.data.shape_keys or not obj.data.shape_keys.key_blocks:
        # No shape keys
        return

    if not any(mo.type == 'MIRROR' and mo.use_mirror_vertex_groups for mo in obj.modifiers):
        # No useful mirrors
        return

    # Make vertex groups for masking. It doesn't actually matter which side is which,
    # only that the modifier's vertex group mirroring function picks it up
    # Even if the vertex group exists, overwrite so the user doesn't have to manually update it
    other_side_vgroup_name = flip_name(side_vgroup_name)
    if not other_side_vgroup_name:
        return
    vgroup = get_vgroup(obj, side_vgroup_name, clean=True)
    vgroup.add(range(len(obj.data.vertices)), 1.0, 'REPLACE')
    vgroup = get_vgroup(obj, other_side_vgroup_name, clean=True)

    for sk in obj.data.shape_keys.key_blocks:
        flipped_name = flip_name(sk.name)
        # Only mirror it if it doesn't already exist
        if flipped_name and flipped_name not in obj.data.shape_keys.key_blocks:
            log(f"Mirroring shape key {sk.name}")
            logger.indent += 1

            sk.vertex_group = side_vgroup_name
            new_sk = duplicate_shape_key(obj, sk.name, flipped_name)
            new_sk.vertex_group = other_side_vgroup_name

            # Attempt to flip the driver, e.g if driven by Arm_L, make it driven by Arm_R instead.
            try:
                flip_data_path = lambda match: f'["{flip_name(match.group(1)) or match.group(1)}"]'
                sk_data_path = f'key_blocks["{sk.name}"]'
                new_sk_data_path = f'key_blocks["{new_sk.name}"]'
                if obj.data.shape_keys.animation_data:
                    for fc in obj.data.shape_keys.animation_data.drivers:
                        if fc.data_path.startswith(sk_data_path):
                            new_data_path = new_sk_data_path + fc.data_path[len(sk_data_path):]
                            logd(f"Driver path: {fc.data_path} -> {new_data_path}")
                            new_fc = obj.data.shape_keys.driver_add(new_data_path)
                            new_fc.driver.expression = fc.driver.expression
                            new_fc.driver.type = fc.driver.type
                            new_fc.driver.use_self = fc.driver.use_self
                            for var in fc.driver.variables:
                                new_var = new_fc.driver.variables.new()
                                new_var.name = var.name
                                new_var.type = var.type
                                for t, new_t in zip(var.targets, new_var.targets):
                                    new_t.bone_target = flip_name(t.bone_target) or t.bone_target
                                    new_t.data_path = re.sub(r'\["([^"]*)"\]', flip_data_path, t.data_path)
                                    logd(f"{var.name} target: {t.bone_target} -> {new_t.bone_target}")
                                    if t.data_path:
                                        logd(f"{var.name} path: {t.data_path} -> {new_t.data_path}")
                                    new_t.id = t.id
                                    new_t.rotation_mode = t.rotation_mode
                                    new_t.transform_space = t.transform_space
                                    new_t.transform_type = t.transform_type
            except Exception as e:
                log(f"Couldn't mirror driver: {e}")

            logger.indent -= 1

def encode_shape_keys(obj, shape_key_name="*", keep=False):
    mesh = obj.data
    if not mesh.shape_keys or not mesh.shape_keys.key_blocks:
        # No shape keys
        return

    ensure_uv_map = lambda name: mesh.uv_layers.get(name) or mesh.uv_layers.new(name=name)

    for sk in mesh.shape_keys.key_blocks[1:]:
        if fnmatch(sk.name, shape_key_name):
            uv_map_names = (
                ensure_uv_map(f"{sk.name}_WPOxy").name,
                ensure_uv_map(f"{sk.name}_WPOzNORx").name,
                ensure_uv_map(f"{sk.name}_NORyz").name,
            )
            log(f"Encoding shape key {sk.name} to UV channels " +
                ", ".join(str(mesh.uv_layers.find(name)) for name in uv_map_names))

            bm = bmesh.new()
            bm.from_mesh(mesh, use_shape_key=True, shape_key_index=mesh.shape_keys.key_blocks.find(sk.name))
            uv_layers = tuple(bm.loops.layers.uv[name] for name in uv_map_names)
            basis_layer = bm.verts.layers.shape[0]
            def set_vert_uvs(vert, co, uv_layer):
                for bmloop in vert.link_loops:
                    bmloop[uv_layer].uv = co

            for vert in bm.verts:
                # Importing to UE4, UV precision degrades very quickly even with "Use Full Precision UVs"
                # Remapping location deltas so that (0,0) is at the center of the UV sheet seems to help
                delta = (vert.co - vert[basis_layer]) * 10.0 + half_vector  # [-10..10]->[0..1]
                normal = (vert.normal + one_vector) * 0.5  # [-1..1]->[0..1]
                set_vert_uvs(vert, (delta.x, delta.y), uv_layers[0])
                set_vert_uvs(vert, (delta.z, 1-normal.x), uv_layers[1])
                set_vert_uvs(vert, (1-normal.y, 1-normal.z), uv_layers[2])

            bm.to_mesh(mesh)
            bm.free()
            if not keep:
                obj.shape_key_remove(sk)

    obj.data.update()

    # Only basis left? Remove it so applying modifiers has less issues
    if mesh.shape_keys and len(mesh.shape_keys.key_blocks) == 1:
        obj.shape_key_clear()

def get_operator_target_vertex_groups(obj, group_select_mode, only_unlocked=False):
    """Returns list of vertex groups to work on."""

    vgroup_idxs = []

    if group_select_mode == 'ACTIVE':
        vgroup_idxs = [obj.vertex_groups.active_index]
    elif group_select_mode == 'BONE_DEFORM':
        armature = obj.find_armature()
        if armature:
            bones = armature.data.bones
            vgroup_idxs = [vgroup.index for vgroup in obj.vertex_groups if
                (not only_unlocked or not vgroup.lock_weight)
                and vgroup.name in bones and bones[vgroup.name].use_deform]
    elif group_select_mode == 'BONE_NOT_DEFORM':
        armature = obj.find_armature()
        if armature:
            bones = armature.data.bones
            vgroup_idxs = [vgroup.index for vgroup in obj.vertex_groups if
                (not only_unlocked or not vgroup.lock_weight)
                and vgroup.name not in bones or not bones[vgroup.name].use_deform]
    elif group_select_mode == 'ALL':
        vgroup_idxs = [vgroup.index for vgroup in obj.vertex_groups
            if (not only_unlocked or not vgroup.lock_weight)]

    return vgroup_idxs

def apply_modifiers(obj, should_apply_modifier, keep_armature=False):
    """Apply modifiers while preserving shape keys and UV layers."""

    # Remember layer names in case they're destroyed by geometry nodes
    uv_layer_names = [uv_layer.name for uv_layer in obj.data.uv_layers]
    vertex_group_names = [vertex_group.name for vertex_group in obj.vertex_groups]
    vertex_color_names = [vertex_color.name for vertex_color in obj.data.vertex_colors]

    override_reasons = []
    try:
        # This isn't very good
        modifier_mask, override_reasons = zip(*(should_apply_modifier(mod) for mod in obj.modifiers))
        modifier_mask = get_modifier_mask(obj, modifier_mask)
    except:
        modifier_mask = get_modifier_mask(obj, should_apply_modifier)
    num_modifiers = sum(modifier_mask)
    num_shape_keys = len(obj.data.shape_keys.key_blocks) - 1 if obj.data.shape_keys else 0

    if not num_modifiers:
        log(f"No modifiers will be applied")
    elif num_shape_keys:
        log(f"Applying {num_modifiers} modifiers with {num_shape_keys} shape keys")
    elif not keep_armature:
        log(f"Flattening with {num_modifiers} modifiers")
    else:
        log(f"Applying {num_modifiers} modifiers")
    logger.indent += 1

    for reason in override_reasons:
        if reason:
            log(reason)

    # Geometry nodes will affect data transfer modifiers, even if the data transfer is first.
    # Possible bug or very unintuitive behavior? If there are no shape keys or modifiers to keep
    # then it's safe to just flatten instead of applying.
    if num_modifiers:
        if num_shape_keys or keep_armature:
            with_object(bpy.ops.gret.shape_key_apply_modifiers, obj, modifier_mask=modifier_mask)
        else:
            for modifier, mask in zip(obj.modifiers, modifier_mask):
                modifier.show_viewport = mask
            dg = bpy.context.evaluated_depsgraph_get()
            bm = bmesh.new()
            bm.from_object(obj, dg)
            bm.to_mesh(obj.data)

    # Remove unused modifiers
    if keep_armature:
        for modifier in obj.modifiers[:]:
            if modifier.type == 'ARMATURE' and keep_armature:
                modifier.show_viewport = True
            else:
                logd(f"Removed {modifier.type} modifier {modifier.name}")
                with_object(bpy.ops.object.modifier_remove, obj, modifier=modifier.name)
    else:
        obj.modifiers.clear()

    # Restore UV layers from attributes
    for name in uv_layer_names:
        if name not in obj.data.uv_layers:
            attr = obj.data.attributes.get(name)
            if attr and attr.domain == 'CORNER' and attr.data_type == 'FLOAT2':
                log(f"Restoring UV layer {name} from attribute")
                uvs = [0.0] * (len(attr.data) * 2)
                attr.data.foreach_get('vector', uvs)
                obj.data.attributes.remove(attr)  # Avoid collisions

                uv_layer = obj.data.uv_layers.new(name=name, do_init=False)
                uv_layer.data.foreach_set('uv', uvs)
            elif attr:
                log(f"Can't restore UV layer {name}, attribute has wrong domain or data type")
            else:
                log(f"Can't restore UV layer {name}, attribute doesn't exist")

    # Restore vertex groups from attributes
    for name in vertex_group_names:
        if name not in obj.vertex_groups:
            attr = obj.data.attributes.get(name)
            if attr and attr.domain == 'POINT' and attr.data_type == 'FLOAT':
                log(f"Restoring vertex group {name} from attribute")
                values = [0.0] * (len(attr.data) * 1)
                attr.data.foreach_get('value', values)
                obj.data.attributes.remove(attr)  # Avoid collisions

                vertex_group = obj.vertex_groups.new(name=name)
                vertex_group_index = vertex_group.index
                bm = bmesh.new()
                bm.from_mesh(obj.data)
                deform_layer = bm.verts.layers.deform.verify()
                for vert, value in zip(bm.verts, values):
                    vert[deform_layer][vertex_group_index] = value
                bm.to_mesh(obj.data)
                bm.free()
            elif attr:
                log(f"Can't restore vertex group {name}, attribute has wrong domain or data type")
            else:
                log(f"Can't restore vertex group {name}, attribute doesn't exist")

    # Restore vertex color layers from attributes
    for name in vertex_color_names:
        if name not in obj.data.vertex_colors:
            attr = obj.data.attributes.get(name)
            if attr and attr.domain == 'CORNER' and attr.data_type == 'FLOAT_COLOR':
                log(f"Restoring vertex color layer {name} from attribute")
                colors = [0.0] * (len(attr.data) * 4)
                attr.data.foreach_get('color', colors)
                obj.data.attributes.remove(attr)  # Avoid collisions

                vertex_color = obj.data.vertex_colors.new(name=name, do_init=False)
                vertex_color.data.foreach_set('color', colors)
            elif attr:
                log(f"Can't restore vertex color layer {name}, attribute has wrong domain or data type")
            else:
                log(f"Can't restore vertex color layer {name}, attribute doesn't exist")

    logger.indent -= 1

def apply_shape_keys_with_vertex_groups(obj):
    if not obj.data.shape_keys:
        return

    for sk in obj.data.shape_keys.key_blocks:
        if sk.vertex_group:
            vgroup = obj.vertex_groups[sk.vertex_group]
            sk.vertex_group = ''

            for vert_idx, vert in enumerate(sk.data):
                v0 = sk.relative_key.data[vert_idx].co
                try:
                    vert.co[:] = v0.lerp(vert.co, vgroup.weight(vert_idx))
                except RuntimeError:
                    vert.co[:] = v0

def merge_islands(obj, mode='ALWAYS', threshold=1e-3):
    """Does 'Remove Doubles' on specified edges. Returns the number of vertices merged."""
    # Reverted to using bpy.ops because bmesh is failing to merge normals correctly
    # TODO This should consider that each vertex has its pair (and only one pair) in another island

    saved_mode = obj.mode

    if mode == 'ALWAYS':
        edit_mesh_elements(obj, 'EDGE')
    elif mode == 'BOUNDARY':
        edit_mesh_elements(obj, 'EDGE')
        bpy.ops.mesh.region_to_loop()
    elif mode == 'TAGGED':
        edit_mesh_elements(obj, 'EDGE', key=lambda e: e.use_freestyle_mark)
    else:
        return 0

    # Shape keys tend to break when removing doubles and vertices don't exactly match. Not sure
    # about the root cause, just moving the vertices together is enough to fix it.
    # Have to exit edit mode since bmesh.from_edit_mesh() won't update shape keys
    if False:
        bpy.ops.object.editmode_toggle()
        bm = bmesh.new()
        bm.from_mesh(obj.data)

        verts = [v for v in bm.verts if v.select]
        targetmap = bmesh.ops.find_doubles(bm, verts=verts, dist=threshold)['targetmap']
        num_shape_keys = len(bm.verts.layers.shape)
        for shape_key_index in range(num_shape_keys):
            shape_layer = bm.verts.layers.shape[shape_key_index]
            for src_vert, dst_vert in targetmap.items():
                src_vert[shape_layer] = dst_vert[shape_layer]

        bm.to_mesh(obj.data)
        bm.free()
        obj.data.update()
        bpy.ops.object.editmode_toggle()

    old_num_verts = len(obj.data.vertices)
    bpy.ops.mesh.remove_doubles(threshold=threshold, use_unselected=False)

    # mesh = obj.data
    # bm = bmesh.new()
    # bm.from_mesh(mesh)
    # bm.edges.ensure_lookup_table()
    # old_num_verts = len(bm.verts)

    # # Seems the following would be the proper way, however as of 2.90.0 it returns NotImplemented
    # # fs_layer = bm.edges.layers.freestyle.active
    # # fs_edges = [e for e in bm.edges if e[fs_layer]]
    # fs_edges = [e for e in bm.edges if mesh.edges[e.index].use_freestyle_mark]

    # # Get list of unique verts
    # fs_verts = list(set(chain.from_iterable(e.verts for e in fs_edges)))
    # bmesh.ops.remove_doubles(bm, verts=fs_verts, dist=1e-5)
    # new_num_verts = len(bm.verts)

    # # Finish and clean up
    # bm.to_mesh(mesh)
    # bm.free()

    # Clean up
    bpy.ops.object.mode_set(mode=saved_mode)
    obj.data.update()
    new_num_verts = len(obj.data.vertices)

    return old_num_verts - new_num_verts

def delete_faces_with_no_material(obj):
    if not any(not mat for mat in obj.data.materials):
        # All material slots are filled, nothing to do
        return

    bm = bmesh.new()
    bm.from_mesh(obj.data)

    bm.faces.ensure_lookup_table()
    delete_geom = [f for f in bm.faces if not obj.data.materials[f.material_index]]
    bmesh.ops.delete(bm, geom=delete_geom, context='FACES')
    if delete_geom:
        log(f"Deleted {len(delete_geom)} faces with no material")

    # Finish and clean up
    bm.to_mesh(obj.data)
    bm.free()

def unsubdivide_preserve_uvs(obj, levels):
    """Split by seams then unsubdivide, preserving UVs. Mesh is expected to be quads."""
    # Works only as intended on intact subdivided meshes, not very reliable

    assert levels > 0
    bm = bmesh.new()
    bm.from_mesh(obj.data)

    seams = [e for e in bm.edges if e.seam]
    bmesh.ops.split_edges(bm, edges=seams, use_verts=False)
    bmesh.ops.unsubdivide(bm, verts=bm.verts, iterations=levels*2)
    seam_verts = [v for v in bm.verts if any(e.seam for e in v.link_edges)]
    bmesh.ops.remove_doubles(bm, verts=seam_verts, dist=1e-5)

    # Finish and clean up
    bm.to_mesh(obj.data)
    bm.free()

def bmesh_vertex_group_bleed_internal(bm, get_weight, set_weight, distance, only_tagged=False):
    # TODO Probably faster if weights were to be cached
    if distance <= 0.0:
        return

    openset = heapdict()
    for vert in bm.verts:
        if not only_tagged or vert.tag:
            w = get_weight(vert)
            if w > 0.0:
                openset[vert] = -w

    while openset:
        vert, w = openset.popitem()
        for edge in vert.link_edges:
            other_vert = edge.other_vert(vert)
            if only_tagged and not other_vert.tag:
                continue
            other_vert_w = -w - (edge.calc_length() / distance)
            if other_vert_w > 0.0:
                other_vert_old_w = get_weight(other_vert)
                if other_vert_w > other_vert_old_w:
                    if other_vert_old_w > 0.0:
                        if other_vert in openset:
                            openset.decrease_key(other_vert, -other_vert_w)
                    else:
                        openset[other_vert] = -other_vert_w
                    set_weight(other_vert, other_vert_w)

def bmesh_vertex_group_bleed(bm, vertex_group_index, distance, power=1.0, only_tagged=False):
    if distance <= 0.0 or power <= 0.0:
        return

    recp_power = 1.0 / power
    deform_layer = bm.verts.layers.deform.verify()
    def get_weight(vert):
        return vert[deform_layer].get(vertex_group_index, 0.0) ** power
    def set_weight(vert, value):
        vert[deform_layer][vertex_group_index] = value ** recp_power
    bmesh_vertex_group_bleed_internal(bm, get_weight, set_weight, distance, only_tagged)

# Internal mesh walkers are unfortunately not exposed for scripting
# https://github.com/blender/blender/blob/master/source/blender/editors/mesh/editmesh_select.c
# https://github.com/blender/blender/blob/master/source/blender/bmesh/intern/bmesh_walkers_impl.c
# https://devtalk.blender.org/t/walking-edge-loops-across-a-mesh-from-c-to-python

def _walk_island(vert):
    vert.tag = True
    yield(vert)
    linked_verts = [e.other_vert(vert) for e in vert.link_edges if not e.other_vert(vert).tag]
    for vert in linked_verts:
        if vert.tag:
            continue
        yield from _walk_island(vert)

def bmesh_find_islands(bm, verts=[]):
    """Takes input verts and finds unconnected islands. Outputs lists of vertices."""
    # From https://blender.stackexchange.com/a/105142

    def set_tag(verts, value):
        for vert in verts:
            vert.tag = value
    set_tag(bm.verts, True)
    set_tag(verts, False)
    ret = {"islands": []}
    verts = set(verts)
    while verts:
        vert = verts.pop()
        verts.add(vert)
        island = set(_walk_island(vert))
        ret["islands"].append(list(island))
        set_tag(island, False)
        verts -= island
    return ret

def _walk_coplanar(face, max_dot):
    face.tag = True
    yield(face)
    for edge in face.edges:
        for other_face in edge.link_faces:
            if other_face.tag:
                continue
            if face.normal.dot(other_face.normal) <= max_dot:
                continue
            yield from _walk_coplanar(other_face, max_dot)

def bmesh_find_coplanar(bm, angle_limit, faces=[]):
    """Takes input faces and finds islands limited by angle (in radians). Outputs lists of faces."""
    # Based on https://blender.stackexchange.com/a/105142

    max_dot = cos(angle_limit)
    def set_tag(faces, value):
        for face in faces:
            face.tag = value
    set_tag(bm.faces, True)
    set_tag(faces, False)
    ret = {"islands": []}
    faces = set(faces)
    while faces:
        face = faces.pop()
        faces.add(face)
        island = set(_walk_coplanar(face, max_dot))
        ret["islands"].append(list(island))
        set_tag(island, False)
        faces -= island
    return ret

def bmloop_uv_share_edge_check(bmloop0, bmloop1, uv_layer):
    assert bmloop0.edge == bmloop1.edge
    uv00 = bmloop0[uv_layer]
    uv01 = bmloop0.link_loop_next[uv_layer]
    uv10 = bmloop1[uv_layer]
    uv11 = bmloop1.link_loop_next[uv_layer]
    if bmloop0.vert != bmloop1.vert:
        uv10, uv11 = uv11, uv10
    return uv00.uv == uv10.uv and uv01.uv == uv11.uv

def bmloop_iter_radial(bmloop):
    bmloop_first = bmloop
    while True:
        bmloop = bmloop.link_loop_radial_next
        yield bmloop
        if bmloop == bmloop_first:
            break
