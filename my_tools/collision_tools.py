from itertools import chain
from mathutils import Matrix, Vector
import bmesh
import bpy
import math
import re
from .math_helpers import (
    get_best_fit_line,
    get_point_dist_to_line,
    get_range_pct,
    get_sq_dist,
)
from .helpers import (
    remove_extra_data,
    select_only,
)

# make_collision TODO:
# - When creating collision from vertices, sometimes the result is offset
# - Multiple objects. Should take away the shape properties and leave only the type
# - Non-axis aligned boxes
# - Symmetrize for convex isn't good
# - Wall collision should try to decompose into boxes

def find_free_col_name(prefix, name):
    n = 0
    while True:
        col_name = f"{prefix}_{name}_{n}"
        n += 1
        if col_name not in bpy.context.scene.objects:
            break
    return col_name

class MY_OT_assign_collision(bpy.types.Operator):
    #tooltip
    """Assigns (renames) the selected collision meshes to the active object"""

    bl_idname = 'my_tools.assign_collision'
    bl_label = "Assign Collision"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return len(context.selected_objects) > 1 and context.object and context.mode == 'OBJECT'

    def execute(self, context):
        collision_prefixes = ("UCX", "UBX", "UCP", "USP")
        for obj in context.selected_objects[:]:
            if obj == context.active_object:
                continue
            prefix = obj.name[:3]
            if prefix in collision_prefixes:
                obj.name = find_free_col_name(prefix, context.active_object.name)

        return {'FINISHED'}

def is_box(bm, sq_threshold=0.001):
    """Check if the shape can be represented by a box by checking if there's a vertex opposite
across the center for each vertex, within some threshold"""
    if len(bm.verts) != 8:
        return False
    center = sum((v.co for v in bm.verts), Vector()) / 8
    for v1 in bm.verts:
        co2 = (center - v1.co) + center
        if not any(get_sq_dist(v2.co, co2) <= sq_threshold for v2 in bm.verts):
            return False
    return True

