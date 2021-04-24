from collections import namedtuple
import bpy
import os
import time

from gret.helpers import (
    beep,
    fail_if_invalid_export_path,
    get_export_path,
    get_nice_export_report,
    load_selection,
    save_selection,
)
from gret import prefs
from gret.log import logger, log, logd
from gret.rig.helpers import (
    clear_pose,
    export_autorig,
    export_autorig_universal,
    export_fbx,
    is_object_arp,
    is_object_arp_humanoid,
)

class ConstantCurve:
    """Mimics FCurve and always returns the same value on evaluation."""
    def __init__(self, value=0.0):
        self.value = value
    def evaluate(self, frame_index):
        return self.value

class GRET_OT_animation_export(bpy.types.Operator):
    bl_idname = 'gret.animation_export'
    bl_label = "Animation Export"
    bl_context = "objectmode"
    bl_options = {'INTERNAL'}

    index: bpy.props.IntProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def _execute(self, context, job, rig):
        start_time = time.time()
        rig_filepath = (rig.proxy.library.filepath if rig.proxy and rig.proxy.library
            else bpy.data.filepath)
        path_fields = {
            'rigfile': os.path.splitext(bpy.path.basename(rig_filepath))[0],
            'rig': rig.name,
        }

        # Select actions to export
        actions = set()
        for job_action in job.actions:
            if job_action:
                if job_action.use_pattern:
                    for action in bpy.data.actions:
                        if not action.library and fnmatch(action.name, job_action.action):
                            actions.add(action)
                else:
                    action = bpy.data.actions.get(job_action.action)
                    if action and not action.library:
                        actions.add(action)

        # One export group for each action
        ExportGroup = namedtuple('ExportGroup', ('suffix', 'action'))
        export_groups = [ExportGroup(suffix="", action=action) for action in actions]

        # Process individual actions
        for action in actions:
            log(f"Processing {action.name}")
            logger.indent += 1

            for cp in job.copy_properties:
                if not cp.source and not cp.destination:
                    continue
                desc = f"{cp.source}->{cp.destination}"

                def resolve_source(source):
                    try:
                        value = float(source)
                        return ConstantCurve(value)
                    except ValueError:
                        pass
                    return next((fc for fc in action.fcurves if fc.data_path == cp.source), None)
                src_fc = resolve_source(cp.source)
                if not src_fc:
                    log(f"Couldn't bake {desc}, invalid source")
                    continue

                dst_fc = next((fc for fc in action.fcurves if fc.data_path == cp.destination), None)
                if dst_fc:
                    # Currently baking to existing curves is not allowed
                    # Would need to duplicate strips, although ARP already does that
                    log(f"Couldn't bake {desc}, destination already exists")
                    continue

                dst_fc = action.fcurves.new(cp.destination)
                self.new_fcurves.append((action, dst_fc))

                log(f"Baking {desc}")
                for frame_idx in range(0, int(action.frame_range[1]) + 1):
                    val = src_fc.evaluate(frame_idx)
                    dst_fc.keyframe_points.insert(frame_idx, val)

            logger.indent -= 1

        if job.disable_auto_eyelid:
            for bone_name in ('c_eyelid_base.l', 'c_eyelid_base.r'):
                pb = rig.pose.bones.get('c_eyelid_base.l')
                if pb:
                    for constraint in (con for con in pb.constraints if not con.mute):
                        constraint.mute = True
                        self.saved_unmuted_constraints.append(constraint)

        # Don't want shape keys animated as I'm using armature custom props to drive them
        # export_fbx_bin.py will skip over absolute shape keys so use that to disable them
        # TODO this should be configurable
        no_shape_keys = True
        if no_shape_keys:
            for mesh in bpy.data.meshes:
                if mesh.shape_keys and mesh.shape_keys.use_relative:
                    self.saved_meshes_with_relative_shape_keys.append(mesh)
                    mesh.shape_keys.use_relative = False

        # Finally export
        for export_group in export_groups:
            path_fields['action'] = export_group.action.name
            path_fields['suffix'] = export_group.suffix
            filepath = get_export_path(job.animation_export_path, path_fields)
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
            if markers and job.export_markers:
                # Export action markers as a comma separated list
                csv_filepath = get_export_path(job.markers_export_path, path_fields)
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

            # Finally export
            actions = [export_group.action]

            if is_object_arp_humanoid(rig):
                log(f"Exporting {filename} via Auto-Rig export")
                result = export_autorig(filepath, context, rig, actions=actions)
            elif is_object_arp(rig):
                log(f"Exporting {filename} via Auto-Rig export (universal)")
                result = export_autorig_universal(filepath, context, rig, actions=actions)
            else:
                log(f"Exporting {filename}")
                result = export_fbx(filepath, context, rig, actions=actions)

            if result == {'FINISHED'}:
                self.exported_files.append(filepath)
            else:
                log("Failed to export!")

    def execute(self, context):
        job = context.scene.gret.export_jobs[self.index]
        rig = job.rig
        assert job.what == 'ANIMATION'

        if not rig or rig.type != 'ARMATURE':
            self.report({'ERROR'}, "No armature selected.")
            return {'CANCELLED'}
        if not rig.visible_get():
            self.report({'ERROR'}, "Currently the rig must be visible to export.")
            return {'CANCELLED'}
        context.view_layer.objects.active = rig

        # Check addon availability and export path
        try:
            fail_if_invalid_export_path(job.animation_export_path, ['action', 'rigfile', 'rig'])
            if job.export_markers:
                fail_if_invalid_export_path(job.markers_export_path, ['action', 'rigfile', 'rig'])
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        saved_selection = save_selection(all_objects=True)
        saved_pose_position = rig.data.pose_position
        saved_action = rig.animation_data.action
        saved_use_global_undo = context.preferences.edit.use_global_undo
        saved_scene_object_names = [o.name for o in context.scene.objects]
        context.preferences.edit.use_global_undo = False
        self.exported_files = []
        self.saved_unmuted_constraints = []
        self.saved_meshes_with_relative_shape_keys = []
        self.new_fcurves = []  # List of (action, fcurve)
        logger.start_logging()
        log(f"Beginning animation export job '{job.name}'")

        try:
            start_time = time.time()
            self._execute(context, job, rig)
            # Finished without errors
            elapsed = time.time() - start_time
            self.report({'INFO'}, get_nice_export_report(self.exported_files, elapsed))
            log("Job complete")
            beep(pitch=1)
        finally:
            # ARP has started leaving behind objects and it breaks subsequent exports
            for obj in context.scene.objects[:]:
                if obj.name not in saved_scene_object_names:
                    log(f"Removing object '{obj.name}' that was left behind")
                    bpy.data.objects.remove(obj, do_unlink=True)
            # Clean up
            for mesh in self.saved_meshes_with_relative_shape_keys:
                mesh.shape_keys.use_relative = True
            for modifier in self.saved_unmuted_constraints:
                modifier.mute = False
            for action, fcurve in self.new_fcurves:
                action.fcurves.remove(fcurve)
            del self.saved_meshes_with_relative_shape_keys
            del self.saved_unmuted_constraints
            del self.new_fcurves
            rig.data.pose_position = saved_pose_position
            rig.animation_data.action = saved_action
            context.preferences.edit.use_global_undo = saved_use_global_undo
            load_selection(saved_selection)
            logger.end_logging()

        return {'FINISHED'}

def register(settings):
    bpy.utils.register_class(GRET_OT_animation_export)

def unregister():
    bpy.utils.unregister_class(GRET_OT_animation_export)
