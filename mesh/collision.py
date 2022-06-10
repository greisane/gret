from itertools import chain
from math import pi, radians, sqrt, isclose
from mathutils import Matrix, Vector
import bmesh
import bpy
import re

from ..math import (
    calc_best_fit_line,
    get_dist_sq,
    get_point_dist_to_line_sq,
    get_range_pct,
)
from ..helpers import get_collection, remove_extra_data, TempModifier

# make_collision TODO:
# - Non-axis aligned boxes
# - Symmetrize for convex isn't good
# - Wall collision should try to decompose into boxes
# - Convex decomposition with v-hacd?

collision_prefixes = ("UCX", "UBX", "UCP", "USP")

def get_collision_objects(context, obj):
    pattern = r"^(?:%s)_%s_\d+$" % ('|'.join(collision_prefixes), obj.name)
    return [o for o in context.scene.objects if re.match(pattern, o.name)]

def find_free_col_name(prefix, name):
    n = 0
    while True:
        col_name = f"{prefix}_{name}_{n}"
        n += 1
        if col_name not in bpy.context.scene.objects:
            break
    return col_name

class GRET_OT_collision_assign(bpy.types.Operator):
    #tooltip
    """Assign selected collision meshes to the active object"""

    bl_idname = 'gret.collision_assign'
    bl_label = "Assign Collision"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 1 and context.object and context.mode == 'OBJECT'

    def execute(self, context):
        for obj in context.selected_objects[:]:
            if obj == context.active_object:
                continue
            prefix = obj.name[:3]
            if prefix in collision_prefixes:
                obj.name = find_free_col_name(prefix, context.active_object.name)
                if obj.data.users == 1:
                    obj.data.name = obj.name

        return {'FINISHED'}

class GRET_OT_collision_copy_to_linked(bpy.types.Operator):
    #tooltip
    """Copy collision meshes from active to linked objects"""

    bl_idname = 'gret.collision_copy_to_linked'
    bl_label = "Copy Collision to Linked"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return bool(context.active_object)

    def execute(self, context):
        obj = context.active_object
        col_objs = get_collision_objects(context, obj)
        if not col_objs:
            self.report({'WARNING'}, "Active object has no collision assigned.")
            return {'CANCELLED'}
        if obj.data.users == 1:
            self.report({'WARNING'}, "Active object data has no other users.")
            return {'CANCELLED'}

        num_linked = 0
        for other_obj in bpy.data.objects:
            if other_obj != obj and other_obj.data == obj.data:
                num_linked += 1

                # Clean collision
                for old_col_obj in get_collision_objects(context, other_obj):
                    bpy.data.objects.remove(old_col_obj, do_unlink=True)

                # Copy collision to other object's location
                obj_to_other = obj.matrix_world.inverted() @ other_obj.matrix_world
                for col_obj in col_objs:
                    name = find_free_col_name(col_obj.name[:3], other_obj.name)
                    new_col_obj = bpy.data.objects.new(name, col_obj.data)
                    new_col_obj.matrix_world = (other_obj.matrix_world @
                        (obj.matrix_world.inverted() @ col_obj.matrix_world))
                    new_col_obj.show_wire = col_obj.show_wire
                    new_col_obj.display_type = col_obj.display_type
                    new_col_obj.display.show_shadows = col_obj.display.show_shadows
                    for collection in col_obj.users_collection:
                        collection.objects.link(new_col_obj)

        self.report({'INFO'}, f"Copied collision to {num_linked} other objects.")
        return {'FINISHED'}

def is_box(bm):
    """Check if the mesh can be represented by a box collision shape."""

    if len(bm.verts) != 8:
        return False
    c = sum((vert.co for vert in bm.verts), Vector()) / len(bm.verts)
    avg_d_sq = sum(get_dist_sq(vert.co, c) for vert in bm.verts) / len(bm.verts)
    return all(isclose(avg_d_sq, get_dist_sq(vert.co, c), abs_tol=0.0001) for vert in bm.verts)