class MY_OT_make_collision(bpy.types.Operator):
    #tooltip
    """Generate collision for the selected geometry"""

    bl_idname = 'my_tools.make_collision'
    bl_label = "Make Collision"
    bl_options = {'REGISTER', 'UNDO'}

    shape: bpy.props.EnumProperty(
        items=[
            ('BOX', "Box", "Box collision.", 'MESH_CUBE', 0),
            ('CYLINDER', "Cylinder", "Cylinder collision.", 'MESH_CYLINDER', 1),
            ('CAPSULE', "Capsule", "Capsule collision.", 'MESH_CAPSULE', 2),
            ('SPHERE', "Sphere", "Sphere collision.", 'MESH_UVSPHERE', 3),
            ('CONVEX', "Convex", "Convex collision.", 'MESH_ICOSPHERE', 4),
            ('WALL', "Wall", "Wall collision.", 'MOD_SOLIDIFY', 5),
        ],
        name="Shape",
        description="Selects the collision shape",
    )
    collection: bpy.props.StringProperty(
        name="Collection",
        description="Name of the collection to link the collision objects to",
        default="Collision",
    )
    wire: bpy.props.BoolProperty(
        name="Wire",
        description="How to display the collision objects in viewport",
        default=True,
    )
    hollow: bpy.props.BoolProperty(
        name="Hollow",
        description="Creates a hollow shape from multiple bodies",
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

    # Box settings
    box_width: bpy.props.FloatProperty(
        name="Width",
        description="Box width",
        subtype='DISTANCE',
        min=0.001,
    )
    box_height: bpy.props.FloatProperty(
        name="Height",
        description="Box height",
        subtype='DISTANCE',
        min=0.001,
    )
    box_depth: bpy.props.FloatProperty(
        name="Depth",
        description="Box depth",
        subtype='DISTANCE',
        min=0.001,
    )
    box_center: bpy.props.FloatVectorProperty(
        name="Center",
        description="Box center",
        subtype='TRANSLATION',
        size=3,
    )

    # Cylinder settings
    cyl_sides: bpy.props.IntProperty(
        name="Sides",
        description="Number of sides",
        default=8,
        min=3,
    )
    cyl_diameter1: bpy.props.FloatProperty(
        name="Diameter 1",
        description="First cylinder diameter",
        subtype='DISTANCE',
        min=0.001,
    )
    cyl_diameter2: bpy.props.FloatProperty(
        name="Diameter 2",
        description="Second cylinder diameter",
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
    cap_diameter: bpy.props.FloatProperty(
        name="Diameter",
        description="Capsule diameter",
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
    sph_diameter: bpy.props.FloatProperty(
        name="Diameter",
        description="Sphere diameter",
        subtype='DISTANCE',
        min=0.001,
    )

    # Convex settings
    planar_angle: bpy.props.FloatProperty(
        name="Max Face Angle",
        description="Use to remove decimation bias towards large, bumpy faces",
        subtype='ANGLE',
        default=math.radians(10.0),
        min=0.0,
        max=math.radians(180.0),
        soft_max=math.radians(90.0),
    )
    decimate_ratio: bpy.props.FloatProperty(
        name="Decimate Ratio",
        description="Percentage of edges to collapse",
        default=1.0,
        min=0.0,
        max=1.0,
    )
    x_symmetry: bpy.props.BoolProperty(
        name="X Symmetry",
        description="Symmetrize across X axis",
        default=False,
    )
    y_symmetry: bpy.props.BoolProperty(
        name="Y Symmetry",
        description="Symmetrize across Y axis",
        default=False,
    )
    z_symmetry: bpy.props.BoolProperty(
        name="Z Symmetry",
        description="Symmetrize across Z axis",
        default=False,
    )

    # Wall settings
    wall_fill_holes: bpy.props.BoolProperty(
        name="Fill holes",
        description="Fill rectangular holes in walls",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return (context.object
            and context.object.type == 'MESH'
            and context.mode in {'OBJECT', 'EDIT_MESH'})

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
        elif self.collection in bpy.data.collections:
            collection = bpy.data.collections[self.collection]
        else:
            collection = bpy.data.collections.new(self.collection)
            context.scene.collection.children.link(collection)
        if bpy.app.version >= (2, 91):
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

    def make_box_collision(self, context, obj):
        v = Vector((self.box_depth, self.box_width, self.box_height)) * 0.5

        bm = bmesh.new()
        verts = bmesh.ops.create_cube(bm, calc_uvs=False)['verts']
        verts[0].co = self.box_center.x - v.x, self.box_center.y - v.y, self.box_center.z - v.z
        verts[1].co = self.box_center.x - v.x, self.box_center.y - v.y, self.box_center.z + v.z
        verts[2].co = self.box_center.x - v.x, self.box_center.y + v.y, self.box_center.z - v.z
        verts[3].co = self.box_center.x - v.x, self.box_center.y + v.y, self.box_center.z + v.z
        verts[4].co = self.box_center.x + v.x, self.box_center.y - v.y, self.box_center.z - v.z
        verts[5].co = self.box_center.x + v.x, self.box_center.y - v.y, self.box_center.z + v.z
        verts[6].co = self.box_center.x + v.x, self.box_center.y + v.y, self.box_center.z - v.z
        verts[7].co = self.box_center.x + v.x, self.box_center.y + v.y, self.box_center.z + v.z

        if self.hollow:
            self.create_split_col_object_from_bm(context, obj, bm, self.thickness, self.offset)
        else:
            self.create_col_object_from_bm(context, obj, bm)
        bm.free()

    def make_cylinder_collision(self, context, obj):
        mat = Matrix.Translation(self.location)
        bm = bmesh.new()
        bmesh.ops.create_cone(bm, cap_ends=True, cap_tris=False, segments=self.cyl_sides,
            diameter1=self.cyl_diameter1, diameter2=self.cyl_diameter2, depth=self.cyl_height,
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
            diameter1=self.cap_diameter, diameter2=self.cap_diameter, depth=self.cap_depth,
            calc_uvs=False, matrix=mat)
        bm.faces.ensure_lookup_table()
        caps = [bm.faces[-1], bm.faces[-4]]
        bmesh.ops.poke(bm, faces=caps, offset=self.cap_diameter)
        self.create_col_object_from_bm(context, obj, bm, "UCP")
        bm.free()

    def make_sphere_collision(self, context, obj):
        mat = Matrix.Translation(self.location)
        bm = bmesh.new()
        bmesh.ops.create_icosphere(bm, subdivisions=2, diameter=self.sph_diameter * 0.5,
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

        # While convex_hull works only on verts, pass all the geometry so it gets tagged
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
        context.view_layer.objects.active = col_obj
        obj.select_set(False)
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.decimate(ratio=self.decimate_ratio)

        # Symmetrize
        if self.x_symmetry:
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.symmetrize(direction='POSITIVE_X')
        if self.y_symmetry:
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.symmetrize(direction='POSITIVE_Y')
        if self.z_symmetry:
            bpy.ops.mesh.select_all(action='SELECT')
            bpy.ops.mesh.symmetrize(direction='POSITIVE_Z')

        bpy.ops.object.mode_set(mode='OBJECT')

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
        bmesh.ops.dissolve_limit(bm, angle_limit=math.radians(5.0),
            verts=bm.verts, edges=bm.edges, use_dissolve_boundaries=False, delimit=set())

        self.create_split_col_object_from_bm(context, obj, bm, self.thickness, self.offset)
        bm.free()

    def execute(self, context):
        for obj in context.selected_objects[:]:
            if context.mode != 'EDIT_MESH':
                pattern = re.compile(rf"^U[A-Z][A-Z]_{obj.name}_\d+")
                for mesh in [mesh for mesh in bpy.data.meshes if pattern.match(mesh.name)]:
                    bpy.data.meshes.remove(mesh)
            select_only(context, obj)
            if self.shape == 'BOX':
                self.make_box_collision(context, obj)
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
        return context.window_manager.invoke_props_dialog(self)

    def calculate_parameters(self, context, obj):
        if context.mode == 'EDIT_MESH':
            bm = bmesh.from_edit_mesh(obj.data)
            vert_cos = [vert.co for vert in bm.verts if vert.select]
        else:
            dg = context.evaluated_depsgraph_get()
            obj_eval = obj.evaluated_get(dg)
            vert_cos = [vert.co for vert in obj_eval.data.vertices]
        if len(vert_cos) < 3:
            raise RuntimeError("Requires at least three vertices")

        axis, center = get_best_fit_line(vert_cos)
        self.location = center

        corner1 = vert_cos[0].copy()
        corner2 = vert_cos[1].copy()
        for co in vert_cos:
            corner1.x = min(corner1.x, co.x)
            corner1.y = min(corner1.y, co.y)
            corner1.z = min(corner1.z, co.z)
            corner2.x = max(corner2.x, co.x)
            corner2.y = max(corner2.y, co.y)
            corner2.z = max(corner2.z, co.z)

        # Box dimensions
        self.box_depth = abs(corner1.x - corner2.x)
        self.box_width = abs(corner1.y - corner2.y)
        self.box_height = abs(corner1.z - corner2.z)
        self.box_center = (corner1 + corner2) * 0.5

        # Cylinder diameters
        self.cyl_diameter1 = self.cyl_diameter2 = 0.001
        for co in vert_cos:
            dx = center.x - co.x
            dy = center.y - co.y
            d = math.sqrt(dx * dx + dy * dy)
            influence2 = get_range_pct(corner1.z, corner2.z, co.z)
            influence1 = 1.0 - influence2
            self.cyl_diameter1 = max(self.cyl_diameter1, d * influence1)
            self.cyl_diameter2 = max(self.cyl_diameter2, d * influence2)
        self.cyl_height = self.box_height

        # Capsule axis and diameter
        self.cap_diameter = 0.001
        depth_sqr = 0.0
        for co in vert_cos:
            dist_to_axis = get_point_dist_to_line(co, axis, center)
            if dist_to_axis > self.cap_diameter:
                self.cap_diameter = dist_to_axis
            dist_along_axis_sqr = (co - center).project(axis).length_squared
            if dist_along_axis_sqr > depth_sqr:
                depth_sqr = dist_along_axis_sqr
        self.cap_rotation = axis.to_track_quat('Z', 'X').to_euler('XYZ')
        self.cap_depth = math.sqrt(depth_sqr) * 2.0 - self.cap_diameter

        # Sphere diameter
        self.sph_diameter = max(self.box_depth, self.box_width, self.box_height)

    def draw(self, context):
        layout = self.layout
        col = layout.column()
        col.prop(self, 'shape')
        col.prop(self, 'wire')
        if self.shape in {'BOX', 'CYLINDER'}:
            col.prop(self, 'hollow')
            if self.hollow:
                col.prop(self, 'thickness')
                col.prop(self, 'offset')
        col.separator()

        if self.shape == 'BOX':
            col.prop(self, 'box_width')
            col.prop(self, 'box_height')
            col.prop(self, 'box_depth')
        elif self.shape == 'CYLINDER':
            col.prop(self, 'cyl_sides')
            col.prop(self, 'cyl_diameter1')
            col.prop(self, 'cyl_diameter2')
            col.prop(self, 'cyl_height')
        elif self.shape == 'CAPSULE':
            col.prop(self, 'cap_diameter')
            col.prop(self, 'cap_depth')
        elif self.shape == 'SPHERE':
            col.prop(self, 'sph_diameter')
        elif self.shape == 'CONVEX':
            col.prop(self, 'planar_angle')
            col.prop(self, 'decimate_ratio')
            col.label(text='Symmetrize')
            row = col.row(align=True)
            row.prop(self, 'x_symmetry', text="X", toggle=1)
            row.prop(self, 'y_symmetry', text="Y", toggle=1)
            row.prop(self, 'z_symmetry', text="Z", toggle=1)
        elif self.shape == 'WALL':
            col.prop(self, 'thickness')
            col.prop(self, 'offset')
            col.prop(self, 'wall_fill_holes')

classes = (
    MY_OT_assign_collision,
    MY_OT_make_collision,
)

def register(settings):
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
