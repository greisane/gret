from collections import namedtuple
from fnmatch import fnmatch
from itertools import chain
import bmesh
import bpy
import math
import os
import time
from .helpers import (
    beep,
    clear_pose,
    fail_if_invalid_export_path,
    fail_if_no_operator,
    get_children_recursive,
    get_export_path,
    get_flipped_name,
    intercept,
    is_object_arp,
    load_selection,
    save_selection,
    select_only,
)

def get_nice_export_report(files, elapsed):
    if len(files) > 5:
        return f"{len(files)} files exported in {elapsed:.2f}s."
    if files:
        filenames = [bpy.path.basename(filepath) for filepath in files]
        return f"Exported {', '.join(filenames)} in {elapsed:.2f}s."
    return "Nothing exported."

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

def merge_basis_shape_keys(context, obj):
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

    # Only basis left? Remove it so applying modifiers has less issues
    if len(obj.data.shape_keys.key_blocks) == 1:
        obj.shape_key_clear()

    # Restore state
    for sk in saved_unmuted_shape_keys:
        sk.mute = False

def mirror_shape_keys(context, obj, side_vgroup_name):
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
            print(f"Mirroring shape key {shape_key.name}")
            shape_key.vertex_group = side_vgroup_name
            new_shape_key = duplicate_shape_key(obj, shape_key.name, flipped_name)
            new_shape_key.vertex_group = other_vgroup_name

def apply_mask_modifier(mask_modifier):
    """Applies a mask modifier in the active object by removing faces instead of vertices \
so the edge boundary is preserved"""

    obj = bpy.context.object
    saved_mode = bpy.context.mode
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

    # Clean up
    bpy.ops.object.mode_set(mode=saved_mode)

def apply_modifiers(context, obj, mask_edge_boundary=False):
    """Apply modifiers while preserving shape keys. Handles some modifiers specially."""

    def should_disable_modifier(mo):
        return (mo.type in {'ARMATURE', 'NORMAL_EDIT'}
            or mo.type == 'DATA_TRANSFER' and 'CUSTOM_NORMAL' in mo.data_types_loops
            or mo.type == 'MASK' and mask_edge_boundary)

    disabled_modifiers = []
    for modifier in obj.modifiers:
        if modifier.show_viewport and should_disable_modifier(modifier):
            modifier.show_viewport = False
            disabled_modifiers.append(modifier)

    context.view_layer.objects.active = obj
    num_shape_keys = len(obj.data.shape_keys.key_blocks) if obj.data.shape_keys else 0
    if num_shape_keys:
        print(f"Applying modifiers on {obj.name} with {num_shape_keys} shape keys")
    bpy.ops.object.apply_modifiers_with_shape_keys()

    for modifier in disabled_modifiers:
        modifier.show_viewport = True
        if modifier.type == 'ARMATURE':
            # Do nothing, just reenable
            pass
        elif modifier.type == 'MASK' and mask_edge_boundary:
            # Try to preserve edge boundaries
            print(f"Applying mask on {obj.name}")
            apply_mask_modifier(modifier)
        else:
            try:
                # Apply post-mirror modifiers
                bpy.ops.object.modifier_apply(modifier=modifier.name)
            except RuntimeError:
                print(f"Couldn't apply {modifier.type} modifier '{modifier.name}' in '{obj.name}'")

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

ik_bone_names = [
    "ik_foot_root",
    "ik_foot.l",
    "ik_foot.r",
    "ik_hand_root",
    "ik_hand_gun",
    "ik_hand.l",
    "ik_hand.r"
]

@intercept(error_result={'CANCELLED'})
def export_autorig(context, filepath, actions):
    scn = context.scene
    rig = context.active_object
    ik_bones_not_found = [s for s in ik_bone_names if
        s not in rig.pose.bones or 'custom_bone' not in rig.pose.bones[s]]
    if not ik_bones_not_found:
        # All IK bones accounted for
        add_ik_bones = False
    elif len(ik_bones_not_found) == len(ik_bone_names):
        # No IK bones present, let ARP create them
        add_ik_bones = True
    else:
        # Only some IK bones found. Probably a mistake
        raise Exception("Some IK bones are missing or not marked for export: "
            + ", ".join(ik_bones_not_found))

    # Configure Auto-Rig and then finally export
    scn.arp_engine_type = 'unreal'
    scn.arp_export_rig_type = 'humanoid'
    scn.arp_ge_sel_only = True

    # Rig Definition
    scn.arp_keep_bend_bones = False
    scn.arp_push_bend = False
    scn.arp_full_facial = True
    scn.arp_export_twist = True
    scn.arp_export_noparent = False

    # Units
    scn.arp_units_x100 = True

    # Unreal Options
    scn.arp_ue_root_motion = True
    scn.arp_rename_for_ue = True
    scn.arp_ue_ik = add_ik_bones
    scn.arp_mannequin_axes = True

    # Animation
    if not actions:
        scn.arp_bake_actions = False
        scn.arp_export_h_actions = False
    else:
        scn.arp_bake_actions = True
        scn.arp_export_h_actions = False
        scn.arp_export_name_actions = True
        scn.arp_export_name_string = ",".join(action.name for action in actions)
        scn.arp_simplify_fac = 0.0

    # Misc
    scn.arp_global_scale = 1.0
    scn.arp_mesh_smooth_type = 'EDGE'
    scn.arp_use_tspace = False
    scn.arp_fix_fbx_rot = False
    scn.arp_fix_fbx_matrix = True
    scn.arp_init_fbx_rot = False
    scn.arp_bone_axis_primary_export = 'Y'
    scn.arp_bone_axis_secondary_export = 'X'

    return bpy.ops.id.arp_export_fbx_panel(filepath=filepath)

