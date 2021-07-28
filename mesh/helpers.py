from mathutils import Vector
import bmesh
import bpy
import re

from ..helpers import get_flipped_name, get_context, select_only
from ..log import logger, log, logd

one_vector = Vector((1, 1, 1))
half_vector = Vector((0.5, 0.5, 0.5))

def edit_mesh_elements(obj, type='VERT', indices=None, key=None):
    """
    Enters edit mode and selects elements of a mesh to be operated on.

    indices: Iterable with the indices of the elements to select. If None, all elements are selected.
    key: A function can be supplied to determine which elements should be selected.

    Returns the number of elements selected.
    """

    select_only(bpy.context, obj)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.reveal()
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.mesh.select_mode(type=type)
    bpy.ops.object.mode_set(mode='OBJECT')

    mesh = obj.data
    num_selected = 0
    if type == 'VERT':
        elements = (mesh.vertices if indices is None else (mesh.vertices[i] for i in indices))
    elif type == 'EDGE':
        elements = (mesh.edges if indices is None else (mesh.edges[i] for i in indices))
    elif type == 'FACE':
        elements = (mesh.polygons if indices is None else (mesh.polygons[i] for i in indices))

    if key is None:
        for el in elements:
            el.select = True
            num_selected += 1
    else:
        for el in elements:
            el.select = bool(key(el))
            num_selected += el.select

    bpy.ops.object.mode_set(mode='EDIT')

    return num_selected

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

def merge_basis_shape_keys(obj):
    shape_key_name_prefixes = ("Key ", "b_")

    if not obj.data.shape_keys or not obj.data.shape_keys.key_blocks:
        # No shape keys
        return

    # Store state
    saved_unmuted_shape_keys = [sk for sk in obj.data.shape_keys.key_blocks if not sk.mute]

    # Mute all but the ones to be merged
    obj.data.shape_keys.key_blocks[0].name = "Basis"  # Rename to make sure it won't be picked up
    for sk in obj.data.shape_keys.key_blocks[:]:
        if any(sk.name.startswith(s) for s in shape_key_name_prefixes):
            if sk.mute:
                # Delete candidate shapekeys that won't be used
                # This ensures muted shapekeys don't unexpectedly return when objects are merged
                obj.shape_key_remove(sk)
        else:
            sk.mute = True

    num_shape_keys = sum([not sk.mute for sk in obj.data.shape_keys.key_blocks])
    if num_shape_keys:
        log(f"Merging {num_shape_keys} basis shape keys")

        # Replace basis with merged
        new_basis = obj.shape_key_add(name="New Basis", from_mix=True)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        new_basis_layer = bm.verts.layers.shape[new_basis.name]
        for vert in bm.verts:
            vert.co[:] = vert[new_basis_layer]
        bm.to_mesh(obj.data)
        bm.free()

        # Remove the merged shapekeys
        for sk in obj.data.shape_keys.key_blocks[:]:
            if not sk.mute:
                obj.shape_key_remove(sk)

    # Restore state
    for sk in saved_unmuted_shape_keys:
        sk.mute = False

    # Only basis left? Remove it so applying modifiers has less issues
    if obj.data.shape_keys and len(obj.data.shape_keys.key_blocks) == 1:
        obj.shape_key_clear()

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
    other_vgroup_name = get_flipped_name(side_vgroup_name)
    if not other_vgroup_name:
        return
    vgroup = obj.vertex_groups.get(side_vgroup_name) or obj.vertex_groups.new(name=side_vgroup_name)
    vgroup.add(range(len(obj.data.vertices)), 1.0, 'REPLACE')
    vgroup = obj.vertex_groups.get(other_vgroup_name) or obj.vertex_groups.new(name=other_vgroup_name)

    for sk in obj.data.shape_keys.key_blocks:
        flipped_name = get_flipped_name(sk.name)
        # Only mirror it if it doesn't already exist
        if flipped_name and flipped_name not in obj.data.shape_keys.key_blocks:
            log(f"Mirroring shape key {sk.name}")
            logger.indent += 1
            sk.vertex_group = side_vgroup_name
            new_sk = duplicate_shape_key(obj, sk.name, flipped_name)
            new_sk.vertex_group = other_vgroup_name

            # Attempt to flip the driver, e.g if driven by Arm_L, make it driven by Arm_R instead.
            try:
                flip_data_path = lambda match: f'["{get_flipped_name(match.group(1)) or match.group(1)}"]'
                sk_data_path = f'key_blocks["{sk.name}"]'
                new_sk_data_path = f'key_blocks["{new_sk.name}"]'
                if obj.data.shape_keys.animation_data:
                    for fc in obj.data.shape_keys.animation_data.drivers:
                        if fc.data_path.startswith(sk_data_path):
                            new_data_path = new_sk_data_path + fc.data_path[len(sk_data_path):]
                            new_fc = obj.data.shape_keys.driver_add(new_data_path)
                            new_fc.driver.expression = fc.driver.expression
                            new_fc.driver.type = fc.driver.type
                            new_fc.driver.use_self = fc.driver.use_self
                            for var in fc.driver.variables:
                                new_var = new_fc.driver.variables.new()
                                new_var.name = var.name
                                new_var.type = var.type
                                for t, new_t in zip(var.targets, new_var.targets):
                                    new_t.bone_target = get_flipped_name(t.bone_target) or t.bone_target
                                    new_t.data_path = re.sub(r'\["([^"]*)"\]', flip_data_path, t.data_path)
                                    new_t.id = t.id
                                    new_t.rotation_mode = t.rotation_mode
                                    new_t.transform_space = t.transform_space
                                    new_t.transform_type = t.transform_type
            except Exception as e:
                log(f"Couldn't mirror driver: {e}")

            logger.indent -= 1

