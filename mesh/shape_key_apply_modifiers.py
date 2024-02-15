from collections import namedtuple, defaultdict
import numpy as np
import bmesh
import bpy

from ..math import get_dist_sq
from ..log import log, logd
from ..helpers import with_object, get_modifier_mask

# shape_key_apply_modifiers TODO:
# - Specialcase more merging modifiers, solidify for example
# - Transfer vertex order. Is it still necessary if all merging modifiers are covered?
#   Is it possible to identify which face went where without guessing?

class ShapeKeyInfo(namedtuple('ShapeKeyInfo', 'coords interpolation mute name slider_max slider_min '
    'value vertex_group')):
    """Helper to preserve shape key information."""

    __slots__ = ()

    @classmethod
    def from_shape_key_with_empty_data(cls, shape_key):
        return cls(
            coords=np.empty(0, dtype=np.single),
            interpolation=shape_key.interpolation,
            mute=shape_key.mute,
            name=shape_key.name,
            # relative_key=shape_key.relative_key.name,
            slider_max=shape_key.slider_max,
            slider_min=shape_key.slider_min,
            value=shape_key.value,
            vertex_group=shape_key.vertex_group,
        )

    @classmethod
    def from_shape_key(cls, shape_key):
        info = cls.from_shape_key_with_empty_data(shape_key)
        info.get_coords_from(shape_key.data)
        return info

    def get_coords_from(self, vertices):
        self.coords.resize(len(vertices) * 3, refcheck=False)
        vertices.foreach_get('co', self.coords)

    def put_coords_into(self, vertices):
        vertices.foreach_set('co', self.coords)

def weld_mesh(mesh, weld_map):
    """Welds mesh vertices according to a source index to destination index weld map."""

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    targetmap = {bm.verts[src_idx]: bm.verts[dst_idx] for src_idx, dst_idx in weld_map.items()}
    bmesh.ops.weld_verts(bm, targetmap=targetmap)
    bm.to_mesh(mesh)
    bm.free()

def apply_modifier(modifier):
    try:
        with_object(bpy.ops.object.modifier_apply, modifier.id_data, modifier=modifier.name)
    except RuntimeError:
        logd(f"Couldn't apply {modifier.type} modifier {modifier.name}")

class ModifierHandler:
    """Subclass this to define special behavior when applying different modifiers."""

    modifier_type = None
    modifier_name = None

    def __init__(self, modifier):
        self.modifier_name = modifier.name

    @classmethod
    def poll(cls, modifier):
        return cls.modifier_type is None or modifier.type == cls.modifier_type

    def apply(self, obj):
        apply_modifier(obj.modifiers[self.modifier_name])

class MirrorModifierHandler(ModifierHandler):
    modifier_type = 'MIRROR'
    weld_map = None  # Specifies vertex pairs to be welded

    def __init__(self, modifier):
        super().__init__(modifier)
        self.merge_dist = modifier.merge_threshold
        self.num_mirrors = sum(modifier.use_axis)

    @classmethod
    def poll(cls, modifier):
        return super().poll(modifier) and modifier.use_mirror_merge and any(modifier.use_axis)

    def apply(self, obj):
        modifier = obj.modifiers[self.modifier_name]

        modifier.use_mirror_merge = False
        with_object(bpy.ops.object.modifier_apply, obj, modifier=modifier.name)

        if not self.weld_map:
            self.fill_weld_map(obj)
        weld_mesh(obj.data, self.weld_map)

    def fill_weld_map(self, obj):
        mesh = obj.data
        num_verts = len(mesh.vertices) // (2 ** self.num_mirrors)  # Num of verts before mirroring
        merge_dist_sq = self.merge_dist ** 2

        # Only consider pairs of mirrored vertices for merging. Probably breaks if flip is enabled
        welds = []
        for n in range(self.num_mirrors):
            num_part_verts = num_verts * (2 ** n)

            new_welds = []
            for src_idx, dst_idx in welds:
                new_welds.append((src_idx + num_part_verts, dst_idx + num_part_verts))
            welds.extend(new_welds)

            for vert_idx in range(num_part_verts):
                vert = mesh.vertices[vert_idx]
                other_vert_idx = vert_idx + num_part_verts
                other_vert = mesh.vertices[other_vert_idx]
                if get_dist_sq(vert.co, other_vert.co) <= merge_dist_sq:
                    welds.append((other_vert_idx, vert_idx))

        # Resolve the welds into a single dict. Not too robust but weld_verts doesn't complain
        self.weld_map = weld_map = {}
        weld_map_reverse = defaultdict(list)
        for src_idx, dst_idx in welds:
            dst_idx = weld_map.get(dst_idx, dst_idx)
            weld_map[src_idx] = dst_idx
            old_idxs = weld_map_reverse.get(src_idx, [])
            for old_idx in old_idxs:
                weld_map[old_idx] = dst_idx
                weld_map_reverse[dst_idx].append(old_idx)
            weld_map_reverse[dst_idx].append(src_idx)

