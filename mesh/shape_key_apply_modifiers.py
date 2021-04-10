from collections import namedtuple, defaultdict
import bmesh
import bpy

from gret.math import get_sq_dist

# shape_key_apply_modifiers TODO:
# - Specialcase more merging modifiers, solidify for example
# - Transfer vertex order. Is it still necessary if all merging modifiers are covered?
#   Is it possible to identify which face went where without guessing?

class ShapeKeyInfo(namedtuple('ShapeKeyInfo', ['coords', 'interpolation', 'mute', 'name',
    'slider_max', 'slider_min', 'value', 'vertex_group'])):
    """Helper to preserve shape key information."""

    @classmethod
    def from_shape_key_with_empty_data(cls, shape_key):
        return cls(
            coords=[],
            interpolation=shape_key.interpolation,
            mute=shape_key.mute,
            name=shape_key.name,
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
        self.coords[:] = [0.0] * (len(vertices) * 3)
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

class ModifierHandler:
    """Subclass this to define special behavior when applying different modifiers."""

    type = None
    name = None

    def __init__(self, modifier):
        self.name = modifier.name

    @classmethod
    def poll(cls, modifier):
        return not cls.type or modifier.type == cls.type

    def apply(self, obj, keep_if_disabled=False):
        modifier = obj.modifiers[self.name]
        if modifier.show_viewport:
            try:
                bpy.ops.object.modifier_apply({'object': obj}, modifier=modifier.name)
            except RuntimeError:
                if not keep_if_disabled:
                    bpy.ops.object.modifier_remove({'object': obj}, modifier=modifier.name)
        elif not keep_if_disabled:
            bpy.ops.object.modifier_remove({'object': obj}, modifier=modifier.name)

class MirrorModifierHandler(ModifierHandler):
    type = 'MIRROR'
    weld_map = None  # Specifies vertex pairs to be welded

    def __init__(self, modifier):
        super().__init__(modifier)
        self.merge_dist = modifier.merge_threshold
        self.num_mirrors = sum(modifier.use_axis)

    @classmethod
    def poll(cls, modifier):
        return super().poll(modifier) and modifier.use_mirror_merge and any(modifier.use_axis)

    def apply(self, obj, keep_if_disabled=False):
        modifier = obj.modifiers[self.name]
        if not modifier.show_viewport:
            super().apply(self, obj, keep_if_disabled)
        else:
            modifier.use_mirror_merge = False
            bpy.ops.object.modifier_apply({'object': obj}, modifier=modifier.name)
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
                if get_sq_dist(vert.co, other_vert.co) <= merge_dist_sq:
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
    type = 'WELD'
    weld_map = None  # Specifies vertex pairs to be welded

    def __init__(self, modifier):
        super().__init__(modifier)
        self.merge_dist = modifier.merge_threshold
        self.vertex_group = modifier.vertex_group
        self.invert_vertex_group = modifier.invert_vertex_group

    @classmethod
    def poll(cls, modifier):
        return super().poll(modifier) and modifier.mode == 'ALL'

    def apply(self, obj, keep_if_disabled=False):
        modifier = obj.modifiers[self.name]
        if not modifier.show_viewport:
            super().apply(self, obj, keep_if_disabled)
        else:
            bpy.ops.object.modifier_remove({'object': obj}, modifier=modifier.name)
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

modifier_handler_classes = (
    MirrorModifierHandler,
    WeldModifierHandler,
    ModifierHandler,
)

class GRET_OT_shape_key_apply_modifiers(bpy.types.Operator):
    #tooltip
    """Applies viewport modifiers while preserving shape keys"""

    bl_idname = "gret.shape_key_apply_modifiers"
    bl_label = "Apply Modifiers with Shape Keys"
    bl_context = "objectmode"
    bl_options = {'REGISTER', 'UNDO'}

    keep_modifiers: bpy.props.BoolProperty(
        name="Keep Disabled Modifiers",
        description="Keep muted or disabled modifiers",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.object and context.object.type == 'MESH'

    def execute(self, context):
        obj = context.object

        if not any(mod.show_viewport for mod in obj.modifiers):
            # There are no modifiers to apply, don't do anything
            return {'FINISHED'}

        if obj.data.users > 1:
            # Make single user copy
            obj.data = obj.data.copy()

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
        for modifier in obj.modifiers[:]:
            for modifier_handler_cls in modifier_handler_classes:
                if modifier_handler_cls.poll(modifier):
                    modifier_handler = modifier_handler_cls(modifier)
                    modifier_handler.apply(obj, keep_if_disabled=self.keep_modifiers)
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
                self.report({'ERROR'}, f"Vertex count for '{shape_key.name}' did not match, "
                    "the shape key will be lost.")
                continue
            shape_key_info.put_coords_into(shape_key.data)

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

def register(settings):
    bpy.utils.register_class(GRET_OT_shape_key_apply_modifiers)
    bpy.types.MESH_MT_shape_key_context_menu.append(draw_menu)

def unregister():
    bpy.types.MESH_MT_shape_key_context_menu.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_shape_key_apply_modifiers)