@intercept(error_result={'CANCELLED'})
def export_fbx(context, filepath, actions):
    if actions:
        # Needs to slap action strips in the NLA
        raise NotImplementedError
    return bpy.ops.export_scene.fbx(
        filepath=filepath
        , check_existing=False
        , axis_forward='-Z'
        , axis_up='Y'
        , use_selection=True
        , use_active_collection=False
        , global_scale=1.0
        , apply_unit_scale=True
        , apply_scale_options='FBX_SCALE_NONE'
        , object_types={'ARMATURE', 'MESH'}
        , use_mesh_modifiers=True
        , use_mesh_modifiers_render=False
        , mesh_smooth_type='EDGE'
        , bake_space_transform=True
        , use_subsurf=False
        , use_mesh_edges=False
        , use_tspace=False
        , use_custom_props=False
        , add_leaf_bones=False
        , primary_bone_axis='Y'
        , secondary_bone_axis='X'
        , use_armature_deform_only=True
        , armature_nodetype='NULL'
        , bake_anim=len(actions) > 0
        , bake_anim_use_all_bones=False
        , bake_anim_use_nla_strips=False
        , bake_anim_use_all_actions=True
        , bake_anim_force_startend_keying=True
        , bake_anim_step=1.0
        , bake_anim_simplify_factor=1.0
        , path_mode='STRIP'
        , embed_textures=False
        , batch_mode='OFF'
        , use_batch_own_dir=False
    )

class MY_OT_scene_export(bpy.types.Operator):
    bl_idname = 'my_tools.scene_export'
    bl_label = "Scene Export"
    bl_context = 'objectmode'
    bl_options = {'INTERNAL'}

    export_path: bpy.props.StringProperty(
        name="Export Path",
        description="""Export path relative to the current folder.
{basename} = Name of this .blend file without extension.
{object} = Name of the object being exported""",
        default="//export/{object}.fbx",
        subtype='FILE_PATH',
    )
    export_collision: bpy.props.BoolProperty(
        name="Export Collision",
        description="Exports collision objects that follow the UE4 naming pattern",
        default=True,
    )
    material_name_prefix: bpy.props.StringProperty(
        name="Material Prefix",
        description="Ensures that exported material names begin with a prefix",
        default="MI_",
    )

    def _execute(self, context):
        collision_prefixes = ("UCX", "UBX", "UCP", "USP")
        exported_armatures = []

        for obj in context.selected_objects[:]:
            if any(obj.name.startswith(s) for s in collision_prefixes):
                # Never export collision objects by themselves
                continue

            select_only(context, obj)

            if obj.type == 'ARMATURE':
                armature = obj
            elif obj.parent and obj.parent.type == 'ARMATURE':
                armature = obj.parent
            else:
                armature = None

            if armature:
                if armature in exported_armatures:
                    # Already exported
                    continue
                # Dealing with an armature, make it the main object and redo selection
                obj.select_set(False)
                armature.select_set(True)
                for child in armature.children:
                    child.select_set(True)
                exported_armatures.append(armature)
                obj = armature

            collision_objs = []
            if not armature and self.export_collision:
                # Extend selection with pertaining collision objects
                pattern = r"^(?:%s)_%s_\d+$" % ('|'.join(collision_prefixes), obj.name)
                for col in context.scene.objects:
                    if re.match(pattern, col.name):
                        col.select_set(True)
                        collision_objs.append(col)

            # Move main object to world center while keeping collision relative transforms
            for col in collision_objs:
                self.saved_transforms[col] = col.matrix_world.copy()
                col.matrix_world = obj.matrix_world.inverted() @ col.matrix_world
            self.saved_transforms[obj] = obj.matrix_world.copy()
            obj.matrix_world.identity()

            # If set, add a prefix to the exported materials
            if self.material_name_prefix and obj.type == 'MESH':
                for mat_slot in obj.material_slots:
                    mat = mat_slot.material
                    if not mat.name.startswith(self.material_name_prefix):
                        self.saved_material_names[mat] = mat.name
                        mat.name = self.material_name_prefix + mat.name

            path_fields['object'] = obj.name
            filepath = get_export_path(self.export_path, **path_fields)
            filename = bpy.path.basename(filepath)

            result = self.export_fbx(context, filepath)
            if result == {'FINISHED'}:
                print(f"Exported {filepath}")
                self.exported_files.append(filename)

    def execute(self, context):
        # Check export path
        path_fields = {
            'object': "None",
        }
        fail_reason = fail_if_invalid_export_path(self.export_path, **path_fields)
        if fail_reason:
            self.report({'ERROR'}, fail_reason)
            return {'CANCELLED'}

        saved_selection = save_selection()
        saved_use_global_undo = context.preferences.edit.use_global_undo
        context.preferences.edit.use_global_undo = False
        self.exported_files = []
        self.saved_material_names = {}
        self.saved_transforms = {}

        try:
            start_time = time.time()
            self._execute(context)
            # Finished without errors
            elapsed = time.time() - start_time
            self.report({'INFO'}, get_nice_export_report(self.exported_files, elapsed))
        finally:
            # Clean up
            for obj, matrix_world in self.saved_transforms.items():
                obj.matrix_world = matrix_world
            for mat, name in self.saved_material_names.items():
                mat.name = name

            load_selection(saved_selection)
            context.preferences.edit.use_global_undo = saved_use_global_undo

        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

