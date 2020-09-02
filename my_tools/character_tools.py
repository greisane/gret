import os
import re
import bpy
from fnmatch import fnmatch
from .helpers import clear_pose, levenshtein_distance
from .stringcase import titlecase

custom_prop_re = re.compile(r'(.+)?\["([^"]+)"\]$')
prop_re = re.compile(r'(.+)\.([^"\.]+)$')
lerp = lambda a, b, t: a * (1.0 - t) + b * t

def parse_prop_path(obj, prop_path):
    # Returns target data, property path and pretty property text if the property was found
    # Otherwise returns None, None, prop_path

    try:
        prop_match = custom_prop_re.search(prop_path)
        if prop_match:
            if prop_match[1]:
                obj = obj.path_resolve(prop_match[1])
            prop_path = f'["{prop_match[2]}"]'
            # Don't attach the object name to text, custom property name should be descriptive enough
            text = titlecase(prop_match[2])
            return obj, prop_path, text

        prop_match = prop_re.search(prop_path)
        if prop_match:
            obj = obj.path_resolve(prop_match[1])
            prop_path = prop_match[2]
            text = f"{obj.name} {titlecase(prop_match[2])}"
            return obj, prop_path, text
    except ValueError:
        pass

    return None, None, prop_path

def get_bone_chain(bone, num):
    bones = [bone]
    num -= 1
    while bone.parent and num > 0:
        bone = bone.parent
        bones.append(bone)
        num -= 1
    return bones

def find_proxy(obj):
    if obj and obj.library:
        # Linked object, find proxy if possible
        return next((o for o in bpy.data.objects if o.proxy == obj), None)
    return obj

class MY_OT_set_camera(bpy.types.Operator):
    #tooltip
    """Switches to the camera attached to the character"""

    bl_idname = "my_tools.set_camera"
    bl_label = "Set Camera"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'ARMATURE'

    def execute(self, context):
        obj = context.object.proxy or context.object

        camera = next((o for o in bpy.data.objects if o.type == 'CAMERA' and o.parent == obj), None)
        if not camera:
            self.report({'INFO'}, "Character has no attached camera.")
            return {'CANCELLED'}

        bpy.context.scene.camera = camera

        self.report({'INFO'}, "Active camera set.")
        return {'FINISHED'}

class MY_OT_set_insertor_target(bpy.types.Operator):
    #tooltip
    """Configures an insertor setup"""

    bl_idname = "my_tools.set_insertor_target"
    bl_label = "Set Insertor Target"
    bl_options = {'INTERNAL', 'UNDO'}

    def get_path_items(self, context):
        return [(o.name, f"{o.name} ({o.parent.name})", "") for o in bpy.data.objects
            if o.type == 'CURVE' and o.parent and o.parent.type == 'ARMATURE']

    path: bpy.props.EnumProperty(
        items=get_path_items,
        name="Target Path",
        description="Path for the insertor to follow",
    )

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj and obj.type == 'ARMATURE' and obj.mode == 'POSE' and obj.data.bones.active

    def execute(self, context):
        obj = context.object

        insertor_bone = obj.data.bones[obj.data.bones.active.name]
        insertor_spline_ik = next(c for c in obj.pose.bones[insertor_bone.name].constraints
            if c.type == 'SPLINE_IK')
        insertor_bones = get_bone_chain(insertor_bone, insertor_spline_ik.chain_count)

        path = bpy.data.objects[self.path]
        insertee = find_proxy(path.parent)
        if not insertee or insertee.type != 'ARMATURE':
            self.report({'ERROR'}, "Path is not parented to an armature.")
            return {'CANCELLED'}

        hook = next((m for m in path.modifiers if m.type == 'HOOK'), None)
        if hook and hook.object and hook.object.type == 'ARMATURE':
            hook_bone = find_proxy(hook.object).pose.bones[hook.subtarget]
            if hook_bone:
                insertor_root = insertor_bones[-1].parent
                if not insertor_root:
                    self.report({'ERROR'}, "Insertor chain must have a parent bone.")
                    return {'CANCELLED'}

                con = hook_bone.constraints.new(type='COPY_TRANSFORMS')
                con.show_expanded = False
                con.name = "Hook Follow"
                con.target = obj
                con.subtarget = insertor_root.name
                con.head_tail = 1.0

        if "_bones" in path:
            def add_collision(bone, other_bone, head_tail):
                con = bone.constraints.new(type='LIMIT_DISTANCE')
                con.show_expanded = False
                con.name = f"{other_bone.name} ({head_tail})"
                con.target = obj
                con.subtarget = other_bone.name
                con.head_tail = head_tail
                con.distance = lerp(other_bone.head_radius, other_bone.tail_radius, head_tail)
                con.limit_mode = 'LIMITDIST_OUTSIDE'

                # Move to top in case there are additional constraints like clamping
                ctx = {'object': insertee, 'constraint': con}
                insertee.data.bones.active = insertee.data.bones[bone.name]
                while bone.constraints[0] != con:
                    result = bpy.ops.constraint.move_up(ctx, constraint=con.name, owner='BONE')
                    if result == {'CANCELLED'}:
                        # Prevent an infinite loop in case the operator fails
                        self.report({'ERROR'}, "Failed to reorder constraints.")
                        break

            saved_layers = insertee.data.layers[:]
            insertee.data.layers[:] = [True] * len(saved_layers)
            patterns = path["_bones"].split(',')

            insertee_bones = [b for b in insertee.pose.bones if any(fnmatch(b.name, s) for s in patterns)]
            for bone in insertee_bones:
                for other_bone in insertor_bones:
                    add_collision(bone, other_bone, 0.33)
                    add_collision(bone, other_bone, 0.66)
                    add_collision(bone, other_bone, 1.0)

            insertee.data.layers[:] = saved_layers

        insertor_spline_ik.target = path

        return {'FINISHED'}