def encode_shape_key(obj, shape_key_index):
    mesh = obj.data
    sk = mesh.shape_keys.key_blocks[shape_key_index]
    ensure_uv_map = lambda name: mesh.uv_layers.get(name) or mesh.uv_layers.new(name=name)
    uv_map_names = (
        ensure_uv_map(f"{sk.name}_WPOxy").name,
        ensure_uv_map(f"{sk.name}_WPOzNORx").name,
        ensure_uv_map(f"{sk.name}_NORyz").name,
    )
    log(f"Encoding shape key {sk.name} to UV channels " +
        ", ".join(str(mesh.uv_layers.find(name)) for name in uv_map_names))

    bm = bmesh.new()
    bm.from_mesh(mesh, use_shape_key=True, shape_key_index=shape_key_index)
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
    obj.data.update()

def apply_modifiers(obj, key=None, keep_armature=False):
    """Apply modifiers while preserving shape keys."""

    ctx = get_context(obj)

    for mod in obj.modifiers:
        enable = key(mod) if key else True
        logd(f"{'Enabled' if enable else 'Disabled'} {mod.type} modifier {mod.name}")
        mod.show_viewport = enable

    bpy.ops.gret.shape_key_apply_modifiers(ctx, keep_modifiers=True)

    for mod in obj.modifiers[:]:
        if mod.type == 'ARMATURE' and keep_armature:
            mod.show_viewport = True
        else:
            logd(f"Removed {mod.type} modifier {mod.name}")
            bpy.ops.object.modifier_remove(ctx, modifier=mod.name)

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

def merge_freestyle_edges(obj):
    """Does 'Remove Doubles' on freestyle marked edges. Returns the number of vertices merged."""
    # Reverted to using bpy.ops because bmesh is failing to merge normals correctly

    saved_mode = bpy.context.mode

    edit_mesh_elements(obj, 'EDGE', key=lambda e: e.use_freestyle_mark)
    old_num_verts = len(obj.data.vertices)
    bpy.ops.mesh.remove_doubles(threshold=1e-5, use_unselected=False)

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

def bmesh_vertex_group_expand(bm, vertex_group_index, distance, power=1.0, only_tagged=False):
    # TODO Rewrite
    # - Algorithm does too much redundant work. Sort starting set by weight?
    # - Power as a parameter isn't very intuitive

    if distance <= 0.0:
        return

    bm.verts.layers.deform.verify()
    deform_layer = bm.verts.layers.deform.active
    def get_weight(vert):
        return vert[deform_layer].get(vertex_group_index, 0.0)
    def set_weight(vert, value):
        vert[deform_layer][vertex_group_index] = value

    if only_tagged:
        openset = [v for v in bm.verts if v.tag and get_weight(v)]
    else:
        openset = [v for v in bm.verts if get_weight(v)]

    while openset:
        vert = openset.pop()
        w = get_weight(vert)
        for edge in vert.link_edges:
            other_vert = edge.other_vert(vert)
            if only_tagged and not other_vert.tag:
                continue
            other_vert_w = w - edge.calc_length() / distance
            if other_vert_w > 0.0:
                other_vert_w **= power
                if other_vert_w > get_weight(other_vert):
                    set_weight(other_vert, other_vert_w)
                    openset.append(other_vert)
