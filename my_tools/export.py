from collections import namedtuple, defaultdict
from fnmatch import fnmatch
import bpy
import math
import os
import re
import time
from .helpers import (
    beep,
    clear_pose,
    fail_if_invalid_export_path,
    fail_if_no_operator,
    get_children_recursive,
    get_export_path,
    intercept,
    is_object_arp,
    is_object_arp_humanoid,
    load_properties,
    load_selection,
    log,
    logger,
    save_properties,
    save_selection,
    select_only,
)
from .mesh_helpers import (
    apply_modifiers,
    apply_shape_keys_with_vertex_groups,
    delete_faces_with_no_material,
    merge_basis_shape_keys,
    merge_freestyle_edges,
    mirror_shape_keys,
    subdivide_verts_with_bevel_weight,
)

class ConstantCurve:
    """Mimics FCurve and always returns the same value on evaluation"""
    def __init__(self, value):
        self.value = value
    def evaluate(self, frame_index):
        return self.value

def get_nice_export_report(files, elapsed):
    if len(files) > 5:
        return f"{len(files)} files exported in {elapsed:.2f}s."
    if files:
        filenames = [bpy.path.basename(filepath) for filepath in files]
        return f"Exported {', '.join(filenames)} in {elapsed:.2f}s."
    return "Nothing exported."

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
    else:
        scn.arp_bake_actions = True
        scn.arp_export_name_actions = True
        scn.arp_export_name_string = ','.join(action.name for action in actions)
        scn.arp_simplify_fac = 0.0

    # Misc
    scn.arp_global_scale = 1.0
    scn.arp_mesh_smooth_type = 'EDGE'
    scn.arp_use_tspace = False
    scn.arp_fix_fbx_rot = True
    scn.arp_fix_fbx_matrix = True
    scn.arp_init_fbx_rot = False
    scn.arp_bone_axis_primary_export = 'Y'
    scn.arp_bone_axis_secondary_export = 'X'
    scn.arp_export_rig_name = 'root'

    return bpy.ops.id.arp_export_fbx_panel(filepath=filepath)

