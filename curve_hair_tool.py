from collections import namedtuple
import bpy
from bpy.props import FloatVectorProperty, IntVectorProperty, EnumProperty
from bpy.props import IntProperty, BoolProperty, StringProperty

bl_info = {
    "name": "Curve Hair Tool",
    "author": "greisane",
    "description": "",
    "version": (0, 1),
    "blender": (2, 79, 0),
    "location": "3D View > Quick Search",
    "category": "Object"
}

def merge_vertex_groups(obj, src_name, dst_name, remove_src=True):
    src = obj.vertex_groups[src_name]
    dst = obj.vertex_groups[dst_name]

    for vert_idx, vert in enumerate(obj.data.vertices):
        try:
            dst.add([vert_idx], src.weight(vert_idx), 'ADD')
        except RuntimeError:
            pass

    if remove_src:
        obj.vertex_groups.remove(src)

def apply_pose(armature, pose_bones):
    PoseBoneInfo = namedtuple("PoseBoneInfo", ["name", "head", "tail"])

    bpy.ops.object.mode_set(mode='OBJECT')
    pose_bone_infos = [PoseBoneInfo(name=pb.name, head=pb.head[:], tail=pb.tail[:])
        for pb in pose_bones]

    bpy.ops.object.mode_set(mode='EDIT')
    for pose_bone_info in pose_bone_infos:
        edit_bone = armature.data.edit_bones[pose_bone_info.name]
        edit_bone.head = pose_bone_info.head
        edit_bone.tail = pose_bone_info.tail

    bpy.ops.object.mode_set(mode='OBJECT')
    for pose_bone_info in pose_bone_infos:
        pose_bone = armature.pose.bones[pose_bone_info.name]
        pose_bone.location = (0.0, 0.0, 0.0)
        pose_bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
        pose_bone.scale = (1.0, 1.0, 1.0)