class MY_OT_rig_export(bpy.types.Operator):
    bl_idname = 'my_tools.rig_export'
    bl_label = "Rig Export"
    bl_context = 'objectmode'
    bl_options = {'INTERNAL'}

    export_path: bpy.props.StringProperty(
        name="Export Path",
        description="""Export path relative to the current folder.
{basename} = Name of this .blend file without extension""",
        default="//export/{basename}.fbx",
        subtype='FILE_PATH',
    )
    export_collection: bpy.props.StringProperty(
        name="Export Collection",
        description="Collection where to place export products",
        default="",
    )
    merge_basis_shape_keys: bpy.props.BoolProperty(
        name="Merge Basis Shape Keys",
        description="Blends 'Key' and 'b_' shapekeys into the basis shape",
        default=True,
    )
    mirror_shape_keys: bpy.props.BoolProperty(
        name="Mirror Shape Keys",
        description="Creates mirrored versions of shape keys that have side suffixes",
        default=True,
    )
    side_vgroup_name: bpy.props.StringProperty(
        name="Side Vertex Group Name",
        description="Name of the vertex groups that will be created on mirroring shape keys",
        default="_side.l",
    )
    apply_modifiers: bpy.props.BoolProperty(
        name="Apply Modifiers",
        description="Allows exporting of shape keys even if the meshes have modifiers",
        default=True,
    )
    join_meshes: bpy.props.BoolProperty(
        name="Join Meshes",
        description="Joins meshes before exporting",
        default=True,
    )
    split_masks: bpy.props.BoolProperty(
        name="Split Masks",
        description="Splits mask modifiers into extra meshes that are exported separately",
        default=False,
    )
    material_name_prefix: bpy.props.StringProperty(
        name="Material Prefix",
        description="Ensures that exported material names begin with a prefix",
        default="MI_",
    )

    def copy_obj(self, obj, copy_data=True):
        new_obj = obj.copy()
        new_obj.name = obj.name + "_"
        if copy_data:
            new_data = obj.data.copy()
            if isinstance(new_data, bpy.types.Mesh):
                self.new_meshes.add(new_data)
            else:
                print(f"Copied data of object {obj.name} won't be released!")
            new_obj.data = new_data
        self.new_objs.add(new_obj)

        # New objects are moved to the scene collection, ensuring they're visible
        bpy.context.scene.collection.objects.link(new_obj)
        new_obj.hide_set(False)
        new_obj.hide_viewport = False
        new_obj.hide_select = False
        return new_obj

    def copy_obj_clone_normals(self, obj):
        new_obj = self.copy_obj(obj, copy_data=True)
        new_obj.data.use_auto_smooth = True  # Enable custom normals
        new_obj.data.auto_smooth_angle = math.pi
        data_transfer = new_obj.modifiers.new("Clone Normals", 'DATA_TRANSFER')
        data_transfer.object = obj
        data_transfer.use_object_transform = False
        data_transfer.use_loop_data = True
        data_transfer.loop_mapping = 'NEAREST_POLY'
        data_transfer.data_types_loops = {'CUSTOM_NORMAL'}
        data_transfer.max_distance = 1e-5
        data_transfer.use_max_distance = True
        return new_obj

    @classmethod
    def poll(cls, context):
        return context.object and context.object.mode == 'OBJECT'

    def _execute(self, context, rig):
        path_fields = {}
        mesh_objs = []
        rig.data.pose_position = 'REST'

        # Clean the target collection first to free the names
        coll = bpy.data.collections.get(self.export_collection)
        if coll:
            for obj in coll.objects:
                bpy.data.objects.remove(obj, do_unlink=True)

        for obj in get_children_recursive(rig):
            # Enable all render modifiers in the originals, except masks
            for modifier in obj.modifiers:
                if modifier.type != 'MASK' and modifier.show_render and not modifier.show_viewport:
                    modifier.show_viewport = True
                    self.saved_disabled_modifiers.add(modifier)
            if obj.type == 'MESH' and not obj.hide_render and obj.find_armature() is rig:
                # Meshes that aren't already doing it will transfer normals from the originals
                if not any(mo.type == 'DATA_TRANSFER' and 'CUSTOM_NORMAL' in mo.data_types_loops
                    for mo in obj.modifiers):
                    mesh_objs.append(self.copy_obj_clone_normals(obj))
                else:
                    mesh_objs.append(self.copy_obj(obj))

        ExportGroup = namedtuple('ExportGroup', ['suffix', 'objects'])
        export_groups = []
        if mesh_objs:
            export_groups.append(ExportGroup(suffix="", objects=mesh_objs[:]))

        if self.split_masks:
            for obj in mesh_objs:
                masks = [mo for mo in obj.modifiers if mo.type == 'MASK' and mo.show_render]
                if not masks:
                    continue

                # As a special case if the only modifier has the same name as the object,
                # just make a new export group for it
                if len(masks) == 1 and masks[0].name == obj.name:
                    export_groups.append(ExportGroup(suffix="_%s" % masks[0].name, objects=[obj]))
                    export_groups[0].objects.remove(obj)
                    obj.modifiers.remove(masks[0])
                    continue

                # Split masked parts into extra meshes that receive normals from the original
                for mask in masks:
                    print(f"Splitting {mask.name} from {obj.name}")
                    new_obj = self.copy_obj_clone_normals(obj)
                    new_obj.name = mask.name

                    # Remove all masks but this one in the new object
                    for new_mask in [mo for mo in new_obj.modifiers if mo.type == 'MASK']:
                        if new_mask.name != mask.name:
                            new_obj.modifiers.remove(new_mask)

                    # New export group for the split off part
                    export_groups.append(ExportGroup(suffix="_%s" % mask.name, objects=[new_obj]))

                # Invert the masks for the part that is left behind
                base_obj = self.copy_obj_clone_normals(obj)
                original_name = obj.name
                obj.name = original_name + "_whole"
                base_obj.name = original_name
                export_groups[0].objects.append(base_obj)

                for modifier in base_obj.modifiers:
                    if modifier.type == 'MASK':
                        modifier.invert_vertex_group = not modifier.invert_vertex_group

                # Apply modifiers in the whole object, which won't be exported
                context.view_layer.objects.active = obj
                export_groups[0].objects.remove(obj)

                if obj.data.shape_keys:
                    if bpy.app.version == (2, 80, 75):
                        # Work around a bug in 2.80, see https://developer.blender.org/T68710
                        while obj.data.shape_keys and obj.data.shape_keys.key_blocks:
                            bpy.ops.object.shape_key_remove(all=False)
                    else:
                        bpy.ops.object.shape_key_remove(all=True)

                for modifier in obj.modifiers[:]:
                    if modifier.type in {'MASK'}:
                        bpy.ops.object.modifier_remove(modifier=modifier.name)
                    elif modifier.show_render:
                        try:
                            bpy.ops.object.modifier_apply(modifier=modifier.name)
                        except RuntimeError:
                            bpy.ops.object.modifier_remove(modifier=modifier.name)
                    else:
                        bpy.ops.object.modifier_remove(modifier=modifier.name)

        # Process individual meshes
        for export_group in export_groups:
            for obj in export_group.objects:
                if self.merge_basis_shape_keys:
                    merge_basis_shape_keys(context, obj)

                if self.mirror_shape_keys:
                    mirror_shape_keys(context, obj, self.side_vgroup_name)

                # Only use modifiers enabled for render. Delete unused modifiers
                for modifier in obj.modifiers[:]:
                    modifier.show_viewport = modifier.show_render
                    if not modifier.show_viewport:
                        bpy.ops.object.modifier_remove(modifier=modifier.name)
                if self.apply_modifiers:
                    apply_modifiers(context, obj, mask_edge_boundary=self.split_masks)

                # If set, add a prefix to the exported materials
                if self.material_name_prefix:
                    for mat_slot in obj.material_slots:
                        mat = mat_slot.material
                        if not mat.name.startswith(self.material_name_prefix):
                            self.saved_material_names[mat] = mat.name
                            mat.name = self.material_name_prefix + mat.name

                context.view_layer.objects.active = obj
                # Remove vertex group filtering from shapekeys
                bpy.ops.object.apply_shape_keys_with_vertex_groups()
                # Refresh vertex color mappings
                bpy.ops.my_tools.vcols_from_src()

        if self.join_meshes:
            for export_group in export_groups:
                objs = export_group.objects
                if len(objs) <= 1:
                    continue

                # Pick the densest object to receive all the others
                ctx = {}
                merged_obj = max(objs, key=lambda ob: len(ob.data.vertices))

                print(f"Merging {', '.join(obj.name for obj in objs if obj is not merged_obj)} " \
                    f"into {merged_obj.name}")

                for obj in objs:
                    if obj is not merged_obj:
                        self.new_objs.discard(obj)
                        self.new_meshes.discard(obj.data)

                ctx['object'] = ctx['active_object'] = merged_obj
                ctx['selected_objects'] = ctx['selected_editable_objects'] = objs

                bpy.ops.object.join(ctx)

                num_verts_merged = merge_freestyle_edges(merged_obj)
                if num_verts_merged > 0:
                    print(f"Welded {num_verts_merged} verts (edges were marked freestyle)")

                # Enable autosmooth for merged object to allow custom normals
                merged_obj.data.use_auto_smooth = True
                merged_obj.data.auto_smooth_angle = math.pi

                export_group.objects[:] = [merged_obj]

        # Finally export
        if self.export_path:
            for export_group in export_groups:
                path_fields['suffix'] = export_group.suffix
                filepath = get_export_path(self.export_path, **path_fields)
                filename = bpy.path.basename(filepath)
                if filepath in self.exported_files:
                    print(f"Skipping {filename} as it would overwrite a file that was just exported")

                for obj in context.scene.objects:
                    obj.select_set(False)
                for obj in export_group.objects:
                    obj.select_set(True)
                rig.select_set(True)
                context.view_layer.objects.active = rig
                rig.data.pose_position = 'POSE'
                clear_pose(rig)

                if is_object_arp(rig):
                    print(f"Exporting {filename} via Auto-Rig export")
                    result = export_autorig(context, filepath, [])
                else:
                    print(f"Exporting {filename}")
                    result = export_fbx(context, filepath, [])

                if result == {'FINISHED'}:
                    self.exported_files.append(filepath)
                else:
                    print("Failed to export!")

        # Keep new objects in the target collection
        coll = bpy.data.collections.get(self.export_collection)
        if coll:
            for export_group in export_groups:
                for obj in export_group.objects:
                    coll.objects.link(obj)
                    context.scene.collection.objects.unlink(obj)
            self.new_objs.clear()
            self.new_meshes.clear()

    def execute(self, context):
        rig = context.object

        if not rig or rig.type != 'ARMATURE':
            self.report({'ERROR'}, "Armature must be the active object.")
            return {'CANCELLED'}

        # Check addon availability and export path
        path_fields = {}
        fail_reason = (fail_if_no_operator('apply_shape_keys_with_vertex_groups')
            or fail_if_no_operator('apply_modifiers_with_shape_keys')
            or fail_if_invalid_export_path(self.export_path, **path_fields))
        if fail_reason:
            self.report({'ERROR'}, fail_reason)
            return {'CANCELLED'}

        saved_selection = save_selection()
        saved_pose_position = rig.data.pose_position
        saved_use_global_undo = context.preferences.edit.use_global_undo
        context.preferences.edit.use_global_undo = False
        self.exported_files = []
        self.new_objs = set()
        self.new_meshes = set()
        self.saved_disabled_modifiers = set()
        self.saved_material_names = {}

        try:
            start_time = time.time()
            self._execute(context, rig)
            # Finished without errors
            elapsed = time.time() - start_time
            self.report({'INFO'}, get_nice_export_report(self.exported_files, elapsed))
        finally:
            # Clean up
            while self.new_objs:
                bpy.data.objects.remove(self.new_objs.pop())
            while self.new_meshes:
                bpy.data.meshes.remove(self.new_meshes.pop())
            while self.saved_disabled_modifiers:
                self.saved_disabled_modifiers.pop().show_viewport = False
            for mat, name in self.saved_material_names.items():
                mat.name = name
            rig.data.pose_position = saved_pose_position
            context.preferences.edit.use_global_undo = saved_use_global_undo
            load_selection(saved_selection)

        if self.export_collection:
            # Crashes if undo is attempted right after a simulate export job
            # Pushing an undo step here seems to prevent that
            bpy.ops.ed.undo_push()

        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

