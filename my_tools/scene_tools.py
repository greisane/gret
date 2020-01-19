import os
import re
from fnmatch import fnmatch
import bpy
from .helpers import save_selection, load_selection, select_only, get_children_recursive
from .helpers import get_export_path, check_invalid_export_path, intercept, beep

class MY_OT_scene_export(bpy.types.Operator):
    #tooltip
    """Exports the selected objects"""

    bl_idname = "my_tools.scene_export"
    bl_label = "Export"

    @intercept(error_result={'CANCELLED'})
    def export_fbx(self, context, filepath):
        return bpy.ops.export_scene.fbx(
            filepath=filepath
            , check_existing=False
            , axis_forward='-Z'
            , axis_up='Y'
            , use_selection=True
            , global_scale=1.0
            , apply_unit_scale=True
            , apply_scale_options='FBX_SCALE_NONE'
            , bake_space_transform=True
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
            , bake_anim=context.scene.my_tools.export_animation_only
            , bake_anim_use_all_bones=False
            , bake_anim_use_nla_strips=False
            , bake_anim_use_all_actions=True
            , bake_anim_force_startend_keying=True
            , bake_anim_step=1.0
            , bake_anim_simplify_factor=1.0
            , path_mode='COPY'
            , embed_textures=False
        )

    def _execute(self, context):
        scn = context.scene
        collision_prefixes = ("UCX", "UBX", "UCP", "USP")
        exported_armatures = []
        path_fields = {
            "num":0,
            "object":"None",
        }

        for obj in context.selected_objects[:]:
            if any(obj.name.startswith(s) for s in collision_prefixes):
                # Don't export collision objects by themselves
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
                if not scn.my_tools.export_animation_only:
                    for child in armature.children:
                        child.select_set(True)

                exported_armatures.append(armature)
                obj = armature

            if obj.type == 'MESH' and scn.my_tools.export_animation_only:
                # Not exporting any meshes
                continue

            collision_objs = []
            if not armature and scn.my_tools.export_collision:
                # Extend selection with pertaining collision objects
                pattern = r"^(?:%s)_%s_\d+$" % ('|'.join(collision_prefixes), obj.name)
                for col in context.scene.objects:
                    if re.match(pattern, col.name):
                        col.select = True
                        collision_objs.append(col)

            # Move main object to world center while keeping collision relative transforms
            saved_transforms = {}
            for col in collision_objs:
                saved_transforms[col] = col.matrix_world.copy()
                col.matrix_world = obj.matrix_world.inverted() * col.matrix_world
            saved_transforms[obj] = obj.matrix_world.copy()
            obj.matrix_world.identity()

            # if obj and obj.type == 'ARMATURE' and scn.my_tools.export_animation_only and name.startswith("SK_"):
            #     # As a special case change SK_ prefix to A_ following UE4 naming conventions
            #     name = "A_" + name[len("SK_"):]
            path_fields["object"] = obj.name
            path_fields["num"] = path_fields["num"] + 1
            filepath = get_export_path(scn.my_tools.export_path, path_fields)
            filename = bpy.path.basename(filepath)

            result = self.export_fbx(context, filepath)
            if result == {'FINISHED'}:
                print(f"Exported {filepath}")
                self.exported_files.append(filename)

            # Restore transforms
            for obj, matrix_world in saved_transforms.items():
                obj.matrix_world = matrix_world

    def execute(self, context):
        scn = context.scene

        if not context.selected_objects:
            self.report({'ERROR'}, "Nothing to export.")
            return {'CANCELLED'}

        path_fields = {
            "num":0,
            "object":"None",
            "action":"None",
        }
        reason = check_invalid_export_path(scn.my_tools.export_path, path_fields)
        if reason:
            self.report({'ERROR'}, reason)
            return {'CANCELLED'}

        saved_selection = save_selection()
        saved_use_global_undo = context.preferences.edit.use_global_undo
        context.preferences.edit.use_global_undo = False
        self.exported_files = []

        try:
            self._execute(context, rig)
        finally:
            # Clean up
            load_selection(saved_selection)
            context.preferences.edit.use_global_undo = saved_use_global_undo

        if not self.exported_files:
            self.report({"INFO"}, "Nothing exported.")
        elif len(self.exported_files) <= 5:
            self.report({"INFO"}, "Exported %s" % ', '.join(self.exported_files))
        else:
            self.report({'INFO'}, "%d files exported." % len(self.exported_files))

        return {'FINISHED'}