@intercept(error_result={'CANCELLED'})
def export_autorig_universal(context, filepath, actions):
    scn = context.scene
    rig = context.active_object

    # Configure Auto-Rig and then finally export
    scn.arp_engine_type = 'unreal'
    scn.arp_export_rig_type = 'mped'
    scn.arp_ge_sel_only = True

    # Rig Definition
    scn.arp_keep_bend_bones = False
    scn.arp_push_bend = False
    scn.arp_export_twist = True
    scn.arp_export_noparent = False

    # Units
    scn.arp_units_x100 = True

    # Unreal Options
    scn.arp_ue_root_motion = True

    # Animation
    if not actions:
        scn.arp_bake_actions = False
    else:
        scn.arp_bake_actions = True
        scn.arp_export_name_actions = True
        scn.arp_export_name_string = ','.join(action.name for action in actions)
        scn.arp_simplify_fac = 0.0

    # Misc
    scn.arp_global_scale = 1.0
    scn.arp_mesh_smooth_type = 'EDGE'
    scn.arp_use_tspace = False
    scn.arp_fix_fbx_rot = True
    scn.arp_fix_fbx_matrix = True
    scn.arp_init_fbx_rot = False
    scn.arp_bone_axis_primary_export = 'Y'
    scn.arp_bone_axis_secondary_export = 'X'
    scn.arp_export_rig_name = 'root'

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
    bl_idname = 'export_scene.my_fbx'
    bl_label = "Scene Export"
    bl_context = 'objectmode'
    bl_options = {'REGISTER'}

    export_path: bpy.props.StringProperty(
        name="Export Path",
        description="""Export path relative to the current folder.
{file} = Name of this .blend file without extension.
{object} = Name of the object being exported.
{collection} = Name of the first collection the object belongs to""",
        default="//export/S_{object}.fbx",
        subtype='FILE_PATH',
    )
    export_collision: bpy.props.BoolProperty(
        name="Export Collision",
        description="Exports collision objects that follow the UE4 naming pattern",
        default=True,
    )
    keep_transforms: bpy.props.BoolProperty(
        name="Keep Transforms",
        description="Keep the position and rotation of objects relative to world center",
        default=False,
    )
    material_name_prefix: bpy.props.StringProperty(
        name="Material Prefix",
        description="Ensures that exported material names begin with a prefix",
        default="MI_",
    )
    debug: bpy.props.BoolProperty(
        name="Debug",
        description="Debug mode with verbose output. Exceptions are caught but not handled",
        default=False,
    )

    def copy_obj(self, obj, copy_data=True):
        new_obj = obj.copy()
        # New object takes the original name as a temporary measure to export collision
        # new_obj.name = obj.name + "_"
        self.saved_object_names[obj] = original_name = obj.name
        obj.name = original_name + "_"
        new_obj.name = original_name
        if copy_data:
            new_data = obj.data.copy()
            if isinstance(new_data, bpy.types.Mesh):
                self.new_meshes.add(new_data)
            else:
                log(f"Copied data of object {obj.name} won't be released!")
            new_obj.data = new_data
        self.new_objs.add(new_obj)

        # New objects are moved to the scene collection, ensuring they're visible
        bpy.context.scene.collection.objects.link(new_obj)
        new_obj.hide_set(False)
        new_obj.hide_viewport = False
        new_obj.hide_select = False
        return new_obj

    def _execute(self, context):
        collision_prefixes = ("UCX", "UBX", "UCP", "USP")

        export_groups = defaultdict(list)  # Filepath to object list
        for obj in context.selected_objects[:]:
            if obj.type != 'MESH':
                # Only meshes
                continue
            if any(obj.name.startswith(s) for s in collision_prefixes):
                # Never export collision objects by themselves
                continue

            log(f"Processing {obj.name}")
            logger.log_indent += 1

            orig_obj, obj = obj, self.copy_obj(obj)
            context.view_layer.objects.active = obj

            merge_basis_shape_keys(obj)

            for modifier in obj.modifiers[:]:
                if modifier.show_viewport:
                    try:
                        bpy.ops.object.modifier_apply(modifier=modifier.name)
                    except RuntimeError:
                        log(f"Couldn't apply {modifier.type} modifier '{modifier.name}'")

            col_objs = []
            if self.export_collision:
                # Extend selection with pertaining collision objects
                pattern = r"^(?:%s)_%s_\d+$" % ('|'.join(collision_prefixes), obj.name)
                col_objs = [o for o in context.scene.objects if re.match(pattern, o.name)]
            if col_objs:
                log(f"Collected {len(col_objs)} collision primitives")

            if not self.keep_transforms:
                # Move main object to world center while keeping collision relative transforms
                for col in col_objs:
                    self.saved_transforms[col] = col.matrix_world.copy()
                    col.matrix_world = obj.matrix_world.inverted() @ col.matrix_world
                obj.matrix_world.identity()

            # If set, ensure prefix for any exported materials
            if self.material_name_prefix:
                for mat_slot in obj.material_slots:
                    mat = mat_slot.material
                    if not mat.name.startswith(self.material_name_prefix):
                        self.saved_material_names[mat] = mat.name
                        mat.name = self.material_name_prefix + mat.name

            # Refresh vertex color and clear the mappings to avoid issues when meshes are merged
            # While in Blender it's more intuitive to author masks starting from black, however
            # UE4 defaults to white. Materials should then use OneMinus to get the original value
            if not obj.data.vertex_colors and not obj.vertex_color_mapping:
                bpy.ops.mesh.vertex_color_mapping_add(r='ZERO', g='ZERO', b='ZERO', a='ZERO')
            bpy.ops.mesh.vertex_color_mapping_refresh(invert=True)
            bpy.ops.mesh.vertex_color_mapping_clear()

            path_fields = {
                'object': obj.name,
                'collection': orig_obj.users_collection[0].name,
            }
            filepath = get_export_path(self.export_path, path_fields)
            export_groups[filepath].append(obj)
            export_groups[filepath].extend(col_objs)

            logger.log_indent -= 1

        # Export each file
        for filepath, objs in export_groups.items():
            select_only(context, objs)

            filename = bpy.path.basename(filepath)
            result = export_fbx(context, filepath, [], no_intercept=self.debug)
            if result == {'FINISHED'}:
                log(f"Exported {filename} with {len(objs)} objects")
                self.exported_files.append(filename)
            else:
                log(f"Failed to export {filename}")

    def execute(self, context):
        # Check addon availability and export path
        try:
            fail_if_no_operator('vertex_color_mapping_refresh', submodule=bpy.ops.mesh)
            fail_if_invalid_export_path(self.export_path, ['object', 'collection'])
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        saved_selection = save_selection()
        saved_use_global_undo = context.preferences.edit.use_global_undo
        context.preferences.edit.use_global_undo = False
        self.exported_files = []
        self.new_objs = set()
        self.new_meshes = set()
        self.saved_object_names = {}
        self.saved_material_names = {}
        self.saved_transforms = {}
        logger.start_logging()

        try:
            start_time = time.time()
            self._execute(context)
            # Finished without errors
            elapsed = time.time() - start_time
            self.report({'INFO'}, get_nice_export_report(self.exported_files, elapsed))
            beep(pitch=2, num=1)
        finally:
            # Clean up
            while self.new_objs:
                bpy.data.objects.remove(self.new_objs.pop())
            while self.new_meshes:
                bpy.data.meshes.remove(self.new_meshes.pop())
            for obj, matrix_world in self.saved_transforms.items():
                obj.matrix_world = matrix_world
            for obj, name in self.saved_object_names.items():
                obj.name = name
            for mat, name in self.saved_material_names.items():
                mat.name = name
            del self.saved_transforms
            del self.saved_object_names
            del self.saved_material_names

            load_selection(saved_selection)
            context.preferences.edit.use_global_undo = saved_use_global_undo
            logger.end_logging()

        return {'FINISHED'}

    # def invoke(self, context, event):
        # return context.window_manager.invoke_props_dialog(self)

