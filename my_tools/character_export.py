import os
import math
from collections import namedtuple
import bpy
from .helpers import get_children_recursive, get_flipped_name, intercept
from .helpers import get_export_path, check_invalid_export_path
from .helpers import is_object_arp, clear_pose

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

    if mask_modifier.vertex_group not in obj.vertex_groups:
        # No such vertex group
        return

    mask_vgroup = obj.vertex_groups[mask_modifier.vertex_group]
    saved_mode = bpy.context.mode

    # Need vertex mode to be set then object mode to actually select
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

def apply_modifiers(context, obj, only_render=True):
    """Apply modifiers while preserving shape keys. Handles some modifiers specially"""
    special_modifier_names = {'ARMATURE', 'MASK', 'DATA_TRANSFER', 'NORMAL_EDIT', 'WEIGHTED_NORMAL'}
    special_modifiers = []
    for modifier in obj.modifiers:
        if only_render:
            modifier.show_viewport = modifier.show_render
        if modifier.show_viewport and modifier.type in special_modifier_names:
            modifier.show_viewport = False
            special_modifiers.append(modifier)

    context.view_layer.objects.active = obj
    num_shape_keys = len(obj.data.shape_keys.key_blocks) if obj.data.shape_keys else 0
    if num_shape_keys:
        print(f"Applying modifiers on {obj.name} with {num_shape_keys} shape keys")
    bpy.ops.object.apply_modifiers_with_shape_keys()

    for modifier in special_modifiers:
        modifier.show_viewport = True
        if modifier.type == 'ARMATURE':
            # Do nothing, just reenable
            pass
        elif modifier.type == 'MASK':
            # Try to preserve edge boundaries
            print(f"Applying mask on {obj.name}")
            apply_mask_modifier(modifier)
        else:
            # Apply post-mirror modifiers
            bpy.ops.object.modifier_apply(modifier=modifier.name)

# @intercept(error_result={'CANCELLED'})
def export_autorig(filepath, actions):
    scn = bpy.context.scene

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
    scn.arp_ue_ik = False
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
def export_fbx(filepath, actions):
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

