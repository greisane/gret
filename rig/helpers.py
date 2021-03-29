from mathutils import Vector, Quaternion, Euler

non_humanoid_bone_names = [
    'thigh_b_ref.l',
    'thigh_b_ref.r',
]
humanoid_bone_names = [
    'c_root_master.x',
]
limb_bone_names = [
    ('shoulder_ref.', 2),
    ('thigh_ref.', 2),
    ('neck_ref.', 1),
    ('ear_01_ref.', 2),
]
# Collected keys with `sorted(set(chain.from_iterable(pb.keys() for pb in C.object.pose.bones)))`
arp_default_pose_values = {
    'auto_eyelid': 0.1,
    'auto_stretch': 0.0,
    'autolips': None,  # Different values
    'bend_all': 0.0,
    'elbow_pin': 0.0,
    'eye_target': 1.0,
    'fingers_grasp': 0.0,
    'fix_roll': 0.0,
    'head_free': 0,
    'ik_fk_switch': 0.0,
    'leg_pin': 0.0,
    'lips_retain': 0.0,
    'lips_stretch': 1.0,
    'pole_parent': 1,
    'stretch_length': 1.0,
    'stretch_mode': 1,  # Bone original
    'volume_variation': 0.0,
    'y_scale': 2,  # Bone original
}
default_pose_values = {}

def is_object_arp(obj):
    """Returns whether the object is an Auto-Rig Pro armature."""
    return obj and obj.type == 'ARMATURE' and obj.pose.bones.get('c_pos')

def is_object_arp_humanoid(obj):
    """Returns whether the object is an Auto-Rig Pro humanoid armature."""
    # This is check_humanoid_limbs() from auto_rig_ge.py but less spaghetti
    if not is_object_arp(obj):
        return False

    if any(bname in obj.data.bones for bname in non_humanoid_bone_names):
        return False
    if not all(bname in obj.data.bones for bname in humanoid_bone_names):
        return False
    for limb_bone_name, max_bones in limb_bone_names:
        if sum(b.name.startswith(limb_bone_name) for b in obj.data.bones) > max_bones:
            return False
    if obj.rig_spine_count < 3:
        return False
    return True

def clear_pose(obj, clear_armature_properties=True, clear_bone_properties=True):
    """Resets the given armature."""

    if not obj or obj.type != 'ARMATURE':
        return

    if clear_armature_properties:
        for prop_name, prop_value in obj.items():
            if isinstance(prop_value, float):
                obj[prop_name] = 0.0

    is_arp = is_object_arp(obj)
    for pose_bone in obj.pose.bones:
        if clear_bone_properties:
            for prop_name, prop_value in pose_bone.items():
                if is_arp and prop_name in arp_default_pose_values:
                    value = arp_default_pose_values[prop_name]
                    if value is not None:
                        pose_bone[prop_name] = value
                elif prop_name in default_pose_values:
                    value = default_pose_values[prop_name]
                    if value is not None:
                        pose_bone[prop_name] = value
                elif prop_name.startswith("_"):
                    continue
                else:
                    try:
                        pose_bone[prop_name] = type(prop_value)()
                    except TypeError:
                        pass
        pose_bone.location = Vector()
        pose_bone.rotation_quaternion = Quaternion()
        pose_bone.rotation_euler = Euler()
        pose_bone.rotation_axis_angle = [0.0, 0.0, 1.0, 0.0]
        pose_bone.scale = Vector((1.0, 1.0, 1.0))

def try_key(obj, prop_path, frame=0):
    try:
        return obj.keyframe_insert(prop_path, frame=frame)
    except TypeError:
        return False
