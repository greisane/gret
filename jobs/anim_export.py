from collections import namedtuple
from fnmatch import fnmatch
from functools import partial
import bpy
import os

from .. import prefs
from ..log import logger, log, logd
from ..helpers import (
    beep,
    fail_if_invalid_export_path,
    get_export_path,
    get_nice_export_report,
    get_bid_filepath,
)
from ..rig.helpers import (
    export_autorig,
    export_fbx,
    is_object_arp,
    is_object_arp_humanoid,
)
from ..operator import SaveContext

class ConstantCurve:
    """Mimics FCurve and always returns the same value on evaluation."""
    def __init__(self, value=0.0):
        self.value = value
    def evaluate(self, frame_index):
        return self.value

def _anim_export(context, job, rig, save, results):
    rig_filepath = get_bid_filepath(rig)
    rig_basename = os.path.splitext(bpy.path.basename(rig_filepath))[0]

    save.selection()
    save.prop(rig.data, 'pose_position', 'REST')
    save.prop(rig.animation_data, 'action')
    context.view_layer.objects.active = rig

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
            save.temporary(action.fcurves, dest_fc)

            log(f"Baking {desc}")
            for frame_idx in range(0, int(action.frame_end) + 1):
                val = src_fc.evaluate(frame_idx)
                dst_fc.keyframe_points.insert(frame_idx, val)

        logger.indent -= 1

    if job.disable_auto_eyelid:
        for bone_name in ('c_eyelid_base.l', 'c_eyelid_base.r'):
            pb = rig.pose.bones.get(bone_name)
            if pb:
                save.prop_foreach(pb.constraints, 'mute', True)

    # Don't want shape keys animated as I'm using armature custom props to drive them
    # export_fbx_bin.py will skip over absolute shape keys so use that to disable them
    # TODO this should be configurable
    no_shape_keys = True
    if no_shape_keys:
        # Might not work
        save.prop_foreach(bpy.data.shape_keys, 'use_relative')

        # for mesh in bpy.data.meshes:
            # if mesh.shape_keys and mesh.shape_keys.use_relative:
            #     self.saved_meshes_with_relative_shape_keys.append(mesh)
            #     mesh.shape_keys.use_relative = False

    # Auto-Rig is not exporting some bones properly when there are strips in the NLA
    # I don't want to dig into the mess that is ARP code, so temporarily mute strips as a workaround
    for obj in bpy.data.objects:
        if obj.animation_data:
            for track in obj.animation_data.nla_tracks:
                save.prop_foreach(track.strips, 'mute', True)

    # Prepare export options
    rename_bone_pairs = job.get_rename_bone_pairs()

    # Export each file
    for export_group in export_groups:
        path_fields = {
            'job': job.name,
            'scene': context.scene.name,
            'rigfile': rig_basename,
            'rig': rig.name.removesuffix('_rig'),
            'action': export_group.action.name,
            'suffix': export_group.suffix,
        }
        filepath = get_export_path(job.animation_export_path, path_fields)
        filename = bpy.path.basename(filepath)
        if filepath in results:
            log(f"Skipping {filename} as it would overwrite a file that was just exported")
            continue

        if is_object_arp_humanoid(rig):
            log(f"Exporting {filename} via Auto-Rig export (humanoid)")
            exporter = partial(export_autorig, humanoid=True)
        elif is_object_arp(rig):
            log(f"Exporting {filename} via Auto-Rig export (universal)")
            exporter = partial(export_autorig, humanoid=False)
        else:
            log(f"Exporting {filename}")
            exporter = export_fbx
        logger.indent += 1

        # If enabled and present, export action markers as a comma separated list
        markers = export_group.action.pose_markers
        if markers and job.export_markers:
            csv_filepath = get_export_path(job.markers_export_path, path_fields)
            csv_filename = bpy.path.basename(csv_filepath)
            csv_separator = ','
            fps = float(context.scene.render.fps)
            if csv_filepath not in results:
                log(f"Writing markers to {csv_filename}")
                with open(csv_filepath, 'w') as fout:
                    field_headers = ["Name", "Frame", "Time"]
                    print(csv_separator.join(field_headers), file=fout)
                    for marker in markers:
                        fields = [marker.name, marker.frame, marker.frame / fps]
                        print(csv_separator.join(str(field) for field in fields), file=fout)
            else:
                log(f"Skipping {csv_filename} as it would overwrite a file that was just exported")

        # Finally export
        result = exporter(filepath, context, rig,
            action=export_group.action,
            export_twist=not job.disable_twist_bones,
            rename_bone_pairs=rename_bone_pairs)

        if result == {'FINISHED'}:
            results.append(filepath)
        else:
            log("Failed to export!")

        logger.indent -= 1

def anim_export(self, context, job):
    assert job.what == 'ANIMATION'
    rig = job.rig

    # Validate job settings
    if not rig or rig.type != 'ARMATURE':
        self.report({'ERROR'}, "No armature selected.")
        return {'CANCELLED'}
    if not rig.visible_get():
        self.report({'ERROR'}, "Currently the rig must be visible to export.")
        return {'CANCELLED'}
    try:
        field_names = ['job', 'scene', 'rigfile', 'rig', 'action']
        fail_if_invalid_export_path(job.animation_export_path, field_names)
        if job.export_markers:
            fail_if_invalid_export_path(job.markers_export_path, field_names)
    except Exception as e:
        self.report({'ERROR'}, str(e))
        return {'CANCELLED'}

    results = []
    logger.start_logging()
    log(f"Beginning animation export job '{job.name}'")

    try:
        with SaveContext(context, "anim_export") as save:
            _anim_export(context, job, rig, save, results)

        # Finished without errors
        log("Job complete")
        if prefs.jobs__beep_on_finish:
            beep(pitch=1)
        self.report({'INFO'}, get_nice_export_report(results, logger.time_elapsed))
    finally:
        job.log = logger.end_logging()

    return {'FINISHED'}