class MY_OT_character_export(bpy.types.Operator):
    bl_idname = "my_tools.character_export"
    bl_label = "Character Export"
    bl_context = "objectmode"

    export_path: bpy.props.StringProperty(
        name="Export Path",
        description="""Export path relative to the current folder.
{basename} = Name of the .blend file without extension, if available.
{action} = Name of the first action being exported, if exporting actions""",
        default="//export/{basename}.fbx",
        subtype='FILE_PATH',
    )
    suffix: bpy.props.StringProperty(
        name="Mesh Suffix",
        description="""Exported mesh suffix, will default to underscore if empty""",
        default="",
    )
    export_meshes: bpy.props.BoolProperty(
        name="Export Meshes",
        description="Whether to export mesh objects",
        default=True,
    )
    export_animation: bpy.props.BoolProperty(
        name="Export Animation",
        description="Whether to export animation data",
        default=True,
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
    split_masks: bpy.props.BoolProperty(
        name="Split Masks",
        description="Splits mask modifiers into extra meshes that are exported separately",
        default=False,
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

    def copy_obj(self, obj, copy_data=True):
        new_obj = obj.copy()
        suffix = self.suffix or "_"
        new_obj.name = obj.name + suffix
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
        return new_obj

    def clone_obj(self, obj):
        new_obj = self.copy_obj(obj, copy_data=True)
        new_obj.data.use_auto_smooth = True # Enable custom normals
        new_obj.data.auto_smooth_angle = math.pi
        data_transfer = new_obj.modifiers.new("Masked", 'DATA_TRANSFER')
        data_transfer.object = whole_obj
        data_transfer.use_object_transform = False
        data_transfer.use_loop_data = True
        data_transfer.loop_mapping = 'NEAREST_POLY'
        data_transfer.data_types_loops = {'CUSTOM_NORMAL'}
        data_transfer.use_max_distance = 1e-5
        data_transfer.use_max_distance = True
        return new_obj

    @classmethod
    def poll(cls, context):
        return context.object and context.object.mode == "OBJECT"

    def _execute(self, context, rig):
        path_fields = {}
        mesh_objs = []
        if self.export_meshes:
            mesh_objs = [self.copy_obj(obj) for obj in get_children_recursive(rig) if
                obj.type == 'MESH' and not obj.hide_render and obj.find_armature() is rig]

        ExportGroup = namedtuple("ExportGroup", ["suffix", "objects", "actions"])
        export_groups = []
        if mesh_objs:
            export_groups.append(ExportGroup(suffix="", objects=mesh_objs[:], actions=[]))

        if self.split_masks:
            for obj in mesh_objs:
                masks = [mo for mo in obj.modifiers if mo.type == 'MASK' and mo.show_render]
                if not masks:
                    continue

                # As a special case if the only modifier has the same name as the object,
                # just make a new export group for it
                if len(masks) == 1 and masks[0].name == obj.name:
                    export_groups.append(ExportGroup(
                        suffix="_%s" % masks[0].name,
                        objects=[obj],
                        actions=[],
                    ))
                    export_groups[0].objects.remove(obj)
                    obj.modifiers.remove(masks[0])
                    continue

                # Split masked parts into extra meshes that receive normals from the original
                for mask in masks:
                    print(f"Splitting {mask.name} from {obj.name}")
                    new_obj = self.clone_obj(obj)
                    new_obj.name = mask.name

                    # Remove all masks but this one in the new object
                    for new_mask in [mo for mo in new_obj.modifiers if mo.type == 'MASK']:
                        if new_mask.name != mask.name:
                            new_obj.modifiers.remove(new_mask)

                    # New export group for the split off part
                    export_groups.append(ExportGroup(
                        suffix="_%s" % mask.name,
                        objects=[new_obj],
                        actions=[],
                    ))

                # Invert the masks for the part that is left behind
                base_obj = self.clone_obj(obj)
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

        for export_group in export_groups:
            for obj in export_group.objects:
                if self.mirror_shape_keys:
                    mirror_shape_keys(context, obj)

                if self.apply_modifiers:
                    apply_modifiers(context, obj)

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

                ctx["object"] = ctx["active_object"] = merged_obj
                ctx["selected_objects"] = ctx["selected_editable_objects"] = objs

                bpy.ops.object.join(ctx)

                # Enable autosmooth for merged object in case there's custom normals
                merged_obj.data.use_auto_smooth = True
                merged_obj.data.auto_smooth_angle = math.pi

                export_group.objects[:] = [merged_obj]

        # Add actions as export groups without meshes
        if self.export_animation and self.actions:
            action_names = set(self.actions.split(","))
            for action_name in action_names:
                action_name = action_name.strip()
                if not action_name:
                    continue
                if action_name not in bpy.data.actions:
                    continue
                export_groups.append(ExportGroup(
                    suffix="",
                    objects=[],
                    actions=[bpy.data.actions[action_name]],
                ))

        # Finally export
        exported_files = []
        for export_group in export_groups:
            for obj in context.scene.objects:
                obj.select_set(False)
            for obj in export_group.objects:
                obj.select_set(True)
            rig.select_set(True)
            context.view_layer.objects.active = rig

            action = None
            if export_group.actions:
                action = export_group.actions[0]
                path_fields["action"] = action.name
            else:
                path_fields["action"] = "None"

            path_fields["suffix"] = export_group.suffix
            filepath = get_export_path(self.export_path, **path_fields)
            filename = bpy.path.basename(filepath)
            if filepath in exported_files:
                print(f"Skipping {filename} as it would overwrite a file that was just exported")

            # rig.data.pose_position = 'POSE' if action else 'REST'
            rig.data.pose_position = 'POSE'
            clear_pose(rig)
            if action:
                rig.animation_data.action = action
                context.scene.frame_preview_start = action.frame_range[0]
                context.scene.frame_preview_end = action.frame_range[1]
                context.scene.use_preview_range = True
                context.scene.frame_current = action.frame_range[0]
                bpy.context.evaluated_depsgraph_get().update()

            if is_object_arp(rig):
                print(f"Exporting {filename} via Auto-Rig export")
                result = export_autorig(filepath, export_group.actions)
            else:
                print(f"Exporting {filename}")
                result = export_fbx(filepath, export_group.actions)

            if result == {'FINISHED'}:
                exported_files.append(filepath)
            else:
                print("Failed to export!")

        # Finished without errors
        if not exported_files:
            self.report({"INFO"}, "Nothing exported.")
        elif len(exported_files) <= 5:
            filenames = [bpy.path.basename(filepath) for filepath in exported_files]
            self.report({"INFO"}, "Exported %s" % ', '.join(filenames))
        else:
            self.report({'INFO'}, "%d files exported." % len(exported_files))

    def execute(self, context):
        rig = context.object

        if not rig or rig.type != 'ARMATURE':
            self.report({'ERROR'}, "Armature must be the active object.")
            return {'CANCELLED'}

        # Check addon availability
        if not self.check_operator("apply_shape_keys_with_vertex_groups"):
            return {'CANCELLED'}

        if self.apply_modifiers and not self.check_operator("apply_modifiers_with_shape_keys"):
            return {'CANCELLED'}

        path_fields = {"action": "None"}
        reason = check_invalid_export_path(self.export_path, **path_fields)
        if reason:
            self.report({'ERROR'}, reason)
            return {'CANCELLED'}

        saved_pose_position = rig.data.pose_position
        rig.data.pose_position = 'REST'
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

        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

def register():
    bpy.utils.register_class(MY_OT_character_export)

def unregister():
    bpy.utils.unregister_class(MY_OT_character_export)
