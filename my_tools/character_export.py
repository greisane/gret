from collections import namedtuple
from itertools import chain
import bmesh
import bpy
import math
import os
import time
from .helpers import (
    check_invalid_export_path,
    clear_pose,
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
    # Store state
    saved_show_only_shape_key = obj.show_only_shape_key
    saved_active_shape_key_index = obj.active_shape_key_index

    shape_key_index = obj.data.shape_keys.key_blocks.find(name)
    obj.active_shape_key_index = shape_key_index

    obj.show_only_shape_key = True
    new_shape_key = obj.shape_key_add(name=new_name, from_mix=True)

    # Restore state
    obj.show_only_shape_key = saved_show_only_shape_key
    obj.active_shape_key_index = saved_active_shape_key_index

    return new_shape_key

def mirror_shape_keys(context, obj):
    if not obj.data.shape_keys or not obj.data.shape_keys.key_blocks:
        # No shape keys
        return

    if not any(mo.type == 'MIRROR' and mo.use_mirror_vertex_groups for mo in obj.modifiers):
        # No useful mirrors
        return

    # Make vertex groups for masking. It doesn't actually matter which side is which,
    # only that the modifier's vertex group mirroring function picks it up
    vgroup = obj.vertex_groups.get("side_L") or obj.vertex_groups.new(name="side_L")
    vgroup.add([vert.index for vert in obj.data.vertices], 1.0, 'REPLACE')
    vgroup = obj.vertex_groups.get("side_R") or obj.vertex_groups.new(name="side_R")

    for shape_key in obj.data.shape_keys.key_blocks:
        flipped_name = get_flipped_name(shape_key.name)
        if flipped_name and flipped_name not in obj.data.shape_keys.key_blocks:
            print(f"Mirroring shape key {shape_key.name}")
            shape_key.vertex_group = "side_L"
            new_shape_key = duplicate_shape_key(obj, shape_key.name, flipped_name)
            new_shape_key.vertex_group = "side_R"

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
    if bpy.context.mode != 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')
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
    if bpy.context.mode != saved_mode:
        bpy.ops.object.mode_set(mode=saved_mode)

def apply_modifiers(context, obj, mask_edge_boundary=False):
    """Apply modifiers while preserving shape keys. Handles some modifiers specially."""

    def should_disable_modifier(mo):
        return (mo.type in {'ARMATURE', 'NORMAL_EDIT', 'WEIGHTED_NORMAL'}
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
            # Apply post-mirror modifiers
            bpy.ops.object.modifier_apply(modifier=modifier.name)

def merge_freestyle_edges(obj):
    """Does 'Remove Doubles' on freestyle marked edges. Returns the number of vertices merged."""
    # Reverted to using bpy.ops because bmesh is failing to merge normals correctly

    saved_mode = bpy.context.mode

    # Need vertex mode to be set then object mode to actually select
    select_only(bpy.context, obj)
    if bpy.context.mode != 'EDIT':
        bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_mode(type='EDGE')
    bpy.ops.mesh.select_all(action='DESELECT')
    bpy.ops.object.mode_set(mode='OBJECT')

    for edge in obj.data.edges:
        edge.select = edge.use_freestyle_mark

    bpy.ops.object.mode_set(mode='EDIT')
    old_num_verts = len(obj.data.vertices)
    bpy.ops.mesh.remove_doubles(threshold=1e-5, use_unselected=False)
    new_num_verts = len(obj.data.vertices)

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
    if bpy.context.mode != saved_mode:
        bpy.ops.object.mode_set(mode=saved_mode)

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
        , global_scale=1.0
        , apply_unit_scale=True
        , apply_scale_options='FBX_SCALE_NONE'
        , bake_space_transform=False
        , object_types={'ARMATURE', 'MESH'}
        , use_mesh_modifiers=True
        , use_mesh_modifiers_render=False
        , mesh_smooth_type='EDGE'
        , use_mesh_edges=False
        , use_tspace=False
        , use_custom_props=False
        , add_leaf_bones=False
        , primary_bone_axis='Y'
        , secondary_bone_axis='X'
        , use_armature_deform_only=True
        , armature_nodetype='NULL'
        , bake_anim=False
        , bake_anim_use_all_bones=False
        , bake_anim_use_nla_strips=False
        , bake_anim_use_all_actions=True
        , bake_anim_force_startend_keying=True
        , bake_anim_step=1.0
        , bake_anim_simplify_factor=0.0
        , path_mode='COPY'
        , embed_textures=False
    )

class MY_OT_rig_export(bpy.types.Operator):
    bl_idname = 'my_tools.rig_export'
    bl_label = "Rig Export"
    bl_context = 'objectmode'
    bl_options = {'INTERNAL'}

    export_path: bpy.props.StringProperty(
        name="Export Path",
        description="""Export path relative to the current folder.
{basename} = Name of the .blend file without extension, if available.
{action} = Name of the first action being exported, if exporting actions""",
        default="//export/{basename}.fbx",
        subtype='FILE_PATH',
    )
    export_collection: bpy.props.StringProperty(
        name="Export Collection",
        description="Collection where to place export products",
        default="",
    )
    apply_modifiers: bpy.props.BoolProperty(
        name="Apply Modifiers",
        description="Allows exporting of shape keys even if the meshes have modifiers",
        default=True,
    )
    mirror_shape_keys: bpy.props.BoolProperty(
        name="Mirror Shape Keys",
        description="Creates mirrored versions of shape keys that have side suffixes",
        default=True,
    )
    join_meshes: bpy.props.BoolProperty(
        name="Join Meshes",
        description="Joins meshes before exporting",
        default=True,
    )
    preserve_mask_normals: bpy.props.BoolProperty(
        name="Preserve Mask Normals",
        description="Preserves normals of meshes that have mask modifiers",
        default=True,
    )
    split_masks: bpy.props.BoolProperty(
        name="Split Masks",
        description="Splits mask modifiers into extra meshes that are exported separately",
        default=False,
    )

    def check_operator(self, bl_idname):
        # hasattr seems to always return True, can't use that
        try:
            getattr(bpy.ops.object, bl_idname)
        except AttributeError:
            self.report({'ERROR'}, "Operator %s is required and couldn't be found." % bl_idname)
            return False
        return True

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
        start_time = time.time()
        path_fields = {}
        mesh_objs = []
        rig.data.pose_position = 'REST'

        # Clean the target collection first to free the names
        coll = bpy.data.collections.get(self.export_collection)
        if coll:
            for obj in coll.objects:
                bpy.data.objects.remove(obj, do_unlink=True)

        for obj in get_children_recursive(rig):
            if obj.type == 'MESH' and not obj.hide_render and obj.find_armature() is rig:
                if self.preserve_mask_normals and any(mo.type == 'MASK' for mo in obj.modifiers):
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
                if self.mirror_shape_keys:
                    mirror_shape_keys(context, obj)

                for modifier in obj.modifiers:
                    modifier.show_viewport = modifier.show_render
                if self.apply_modifiers:
                    apply_modifiers(context, obj, mask_edge_boundary=self.split_masks)

                context.view_layer.objects.active = obj
                bpy.ops.object.apply_shape_keys_with_vertex_groups()

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
                if num_verts_merged:
                    print(f"Merged {num_verts_merged} duplicate verts (edges were marked freestyle)")

                # Enable autosmooth for merged object to allow custom normals
                merged_obj.data.use_auto_smooth = True
                merged_obj.data.auto_smooth_angle = math.pi

                export_group.objects[:] = [merged_obj]

        # Finally export
        exported_files = []
        if self.export_path:
            for export_group in export_groups:
                path_fields['suffix'] = export_group.suffix
                filepath = get_export_path(self.export_path, **path_fields)
                filename = bpy.path.basename(filepath)
                if filepath in exported_files:
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
                    result = export_autorig(context, filepath, export_group.actions)
                else:
                    print(f"Exporting {filename}")
                    result = export_fbx(context, filepath, export_group.actions)

                if result == {'FINISHED'}:
                    exported_files.append(filepath)
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

        # Finished without errors
        elapsed = time.time() - start_time
        self.report({'INFO'}, get_nice_export_report(exported_files, elapsed))

    def execute(self, context):
        rig = context.object

        if not rig or rig.type != 'ARMATURE':
            self.report({'ERROR'}, "Armature must be the active object.")
            return {'CANCELLED'}

        # Check addon availability
        if not self.check_operator('apply_shape_keys_with_vertex_groups'):
            return {'CANCELLED'}

        if self.apply_modifiers and not self.check_operator('apply_modifiers_with_shape_keys'):
            return {'CANCELLED'}

        path_fields = {}
        reason = check_invalid_export_path(self.export_path, **path_fields)
        if reason:
            self.report({'ERROR'}, reason)
            return {'CANCELLED'}

        saved_selection = save_selection()
        saved_pose_position = rig.data.pose_position
        saved_use_global_undo = context.preferences.edit.use_global_undo
        context.preferences.edit.use_global_undo = False
        self.new_objs = set()
        self.new_meshes = set()

        try:
            self._execute(context, rig)
        finally:
            # Clean up
            while self.new_objs:
                bpy.data.objects.remove(self.new_objs.pop())
            while self.new_meshes:
                bpy.data.meshes.remove(self.new_meshes.pop())
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
{basename} = Name of the .blend file without extension, if available.
{action} = Name of the first action being exported""",
        default="//export/{action}.fbx",
        subtype='FILE_PATH',
    )
    actions: bpy.props.StringProperty(
        name="Action Names",
        description="Comma separated list of actions to export",
        default=""
    )

    def check_operator(self, bl_idname):
        # hasattr seems to always return True, can't use that
        try:
            getattr(bpy.ops.object, bl_idname)
        except AttributeError:
            self.report({'ERROR'}, "Operator %s is required and couldn't be found." % bl_idname)
            return False
        return True

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
        exported_files = []
        if self.export_path:
            for export_group in export_groups:
                if not export_group.action:
                    continue

                path_fields["action"] = export_group.action.name
                path_fields["suffix"] = export_group.suffix
                filepath = get_export_path(self.export_path, **path_fields)
                filename = bpy.path.basename(filepath)
                if filepath in exported_files:
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
                    exported_files.append(filepath)
                else:
                    print("Failed to export!")

        # Finished without errors
        elapsed = time.time() - start_time
        self.report({'INFO'}, get_nice_export_report(exported_files, elapsed))

    def execute(self, context):
        rig = context.object

        if not rig or rig.type != 'ARMATURE':
            self.report({'ERROR'}, "Armature must be the active object.")
            return {'CANCELLED'}

        path_fields = {'action': "None"}
        reason = check_invalid_export_path(self.export_path, **path_fields)
        if reason:
            self.report({'ERROR'}, reason)
            return {'CANCELLED'}

        saved_selection = save_selection()
        saved_pose_position = rig.data.pose_position
        saved_use_global_undo = context.preferences.edit.use_global_undo
        context.preferences.edit.use_global_undo = False

        try:
            self._execute(context, rig)
        finally:
            # Clean up
            rig.data.pose_position = saved_pose_position
            context.preferences.edit.use_global_undo = saved_use_global_undo
            load_selection(saved_selection)

        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)


classes = (
    MY_OT_rig_export,
    MY_OT_animation_export,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
