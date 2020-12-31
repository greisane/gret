from bpy.app.handlers import persistent
from collections import OrderedDict
from fnmatch import fnmatch
import bpy
import json
import os
import re
from .helpers import (
    clear_pose,
    get_flipped_name,
)
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
            # Fetch value to make sure the property exists
            value = obj.path_resolve(prop_path)
            # Don't attach the object name to text, custom property name should be descriptive enough
            text = titlecase(prop_match[2])
            return obj, prop_path, text

        prop_match = prop_re.search(prop_path)
        if prop_match:
            obj = obj.path_resolve(prop_match[1])
            prop_path = prop_match[2]
            # Fetch value to make sure the property exists
            value = obj.path_resolve(prop_path)
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

    bl_idname = 'my_tools.set_camera'
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

    bl_idname = 'my_tools.set_insertor_target'
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

        if '_bones' in path:
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
            patterns = path['_bones'].split(',')

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

    bl_idname = 'my_tools.clear_insertor_target'
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

        if insertee and insertee.type == 'ARMATURE' and '_bones' in path:
            insertee_bones = [b for b in insertee.pose.bones if fnmatch(b.name, path['_bones'])]
            for bone in insertee_bones:
                for con in bone.constraints[:]:
                    if con.type == 'LIMIT_DISTANCE' and con.target in insertor_bones:
                        bone.constraints.remove(con)

        insertor_spline_ik.target = None

        return {'FINISHED'}

class MY_OT_property_add(bpy.types.Operator):
    #tooltip
    """Add a property to the list"""

    bl_idname = 'my_tools.property_add'
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

        properties = list(obj.get('properties', []))
        properties.append(self.path)
        properties.sort(key=lambda prop_path: parse_prop_path(obj, prop_path)[2])
        obj['properties'] = properties

        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

class MY_OT_property_remove(bpy.types.Operator):
    #tooltip
    """Remove the property from the list"""

    bl_idname = 'my_tools.property_remove'
    bl_label = "Remove Property"
    bl_options = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        obj = context.object

        properties = list(obj.get('properties', []))
        if self.index >= 0 and self.index < len(properties):
            del properties[self.index]
        obj['properties'] = properties

        return {'FINISHED'}

class MY_OT_propagate_bone_inherit_scale(bpy.types.Operator):
    #tooltip
    """Propagates 'Inherit Scale' from the selected bone to children"""

    bl_idname = 'my_tools.propagate_bone_inherit_scale'
    bl_label = "Propagate Bone Inherit Scale"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'POSE' and context.selected_pose_bones_from_active_object

    def execute(self, context):
        obj = context.object

        for active_pbone in context.selected_pose_bones_from_active_object:
            active_bone = obj.data.bones[active_pbone.name]
            for bone in active_bone.children_recursive:
                bone.inherit_scale = active_bone.inherit_scale

        return {'FINISHED'}