class MY_OT_animation_export(bpy.types.Operator):
    bl_idname = 'my_tools.animation_export'
    bl_label = "Animation Export"
    bl_context = "objectmode"
    bl_options = {'INTERNAL'}

    export_path: bpy.props.StringProperty(
        name="Export Path",
        description="""Export path relative to the current folder.
{basename} = Name of this .blend file without extension.
{action} = Name of the first action being exported""",
        default="//export/{action}.fbx",
        subtype='FILE_PATH',
    )
    actions: bpy.props.StringProperty(
        name="Action Names",
        description="Comma separated list of actions to export",
        default=""
    )

    @classmethod
    def poll(cls, context):
        return context.object and context.object.mode == "OBJECT"

    def _execute(self, context, rig):
        start_time = time.time()
        path_fields = {}

        ExportGroup = namedtuple('ExportGroup', ['suffix', 'action'])
        export_groups = []

        # Add actions as export groups without meshes
        action_names = set(self.actions.split(","))
        for action_name in action_names:
            action_name = action_name.strip()
            if not action_name:
                continue
            if action_name not in bpy.data.actions:
                continue
            export_groups.append(ExportGroup(
                suffix="",
                action=bpy.data.actions[action_name],
            ))

        # Finally export
        if self.export_path:
            for export_group in export_groups:
                if not export_group.action:
                    continue

                path_fields['action'] = export_group.action.name
                path_fields['suffix'] = export_group.suffix
                filepath = get_export_path(self.export_path, **path_fields)
                filename = bpy.path.basename(filepath)
                if filepath in self.exported_files:
                    print(f"Skipping {filename} as it would overwrite a file that was just exported")

                rig.select_set(True)
                context.view_layer.objects.active = rig
                rig.data.pose_position = 'POSE'
                clear_pose(rig)

                rig.animation_data.export_group.action = export_group.action
                context.scene.frame_preview_start = export_group.action.frame_range[0]
                context.scene.frame_preview_end = export_group.action.frame_range[1]
                context.scene.use_preview_range = True
                context.scene.frame_current = export_group.action.frame_range[0]
                bpy.context.evaluated_depsgraph_get().update()

                if is_object_arp(rig):
                    print(f"Exporting {filename} via Auto-Rig export")
                    result = export_autorig(context, filepath, [export_group.action])
                else:
                    print(f"Exporting {filename}")
                    result = export_fbx(context, filepath, [export_group.action])

                if result == {'FINISHED'}:
                    self.exported_files.append(filepath)
                else:
                    print("Failed to export!")

    def execute(self, context):
        rig = context.object

        if not rig or rig.type != 'ARMATURE':
            self.report({'ERROR'}, "Armature must be the active object.")
            return {'CANCELLED'}

        path_fields = {'action': "None"}
        fail_reason = fail_if_invalid_export_path(self.export_path, **path_fields)
        if fail_reason:
            self.report({'ERROR'}, fail_reason)
            return {'CANCELLED'}

        saved_selection = save_selection()
        saved_pose_position = rig.data.pose_position
        saved_action = rig.animation_data.action
        saved_use_global_undo = context.preferences.edit.use_global_undo
        context.preferences.edit.use_global_undo = False
        self.exported_files = []

        try:
            start_time = time.time()
            self._execute(context, rig)
            # Finished without errors
            elapsed = time.time() - start_time
            self.report({'INFO'}, get_nice_export_report(self.exported_files, elapsed))
        finally:
            # Clean up
            rig.data.pose_position = saved_pose_position
            rig.animation_data.action = saved_action
            context.preferences.edit.use_global_undo = saved_use_global_undo
            load_selection(saved_selection)

        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