class MY_OT_rig_export(bpy.types.Operator):
    bl_idname = 'my_tools.rig_export'
    bl_label = "Rig Export"
    bl_context = 'objectmode'
    bl_options = {'INTERNAL'}

    export_path: bpy.props.StringProperty(
        name="Export Path",
        description="""Export path relative to the current folder.
{file} = Name of this .blend file without extension.
{rigfile} = Name of the .blend file the rig is linked from, without extension.
{rig} = Name of the rig being exported""",
        default="//export/{file}.fbx",
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
    modifier_tags: bpy.props.StringProperty(
        name="Modifier Tags",
        description="""Tagged modifiers are only applied if the tag is found in this list.
Separate tags with commas. Tag modifiers with 'g:tag'""",
        default="",
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
    debug: bpy.props.BoolProperty(
        name="Debug",
        description="Debug mode with verbose output. Exceptions are caught but not handled",
        default=False,
    )

    def copy_obj(self, obj, copy_data=True):
        new_obj = obj.copy()
        new_obj.name = obj.name + "_"
        if copy_data:
            new_data = obj.data.copy()
            if isinstance(new_data, bpy.types.Mesh):
                self.new_meshes.add(new_data)
            else:
                log(f"Copied data of object {obj.name} won't be released!")
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

        # I don't see a way to check if topology mapping is working or not, so clone normals twice
        data_transfer = new_obj.modifiers.new("_Clone Normals", 'DATA_TRANSFER')
        data_transfer.object = obj
        data_transfer.use_object_transform = False
        data_transfer.use_loop_data = True
        data_transfer.loop_mapping = 'NEAREST_POLYNOR'  # 'NEAREST_POLY' fails on sharp edges
        data_transfer.data_types_loops = {'CUSTOM_NORMAL'}
        data_transfer.max_distance = 1e-5
        data_transfer.use_max_distance = True

        data_transfer = new_obj.modifiers.new("_Clone Normals Topology", 'DATA_TRANSFER')
        data_transfer.object = obj
        data_transfer.use_object_transform = False
        data_transfer.use_loop_data = True
        data_transfer.loop_mapping = 'TOPOLOGY'
        data_transfer.data_types_loops = {'CUSTOM_NORMAL'}
        data_transfer.max_distance = 1e-5
        data_transfer.use_max_distance = True
        return new_obj

    def sanitize_mesh(self, obj):
        # Enable autosmooth to allow custom normals
        obj.data.use_auto_smooth = True
        obj.data.auto_smooth_angle = math.pi

        # Ensure basis is selected
        obj.active_shape_key_index = 0
        obj.show_only_shape_key = False

        # After messing with shapekeys basis may be left in an undesirable state
        # Not sure why, and data.update() doesn't seem to fix it
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.object.mode_set(mode='OBJECT')

        # Delete drivers made invalid by deleted modifiers and so on
        if obj.animation_data:
            for fc in obj.animation_data.drivers[:]:
                if not fc.driver.is_valid:
                    obj.animation_data.drivers.remove(fc)

    @classmethod
    def poll(cls, context):
        return context.object and context.object.mode == 'OBJECT'

    def _execute(self, context, rig):
        rig_filepath = (rig.proxy.library.filepath if rig.proxy and rig.proxy.library
            else bpy.data.filepath)
        path_fields = {
            'rigfile': os.path.splitext(bpy.path.basename(rig_filepath))[0],
            'rig': rig.name,
        }
        mesh_objs = []
        original_objs = []
        rig.data.pose_position = 'REST'

        for obj in get_children_recursive(rig):
            # Enable all render modifiers in the originals, except masks
            for modifier in obj.modifiers:
                if modifier.type != 'MASK' and modifier.show_render and not modifier.show_viewport:
                    modifier.show_viewport = True
                    self.saved_disabled_modifiers.add(modifier)
            if obj.type == 'MESH':
                self.saved_auto_smooth[obj] = (obj.data.use_auto_smooth, obj.data.auto_smooth_angle)
                obj.data.use_auto_smooth = True
                obj.data.auto_smooth_angle = math.pi
                if not obj.hide_render and obj.find_armature() is rig:
                    original_objs.append(obj)
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
                    log(f"Splitting {mask.name} from {obj.name}")
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

        any_modifier_tags = set(self.modifier_tags.split(','))
        kept_modifiers = []  # List of (object name, modifier index, modifier properties)
        wants_subsurf = {}  # Object name to subsurf level
        def should_enable_modifier(mo):
            tags = set(re.findall(r"g:(\S+)", mo.name))
            return mo.show_render and (not tags or any(s in tags for s in any_modifier_tags))

        # Process individual meshes
        for export_group in export_groups:
            for obj in export_group.objects:
                log(f"Processing {obj.name}")
                logger.log_indent += 1

                delete_faces_with_no_material(obj)

                if self.merge_basis_shape_keys:
                    merge_basis_shape_keys(obj)

                if self.mirror_shape_keys:
                    mirror_shape_keys(obj, self.side_vgroup_name)

                # Only use modifiers enabled for render. Delete unused modifiers
                context.view_layer.objects.active = obj
                for modifier_idx, modifier in enumerate(obj.modifiers[:]):
                    if should_enable_modifier(modifier):
                        if (modifier.type == 'SUBSURF' and modifier.levels > 0
                            and self.join_meshes and len(export_group.objects) > 1):
                            # Subsurf will be applied after merge, otherwise boundaries won't match up
                            wants_subsurf[obj.name] = modifier.levels
                            bpy.ops.object.modifier_remove(modifier=modifier.name)
                        else:
                            modifier.show_viewport = True
                    else:
                        if '!keep' in modifier.name:
                            # Store the modifier to recreate it later
                            kept_modifiers.append((obj.name, modifier_idx, save_properties(modifier)))
                        bpy.ops.object.modifier_remove(modifier=modifier.name)
                if self.apply_modifiers:
                    apply_modifiers(obj, mask_edge_boundary=self.split_masks)

                # If set, ensure prefix for any exported materials
                if self.material_name_prefix:
                    for mat_slot in obj.material_slots:
                        mat = mat_slot.material
                        if mat and not mat.name.startswith(self.material_name_prefix):
                            self.saved_material_names[mat] = mat.name
                            mat.name = self.material_name_prefix + mat.name

                # Remove vertex group filtering from shapekeys
                apply_shape_keys_with_vertex_groups(obj)

                # Refresh vertex color and clear the mappings to avoid issues when meshes are merged
                # While in Blender it's more intuitive to author masks starting from black, however
                # UE4 defaults to white. Materials should then use OneMinus to get the original value
                if not obj.data.vertex_colors and not obj.vertex_color_mapping:
                    bpy.ops.mesh.vertex_color_mapping_add(r='ZERO', g='ZERO', b='ZERO', a='ZERO')
                bpy.ops.mesh.vertex_color_mapping_refresh(invert=True)
                bpy.ops.mesh.vertex_color_mapping_clear()

                # Ensure proper mesh state
                self.sanitize_mesh(obj)

                logger.log_indent -= 1

        merges = {}
        if self.join_meshes:
            for export_group in export_groups:
                objs = export_group.objects
                if len(objs) <= 1:
                    continue

                # Pick the densest object to receive all the others
                merged_obj = max(objs, key=lambda ob: len(ob.data.vertices))
                merges.update({obj.name: merged_obj for obj in objs})
                log(f"Merging {', '.join(obj.name for obj in objs if obj is not merged_obj)} " \
                    f"into {merged_obj.name}")
                logger.log_indent += 1

                subsurf_levels = max(wants_subsurf.get(obj.name, 0) for obj in objs)
                for obj in objs:
                    if subsurf_levels:
                        # Mark vertices that belong to a subsurf mesh
                        obj.data.use_customdata_vertex_bevel = True
                        for vert in obj.data.vertices:
                            vert.bevel_weight = obj.name in wants_subsurf
                    if obj is not merged_obj:
                        self.new_objs.discard(obj)
                        self.new_meshes.discard(obj.data)

                ctx = {}
                ctx['object'] = ctx['active_object'] = merged_obj
                ctx['selected_objects'] = ctx['selected_editable_objects'] = objs
                bpy.ops.object.join(ctx)
                objs[:] = [merged_obj]

                # Joining objects won't copy drivers, so do that now
                for original_obj in original_objs:
                    if original_obj.data.shape_keys and original_obj.data.shape_keys.animation_data:
                        for fc in original_obj.data.shape_keys.animation_data.drivers:
                            if merged_obj.data.shape_keys.animation_data is None:
                                merged_obj.data.shape_keys.animation_data_create()
                            merged_obj.data.shape_keys.animation_data.drivers.from_existing(src_driver=fc)

                # Ensure proper mesh state
                self.sanitize_mesh(merged_obj)

                num_verts_merged = merge_freestyle_edges(merged_obj)
                if num_verts_merged > 0:
                    log(f"Welded {num_verts_merged} verts (edges were marked freestyle)")

                if subsurf_levels:
                    subdivide_verts_with_bevel_weight(merged_obj, levels=subsurf_levels)
                    merged_obj.data.use_customdata_vertex_bevel = False

                logger.log_indent -= 1

        # Finally export
        if self.export_path:
            for export_group in export_groups:
                path_fields['suffix'] = export_group.suffix
                filepath = get_export_path(self.export_path, path_fields)
                filename = bpy.path.basename(filepath)
                if filepath in self.exported_files:
                    log(f"Skipping {filename} as it would overwrite a file that was just exported")

                for obj in context.scene.objects:
                    obj.select_set(False)
                for obj in export_group.objects:
                    obj.select_set(True)
                rig.select_set(True)
                context.view_layer.objects.active = rig
                rig.data.pose_position = 'POSE'
                clear_pose(rig)

                if is_object_arp_humanoid(rig):
                    log(f"Exporting {filename} via Auto-Rig export")
                    result = export_autorig(context, filepath, [], no_intercept=self.debug)
                elif is_object_arp(rig):
                    log(f"Exporting {filename} via Auto-Rig export (universal)")
                    result = export_autorig_universal(context, filepath, [], no_intercept=self.debug)
                else:
                    # Temporarily rename the armature as it's the root bone itself
                    saved_rig_name = rig.name
                    rig.name = "root"
                    log(f"Exporting {filename}")
                    result = export_fbx(context, filepath, [], no_intercept=self.debug)
                    rig.name = saved_rig_name

                if result == {'FINISHED'}:
                    self.exported_files.append(filepath)
                else:
                    log("Failed to export!")

        # Keep new objects in the target collection
        coll = bpy.data.collections.get(self.export_collection)
        if coll:
            for export_group in export_groups:
                for obj in export_group.objects:
                    coll.objects.link(obj)
                    context.scene.collection.objects.unlink(obj)
                    # Disable features on output meshes for performance
                    obj.data.use_auto_smooth = False
                    obj.data.use_customdata_vertex_bevel = False
                    obj.data.use_customdata_edge_bevel = False
                    obj.data.use_customdata_edge_crease = False
            if kept_modifiers:
                # Recreate modifiers that were stored
                log(f"Restoring {len(kept_modifiers)} modifiers")
                for obj_name, index, properties in kept_modifiers:
                    obj = bpy.data.objects.get(obj_name) or merges.get(obj_name)
                    if obj:
                        mod = obj.modifiers.new(name=properties['name'], type=properties['type'])
                        load_properties(mod, properties)

                        new_index = min(index, len(obj.modifiers) - 1)
                        ctx = {'object': obj}
                        bpy.ops.object.modifier_move_to_index(ctx, modifier=mod.name, index=new_index)
            # Don't delete the new stuff
            self.new_objs.clear()
            self.new_meshes.clear()

    def execute(self, context):
        rig = context.object

        if not rig or rig.type != 'ARMATURE':
            self.report({'ERROR'}, "Armature must be the active object.")
            return {'CANCELLED'}

        # Check addon availability and export path
        try:
            fail_if_no_operator('apply_modifiers_with_shape_keys')
            fail_if_no_operator('vertex_color_mapping_refresh', submodule=bpy.ops.mesh)
            fail_if_invalid_export_path(self.export_path, ['rigfile', 'rig'])
        except Exception as e:
            self.report({'ERROR'}, str(e))
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
        self.saved_auto_smooth = {}
        logger.start_logging()

        try:
            start_time = time.time()
            self._execute(context, rig)
            # Finished without errors
            elapsed = time.time() - start_time
            self.report({'INFO'}, get_nice_export_report(self.exported_files, elapsed))
            beep(pitch=0)
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
            for obj, (value, angle) in self.saved_auto_smooth.items():
                obj.data.use_auto_smooth = value
                obj.data.auto_smooth_angle = angle
            del self.saved_material_names
            del self.saved_auto_smooth
            rig.data.pose_position = saved_pose_position
            context.preferences.edit.use_global_undo = saved_use_global_undo
            load_selection(saved_selection)
            logger.end_logging()

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
{file} = Name of this .blend file without extension.
{rigfile} = Name of the .blend file the rig is linked from, without extension.
{action} = Name of the action being exported""",
        default="//export/{action}.fbx",
        subtype='FILE_PATH',
    )
    markers_export_path: bpy.props.StringProperty(
        name="Markers Export Path",
        description="""Export path for markers relative to the current folder.
If available, markers names and frame times are written as a list of comma-separated values.
{file} = Name of this .blend file without extension.
{rigfile} = Name of the .blend file the rig is linked from, without extension.
{action} = Name of the action being exported""",
        default="//export/{action}.csv",
        subtype='FILE_PATH',
    )
    actions: bpy.props.StringProperty(
        name="Action Names",
        description="Comma separated list of actions to export",
        default=""
    )
    disable_auto_eyelid: bpy.props.BoolProperty(
        name="Disable Auto-Eyelid",
        description="Disables Auto-Eyelid (ARP only)",
        default=True,
    )
    debug: bpy.props.BoolProperty(
        name="Debug",
        description="Debug mode with verbose output. Exceptions are caught but not handled",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.object and context.object.mode == "OBJECT"

    def _execute(self, context, rig):
        start_time = time.time()
        rig_filepath = (rig.proxy.library.filepath if rig.proxy and rig.proxy.library
            else bpy.data.filepath)
        path_fields = {
            'rigfile': os.path.splitext(bpy.path.basename(rig_filepath))[0],
            'rig': rig.name,
        }

        ExportGroup = namedtuple('ExportGroup', ['suffix', 'action'])
        export_groups = []

        if self.disable_auto_eyelid:
            for bone_name in ('c_eyelid_base.l', 'c_eyelid_base.r'):
                pb = rig.pose.bones.get('c_eyelid_base.l')
                if pb:
                    for constraint in (con for con in pb.constraints if not con.mute):
                        constraint.mute = True
                        self.saved_unmuted_constraints.append(constraint)

        # Add actions as export groups without meshes
        action_names = set(self.actions.split(','))
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
                filepath = get_export_path(self.export_path, path_fields)
                filename = bpy.path.basename(filepath)
                if filepath in self.exported_files:
                    log(f"Skipping {filename} as it would overwrite a file that was just exported")
                    continue

                rig.select_set(True)
                context.view_layer.objects.active = rig
                rig.data.pose_position = 'POSE'
                clear_pose(rig)

                rig.animation_data.action = export_group.action
                context.scene.frame_preview_start = export_group.action.frame_range[0]
                context.scene.frame_preview_end = export_group.action.frame_range[1]
                context.scene.use_preview_range = True
                context.scene.frame_current = export_group.action.frame_range[0]
                bpy.context.evaluated_depsgraph_get().update()

                markers = export_group.action.pose_markers
                if markers and self.markers_export_path:
                    # Export action markers as a comma separated list
                    csv_filepath = get_export_path(self.markers_export_path, path_fields)
                    csv_filename = bpy.path.basename(csv_filepath)
                    csv_separator = ','
                    fps = float(context.scene.render.fps)
                    if csv_filepath not in self.exported_files:
                        log(f"Writing markers to {csv_filename}")
                        with open(csv_filepath, 'w') as fout:
                            field_headers = ["Name", "Frame", "Time"]
                            print(csv_separator.join(field_headers), file=fout)
                            for marker in markers:
                                fields = [marker.name, marker.frame, marker.frame / fps]
                                print(csv_separator.join(str(field) for field in fields), file=fout)
                    else:
                        log(f"Skipping {csv_filename} as it would overwrite a file that was " \
                            "just exported")

                actions = [export_group.action]

                if is_object_arp_humanoid(rig):
                    log(f"Exporting {filename} via Auto-Rig export")
                    result = export_autorig(context, filepath, actions, no_intercept=self.debug)
                elif is_object_arp(rig):
                    log(f"Exporting {filename} via Auto-Rig export (universal)")
                    result = export_autorig_universal(context, filepath, actions, no_intercept=self.debug)
                else:
                    log(f"Exporting {filename}")
                    result = export_fbx(context, filepath, actions, no_intercept=self.debug)

                if result == {'FINISHED'}:
                    self.exported_files.append(filepath)
                else:
                    log("Failed to export!")

    def execute(self, context):
        rig = context.object

        if not rig or rig.type != 'ARMATURE':
            self.report({'ERROR'}, "Armature must be the active object.")
            return {'CANCELLED'}

        try:
            fail_if_invalid_export_path(self.export_path, ['action', 'rigfile', 'rig'])
            if self.markers_export_path:
                fail_if_invalid_export_path(self.markers_export_path, ['action', 'rigfile', 'rig'])
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        saved_selection = save_selection()
        saved_pose_position = rig.data.pose_position
        saved_action = rig.animation_data.action
        saved_use_global_undo = context.preferences.edit.use_global_undo
        saved_scene_object_names = [o.name for o in context.scene.objects]
        context.preferences.edit.use_global_undo = False
        self.exported_files = []
        self.saved_unmuted_constraints = []
        logger.start_logging()

        try:
            start_time = time.time()
            self._execute(context, rig)
            # Finished without errors
            elapsed = time.time() - start_time
            self.report({'INFO'}, get_nice_export_report(self.exported_files, elapsed))
            beep(pitch=1)
        finally:
            # ARP has started leaving behind objects and it breaks subsequent exports
            for obj in context.scene.objects[:]:
                if obj.name not in saved_scene_object_names:
                    log(f"Removing object '{obj.name}' that was left behind")
                    bpy.data.objects.remove(obj, do_unlink=True)
            # Clean up
            for modifier in self.saved_unmuted_constraints:
                modifier.mute = False
            del self.saved_unmuted_constraints
            rig.data.pose_position = saved_pose_position
            rig.animation_data.action = saved_action
            context.preferences.edit.use_global_undo = saved_use_global_undo
            load_selection(saved_selection)
            logger.end_logging()

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

def refresh_job_list(context):
    """Call after changing the job list, keeps job indices up to date"""
    for job_idx, job in enumerate(context.scene.my_tools.export_jobs):
        for coll in job.collections:
            coll.job_index = job_idx
        for action in job.actions:
            action.job_index = job_idx
        for copy_property in job.copy_properties:
            copy_property.job_index = job_idx
        for remap_material in job.remap_materials:
            remap_material.job_index = job_idx

class MY_OT_export_job_remove(bpy.types.Operator):
    #tooltip
    """Removes an export job"""

    bl_idname = 'my_tools.export_job_remove'
    bl_label = "Remove Export Job"
    bl_options = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty(options={'HIDDEN'})

    def execute(self, context):
        context.scene.my_tools.export_jobs.remove(self.index)
        refresh_job_list(context)

        return {'FINISHED'}

class MY_OT_export_job_move_up(bpy.types.Operator):
    #tooltip
    """Moves the export job up"""

    bl_idname = 'my_tools.export_job_move_up'
    bl_label = "Move Export Job Up"
    bl_options = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty(options={'HIDDEN'})

    def execute(self, context):
        context.scene.my_tools.export_jobs.move(self.index, self.index - 1)
        refresh_job_list(context)

        return {'FINISHED'}

class MY_OT_export_job_move_down(bpy.types.Operator):
    #tooltip
    """Moves the export job down"""

    bl_idname = 'my_tools.export_job_move_down'
    bl_label = "Move Export Job Down"
    bl_options = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty(options={'HIDDEN'})

    def execute(self, context):
        context.scene.my_tools.export_jobs.move(self.index, self.index + 1)
        refresh_job_list(context)

        return {'FINISHED'}

class MY_OT_export_job_run(bpy.types.Operator):
    #tooltip
    """Execute export job"""

    bl_idname = 'my_tools.export_job_run'
    bl_label = "Execute Export Job"

    index: bpy.props.IntProperty(options={'HIDDEN'})
    debug: bpy.props.BoolProperty(options={'HIDDEN'})
    bl_options = {'INTERNAL'}

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
                    if not coll and all(not jc.collection for jc in job.collections):
                        # When no collections are set use the scene collection
                        coll = scn.collection
                    if should_export(job_coll, coll):
                        for obj in coll.objects:
                            if obj not in objs and should_export(job_coll, obj):
                                obj.hide_select = False
                                obj.hide_viewport = False
                                obj.hide_render = False
                                objs.add(obj)
                select_only(context, objs)
            elif not context.selected_objects:
                self.report({'ERROR'}, "Nothing to export.")
                return {'CANCELLED'}

            log(f"Beginning scene export job '{job.name}'")

            bpy.ops.export_scene.my_fbx(
                export_path=job.scene_export_path,
                export_collision=job.export_collision,
                keep_transforms=job.keep_transforms,
                material_name_prefix=job.material_name_prefix,
                debug=self.debug,
            )

        elif job.what == 'RIG':
            if not job.rig:
                self.report({'ERROR'}, "No armature selected.")
                return {'CANCELLED'}
            if not job.rig.visible_get():
                self.report({'ERROR'}, "Currently the rig must be visible to export.")
                return {'CANCELLED'}
            if job.to_collection and not job.export_collection:
                self.report({'ERROR'}, "No collection selected to export to.")
                return {'CANCELLED'}
            context.view_layer.objects.active = job.rig
            bpy.ops.object.mode_set(mode='OBJECT')

            log(f"Beginning rig export job '{job.name}'")

            # Find all unique objects that should be considered for export
            all_objs = set()
            for job_coll in job.collections:
                coll = job_coll.collection
                if not coll and all(not jc.collection for jc in job.collections):
                    # When no collections are set use the scene collection
                    coll = scn.collection
                if should_export(job_coll, coll):
                    all_objs.update(obj for obj in coll.objects if should_export(job_coll, obj))

            # Mark the objects that should be exported as render so they will be picked up
            objs = set()
            for obj in all_objs:
                if obj.type == 'MESH':
                    saved_materials = []
                    for mat_idx, mat in enumerate(obj.data.materials):
                        for remap_material in job.remap_materials:
                            if mat and mat is remap_material.source:
                                saved_materials.append((obj, mat_idx, mat))
                                obj.data.materials[mat_idx] = remap_material.destination
                                break
                    if all(not mat for mat in obj.data.materials):
                        log(f"Not exporting '{obj.name}' because it has no materials")
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
            if job.to_collection and job.clean_collection:
                # Clean the target collection first
                # Currently not checking whether the rig is in here, it will probably explode
                log(f"Cleaning target collection")
                for obj in export_coll.objects:
                    bpy.data.objects.remove(obj, do_unlink=True)

            bpy.ops.my_tools.rig_export(
                export_path=job.rig_export_path if not job.to_collection else "",
                export_collection=export_coll.name if job.to_collection and export_coll else "",
                merge_basis_shape_keys=job.merge_basis_shape_keys,
                mirror_shape_keys=job.mirror_shape_keys,
                side_vgroup_name=job.side_vgroup_name,
                apply_modifiers=job.apply_modifiers,
                modifier_tags=job.modifier_tags,
                join_meshes=job.join_meshes,
                split_masks=job.split_masks,
                material_name_prefix=job.material_name_prefix,
                debug=self.debug,
            )

        elif job.what == 'ANIMATION':
            if not job.rig:
                self.report({'ERROR'}, "No armature selected.")
                return {'CANCELLED'}
            if not job.rig.visible_get():
                self.report({'ERROR'}, "Currently the rig must be visible to export.")
                return {'CANCELLED'}
            context.view_layer.objects.active = job.rig
            bpy.ops.object.mode_set(mode='OBJECT')

            log(f"Beginning animation export job '{job.name}'")

            action_names = set()
            for job_action in job.actions:
                if job_action:
                    if not job_action.use_pattern:
                        action_names.add(job_action.action)
                    else:
                        action_names.update(action.name for action in bpy.data.actions
                            if not action.library and fnmatch(action.name, job_action.action))

            for cp in job.copy_properties:
                if not cp.source and not cp.destination:
                    # Empty row
                    continue
                for action_name in action_names:
                    action = bpy.data.actions.get(action_name)
                    if not action:
                        continue
                    if action.library:
                        # Never export linked actions
                        continue

                    try:
                        fcurve_src = next(fc for fc in action.fcurves if fc.data_path == cp.source)
                    except StopIteration:
                        try:
                            value = float(cp.source)
                            fcurve_src = ConstantCurve(value)
                        except ValueError:
                            self.report({'ERROR'}, f"Couldn't bake {cp.source} -> {cp.destination} " \
                                f"in '{action_name}', source doesn't exist")
                            return {'CANCELLED'}

                    try:
                        fcurve_dst = next(fc for fc in action.fcurves if fc.data_path == cp.destination)
                        if fcurve_dst:
                            # Currently baking to existing curves is not allowed
                            # Would need to duplicate strips, although ARP already does that
                            log(f"Couldn't bake {cp.source} -> {cp.destination}, " \
                                "destination already exists")
                            self.report({'ERROR'}, f"Couldn't bake {cp.source} -> {cp.destination} " \
                                f"in '{action_name}', destination already exists")
                            return {'CANCELLED'}
                    except StopIteration:
                        fcurve_dst = action.fcurves.new(cp.destination)
                        self.new_fcurves.append((action, fcurve_dst))

                    log(f"Baking {cp.source} -> {cp.destination} in '{action_name}'")
                    for frame_idx in range(0, int(action.frame_range[1]) + 1):
                        val = fcurve_src.evaluate(frame_idx)
                        fcurve_dst.keyframe_points.insert(frame_idx, val)

            bpy.ops.my_tools.animation_export(
                export_path=job.animation_export_path,
                markers_export_path=job.markers_export_path if job.export_markers else "",
                actions=','.join(action_names),
                disable_auto_eyelid=job.disable_auto_eyelid,
                debug=self.debug,
            )

        log("Job complete")

    def execute(self, context):
        saved_selection = save_selection(all_objects=True)
        self.new_fcurves = []  # List of (action, fcurve)
        self.saved_materials = []  # List of (obj, material_idx, material)
        logger.start_logging()

        try:
            self._execute(context)
        finally:
            # Clean up
            for action, fcurve in self.new_fcurves:
                action.fcurves.remove(fcurve)
            for obj, material_idx, material in self.saved_materials:
                obj.data.materials[material_idx] = material
            del self.new_fcurves
            del self.saved_materials
            load_selection(saved_selection)
            logger.end_logging()

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
            row = row.row(align=True)
            split = row.split()
            op = split.operator('my_tools.export_job_move_up', icon='TRIA_UP', text="", emboss=False)
            op.index = job_idx
            split.enabled = job_idx > 0
            split = row.split()
            op = split.operator('my_tools.export_job_move_down', icon='TRIA_DOWN', text="", emboss=False)
            op.index = job_idx
            split.enabled = job_idx < len(scn.my_tools.export_jobs) - 1
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
                    col.prop(job, 'keep_transforms')
                    col.prop(job, 'material_name_prefix', text="M. Prefix")

                    col = box.column(align=True)
                    col.prop(job, 'scene_export_path', text="")

                elif job.what == 'RIG' or job.what == 'MESH':  # 'MESH' for backwards compat
                    box.prop(job, 'rig')
                    add_collection_layout()

                    col = box.column()
                    col.prop(job, 'merge_basis_shape_keys')

                    row = col.row(align=True)
                    row.prop(job, 'mirror_shape_keys')
                    split = row.split(align=True)
                    split.prop(job, 'side_vgroup_name', text="")
                    split.enabled = job.mirror_shape_keys

                    row = col.row(align=True)
                    row.prop(job, 'apply_modifiers')
                    split = row.split(align=True)
                    split.prop(job, 'modifier_tags', text="")
                    split.enabled = job.apply_modifiers

                    col.prop(job, 'join_meshes')
                    # Don't have an use for Split Masks currently and too many options gets confusing
                    # col.prop(job, 'split_masks')

                    col = box.column(align=True)
                    col.label(text="Remap Materials:")
                    for remap_material in job.remap_materials:
                        row = col.row(align=True)
                        row.prop(remap_material, 'source', text="")
                        row.label(text="", icon='FORWARD')
                        row.prop(remap_material, 'destination', text="")
                    col.prop(job, 'material_name_prefix', text="M. Prefix")

                    col = box.column(align=True)
                    col.prop(job, 'to_collection')
                    if job.to_collection:
                        row = col.row(align=True)
                        row.prop(job, 'export_collection', text="")
                        row.prop(job, 'clean_collection', icon='TRASH', text="")
                    else:
                        col.prop(job, 'rig_export_path', text="")

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

                    col = box.column()
                    col.prop(job, 'disable_auto_eyelid')

                    row = col.row(align=True)
                    row.prop(job, 'export_markers')
                    split = row.split(align=True)
                    split.prop(job, 'markers_export_path', text="")
                    split.enabled = job.export_markers

                    col = box.column(align=True)
                    col.label(text="Bake Properties:")
                    for copy_property in job.copy_properties:
                        row = col.row(align=True)
                        row.prop(copy_property, 'source', text="")
                        row.label(text="", icon='FORWARD')
                        row.prop(copy_property, 'destination', text="")

                    col = box.column(align=True)
                    col.prop(job, 'animation_export_path', text="")

            row = col.row(align=True)
            op = row.operator('my_tools.export_job_run', icon='INDIRECT_ONLY_ON', text="Execute")
            op.index = job_idx
            op.debug = False
            op = row.operator('my_tools.export_job_run', icon='INDIRECT_ONLY_OFF', text="")
            op.index = job_idx
            op.debug = True

classes = (
    MY_OT_animation_export,
    MY_OT_export_job_add,
    MY_OT_export_job_move_down,
    MY_OT_export_job_move_up,
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