class MY_OT_vertex_group_subdivide(bpy.types.Operator):
    #tooltip
    """Subdivide weights along the corresponding armature bone, if it exists"""

    bl_idname = 'my_tools.vertex_group_subdivide'
    bl_label = "Subdivide"
    bl_options = {'REGISTER', 'UNDO'}

    number_cuts: bpy.props.IntProperty(
        name="Subdivisions",
        description="Number of subdivisions",
        default=2,
        min=1,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'PAINT_WEIGHT' and context.object and context.object.vertex_groups.active

    def execute(self, context):
        obj = context.object
        vgroups = obj.vertex_groups
        src_vg = vgroups.active

        armature = obj.find_armature()
        if not armature:
            self.report({'ERROR'}, "No armature found.")
            return {'CANCELLED'}

        bone = armature.data.bones.get(src_vg.name)
        if not bone:
            self.report({'ERROR'}, "No bone associated with the vertex group.")
            return {'CANCELLED'}

        bone_dir = bone.tail - bone.head
        bone_length = bone_dir.length
        bone_dir /= bone_length
        dst_vgs = [vgroups.new(name=f"{src_vg.name}.{n:03d}") for n in range(self.number_cuts)]

        for vert in obj.data.vertices:
            for vg in vert.groups:
                if vg.group == src_vg.index:
                    x = bone_dir.dot(vert.co - bone.head) / bone_length * len(dst_vgs)
                    for n, dst_vg in enumerate(dst_vgs):
                        t = 1.0
                        if n > 0:
                            t = min(t, x + 0.5 - n)
                        if n < len(dst_vgs) - 1:
                            t = min(t, (n + 1.5) - x)
                        t = max(0.0, min(1.0, t))
                        dst_vg.add([vert.index], vg.weight * t, 'REPLACE')

        # Remove original
        vgroups.remove(src_vg)

        return {'FINISHED'}

class MY_OT_selection_set_toggle(bpy.types.Operator):
    #tooltip
    """Toggle this bone selection set. Shift-click to extend selection"""

    bl_idname = 'my_tools.selection_set_toggle'
    bl_label = "Toggle Bone Selection Set"
    bl_options = {'INTERNAL', 'UNDO'}

    name: bpy.props.StringProperty(options={'HIDDEN'})
    extend: bpy.props.BoolProperty(options={'HIDDEN'}, default=False)

    @classmethod
    def poll(cls, context):
        return context.object and context.mode == 'POSE'

    def execute(self, context):
        obj = context.object
        sel_set = obj.selection_sets.get(self.name, None)
        sel_set_index = obj.selection_sets.find(self.name)
        if not sel_set:
            return {'CANCELLED'}

        sel_set.is_selected = not sel_set.is_selected
        for pbone in context.visible_pose_bones:
            bone = pbone.bone
            if not self.extend:
                bone.select = False
            if pbone.name in sel_set.bone_ids:
                bone.select = sel_set.is_selected

        if not self.extend:
            for idx, sel_set in enumerate(obj.selection_sets):
                if idx != sel_set_index:
                    sel_set.is_selected = False

        return {'FINISHED'}

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

class MY_OT_selection_set_copy(bpy.types.Operator):
    #tooltip
    """Copy bone selection sets to clipboard"""

    bl_idname = 'my_tools.selection_set_copy'
    bl_label = "Copy Bone Selection Sets"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        obj = context.object

        sel_sets = getattr(obj, 'selection_sets')
        if sel_sets:
            sel_sets_list = [(name, sel_set.bone_ids.keys()) for name, sel_set in sel_sets.items()]
            sel_sets_json = json.dumps(sel_sets_list)
            context.window_manager.clipboard = sel_sets_json
            self.report({'INFO'}, "Copied bone selection sets to clipboard.")

        return {'FINISHED'}

class MY_OT_selection_set_paste(bpy.types.Operator):
    #tooltip
    """Pastes bone selection sets from clipboard"""

    bl_idname = 'my_tools.selection_set_paste'
    bl_label = "Paste Bone Selection Sets"
    bl_options = {'INTERNAL', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object is not None

    def execute(self, context):
        obj = context.object

        sel_sets = getattr(obj, 'selection_sets')
        if sel_sets is None:
            return {'CANCELLED'}

        try:
            sel_sets_list = json.loads(context.window_manager.clipboard)
        except:
            return {'CANCELLED'}

        try:
            for name, bone_names in sel_sets_list:
                if name not in sel_sets:
                    sel_set = sel_sets.add()
                    sel_set.name = name
                    for bone_name in bone_names:
                        sel_set_bone = sel_set.bone_ids.add()
                        sel_set_bone.name = bone_name
            self.report({'INFO'}, "Pasted bone selection sets from clipboard.")
        except:
            pass
        return {'FINISHED'}

        return {'FINISHED'}

class MY_PT_character_tools(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "My Tools"
    bl_label = "Character Tools"

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
        row.operator('my_tools.property_add', icon='ADD', text="")

        properties = obj.get('properties')
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

                row.operator('my_tools.property_remove', icon='X', text="").index = idx

        if hasattr(obj, 'selection_sets'):
            box = layout.box()
            row = box.row()
            row.label(text="Bone Selection Sets", icon='GROUP_BONE')
            row = row.row(align=True)
            row.operator('my_tools.selection_set_copy', icon='COPYDOWN', text="")
            row.operator('my_tools.selection_set_paste', icon='PASTEDOWN', text="")

            selection_sets = OrderedDict(reversed(obj.selection_sets.items()))
            if selection_sets:
                col = box.column(align=True)
                while selection_sets:
                    name, sel_set = selection_sets.popitem()
                    other_name = get_flipped_name(name)
                    other_sel_set = selection_sets.pop(other_name, None)

                    row = col.row(align=True)
                    if other_sel_set:
                        row.operator('my_tools.selection_set_toggle', text=other_name,
                            depress=other_sel_set.is_selected).name = other_name
                    row.operator('my_tools.selection_set_toggle', text=name,
                        depress=sel_set.is_selected).name = name

        if obj and obj.type == 'ARMATURE' and obj.mode == 'POSE' and obj.data.bones.active:
            selected_bone = obj.pose.bones[obj.data.bones.active.name]
            spline_ik = next((m for m in selected_bone.constraints if m.type == 'SPLINE_IK'), None)

            if spline_ik and spline_ik.target:
                layout.operator('my_tools.clear_insertor_target',
                    text="Clear Insertor Target", icon='CONSTRAINT_BONE')
            elif spline_ik:
                layout.operator_menu_enum('my_tools.set_insertor_target', 'path',
                    text="Set Insertor Target", icon='CONSTRAINT_BONE')

        layout.operator('my_tools.set_camera', icon='CAMERA_DATA')

classes = (
    MY_OT_clear_insertor_target,
    MY_OT_propagate_bone_inherit_scale,
    MY_OT_property_add,
    MY_OT_property_remove,
    MY_OT_selection_set_copy,
    MY_OT_selection_set_paste,
    MY_OT_selection_set_toggle,
    MY_OT_set_camera,
    MY_OT_set_insertor_target,
    MY_OT_vertex_group_subdivide,
    MY_PT_character_tools,
)

saved_unhidden_collections = set()
@persistent
def save_pre(dummy):
    # Automatically hide the rig armature collection on saving since I'm always forgetting
    # This is so that the linked armature doesn't interfere with the proxy when linking
    for coll in bpy.data.collections:
        if coll.name.endswith('_grp_rig') and not coll.library and not coll.hide_viewport:
            saved_unhidden_collections.add(coll.name)
            coll.hide_viewport = True

@persistent
def save_post(dummy):
    for coll_name in saved_unhidden_collections:
        coll = bpy.data.collections.get(coll_name)
        if coll:
            coll.hide_viewport = False
    saved_unhidden_collections.clear()

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.app.handlers.save_pre.append(save_pre)
    bpy.app.handlers.save_post.append(save_post)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    bpy.app.handlers.save_pre.remove(save_pre)
    bpy.app.handlers.save_post.remove(save_post)