class WeldModifierHandler(ModifierHandler):
    modifier_type = 'WELD'
    weld_map = None  # Specifies vertex pairs to be welded

    def __init__(self, modifier):
        super().__init__(modifier)
        self.merge_dist = modifier.merge_threshold
        self.vertex_group = modifier.vertex_group
        self.invert_vertex_group = modifier.invert_vertex_group

    @classmethod
    def poll(cls, modifier):
        return super().poll(modifier) and modifier.mode == 'ALL'

    def apply(self, obj):
        modifier = obj.modifiers[self.modifier_name]

        with_object(bpy.ops.object.modifier_remove, obj, modifier=modifier.name)

        if not self.weld_map:
            self.fill_weld_map(obj)
        weld_mesh(obj.data, self.weld_map)

    def fill_weld_map(self, obj):
        mesh = obj.data
        vg = obj.vertex_groups.get(self.vertex_group)
        invert = self.invert_vertex_group

        bm = bmesh.new()
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        deform_layer = bm.verts.layers.deform.active
        if deform_layer and vg:
            # Handle vertex group filtering
            verts = [v for v in bm.verts if bool(v[deform_layer].get(vg.index, 0.0)) != invert]
        else:
            verts = bm.verts
        targetmap = bmesh.ops.find_doubles(bm, verts=verts, dist=self.merge_dist)['targetmap']
        self.weld_map = {src.index: dst.index for src, dst in targetmap.items()}
        bm.free()

class DecimateModifierHandler(ModifierHandler):
    # Only works with "collapse" decimate type. There are no operators for the other types as far as i'm aware.
    modifier_type = "DECIMATE"

    def __init__(self, modifier):
        super().__init__(modifier)
        self.ratio = modifier.ratio
        self.vertex_group = modifier.vertex_group
        self.invert_vertex_group = modifier.invert_vertex_group
        self.vertex_group_factor = modifier.vertex_group_factor
        self.use_symmetry = modifier.use_symmetry
        self.symmetry_axis = modifier.symmetry_axis

    @classmethod
    def poll(cls, modifier):
        return super().poll(modifier) and modifier.decimate_type == "COLLAPSE"

    def apply(self, obj):
        print("wow")
        modifier = obj.modifiers[self.modifier_name]
        # There are special EDIT modes depending on object type, but mode_set only accepts EDIT.
        mode = bpy.context.mode if not bpy.context.mode.count('EDIT') else 'EDIT'
        active_obj = bpy.context.view_layer.objects.active
        selected_objs = bpy.context.selected_objects
        shapekey_index = obj.active_shape_key_index

        obj.active_shape_key_index = 0
        if self.vertex_group:
            obj.vertex_groups.active_index = obj.vertex_groups.get(self.vertex_group).index

        # Makes sure everything is deselected first.
        bpy.ops.object.mode_set(mode='OBJECT')
        bpy.ops.object.select_all(action='DESELECT')

        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.reveal()
        bpy.ops.mesh.select_all(action='SELECT')

        with_object(bpy.ops.mesh.decimate, obj, ratio=self.ratio, use_vertex_group=True if self.vertex_group else False, vertex_group_factor=self.vertex_group_factor, invert_vertex_group=self.invert_vertex_group, use_symmetry=self.use_symmetry, symmetry_axis=self.symmetry_axis)
        obj.modifiers.remove(modifier)

        # Reassigns the selected objects, active object and mode, as well as the active shapekey in the object.
        obj.active_shape_key_index = shapekey_index
        for obj in selected_objs:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = active_obj
        bpy.ops.object.mode_set(mode=mode)



modifier_handler_classes = (
    MirrorModifierHandler,
    WeldModifierHandler,
    DecimateModifierHandler,
    ModifierHandler,
)

