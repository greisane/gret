from collections import namedtuple
from itertools import chain
import bmesh
import bpy
from .helpers import (
    get_flipped_name,
    log,
    logger,
    select_only,
)

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

    num_shape_keys = len([sk for sk in obj.data.shape_keys.key_blocks if not sk.mute])
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
    vgroup.add([vert.index for vert in obj.data.vertices], 1.0, 'REPLACE')
    vgroup = obj.vertex_groups.get(other_vgroup_name) or obj.vertex_groups.new(name=other_vgroup_name)

    for shape_key in obj.data.shape_keys.key_blocks:
        flipped_name = get_flipped_name(shape_key.name)
        # Only mirror it if it doesn't already exist
        if flipped_name and flipped_name not in obj.data.shape_keys.key_blocks:
            log(f"Mirroring shape key {shape_key.name}")
            shape_key.vertex_group = side_vgroup_name
            new_shape_key = duplicate_shape_key(obj, shape_key.name, flipped_name)
            new_shape_key.vertex_group = other_vgroup_name

def apply_mask_modifier(obj, mask_modifier):
    """Applies a mask modifier in the active object by removing faces instead of vertices \
so the edge boundary is preserved"""

    if mask_modifier.vertex_group not in obj.vertex_groups:
        # No such vertex group
        return
    mask_vgroup = obj.vertex_groups[mask_modifier.vertex_group]

    # Need vertex mode to be set then object mode to actually select
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.reveal()
    bpy.ops.mesh.select_mode(type='FACE')
    bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.mesh.select_mode(type='VERT')
    bpy.ops.object.mode_set(mode='OBJECT')

    for vert in obj.data.vertices:
        vert.select = any(vgroup.group == mask_vgroup.index for vgroup in vert.groups)

    # I'm sure there's a nice clean way to do this with bmesh but I can't be bothered
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='FACE')
    if not mask_modifier.invert_vertex_group:
        bpy.ops.mesh.select_all(action='INVERT')
    bpy.ops.mesh.delete(type='FACE')

    obj.modifiers.remove(mask_modifier)

def apply_modifiers(obj, mask_edge_boundary=False):
    """Apply modifiers while preserving shape keys. Handles some modifiers specially."""

    modifiers = []
    num_shape_keys = len(obj.data.shape_keys.key_blocks) if obj.data.shape_keys else 0
    if num_shape_keys:
        def should_disable_modifier(mo):
            return (mo.type in {'ARMATURE', 'NORMAL_EDIT'}
                or mo.type == 'DATA_TRANSFER' and 'CUSTOM_NORMAL' in mo.data_types_loops
                or mo.type == 'MASK' and mask_edge_boundary)

        for modifier in obj.modifiers:
            # Disable modifiers to be applied after mirror
            if modifier.show_viewport and should_disable_modifier(modifier):
                modifier.show_viewport = False
                modifiers.append(modifier)

        log(f"Applying modifiers with {num_shape_keys} shape keys")
        bpy.ops.object.apply_modifiers_with_shape_keys({'object': obj})
    else:
        modifiers = [mo for mo in obj.modifiers if mo.show_viewport]

    for modifier in modifiers:
        modifier.show_viewport = True
        if modifier.type == 'ARMATURE':
            # Do nothing, just reenable
            pass
        elif modifier.type == 'MASK' and mask_edge_boundary:
            # Try to preserve edge boundaries
            log(f"Applying mask '{modifier.name}' while preserving boundaries")
            apply_mask_modifier(obj, modifier)
        else:
            if modifier.name == "_Clone Normals":
                log(f"Cloning normals from original")
            try:
                bpy.ops.object.modifier_apply(modifier=modifier.name)
            except RuntimeError:
                log(f"Couldn't apply {modifier.type} modifier '{modifier.name}'")

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

    # Need vertex mode to be set then object mode to actually select
    select_only(bpy.context, obj)
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='EDGE')
    bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.object.mode_set(mode='OBJECT')

    for edge in obj.data.edges:
        edge.select = edge.use_freestyle_mark

    bpy.ops.object.mode_set(mode='EDIT')
    old_num_verts = len(obj.data.vertices)
    bpy.ops.mesh.remove_doubles(threshold=1e-5, use_unselected=False)

    # mesh = obj.data
    # bm = bmesh.new()
    # bm.from_mesh(mesh)
    # bm.edges.ensure_lookup_table()
    # old_num_verts = len(bm.verts)

    # # Seems the following would be the proper way, however as of 2.90.0 it returns NotImplemented
    # # fs_layer = bm.edges.layers.freestyle.active
    # # fs_edges = [e for e in bm.edges if bm.edges[idx][fs_layer]]
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

    delete_geom = [f for f in bm.faces if not obj.data.materials[f.material_index]]
    bmesh.ops.delete(bm, geom=delete_geom, context='FACES')
    log(f"Deleted {len(delete_geom)} faces with no material")

    # Finish and clean up
    bm.to_mesh(obj.data)
    bm.free()
