import bpy

from ..log import log, logd

class GRET_OT_pose_set(bpy.types.Operator):
    """Go to the frame for this pose. Ctrl-click to rename"""

    bl_idname = 'gret.pose_set'
    bl_label = "Set Pose"
    bl_options = {'INTERNAL', 'UNDO'}

    name: bpy.props.StringProperty(options={'HIDDEN'})
    new_name: bpy.props.StringProperty(name="New name", default="")

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.animation_data and obj.animation_data.action

    def execute(self, context):
        obj = context.active_object
        if not self.name:
            return {'CANCELLED'}

        action = obj.animation_data.action
        marker = action.pose_markers.get(self.name, None)
        if marker:
            if self.new_name:
                # Rename
                if self.new_name in action.pose_markers:
                    # Blender allows it, but don't permit conflicting pose names
                    return {'CANCELLED'}
                marker.name = self.new_name
            else:
                context.scene.frame_set(frame=marker.frame)

        return {'FINISHED'}

    def invoke(self, context, event):
        if event.ctrl:
            # Rename
            self.new_name = self.name
            return context.window_manager.invoke_props_dialog(self)
        else:
            self.new_name = ""
            return self.execute(context)

class GRET_OT_pose_make(bpy.types.Operator):
    """Create a pose marker for every frame in the action"""

    bl_idname = 'gret.pose_make'
    bl_label = "Make Poses"
    bl_options = {'INTERNAL', 'UNDO'}

    create_custom_properties: bpy.props.BoolProperty(
        name="For Pose Blender",
        description="Create a custom property for each pose, required for pose blending",
        default=False,
    )

    key_custom_properties: bpy.props.BoolProperty(
        name="For Exporting",
        description="""Key pose weight custom property for each frame.
Not required for pose blending but required for exporting""",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.animation_data and obj.animation_data.action

    def execute(self, context):
        obj = context.active_object
        action = obj.animation_data.action
        start_frame, last_frame = int(action.curve_frame_range[0]), int(action.curve_frame_range[1] + 1)

        unused_markers = action.pose_markers[:]
        for frame in range(start_frame, last_frame):
            marker = next((m for m in action.pose_markers if m.frame == frame), None)
            if marker:
                # There is a marker for this frame, don't remove it
                unused_markers.remove(marker)
            else:
                # Create a marker for this frame
                # Docs read that new() takes a frame argument, this doesn't seem to be the case
                new_marker = action.pose_markers.new(name=f"Pose {frame:03d}")
                new_marker.frame = frame

        for marker in unused_markers:
            log(f"Removed unused pose marker {marker.name}")
            action.pose_markers.remove(marker)

        if self.create_custom_properties and obj.override_library:
            self.report({'WARNING'}, "Can't create custom properties from an override data-block.")
        elif self.create_custom_properties:
            for marker in action.pose_markers:
                if marker.name not in obj:
                    obj[marker.name] = 0.0

                obj.property_overridable_library_set(f'["{marker.name}"]', True)
                obj.id_properties_ui(marker.name).update(default=0.0, description="Pose weight",
                    min=0.0, max=1.0, soft_min=0.0, soft_max=1.0)

            if self.key_custom_properties:
                group = action.groups.get(action.name) or action.groups.new(name=action.name)
                data_path_to_fc = {fc.data_path: fc for fc in action.fcurves}

                for marker in action.pose_markers:
                    data_path = f'["{marker.name}"]'
                    fc = data_path_to_fc.get(data_path)
                    if fc:
                        action.fcurves.remove(fc)
                    data_path_to_fc[data_path] = fc = action.fcurves.new(data_path)
                    fc.group = group

                    if marker.frame > start_frame:
                        fc.keyframe_points.insert(marker.frame - 1, 0.0).interpolation = 'LINEAR'
                    fc.keyframe_points.insert(marker.frame, 1.0).interpolation = 'LINEAR'
                    if marker.frame < last_frame:
                        fc.keyframe_points.insert(marker.frame + 1, 0.0).interpolation = 'LINEAR'

        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.prop(self, 'create_custom_properties')
        row = col.row(align=True)
        row.prop(self, 'key_custom_properties')
        row.enabled = self.create_custom_properties

class GRET_PT_pose_markers(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "gret"
    bl_label = "Pose Markers"
    bl_parent_id = 'GRET_PT_animation'

    @classmethod
    def poll(cls, context):
        return True
        # (context.active_object
        #     and context.active_object.type == 'ARMATURE'
        #     and context.mode in {'OBJECT', 'POSE'})

    def draw_header(self, context):
        layout = self.layout
        layout.label(text="", icon='BOOKMARKS')

    def draw(self, context):
        layout = self.layout
        settings = context.scene.gret
        obj = context.active_object
        active_action = obj.animation_data.action if obj.animation_data else None

        if active_action:
            layout.operator('gret.pose_make', icon='ADD')

            if active_action.pose_markers:
                col = layout.column(align=True)
                if settings.poses_sorted:
                    markers = sorted(active_action.pose_markers, key=lambda p: p.name)
                else:
                    markers = active_action.pose_markers
                for marker in markers:
                    selected = marker.frame == context.scene.frame_current
                    row = col.row(align=True)
                    row.label(text="", icon='PMARKER_ACT' if selected else 'PMARKER_SEL')
                    op = row.operator('gret.pose_set', text=marker.name)
                    op.name = marker.name
        else:
            layout.label(text="There is no active action.")

            # No good place to put the sort button, I guess it's not that important to have it
            # row = layout.row(align=True)
            # row.split()
            # row.prop(settings, 'poses_sorted', icon='SORTALPHA', text="")

classes = (
    GRET_OT_pose_make,
    GRET_OT_pose_set,
    GRET_PT_pose_markers,
)

def register(settings, prefs):
    for cls in classes:
        bpy.utils.register_class(cls)

    settings.add_property('poses_sorted', bpy.props.BoolProperty(
        name="Sort Poses",
        description="Displays pose markers sorted alphabetically",
        default=False,
        options=set(),
    ))

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