# Incomplete map of modifier type to icon
modifier_icons = {
    'DATA_TRANSFER': 'MOD_DATA_TRANSFER',
    'MESH_CACHE': 'MOD_MESHDEFORM',
    'MESH_SEQUENCE_CACHE': 'MOD_MESHDEFORM',
    'NORMAL_EDIT': 'MOD_NORMALEDIT',
    'WEIGHTED_NORMAL': 'MOD_NORMALEDIT',
    'UV_PROJECT': 'MOD_UVPROJECT',
    'UV_WARP': 'MOD_UVPROJECT',
    'VERTEX_WEIGHT_EDIT': 'MOD_VERTEX_WEIGHT',
    'VERTEX_WEIGHT_MIX': 'MOD_VERTEX_WEIGHT',
    'VERTEX_WEIGHT_PROXIMITY': 'MOD_VERTEX_WEIGHT',

    'ARRAY': 'MOD_ARRAY',
    'BEVEL': 'MOD_BEVEL',
    'BOOLEAN': 'MOD_BOOLEAN',
    'BUILD': 'MOD_BUILD',
    'DECIMATE': 'MOD_DECIM',
    'EDGE_SPLIT': 'MOD_EDGESPLIT',
    'NODES': 'NODETREE',
    'MASK': 'MOD_MASK',
    'MIRROR': 'MOD_MIRROR',
    'MULTIRES': 'MOD_MULTIRES',
    'REMESH': 'MOD_REMESH',
    'SCREW': 'MOD_SCREW',
    'SKIN': 'MOD_SKIN',
    'SOLIDIFY': 'MOD_SOLIDIFY',
    'SUBSURF': 'MOD_SUBSURF',
    'TRIANGULATE': 'MOD_TRIANGULATE',
    'VOLUME_TO_MESH': 'VOLUME_DATA',
    'WELD': 'AUTOMERGE_OFF',
    'WIREFRAME': 'MOD_WIREFRAME',

    'ARMATURE': 'MOD_ARMATURE',
    'CAST': 'MOD_CAST',
    'CURVE': 'MOD_CURVE',
    'DISPLACE': 'MOD_DISPLACE',
    'HOOK': 'HOOK',
    'LAPLACIANDEFORM': 'MOD_MESHDEFORM',
    'LATTICE': 'MOD_LATTICE',
    'MESH_DEFORM': 'MOD_MESHDEFORM',
    'SHRINKWRAP': 'MOD_SHRINKWRAP',
    'SIMPLE_DEFORM': 'MOD_SIMPLEDEFORM',
    'SMOOTH': 'MOD_SMOOTH',
    'CORRECTIVE_SMOOTH': 'MOD_SMOOTH',
    'LAPLACIANSMOOTH': 'MOD_SMOOTH',
    'SURFACE_DEFORM': 'MOD_MESHDEFORM',
    'WARP': 'MOD_WARP',
    'WAVE': 'MOD_WAVE',
}

ignored_modifier_types = frozenset((
    'CLOTH',
    'COLLISION',
    'DYNAMIC_PAINT',
    'EXPLODE',
    'FLUID',
    'OCEAN',
    'PARTICLE_INSTANCE',
    'PARTICLE_SYSTEM',
    'SOFT_BODY',
))