class GRET_OT_collision_make(bpy.types.Operator):
    #tooltip
    """Generate collision for the selected geometry"""

    bl_idname = 'gret.collision_make'
    bl_label = "Make Collision"
    bl_options = {'REGISTER', 'UNDO'}

    shape: bpy.props.EnumProperty(
        name="Shape",
        description="Selects the collision shape",
        items=[
            ('AABB', "AABB", "Axis-aligned box collision.", 'MESH_PLANE', 0),
            ('CYLINDER', "Cylinder", "Cylinder collision.", 'MESH_CYLINDER', 1),
            ('CAPSULE', "Capsule", "Capsule collision.", 'MESH_CAPSULE', 2),
            ('SPHERE', "Sphere", "Sphere collision.", 'MESH_UVSPHERE', 3),
            ('CONVEX', "Convex", "Convex collision.", 'MESH_ICOSPHERE', 4),
            ('WALL', "Wall", "Wall collision.", 'MOD_SOLIDIFY', 5),
        ],
    )
    collection: bpy.props.StringProperty(
        name="Collection",
        description="Name of the collection for the collision objects",
        default="Collision",
    )
    wire: bpy.props.BoolProperty(
        name="Wire",
        description="How to display the collision objects in viewport",
        default=False,
    )
    hollow: bpy.props.BoolProperty(
        name="Hollow",
        description="Create a hollow shape from multiple bodies",
        default=False,
    )
    thickness: bpy.props.FloatProperty(
        name="Thickness",
        description="Wall thickness",
        subtype='DISTANCE',
        default=0.2,
        min=0.001,
    )
    offset: bpy.props.FloatProperty(
        name="Offset",
        description="Offset the thickness from the center",
        subtype='DISTANCE',
        default=-1.0,
        min=-1.0,
        max=1.0,
    )
    location: bpy.props.FloatVectorProperty(
        name="Location",
        description="Shape location",
        subtype='TRANSLATION',
        size=3,
    )

    # AABB settings
    aabb_width: bpy.props.FloatProperty(
        name="Width",
        description="Bounding box width",
        subtype='DISTANCE',
        min=0.001,
    )
    aabb_height: bpy.props.FloatProperty(
        name="Height",
        description="Bounding box height",
        subtype='DISTANCE',
        min=0.001,
    )
    aabb_depth: bpy.props.FloatProperty(
        name="Depth",
        description="Bounding box depth",
        subtype='DISTANCE',
        min=0.001,
    )

    # Cylinder settings
    cyl_caps: bpy.props.BoolProperty(
        name="Caps",
        description="Create shapes for the top and bottom of the cylinder",
        default=False,
    )
    cyl_sides: bpy.props.IntProperty(
        name="Sides",
        description="Number of sides",
        default=8,
        min=3,
    )
    cyl_rotate: bpy.props.BoolProperty(
        name="Rotate",
        description="Rotate cylinder by half",
    )
    cyl_radius1: bpy.props.FloatProperty(
        name="Radius 1",
        description="First cylinder radius",
        subtype='DISTANCE',
        min=0.001,
    )
    cyl_radius2: bpy.props.FloatProperty(
        name="Radius 2",
        description="Second cylinder radius",
        subtype='DISTANCE',
        min=0.001,
    )
    cyl_height: bpy.props.FloatProperty(
        name="Height",
        description="Cylinder height",
        subtype='DISTANCE',
        min=0.001,
    )

    # Capsule settings
    cap_radius: bpy.props.FloatProperty(
        name="Radius",
        description="Capsule radius",
        subtype='DISTANCE',
        min=0.001,
    )
    cap_depth: bpy.props.FloatProperty(
        name="Depth",
        description="Capsule depth",
        subtype='DISTANCE',
        min=0.001,
    )
    cap_rotation: bpy.props.FloatVectorProperty(
        name="Rotation",
        description="Capsule rotation",
        subtype='EULER',
        size=3,
    )

    # Sphere settings
    sph_radius: bpy.props.FloatProperty(
        name="Radius",
        description="Sphere radius",
        subtype='DISTANCE',
        min=0.001,
    )

    # Convex settings
    planar_angle: bpy.props.FloatProperty(
        name="Max Face Angle",
        description="Use to remove decimation bias towards large, bumpy faces",
        subtype='ANGLE',
        default=radians(10.0),
        min=0.0,
        max=radians(180.0),
        soft_max=radians(90.0),
    )
    decimate_ratio: bpy.props.FloatProperty(
        name="Decimate Ratio",
        description="Percentage of edges to collapse",
        subtype='FACTOR',
        default=1.0,
        min=0.0,
        max=1.0,
    )
    use_symmetry: bpy.props.BoolProperty(
        name="Symmetry",
        description="Maintain symmetry on an axis",
        default=False,
    )
    symmetry_axis: bpy.props.EnumProperty(
        name="Symmetry Axis",
        description="Axis of symmetry",
        items=[
            ('X', "X", "X"),
            ('Y', "Y", "Y"),
            ('Z', "Z", "Z"),
        ],
    )

    # Wall settings
    wall_fill_holes: bpy.props.BoolProperty(
        name="Fill holes",
        description="Fill rectangular holes in walls",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and obj.mode in {'OBJECT', 'EDIT'}

    def create_col_object_from_bm(self, context, obj, bm, prefix=None):
        if not prefix:
            # Autodetect (should detect sphere too)
            prefix = "UBX" if is_box(bm) else "UCX"

        name = find_free_col_name(prefix, obj.name)
        data = bpy.data.meshes.new(name)
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        bm.to_mesh(data)

        col_obj = bpy.data.objects.new(name, data)
        col_obj.matrix_world = obj.matrix_world
        col_obj.show_wire = True
        col_obj.display_type = 'WIRE' if self.wire else 'SOLID'
        col_obj.display.show_shadows = False
        # bmeshes created with from_mesh or from_object may have some UVs or customdata
        remove_extra_data(col_obj)

        # Link to scene
        if not self.collection:
            collection = context.scene.collection
        else:
            collection = get_collection(context, self.collection, allow_duplicate=True, clean=False)
            collection.color_tag = 'COLOR_04'
        collection.objects.link(col_obj)
        return col_obj

    def create_split_col_object_from_bm(self, context, obj, bm, thickness, offset=0.0):
        # Based on https://github.com/blender/blender-addons/blob/master/mesh_tools/split_solidify.py
        # by zmj100, updated by zeffii to BMesh
        distance = thickness * (offset + 1.0) * 0.5
        src_bm = bm
        src_bm.faces.ensure_lookup_table()
        src_bm.verts.ensure_lookup_table()
        src_bm.normal_update()
        for src_f in src_bm.faces:
            bm = bmesh.new()
            # Add new vertices
            vs1 = []
            vs2 = []
            for v in src_f.verts:
                p1 = v.co + src_f.normal * distance  # Out
                p2 = v.co + src_f.normal * (distance - thickness)  # In
                vs1.append(bm.verts.new(p1))
                vs2.append(bm.verts.new(p2))

            # Add new faces
            n = len(vs1)
            bm.faces.new(vs1)
            for i in range(n):
                j = (i + 1) % n
                vseq = vs1[i], vs2[i], vs2[j], vs1[j]
                bm.faces.new(vseq)
            vs2.reverse()
            bm.faces.new(vs2)

            self.create_col_object_from_bm(context, obj, bm)
            bm.free()

    def make_aabb_collision(self, context, obj):
        v = Vector((self.aabb_depth, self.aabb_width, self.aabb_height)) * 0.5

        bm = bmesh.new()
        verts = bmesh.ops.create_cube(bm, calc_uvs=False)['verts']
        verts[0].co = self.location.x - v.x, self.location.y - v.y, self.location.z - v.z
        verts[1].co = self.location.x - v.x, self.location.y - v.y, self.location.z + v.z
        verts[2].co = self.location.x - v.x, self.location.y + v.y, self.location.z - v.z
        verts[3].co = self.location.x - v.x, self.location.y + v.y, self.location.z + v.z
        verts[4].co = self.location.x + v.x, self.location.y - v.y, self.location.z - v.z
        verts[5].co = self.location.x + v.x, self.location.y - v.y, self.location.z + v.z
        verts[6].co = self.location.x + v.x, self.location.y + v.y, self.location.z - v.z
        verts[7].co = self.location.x + v.x, self.location.y + v.y, self.location.z + v.z

        if self.hollow:
            self.create_split_col_object_from_bm(context, obj, bm, self.thickness, self.offset)
        else:
            self.create_col_object_from_bm(context, obj, bm)
        bm.free()

    def make_cylinder_collision(self, context, obj):
        mat = Matrix.Translation(self.location)
        if self.cyl_rotate:
            mat @= Matrix.Rotation(pi / self.cyl_sides, 4, 'Z')
        bm = bmesh.new()
        cap_ends = not self.hollow or self.cyl_caps
        bmesh.ops.create_cone(bm, cap_ends=cap_ends, cap_tris=False, segments=self.cyl_sides,
            radius1=self.cyl_radius1, radius2=self.cyl_radius2, depth=self.cyl_height,
            calc_uvs=False, matrix=mat)
        if self.hollow:
            self.create_split_col_object_from_bm(context, obj, bm, self.thickness, self.offset)
        else:
            self.create_col_object_from_bm(context, obj, bm)
        bm.free()

    def make_capsule_collision(self, context, obj):
        mat = Matrix.Translation(self.location) @ self.cap_rotation.to_matrix().to_4x4()
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=8,
            radius1=self.cap_radius, radius2=self.cap_radius, depth=self.cap_depth,
            calc_uvs=False, matrix=mat)
        bm.faces.ensure_lookup_table()
        caps = [bm.faces[-1], bm.faces[-4]]
        bmesh.ops.poke(bm, faces=caps, offset=self.cap_radius)
        self.create_col_object_from_bm(context, obj, bm, "UCP")
        bm.free()

    def make_sphere_collision(self, context, obj):
        mat = Matrix.Translation(self.location)
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=2, radius=self.sph_radius*0.5,
            calc_uvs=False, matrix=mat)
        self.create_col_object_from_bm(context, obj, bm, "USP")
        bm.free()

    def make_convex_collision(self, context, obj):
        if context.mode == 'EDIT_MESH':
            bm = bmesh.from_edit_mesh(obj.data).copy()
            bm.verts.ensure_lookup_table()
            bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.select], context='VERTS')
        else:
            bm = bmesh.new()
            dg = context.evaluated_depsgraph_get()
            bm.from_object(obj, dg)

        # Clean incoming mesh
        bm.edges.ensure_lookup_table()
        for edge in bm.edges:
            edge.seam = False
            edge.smooth = True
        bm.faces.ensure_lookup_table()
        for face in bm.faces:
            face.smooth = False

        # While convex_hull works only on verts, pass all the geometry so that it gets tagged
        geom = list(chain(bm.verts, bm.edges, bm.faces))
        result = bmesh.ops.convex_hull(bm, input=geom, use_existing_faces=True)
        # geom_interior: elements that ended up inside the hull rather than part of it
        # geom_unused: elements that ended up inside the hull and are are unused by other geometry
        # The two sets may intersect, so for now just delete one of them. I haven't found a case yet
        # where this leaves out unwanted geometry
        bmesh.ops.delete(bm, geom=result['geom_interior'], context='TAGGED_ONLY')
        bm.normal_update()
        bmesh.ops.dissolve_limit(bm, angle_limit=self.planar_angle,
            verts=bm.verts, edges=bm.edges, use_dissolve_boundaries=False, delimit=set())
        bmesh.ops.triangulate(bm, faces=bm.faces)
        col_obj = self.create_col_object_from_bm(context, obj, bm, "UCX")
        bm.free()

        # Decimate (no bmesh op for this currently?)
        with TempModifier(col_obj, type='DECIMATE') as dec_mod:
            dec_mod.ratio = self.decimate_ratio
            dec_mod.use_symmetry = self.use_symmetry
            dec_mod.symmetry_axis = self.symmetry_axis

    def make_wall_collision(self, context, obj):
        if context.mode == 'EDIT_MESH':
            bm = bmesh.from_edit_mesh(obj.data).copy()
            bm.verts.ensure_lookup_table()
            bmesh.ops.delete(bm, geom=[v for v in bm.verts if not v.select], context='VERTS')
        else:
            bm = bmesh.new()
            dg = context.evaluated_depsgraph_get()
            bm.from_object(obj, dg)

        if self.wall_fill_holes:
            result = bmesh.ops.holes_fill(bm, edges=bm.edges, sides=4)
            hole_edges = list(chain.from_iterable(f.edges for f in result['faces']))
            bmesh.ops.dissolve_edges(bm, edges=hole_edges, use_verts=True)
        bmesh.ops.split_edges(bm, edges=bm.edges)
        bmesh.ops.dissolve_limit(bm, angle_limit=radians(5.0),
            verts=bm.verts, edges=bm.edges, use_dissolve_boundaries=False, delimit=set())

        self.create_split_col_object_from_bm(context, obj, bm, self.thickness, self.offset)
        bm.free()

    def execute(self, context):
        obj = context.active_object

        if obj.mode != 'EDIT':
            # When working from object mode, it follows that there should be only one collision shape
            pattern = re.compile(rf"^U[A-Z][A-Z]_{obj.name}_\d+")
            for mesh in [mesh for mesh in bpy.data.meshes if pattern.match(mesh.name)]:
                bpy.data.meshes.remove(mesh)

        if self.shape == 'AABB':
            self.make_aabb_collision(context, obj)
        elif self.shape == 'CYLINDER':
            self.make_cylinder_collision(context, obj)
        elif self.shape == 'CAPSULE':
            self.make_capsule_collision(context, obj)
        elif self.shape == 'SPHERE':
            self.make_sphere_collision(context, obj)
        elif self.shape == 'CONVEX':
            self.make_convex_collision(context, obj)
        elif self.shape == 'WALL':
            self.make_wall_collision(context, obj)

        return {'FINISHED'}

    def invoke(self, context, event):
        # Calculate initial properties
        try:
            self.calculate_parameters(context, context.object)
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        # Ideally this would execute once then show the popup dialog, doesn't seem possible
        return context.window_manager.invoke_props_dialog(self)

    def calculate_parameters(self, context, obj):
        if obj.mode == 'EDIT':
            bm = bmesh.from_edit_mesh(obj.data)
            vert_cos = [vert.co for vert in bm.verts if vert.select]
        else:
            dg = context.evaluated_depsgraph_get()
            obj_eval = obj.evaluated_get(dg)
            vert_cos = [vert.co for vert in obj_eval.data.vertices]
        if len(vert_cos) < 3:
            raise RuntimeError("Requires at least three vertices")

        axis, _ = calc_best_fit_line(vert_cos)

        corner1 = vert_cos[0].copy()
        corner2 = vert_cos[1].copy()
        for co in vert_cos:
            corner1.x = min(corner1.x, co.x)
            corner1.y = min(corner1.y, co.y)
            corner1.z = min(corner1.z, co.z)
            corner2.x = max(corner2.x, co.x)
            corner2.y = max(corner2.y, co.y)
            corner2.z = max(corner2.z, co.z)

        # Bounding box dimensions
        self.aabb_depth = abs(corner1.x - corner2.x)
        self.aabb_width = abs(corner1.y - corner2.y)
        self.aabb_height = abs(corner1.z - corner2.z)
        self.location = center = (corner1 + corner2) * 0.5

        # Cylinder radius
        self.cyl_radius1 = self.cyl_radius2 = 0.001
        for co in vert_cos:
            dx = center.x - co.x
            dy = center.y - co.y
            d = sqrt(dx * dx + dy * dy)
            influence2 = get_range_pct(corner1.z, corner2.z, co.z)
            influence1 = 1.0 - influence2
            self.cyl_radius1 = max(self.cyl_radius1, d * influence1)
            self.cyl_radius2 = max(self.cyl_radius2, d * influence2)
        self.cyl_height = self.aabb_height

        # Capsule axis and radius
        radius_sq = 0.001
        depth_sq = 0.0
        for co in vert_cos:
            dist_to_axis_sq = get_point_dist_to_line_sq(co, axis, center)
            if dist_to_axis_sq > radius_sq:
                radius_sq = dist_to_axis_sq
            dist_along_axis_sq = (co - center).project(axis).length_squared
            if dist_along_axis_sq > depth_sq:
                depth_sq = dist_along_axis_sq
        self.cap_radius = sqrt(radius_sq)
        self.cap_rotation = axis.to_track_quat('Z', 'X').to_euler('XYZ')
        self.cap_depth = sqrt(depth_sq) * 2.0 - self.cap_radius

        # Sphere radius
        self.sph_radius = max(self.aabb_depth, self.aabb_width, self.aabb_height)

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True

        col = layout.column()
        row = col.row(align=True)
        row.prop(self, 'shape')
        row.prop(self, 'wire', icon='MOD_WIREFRAME', text="")
        if self.shape in {'AABB', 'CYLINDER'}:
            col.separator()
            col.prop(self, 'hollow')
            if self.hollow:
                col.prop(self, 'thickness')
                col.prop(self, 'offset')

        if self.shape == 'AABB':
            col.prop(self, 'aabb_width')
            col.prop(self, 'aabb_height')
            col.prop(self, 'aabb_depth')
        elif self.shape == 'CYLINDER':
            col.prop(self, 'cyl_rotate')
            col.prop(self, 'cyl_sides')
            col.prop(self, 'cyl_radius1')
            col.prop(self, 'cyl_radius2')
            col.prop(self, 'cyl_height')
            col.prop(self, 'cyl_caps')
        elif self.shape == 'CAPSULE':
            col.prop(self, 'cap_radius')
            col.prop(self, 'cap_depth')
        elif self.shape == 'SPHERE':
            col.prop(self, 'sph_radius')
        elif self.shape == 'CONVEX':
            col.prop(self, 'planar_angle')
            col.prop(self, 'decimate_ratio')
            row = col.row(align=True, heading="Symmetrize")
            row.prop(self, 'use_symmetry', text="")
            row.prop(self, 'symmetry_axis', expand=True)
        elif self.shape == 'WALL':
            col.prop(self, 'thickness')
            col.prop(self, 'offset')
            col.prop(self, 'wall_fill_holes')

def draw_panel(self, context):
    layout = self.layout

    box = layout.box()
    col = box.column(align=False)
    col.label(text="Collision", icon='MESH_CUBE')

    row = col.row(align=True)
    row.operator('gret.collision_make', text="Make")
    row.operator('gret.collision_assign', text="Assign")

classes = (
    GRET_OT_collision_assign,
    GRET_OT_collision_copy_to_linked,
    GRET_OT_collision_make,
)

def register(settings, prefs):
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