class MY_OT_clear_insertor_target(bpy.types.Operator):
    #tooltip
    """Reverts an insertor setup"""

    bl_idname = "my_tools.clear_insertor_target"
    bl_label = "Clear Insertor Target"
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj and obj.type == 'ARMATURE' and obj.mode == 'POSE' and obj.data.bones.active

    def execute(self, context):
        obj = context.object

        insertor_bone = obj.data.bones[obj.data.bones.active.name]
        insertor_spline_ik = next(c for c in obj.pose.bones[insertor_bone.name].constraints
            if c.type == 'SPLINE_IK')
        insertor_bones = get_bone_chain(insertor_bone, insertor_spline_ik.chain_count)

        path = insertor_spline_ik.target
        insertee = find_proxy(path.parent)

        hook = next((m for m in path.modifiers if m.type == 'HOOK'), None)
        if hook and hook.object and hook.object.type == 'ARMATURE':
            hook_bone = find_proxy(hook.object).pose.bones[hook.subtarget]
            if hook_bone:
                for con in hook_bone.constraints[:]:
                    if con.type == 'COPY_TRANSFORMS' and con.target == obj:
                        hook_bone.constraints.remove(con)

        if insertee and insertee.type == 'ARMATURE' and "_bones" in path:
            insertee_bones = [b for b in insertee.pose.bones if fnmatch(b.name, path["_bones"])]
            for bone in insertee_bones:
                for con in bone.constraints[:]:
                    if con.type == 'LIMIT_DISTANCE' and con.target in insertor_bones:
                        bone.constraints.remove(con)

        insertor_spline_ik.target = None

        return {'FINISHED'}

class MY_OT_property_add(bpy.types.Operator):
    #tooltip
    """Add a property to the list"""

    bl_idname = "my_tools.property_add"
    bl_label = "Add Property"
    bl_options = {'INTERNAL', 'UNDO'}

    path: bpy.props.StringProperty(
        name="Property Path",
        description="Path to an existing property",
        default="",
    )

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        obj = context.object

        if not self.path:
            return {'CANCELLED'}

        properties = list(obj.get("properties", []))
        properties.append(self.path)
        properties.sort(key=lambda prop_path: parse_prop_path(obj, prop_path)[2])
        obj["properties"] = properties

        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

class MY_OT_property_remove(bpy.types.Operator):
    #tooltip
    """Remove the property from the list"""

    bl_idname = "my_tools.property_remove"
    bl_label = "Remove Property"
    bl_options = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        obj = context.object

        properties = list(obj.get("properties", []))
        if self.index >= 0 and self.index < len(properties):
            del properties[self.index]
        obj["properties"] = properties

        return {'FINISHED'}

class MY_OT_propagate_bone_inherit_scale(bpy.types.Operator):
    #tooltip
    """Propagates 'Inherit Scale' from the selected bone to children"""

    bl_idname = "my_tools.propagate_bone_inherit_scale"
    bl_label = "Propagate Bone Inherit Scale"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.selected_pose_bones_from_active_object

    def execute(self, context):
        obj = context.object

        for active_pbone in context.selected_pose_bones_from_active_object:
            active_bone = obj.data.bones[active_pbone.name]
            for bone in active_bone.children_recursive:
                bone.inherit_scale = active_bone.inherit_scale

        return {'FINISHED'}

class MY_PT_character(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "My Tools"
    bl_label = "Character"

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'POSE'} and context.object

    def draw(self, context):
        obj = context.object
        scn = context.scene
        layout = self.layout

        box = layout.box()
        row = box.row()
        row.label(text="Properties", icon='PROPERTIES')
        row.operator("my_tools.property_add", icon='ADD', text="")

        properties = obj.get("properties")
        if properties:
            col = box.column(align=True)

            for idx, prop_path in enumerate(properties):
                row = col.row(align=True)
                data, prop_path, label = parse_prop_path(obj, prop_path)

                if data:
                    row.prop(data, prop_path, text=label)
                else:
                    row.alert = True
                    row.label(text=f"Missing: {label}")

                row.operator("my_tools.property_remove", icon='X', text="").index = idx

        if obj and obj.type == 'ARMATURE' and obj.mode == 'POSE' and obj.data.bones.active:
            selected_bone = obj.pose.bones[obj.data.bones.active.name]
            spline_ik = next((m for m in selected_bone.constraints if m.type == 'SPLINE_IK'), None)

            if spline_ik and spline_ik.target:
                layout.operator("my_tools.clear_insertor_target",
                    text="Clear Insertor Target", icon='CONSTRAINT_BONE')
            elif spline_ik:
                layout.operator_menu_enum("my_tools.set_insertor_target", 'path',
                    text="Set Insertor Target", icon='CONSTRAINT_BONE')

        layout.operator("my_tools.set_camera", icon='CAMERA_DATA')

classes = (
    MY_OT_set_camera,
    MY_OT_set_insertor_target,
    MY_OT_clear_insertor_target,
    MY_OT_property_add,
    MY_OT_property_remove,
    MY_OT_propagate_bone_inherit_scale,
    MY_PT_character,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