class MY_PT_scene_export(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "My Tools"
    bl_label = "Scene Export"

    def draw(self, context):
        scn = context.scene
        layout = self.layout

        col = layout.column()
        col.prop(scn.my_tools, "export_animation_only")
        col1 = col.column(align=True)
        col1.enabled = not scn.my_tools.export_animation_only
        col1.prop(scn.my_tools, "export_collision")
        col1 = col.column(align=True)
        col1.prop(scn.my_tools, "export_path", text="")
        col1.operator("my_tools.scene_export", icon='FORWARD', text="Export selected")

class MY_OT_character_export_add(bpy.types.Operator):
    #tooltip
    """Add a new character export job"""

    bl_idname = "my_tools.character_export_add"
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

        return {'FINISHED'}

class MY_OT_character_export_remove(bpy.types.Operator):
    #tooltip
    """Removes a character export job"""

    bl_idname = "my_tools.character_export_remove"
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

        return {'FINISHED'}

class MY_OT_character_export_execute(bpy.types.Operator):
    #tooltip
    """Execute character export job"""

    bl_idname = "my_tools.character_export_execute"
    bl_label = "Execute Export Job"

    index: bpy.props.IntProperty(options={'HIDDEN'})

    def execute(self, context):
        scn = context.scene
        job = scn.my_tools.export_jobs[self.index]

        if not job.rig:
            self.report({'ERROR'}, "No armature selected.")
            return {'CANCELLED'}

        if not job.rig.visible_get():
            self.report({'ERROR'}, "Currently the rig must be visible to export.")
            return {'CANCELLED'}

        saved_action = job.rig.animation_data.action
        saved_mode = context.mode
        saved_selection = save_selection()
        saved_hide = {}
        new_fcurves = []
        bpy.context.view_layer.objects.active = job.rig
        bpy.ops.object.mode_set(mode='OBJECT')

        if job.what == 'MESH':
            print(f'Beginning mesh export job "{job.name}"')

            # Mark the objects that should be exported as render so they will be picked up
            for job_coll in job.collections:
                coll = job_coll.collection
                if not coll:
                    continue

                if (job_coll.export_viewport and not coll.hide_viewport
                    or job_coll.export_render and not coll.hide_render):
                    for obj in coll.objects:
                        if (job_coll.export_viewport and not obj.hide_viewport
                            or job_coll.export_render and not obj.hide_render):
                            saved_hide[obj] = (obj.hide_select, obj.hide_viewport, obj.hide_render)
                            obj.hide_select = False
                            obj.hide_render = False

            # Hide all objects that shouldn't be exported
            for obj in get_children_recursive(job.rig):
                if obj not in saved_hide:
                    obj.hide_render = True

            bpy.ops.my_tools.character_export(
                export_path=job.export_path,
                export_meshes=True,
                export_animation=False,
                suffix=job.suffix,
                apply_modifiers=job.apply_modifiers,
                mirror_shape_keys=job.mirror_shape_keys,
                join_meshes=job.join_meshes,
                split_masks=job.split_masks,
            )
            beep(0)

        elif job.what == 'ANIMATION':
            print(f'Beginning animation export job "{job.name}"')

            action_names = set()
            for job_action in job.actions:
                if not job_action:
                    continue

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

                    print(f"Baking {cp.source} -> {cp.destination} in {action_name}")

                    fcurve_dst = action.fcurves.new(cp.destination)
                    new_fcurves.append((action, fcurve_dst))

                    for frame_idx in range(0, int(action.frame_range[1]) + 1):
                        val = fcurve_src.evaluate(frame_idx)
                        fcurve_dst.keyframe_points.insert(frame_idx, val)

            bpy.ops.my_tools.character_export(
                export_path=job.export_path,
                export_meshes=False,
                export_animation=True,
                actions=",".join(action_names),
            )
            beep(1)

        # Clean up
        for action, fcurve in new_fcurves:
            action.fcurves.remove(fcurve)

        for obj, hide in saved_hide.items():
            hide_select, hide_viewport, hide_render = hide
            obj.hide_select = hide_select
            obj.hide_viewport = hide_viewport
            obj.hide_render = hide_render

        job.rig.animation_data.action = saved_action
        if context.mode != saved_mode:
            bpy.ops.object.mode_set(mode=saved_mode)
        load_selection(saved_selection)

        print("Job complete")

        return {'FINISHED'}

class MY_PT_character_export(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "My Tools"
    bl_label = "Character Export"

    def draw(self, context):
        layout = self.layout
        scn = context.scene

        layout.operator("my_tools.character_export_add", text="Add")

        for job_idx, job in enumerate(scn.my_tools.export_jobs):
            col_job = layout.column(align=True)
            box = col_job.box()
            row = box.row()
            icon = 'DISCLOSURE_TRI_DOWN' if job.show_expanded else 'DISCLOSURE_TRI_RIGHT'
            row.prop(job, "show_expanded", icon=icon, text="", emboss=False)
            row.prop(job, "what", text="", expand=True)
            row.prop(job, "name", text="")
            op = row.operator("my_tools.character_export_remove", icon='X', text="", emboss=False)
            op.index = job_idx
            box = col_job.box()
            col = box

            if job.show_expanded:
                box.prop(job, "rig")

                if job.what == 'MESH':
                    col = box.column(align=True)
                    for coll in job.collections:
                        row = col.row(align=True)
                        row.prop(coll, "collection", text="")
                        row.prop(coll, "export_viewport", icon='RESTRICT_VIEW_OFF', text="")
                        row.prop(coll, "export_render", icon='RESTRICT_RENDER_OFF', text="")

                    col = box.column()
                    col.prop(job, "suffix")
                    col.prop(job, "apply_modifiers")
                    col.prop(job, "mirror_shape_keys")
                    col.prop(job, "join_meshes")
                    col.prop(job, "split_masks")
                elif job.what == 'ANIMATION':
                    col = box.column(align=True)
                    for action in job.actions:
                        row = col.row(align=True)
                        if not action.use_pattern:
                            row.prop_search(action, "action", bpy.data, "actions", text="")
                        else:
                            row.prop(action, "action", text="")
                        row.prop(action, "use_pattern", icon='SELECT_SET', text="")

                    col = box.column(align=True)
                    col.label(text="Bake Properties:")
                    for copy_property in job.copy_properties:
                        row = col.row(align=True)
                        row.prop(copy_property, "source", text="")
                        row.label(text="", icon='FORWARD')
                        row.prop(copy_property, "destination", text="")

                col = box.column(align=True)
                col.prop(job, "export_path", text="")

            op = col.operator("my_tools.character_export_execute", icon='FORWARD', text="Execute")
            op.index = job_idx

classes = (
    MY_OT_scene_export,
    MY_PT_scene_export,
    MY_OT_character_export_add,
    MY_OT_character_export_remove,
    MY_OT_character_export_execute,
    MY_PT_character_export,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