class MY_OT_export_job_add(bpy.types.Operator):
    #tooltip
    """Add a new export job"""

    bl_idname = 'my_tools.export_job_add'
    bl_label = "Add Export Job"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        scn = context.scene
        job = scn.my_tools.export_jobs.add()
        job_index = len(scn.my_tools.export_jobs) - 1
        job.name = "Job #%d" % (job_index + 1)
        collection = job.collections.add()
        collection.job_index = job_index
        action = job.actions.add()
        action.job_index = job_index
        copy_property = job.copy_properties.add()
        copy_property.job_index = job_index
        remap_material = job.remap_materials.add()
        remap_material.job_index = job_index

        return {'FINISHED'}

class MY_OT_export_job_remove(bpy.types.Operator):
    #tooltip
    """Removes an export job"""

    bl_idname = 'my_tools.export_job_remove'
    bl_label = "Remove Export Job"
    bl_options = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty(options={'HIDDEN'})

    def execute(self, context):
        scn = context.scene
        scn.my_tools.export_jobs.remove(self.index)

        # Job list changed, keep indices updated
        for job_idx, job in enumerate(scn.my_tools.export_jobs):
            for coll in job.collections:
                coll.job_index = job_idx
            for action in job.actions:
                action.job_index = job_idx
            for copy_property in job.copy_properties:
                copy_property.job_index = job_idx
            for remap_material in job.remap_materials:
                remap_material.job_index = job_idx

        return {'FINISHED'}