class GRET_OT_shape_key_apply_modifiers(bpy.types.Operator):
    """Applies viewport modifiers while preserving shape keys"""

    bl_idname = "gret.shape_key_apply_modifiers"
    bl_label = "Apply Modifiers with Shape Keys"
    bl_context = "objectmode"
    bl_options = {'REGISTER', 'UNDO'}

    modifier_mask: bpy.props.BoolVectorProperty(
        name="Apply Modifier",
        description="Whether this modifier should be applied",
        size=32,  # Maximum allowed by Blender, will need some hack if more are required
        default=[True] * 32,
    )

    modifier_info = []  # Only used to draw buttons when operator is invoked

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.object and context.object.type == 'MESH'

    def draw(self, context):
        layout = self.layout
        layout.ui_units_x = 10.0
        obj = context.object

        layout.label(text="Select modifiers to apply:")

        col = layout.column(align=True)
        for modifier_index, (modifier_type, modifier_name) in enumerate(self.modifier_info):
            if modifier_type in ignored_modifier_types:
                continue

            icon = modifier_icons.get(modifier_type, 'BLANK1')
            col.prop(self, 'modifier_mask', index=modifier_index, icon=icon, text=modifier_name)

    def invoke(self, context, event):
        obj = context.object

        # Cache modifier info to be shown on panel. Otherwise redo_last won't work correctly
        # Side note: the displayed icon for show_viewport is hardcoded to change when toggled on
        def should_apply_modifier(mod):
            return (mod.show_viewport
                and mod.type not in ignored_modifier_types
                and mod.type != 'ARMATURE')  # Don't apply armatures by default
        self.modifier_info = [(mod.type, mod.name) for mod in obj.modifiers]
        self.modifier_mask = get_modifier_mask(obj, should_apply_modifier)

        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        obj = context.active_object

        if not any(self.modifier_mask[:len(obj.modifiers)]):
            # There are no modifiers to apply
            return {'FINISHED'}

        if obj.data.users > 1:
            # Make single user copy
            obj.data = obj.data.copy()

        num_shape_keys = len(obj.data.shape_keys.key_blocks) if obj.data.shape_keys else 0
        if not num_shape_keys:
            # No shape keys, just apply the modifiers
            for modifier, mask in zip(obj.modifiers[:], self.modifier_mask):
                if mask:
                    apply_modifier(modifier)
            return {'FINISHED'}

        print(f"Applying modifiers with {num_shape_keys} shape keys")
        mesh_copy = obj.data.copy()  # Copy for convenience, to be able to call from_existing(fcurve)
        shape_keys = obj.data.shape_keys.key_blocks if obj.data.shape_keys else []
        shape_key_infos = []
        saved_active_shape_key_index = obj.active_shape_key_index
        saved_show_only_shape_key = obj.show_only_shape_key

        # Start by separating each shape key so modifiers can be applied one by one
        shape_key_objs = []
        for shape_key in shape_keys:
            shape_key_info = ShapeKeyInfo.from_shape_key(shape_key)
            shape_key_infos.append(shape_key_info)

            new_obj = obj.copy()
            new_obj.name = f"{obj.name}_{shape_key.name}"
            new_obj.data = obj.data.copy()
            shape_key_objs.append(new_obj)

        # Handle modifiers accordingly. This means recording welded vertex pairs for mirrors and such
        obj.shape_key_clear()
        modifier_handlers = []
        post_modifier_handlers = []
        for modifier, mask in zip(obj.modifiers[:], self.modifier_mask):
            if mask:
                for modifier_handler_cls in modifier_handler_classes:
                    if modifier_handler_cls.poll(modifier):
                        modifier_handler = modifier_handler_cls(modifier)

                        # Hardcoded. if more special handlers are added later a bool check in the Handler class would be better
                        if modifier_handler.modifier_name == "Decimate":
                            post_modifier_handlers.append(modifier_handler)
                            break

                        modifier_handler.apply(obj)
                        modifier_handlers.append(modifier_handler)
                        break

        # Store vertex coordinates of each shape key with modifiers applied
        for sk_info, sk_obj in zip(shape_key_infos, shape_key_objs):
            sk_mesh = sk_obj.data
            sk_obj.shape_key_clear()
            sk_info.put_coords_into(sk_mesh.vertices)
            for modifier_handler in modifier_handlers:
                modifier_handler.apply(sk_obj)
            sk_info.get_coords_from(sk_mesh.vertices)

            bpy.data.objects.remove(sk_obj)
            bpy.data.meshes.remove(sk_mesh)

        # Add the shape keys back
        for shape_key_info in shape_key_infos:
            shape_key = obj.shape_key_add()
            shape_key.interpolation = shape_key_info.interpolation
            shape_key.mute = shape_key_info.mute
            shape_key.name = shape_key_info.name
            shape_key.slider_max = shape_key_info.slider_max
            shape_key.slider_min = shape_key_info.slider_min
            shape_key.value = shape_key_info.value
            shape_key.vertex_group = shape_key_info.vertex_group
            if len(shape_key.data) * 3 != len(shape_key_info.coords):
                self.report({'ERROR'}, f"Vertex count for {shape_key.name} did not match, "
                    "the shape key will be lost.")
                continue
            shape_key_info.put_coords_into(shape_key.data)

        # For modifiers that should be applied after all the shapekeys are sorted.
        # The Decimate modifier is special in the sense that it already exists as an operator, which can be used while preserving the shapekeys,
        # so instead of wastefully applying it for every single copy, do it at the ending.
        for modifier_handler in post_modifier_handlers:
            modifier_handler.apply(obj)

        # Recreate drivers
        if mesh_copy.shape_keys and mesh_copy.shape_keys.animation_data:
            for fcurve in mesh_copy.shape_keys.animation_data.drivers:
                if obj.data.shape_keys.animation_data is None:
                    obj.data.shape_keys.animation_data_create()
                obj.data.shape_keys.animation_data.drivers.from_existing(src_driver=fcurve)

        # Clean up
        obj.show_only_shape_key = saved_show_only_shape_key
        obj.active_shape_key_index = saved_active_shape_key_index
        bpy.data.meshes.remove(mesh_copy)

        return {'FINISHED'}

def draw_menu(self, context):
    self.layout.operator(GRET_OT_shape_key_apply_modifiers.bl_idname, icon='CHECKMARK')

def register(settings, prefs):
    bpy.utils.register_class(GRET_OT_shape_key_apply_modifiers)
    bpy.types.MESH_MT_shape_key_context_menu.append(draw_menu)

def unregister():
    bpy.types.MESH_MT_shape_key_context_menu.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_shape_key_apply_modifiers)
