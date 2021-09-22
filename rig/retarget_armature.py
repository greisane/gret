from math import ceil
import bpy
import numpy as np

from ..log import log, logd, logger
from ..rbf import *

class GRET_OT_retarget_armature(bpy.types.Operator):
    #tooltip
    """Retarget an armature or selected bones to fit a modified version of the source mesh."""
    # Note: If vertex order gets messed up, try using an addon like Transfer Vert Order to fix it

    bl_idname = 'gret.retarget_armature'
    bl_label = "Retarget Armature"
    bl_options = {'INTERNAL', 'UNDO'}

    source: bpy.props.StringProperty(
        name="Source",
        description="Source mesh object that the meshes were originally fit to",
    )
    destination: bpy.props.StringProperty(
        name="Destination",
        description="Modified mesh object to retarget to",
    )
    use_shape_key: bpy.props.BoolProperty(
        name="Use Shape Key",
        description="Destination is the name of a shape key in the source mesh",
        default=False,
    )
    function: bpy.props.EnumProperty(
        items=[
            ('LINEAR', "Linear", "Linear function"),
            ('GAUSSIAN', "Gaussian", "Gaussian function"),
            ('PLATE', "Thin Plate", "Thin plate function"),
            ('BIHARMONIC', "Biharmonic", "Multi quadratic biharmonic"),
            ('INV_BIHARMONIC', "Inverse Biharmonic", "Inverse multi quadratic biharmonic"),
            ('C2', "C2", "Beckert-Wendland C2 basis"),
        ],
        name="Function",
        description="Radial basis function kernel",
        default='BIHARMONIC',  # Least prone to explode and not too slidy
    )
    radius: bpy.props.FloatProperty(
        name="Radius",
        description="Smoothing parameter for the radial basis function",
        subtype='DISTANCE',
        default=0.5,
        min=0.0,
    )
    only_selection: bpy.props.BoolProperty(
        name="Only Vertex Selection",
        description="""Sample only the current vertex selection of the source mesh.
Use to speed up retargeting by selecting only the areas of importance""",
        default=False,
    )
    high_quality: bpy.props.BoolProperty(
        name="High Quality",
        description="Sample more vertices for higher accuracy. Slow on dense meshes",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.mode in {'OBJECT', 'EDIT_ARMATURE'} and context.selected_objects

    def execute(self, context):
        objs = context.selected_objects
        src_obj = bpy.data.objects.get(self.source)
        dst_is_shape_key = self.destination.startswith('s_')
        dst_obj = bpy.data.objects.get(self.destination[2:]) if not dst_is_shape_key else src_obj
        dst_shape_key_name = self.destination[2:] if dst_is_shape_key else None
        assert src_obj and dst_obj and src_obj.type == 'MESH' and dst_obj.type == 'MESH'

        num_vertices = len(src_obj.data.vertices)
        if num_vertices == 0:
            self.report({'ERROR'}, "Source mesh has no vertices.")
            return {'CANCELLED'}
        if num_vertices != len(dst_obj.data.vertices):
            self.report({'ERROR'}, "Source and destination meshes must have equal number of vertices.")
            return {'CANCELLED'}

        # Increase vertex sampling stride to speed up calculation (reduces accuracy)
        # A cap is still necessary since growth is not linear and it will take forever. Parallelize?
        # In practice sampling many vertices in a dense mesh doesn't change the result that much
        vertex_cap = 5000 if self.high_quality else 1000
        mask = [v.select for v in src_obj.data.vertices] if self.only_selection else None
        num_masked = sum(mask) if mask else num_vertices
        stride = ceil(num_masked / vertex_cap)
        if num_masked == 0:
            self.report({'ERROR'}, "Source mesh has no vertices selected.")
            return {'CANCELLED'}
        logd(f"num_verts={num_masked}/{num_vertices} stride={stride} total={num_masked//stride}")

        rbf_kernel, scale = rbf_kernels.get(self.function, (linear, 1.0))
        src_pts = get_mesh_points(src_obj, mask=mask, stride=stride)
        dst_pts = get_mesh_points(dst_obj, shape_key=dst_shape_key_name, mask=mask, stride=stride)
        try:
            weights = get_weight_matrix(src_pts, dst_pts, rbf_kernel, self.radius * scale)
        except np.linalg.LinAlgError:
            # Solving for C2 kernel may throw 'SVD did not converge' sometimes
            self.report({'ERROR'}, "Failed to retarget. Try a different function or radius.")
            return {'CANCELLED'}

        for obj in objs:
            if obj.type != 'ARMATURE':
                continue
            is_editing = obj.mode == 'EDIT'
            if not is_editing:
                bpy.ops.object.editmode_toggle()

            # Get the mesh points in retarget destination space
            dst_to_obj = obj.matrix_world.inverted() @ dst_obj.matrix_world
            obj_to_dst = dst_to_obj.inverted()
            bone_pts = get_armature_points(obj, matrix=obj_to_dst, only_selected=is_editing)
            num_bone_pts = bone_pts.shape[0]
            if num_bone_pts == 0:
                continue

            dist = get_distance_matrix(bone_pts, src_pts, rbf_kernel, self.radius * scale)
            identity = np.ones((num_bone_pts, 1))
            h = np.bmat([[dist, identity, bone_pts]])
            new_bone_pts = np.asarray(np.dot(h, weights))

            # Result back to local space
            new_bone_pts = np.c_[new_bone_pts, identity]
            new_bone_pts = np.einsum('ij,aj->ai', dst_to_obj, new_bone_pts)
            new_bone_pts = new_bone_pts[:, :-1]

            index = 0
            for bone in obj.data.edit_bones:
                if not is_editing or bone.select_head:
                    bone.head[:] = new_bone_pts[index]
                    index += 1
                if not is_editing or bone.select_tail:
                    bone.tail[:] = new_bone_pts[index]
                    index += 1
            if not is_editing:
                bpy.ops.object.editmode_toggle()

        return {'FINISHED'}

def draw_panel(self, context):
    layout = self.layout
    settings = context.scene.gret
    obj = context.object

    box = layout.box()
    box.label(text="Retarget Armature", icon='MOD_MESHDEFORM')
    col = box.column(align=False)

    row = col.row(align=True)
    row.prop(settings, 'retarget_src', text="")
    row.label(text="", icon='FORWARD')
    row.prop(settings, 'retarget_dst', text="")

    row = col.row(align=True)
    row.prop(settings, 'retarget_function', text="")
    row.prop(settings, 'retarget_radius', text="")

    col.prop(settings, 'retarget_only_selection')
    col.prop(settings, 'retarget_high_quality')

    if obj and obj.data and getattr(obj.data, 'use_mirror_x', False):
        col.label(text="X-Axis Mirror is enabled.")

    row = col.row(align=True)
    op = row.operator('gret.retarget_armature', icon='CHECKMARK', text="Retarget")
    if settings.retarget_src and settings.retarget_dst != 'NONE':
        op.source = settings.retarget_src.name
        op.destination = settings.retarget_dst
        op.function = settings.retarget_function
        op.radius = settings.retarget_radius
        op.only_selection = settings.retarget_only_selection
        op.high_quality = settings.retarget_high_quality
    else:
        row.enabled = False

def retarget_src_update(self, context):
    # On changing the source object, reset the destination object
    context.scene.gret.retarget_dst = 'NONE'

items = []
def retarget_dst_items(self, context):
    # Return shape keys of the source object and mesh objects with the same amount of vertices
    settings = context.scene.gret
    src_obj = settings.retarget_src

    items.clear()
    items.append(('NONE', "", ""))
    if src_obj:
        src_mesh = src_obj.data
        for o in context.scene.objects:
            if o.type == 'MESH' and o != src_obj and len(o.data.vertices) == len(src_mesh.vertices):
                items.append(('o_' + o.name, o.name, f"Object '{o.name}'", 'OBJECT_DATA', len(items)))
        if src_mesh.shape_keys:
            for sk in src_mesh.shape_keys.key_blocks:
                items.append(('s_' + sk.name, sk.name, f"Shape Key '{sk.name}'", 'SHAPEKEY_DATA', len(items)))
    return items

def register(settings):
    bpy.utils.register_class(GRET_OT_retarget_armature)

    settings.add_property('retarget_src', bpy.props.PointerProperty(
        name="Mesh Retarget Source",
        description="Base mesh that the meshes are fit to",
        type=bpy.types.Object,
        poll=lambda self, obj: obj and obj.type == 'MESH',
        update=retarget_src_update,
    ))
    settings.add_property('retarget_dst', bpy.props.EnumProperty(
        name="Mesh Retarget Destination",
        description="""Mesh or shape key to retarget to.
Expected to share topology and vertex order with the source mesh""",
        items=retarget_dst_items,
    ))
    retarget_props = GRET_OT_retarget_armature.__annotations__
    settings.add_property('retarget_function', retarget_props['function'])
    settings.add_property('retarget_radius', retarget_props['radius'])
    settings.add_property('retarget_only_selection', retarget_props['only_selection'])
    settings.add_property('retarget_high_quality', retarget_props['high_quality'])

def unregister():
    bpy.utils.unregister_class(GRET_OT_retarget_armature)