class MY_OT_export_job_run(bpy.types.Operator):
    #tooltip
    """Execute export job"""

    bl_idname = 'my_tools.export_job_run'
    bl_label = "Execute Export Job"

    index: bpy.props.IntProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def _execute(self, context):
        scn = context.scene
        job = scn.my_tools.export_jobs[self.index]

        def should_export(job_coll, what):
            if job_coll is None or what is None:
                return False
            return (job_coll.export_viewport and not what.hide_viewport
                or job_coll.export_render and not what.hide_render)

        if job.what == 'SCENE':
            if not job.selection_only:
                objs = set()
                for job_coll in job.collections:
                    coll = job_coll.collection
                    if should_export(job_coll, coll):
                        for obj in coll.objects:
                            if obj not in objs and should_export(job_coll, obj):
                                obj.hide_select = False
                                obj.hide_viewport = False
                                obj.hide_render = False
                                objs.add(obj)
                select_only(context, objs)

            if not context.selected_objects:
                self.report({'ERROR'}, "Nothing to export.")
                return {'CANCELLED'}

            print(f"Beginning scene export job '{job.name}'")

            bpy.ops.my_tools.scene_export(
                export_path=job.export_path,
                export_collision=job.export_collision,
                material_name_prefix=job.material_name_prefix,
            )

        elif job.what == 'RIG':
            if not job.rig:
                self.report({'ERROR'}, "No armature selected.")
                return {'CANCELLED'}
            if not job.rig.visible_get():
                self.report({'ERROR'}, "Currently the rig must be visible to export.")
                return {'CANCELLED'}
            context.view_layer.objects.active = job.rig
            bpy.ops.object.mode_set(mode='OBJECT')

            print(f"Beginning rig export job '{job.name}'")

            # Mark the objects that should be exported as render so they will be picked up
            objs = set()
            for job_coll in job.collections:
                coll = job_coll.collection
                if should_export(job_coll, coll):
                    for obj in coll.objects:
                        if obj not in objs and should_export(job_coll, obj):
                            if obj.type == 'MESH':
                                saved_materials = []
                                for mat_idx, mat in enumerate(obj.data.materials):
                                    for remap_material in job.remap_materials:
                                        if mat and mat is remap_material.source:
                                            saved_materials.append((obj, mat_idx, mat))
                                            obj.data.materials[mat_idx] = remap_material.destination
                                            break
                                if all(not mat for mat in obj.data.materials):
                                    print(f"Not exporting '{obj.name}' because it has no materials")
                                    # Undo any remaps
                                    for obj, material_idx, material in saved_materials:
                                        obj.data.materials[material_idx] = material
                                    continue
                                self.saved_materials.extend(saved_materials)
                            obj.hide_select = False
                            obj.hide_render = False
                            objs.add(obj)

            # Hide all objects that shouldn't be exported
            for obj in get_children_recursive(job.rig):
                obj.hide_render = obj not in objs

            export_coll = job.export_collection
            bpy.ops.my_tools.rig_export(
                export_path=job.export_path if not job.to_collection else "",
                export_collection=export_coll.name if job.to_collection and export_coll else "",
                merge_basis_shape_keys=job.merge_basis_shape_keys,
                mirror_shape_keys=job.mirror_shape_keys,
                side_vgroup_name=job.side_vgroup_name,
                apply_modifiers=job.apply_modifiers,
                join_meshes=job.join_meshes,
                split_masks=job.split_masks,
                material_name_prefix=job.material_name_prefix,
            )
            beep(0)

        elif job.what == 'ANIMATION':
            if not job.rig:
                self.report({'ERROR'}, "No armature selected.")
                return {'CANCELLED'}
            if not job.rig.visible_get():
                self.report({'ERROR'}, "Currently the rig must be visible to export.")
                return {'CANCELLED'}
            context.view_layer.objects.active = job.rig
            bpy.ops.object.mode_set(mode='OBJECT')

            print(f"Beginning animation export job '{job.name}'")

            action_names = set()
            for job_action in job.actions:
                if job_action:
                    if not job_action.use_pattern:
                        action_names.add(job_action.action)
                    else:
                        action_names.update(action.name for action in bpy.data.actions
                            if fnmatch(action.name, job_action.action))

            for cp in job.copy_properties:
                for action_name in action_names:
                    action = bpy.data.actions.get(action_name)
                    if not action:
                        continue

                    try:
                        fcurve_src = next(fc for fc in action.fcurves if fc.data_path == cp.source)
                    except StopIteration:
                        continue

                    print(f"Baking {cp.source} -> {cp.destination} in '{action_name}'")

                    fcurve_dst = action.fcurves.new(cp.destination)
                    self.new_fcurves.append((action, fcurve_dst))
                    for frame_idx in range(0, int(action.frame_range[1]) + 1):
                        val = fcurve_src.evaluate(frame_idx)
                        fcurve_dst.keyframe_points.insert(frame_idx, val)

            bpy.ops.my_tools.animation_export(
                export_path=job.export_path,
                actions=",".join(action_names),
            )
            beep(1)

        print("Job complete")

    def execute(self, context):
        saved_selection = save_selection(all_objects=True)
        self.new_fcurves = []  # List of (action, fcurve)
        self.saved_materials = []  # List of (obj, material_idx, material)

        try:
            self._execute(context)
        finally:
            # Clean up
            for action, fcurve in self.new_fcurves:
                action.fcurves.remove(fcurve)
            for obj, material_idx, material in self.saved_materials:
                obj.data.materials[material_idx] = material
            load_selection(saved_selection)

        return {'FINISHED'}