class SkinCurve(bpy.types.Operator):
    bl_idname = "object.skin_curve"
    bl_label = "Skin Curve"
    bl_context = "objectmode"
    bl_options = {'REGISTER', 'UNDO'}

    bone_name_format = StringProperty(
        name="Bone names",
        description="Name format for new bones",
        default="{name}_{bone_num:02d}",
    )
    chain_length = IntProperty(
        name="Bone chain length",
        description="Number of bones from each curve",
        min=1,
        default=3,
    )
    smooth_repeat = IntProperty(
        name="Smooth weights",
        description="Weight smoothing iterations",
        min=0,
        default=6,
    )
    root_bone_name = StringProperty(
        name="Root bone",
        description="Optional, name of an existing bone to attach new chains to",
        default="hair_root",
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def create_bone_chain(self, context, curve, armature, num_bones):
        """Creates a bone chain from a curve. Returns the names of the bones created."""

        bpy.ops.object.mode_set(mode='EDIT')
        last_bone = None
        bone_names = []
        for n in range(num_bones):
            name = self.bone_name_format.format(name=curve.name, bone_num=n)
            bone = armature.data.edit_bones.new(name)
            bone_names.append(bone.name)
            bone.head = (1.0, 1.0, n)
            bone.tail = (1.0, 1.0, n + 1.0)
            bone.parent = last_bone
            bone.use_connect = True
            last_bone = bone

        bpy.ops.object.mode_set(mode='OBJECT')
        pose_bones = [armature.pose.bones[s] for s in bone_names]

        # Bend chain along the spline with a temporary modifier
        spline_ik = pose_bones[-1].constraints.new(type='SPLINE_IK')
        spline_ik.target = curve
        spline_ik.chain_count = num_bones
        spline_ik.use_curve_radius = False
        apply_pose(armature, pose_bones)
        pose_bones[-1].constraints.remove(spline_ik)

        return bone_names

    def skin_curve(self, context, curve, armature, bone_names):
        """Skins a curve to the given list of bones. Returns the curve converted to a mesh."""

        # Curve to mesh
        mesh = curve.to_mesh(context.scene, False, 'PREVIEW')
        mesh_obj = bpy.data.objects.new(curve.name + "_mesh", mesh)
        mesh_obj.parent = armature
        mesh_obj.matrix_world = curve.matrix_world
        context.scene.objects.link(mesh_obj)
        curve.hide = True

        # Bind mesh
        modifier = mesh_obj.modifiers.new(name="Armature", type='ARMATURE')
        modifier.object = armature
        modifier.use_vertex_groups = True
        modifier.use_bone_envelopes = False
        modifier.use_deform_preserve_volume = False

        # Skinning
        context.scene.objects.active = mesh_obj
        bpy.ops.object.mode_set(mode='WEIGHT_PAINT')
        mesh_obj.data.use_paint_mask_vertex = True
        bpy.ops.paint.vert_select_all(action='SELECT')

        for bone in armature.data.bones:
            bone.select = bone.name in bone_names

        bpy.ops.paint.weight_from_bones(type='AUTOMATIC')
        bpy.ops.object.vertex_group_smooth(group_select_mode='ALL',
            factor=1.0, repeat=self.smooth_repeat, expand=0.0)
        # bpy.ops.object.vertex_group_limit_total(group_select_mode='ALL', limit=4)
        bpy.ops.object.vertex_group_normalize_all(group_select_mode='ALL', lock_active=False)

        # Clean up
        mesh_obj.data.use_paint_mask_vertex = False
        bpy.ops.object.mode_set(mode='OBJECT')

        return mesh_obj

    def execute(self, context):
        obj = context.object
        armature = None
        curves = [ob for ob in context.selected_objects if ob.type == 'CURVE']

        if obj.type == 'ARMATURE':
            armature = obj
        elif obj.type == 'MESH' and obj.parent and obj.parent.type == 'ARMATURE':
            armature = obj.parent

        if not armature:
            self.report({'ERROR'}, "Active object must be an armature or a mesh parented to one.")
            return {'CANCELLED'}

        if not curves:
            self.report({'ERROR'}, "Select curves and then the target armature or mesh.")
            return {'CANCELLED'}

        if self.root_bone_name and self.root_bone_name not in armature.data.bones:
            self.report({'ERROR'}, "There is no bone with that name to use as the root bone.")
            return {'CANCELLED'}

        mesh_objs = []
        for curve in curves:
            bone_names = self.create_bone_chain(context, curve, armature, self.chain_length)
            mesh_obj = self.skin_curve(context, curve, armature, bone_names)
            mesh_objs.append(mesh_obj)

            if self.root_bone_name and len(bone_names) >= 2:
                # Attach to root
                #merge_vertex_groups(mesh_obj, bone_names[0], "hair_root")
                mesh_obj.vertex_groups[bone_names[0]].name = self.root_bone_name

                context.scene.objects.active = armature
                bpy.ops.object.mode_set(mode='EDIT')
                edit_bones = armature.data.edit_bones
                edit_bones[bone_names[1]].use_connect = False
                edit_bones[bone_names[1]].parent = edit_bones[self.root_bone_name]
                edit_bones.remove(edit_bones[bone_names[0]])

        if obj.type == 'MESH':
            # Join with target mesh
            ctx = bpy.context.copy()
            ctx['active_object'] = obj
            ctx['selected_objects'] = mesh_objs
            ctx['selected_editable_bases'] = [ctx.scene.object_bases[ob.name] for ob in mesh_objs]
            bpy.ops.object.join(ctx)
            mesh_objs = [obj]

        # Clean up and select result
        for obj in context.scene.objects:
            obj.select = False
        for obj in mesh_objs:
            obj.select = True

        bpy.ops.object.mode_set(mode='OBJECT')

        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

def register():
    bpy.utils.register_class(SkinCurve)

def unregister():
    bpy.utils.unregister_class(SkinCurve)

if __name__ == '__main__':
    register()