class MY_PT_export_jobs(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "My Tools"
    bl_label = "Export Jobs"

    def draw(self, context):
        layout = self.layout
        scn = context.scene

        layout.operator("my_tools.export_job_add", text="Add")

        for job_idx, job in enumerate(scn.my_tools.export_jobs):
            col_job = layout.column(align=True)
            box = col_job.box()
            row = box.row()
            icon = 'DISCLOSURE_TRI_DOWN' if job.show_expanded else 'DISCLOSURE_TRI_RIGHT'
            row.prop(job, 'show_expanded', icon=icon, text="", emboss=False)
            row.prop(job, 'what', text="", expand=True)
            row.prop(job, 'name', text="")
            op = row.operator('my_tools.export_job_remove', icon='X', text="", emboss=False)
            op.index = job_idx
            box = col_job.box()
            col = box

            if job.show_expanded:
                def add_collection_layout():
                    col = box.column(align=True)
                    for coll in job.collections:
                        row = col.row(align=True)
                        row.prop(coll, 'collection', text="")
                        row.prop(coll, 'export_viewport', icon='RESTRICT_VIEW_OFF', text="")
                        row.prop(coll, 'export_render', icon='RESTRICT_RENDER_OFF', text="")
                    return col

                if job.what == 'SCENE':
                    col.prop(job, 'selection_only')
                    add_collection_layout().enabled = not job.selection_only

                    col = box.column()
                    col.prop(job, 'export_collision')
                    col.prop(job, 'material_name_prefix', text="M. Prefix")

                    col = box.column(align=True)
                    col.prop(job, 'export_path', text="")

                elif job.what == 'RIG' or job.what == 'MESH':  # 'MESH' for backwards compat
                    box.prop(job, 'rig')
                    add_collection_layout()

                    col = box.column()
                    col.prop(job, 'merge_basis_shape_keys')
                    row = col.row(align=True)
                    row.prop(job, 'mirror_shape_keys')
                    row.prop(job, 'side_vgroup_name', text="")
                    col.prop(job, 'apply_modifiers')
                    col.prop(job, 'join_meshes')
                    # Don't have an use for Split Masks currently and too many options gets confusing
                    # col.prop(job, 'split_masks')
                    col.prop(job, 'material_name_prefix', text="M. Prefix")

                    col = box.column(align=True)
                    col.label(text="Remap Materials:")
                    for remap_material in job.remap_materials:
                        row = col.row(align=True)
                        row.prop(remap_material, 'source', text="")
                        row.label(text="", icon='FORWARD')
                        row.prop(remap_material, 'destination', text="")

                    col = box.column(align=True)
                    col.prop(job, 'to_collection')
                    col.prop(job, 'export_collection' if job.to_collection else "export_path", text="")

                elif job.what == 'ANIMATION':
                    box.prop(job, 'rig')

                    col = box.column(align=True)
                    for action in job.actions:
                        row = col.row(align=True)
                        if not action.use_pattern:
                            row.prop_search(action, 'action', bpy.data, "actions", text="")
                        else:
                            row.prop(action, 'action', text="")
                        row.prop(action, 'use_pattern', icon='SELECT_SET', text="")

                    col = box.column(align=True)
                    col.label(text="Bake Properties:")
                    for copy_property in job.copy_properties:
                        row = col.row(align=True)
                        row.prop(copy_property, 'source', text="")
                        row.label(text="", icon='FORWARD')
                        row.prop(copy_property, 'destination', text="")

                    col = box.column(align=True)
                    col.prop(job, 'export_path', text="")

            op = col.operator('my_tools.export_job_run', icon='FORWARD', text="Execute")
            op.index = job_idx

classes = (
    MY_OT_animation_export,
    MY_OT_export_job_add,
    MY_OT_export_job_remove,
    MY_OT_export_job_run,
    MY_OT_rig_export,
    MY_OT_scene_export,
    MY_PT_export_jobs,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
