from math import sin, cos, pi
from mathutils import Vector
import bpy
from .helpers import select_only

def get_selected_active_object(context, types=set()):
    if not context.active_object or not context.active_object.select_get():
        return None
    if types and context.active_object.type not in types:
        return None
    return context.active_object

class GRET_OT_wall_add(bpy.types.Operator):
    """Construct a flat wall mesh.
A collection is created where meshes can be put to create openings."""

    bl_idname = 'gret.wall_add'
    bl_label = "Add Wall"
    bl_options = {'REGISTER', 'UNDO'}

    thickness: bpy.props.FloatProperty(
        name="Thickness",
        description="Wall thickness",
        subtype='DISTANCE',
        default=0.2,
        min=0.001,
    )
    bool_collection_name: bpy.props.StringProperty(
        name="Boolean Collection",
        description="Name of the collection containing the boolean objects",
        default="_cut",
    )
    back_vgroup_name: bpy.props.StringProperty(
        name="Backside Vertex Group",
        description="Name of the vertex group receiving the back side of the wall",
        default="_back",
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        bpy.ops.mesh.primitive_plane_add()
        obj = bpy.context.active_object

        # Ensure collection exists
        if self.bool_collection_name in bpy.data.collections:
            bool_collection = bpy.data.collections[self.bool_collection_name]
        else:
            bool_collection = bpy.data.collections.new(self.bool_collection_name)
            context.scene.collection.children.link(bool_collection)
        if bpy.app.version >= (2, 91):
            bool_collection.color_tag = 'COLOR_08'

        # Ensure vertex group exists
        if self.back_vgroup_name not in obj.vertex_groups:
            obj.vertex_groups.new(name=self.back_vgroup_name)

        obj.modifiers.clear()

        # Solidify is necessary for the EXACT boolean solver to work on planes
        # Temporarily mark the new faces so they may be masked away later
        mo = obj.modifiers.new(type='SOLIDIFY', name="")
        mo.show_expanded = False
        mo.thickness = 0.0001
        mo.offset = 0.0
        mo.use_rim = False
        mo.shell_vertex_group = self.back_vgroup_name
        mo.rim_vertex_group = self.back_vgroup_name

        # Boolean cuts out the openings
        mo = obj.modifiers.new(type='BOOLEAN', name="")
        mo.show_expanded = True  # Don't hide, user may want to change FAST for EXACT
        mo.operation = 'DIFFERENCE'
        mo.operand_type = 'COLLECTION'
        mo.collection = bool_collection
        mo.solver = 'FAST'

        # Undo the previous solidify
        mo = obj.modifiers.new(type='MASK', name="")
        mo.show_expanded = False
        mo.vertex_group = self.back_vgroup_name
        mo.invert_vertex_group = True
        mo = obj.modifiers.new(type='WELD', name="")
        mo.merge_threshold = 0.1

        # Clear the target vertex group
        mo = obj.modifiers.new(type='VERTEX_WEIGHT_EDIT', name=f"Clear {self.back_vgroup_name}")
        mo.show_expanded = False
        mo.vertex_group = self.back_vgroup_name
        mo.use_remove = True
        mo.remove_threshold = 1.0

        # Finally make the backside
        mo = obj.modifiers.new(type='SOLIDIFY', name="")
        mo.show_expanded = False
        mo.thickness = self.thickness
        mo.offset = -1.0
        mo.use_even_offset = False  # Even thickness may cause degenerate faces to explode
        mo.use_rim = False
        mo.shell_vertex_group = self.back_vgroup_name

        # Collapse UVs for the backside
        mo = obj.modifiers.new(type='UV_WARP', name=f"No {self.back_vgroup_name} UV")
        mo.show_expanded = False
        mo.vertex_group = self.back_vgroup_name
        mo.scale[0] = 0.0
        mo.scale[1] = 0.0

        return {'FINISHED'}

class GRET_OT_strap_add(bpy.types.Operator):
    """Construct a strap mesh wrapping around the selected object"""

    bl_idname = 'gret.strap_add'
    bl_label = "Add Strap"
    bl_options = {'REGISTER', 'UNDO'}

    width: bpy.props.FloatProperty(
        name="Width",
        description="Strap width",
        subtype='DISTANCE',
        default=0.05,
        min=0.0,
    )
    thickness: bpy.props.FloatProperty(
        name="Thickness",
        description="Strap thickness",
        subtype='DISTANCE',
        default=0.01,
        min=0.0,
    )
    offset: bpy.props.FloatProperty(
        name="Offset",
        description="Distance to keep from the target",
        subtype='DISTANCE',
        default=0.03,
    )
    subdivisions: bpy.props.IntProperty(
        name="Subdivisions",
        description="Subdivision level",
        default=1,
        min=0,
        soft_max=6,
    )
    use_smooth_shade: bpy.props.BoolProperty(
        name="Smooth Shade",
        description="Output faces with smooth shading rather than flat shaded",
        default=False,
    )
    use_snap_to_surface: bpy.props.BoolProperty(
        name="Snap To Surface",
        description="Completely snap to surface. Otherwise only inside points get pushed out",
        default=False,
    )

    def execute(self, context):
        target_obj = get_selected_active_object(context)

        mesh = bpy.data.meshes.new("Strap")
        v = Vector((0.1, 0.0, 0.0))
        vertices = [v * 0, v * 1, v * 2, v * 3]
        edges = [(0, 1), (1, 2), (2, 3)]
        mesh.from_pydata(vertices, edges, [])
        mesh.update()

        obj = bpy.data.objects.new("Strap", mesh)
        obj.location = context.scene.cursor.location
        context.collection.objects.link(obj)
        context.view_layer.objects.active = obj

        if target_obj:
            mod = obj.modifiers.new(type='SHRINKWRAP', name="")
            mod.wrap_method = 'TARGET_PROJECT'
            mod.wrap_mode = 'OUTSIDE_SURFACE' if self.use_snap_to_surface else 'OUTSIDE'
            mod.target = target_obj
            mod.offset = self.thickness + self.offset
            mod.show_in_editmode = True
            mod.show_on_cage = True

        mod = obj.modifiers.new(type='SUBSURF', name="")
        mod.levels = self.subdivisions
        mod.render_levels = self.subdivisions
        mod.show_in_editmode = True
        mod.show_on_cage = True

        mod = obj.modifiers.new(type='SKIN', name="")
        mod.use_x_symmetry = False
        # Smooth shade looks wrong with no thickness
        mod.use_smooth_shade = False if self.thickness <= 0.0 else self.use_smooth_shade
        mod.show_in_editmode = True
        mod.show_on_cage = True
        for skin_vert in mesh.skin_vertices[0].data:
            skin_vert.radius = (self.thickness, self.width)

        # Ideally there would be a weld modifier here when thickness is 0
        # Weld modifier isn't consistent about normals in this case, causing faces to get flipped
        # mod = obj.modifiers.new(type='WELD', name="")

        return {'FINISHED'}

class GRET_OT_rope_add(bpy.types.Operator):
    """Construct a rope mesh following the selected curve"""

    bl_idname = 'gret.rope_add'
    bl_label = "Add Rope"
    bl_options = {'REGISTER', 'UNDO'}

    rows: bpy.props.IntProperty(
        name="Number of Rows",
        description="Number of rows",
        default=10,
        min=1,
    )
    cuts: bpy.props.IntProperty(
        name="Number of Cuts",
        description="Number of cuts for each row",
        default=2,
        min=0,
        soft_max=100,
    )
    radius: bpy.props.FloatProperty(
        name="Radius",
        description="Rope radius",
        subtype='DISTANCE',
        default=0.05,
        min=0.0,
    )
    row_height: bpy.props.FloatProperty(
        name="Row Height",
        description="Height of each row",
        subtype='DISTANCE',
        default=0.1,
        min=0.0,
    )
    depth: bpy.props.FloatProperty(
        name="Depth",
        description="Depth of the groove",
        subtype='DISTANCE',
        default=0.01,
        min=0.0,
    )
    spread: bpy.props.FloatProperty(
        name="Spread",
        description="Width ratio of the groove",
        subtype='FACTOR',
        default=0.2,
        min=0.0,
        max=1.0,
    )
    subdivisions: bpy.props.IntProperty(
        name="Subdivisions",
        description="Subdivision level",
        default=1,
        min=0,
        soft_max=6,
    )
    use_smooth_shade: bpy.props.BoolProperty(
        name="Smooth Shade",
        description="Output faces with smooth shading rather than flat shaded",
        default=True,
    )

    def execute(self, context):
        target_obj = get_selected_active_object(context, types={'CURVE'})

        mesh = bpy.data.meshes.new("Rope")
        theta = pi/4 * (1.0 - self.spread)  # [45..0] degrees for spread [0..1]
        r0 = self.radius - self.depth
        r1 = self.radius
        vertices = [
            Vector((cos(0.0) * r0, sin(0.0) * r0, 0.0)),
            Vector((cos(pi/4 - theta) * r1, sin(pi/4 - theta) * r1, 0.0)),
            Vector((cos(pi/4) * r1, sin(pi/4) * r1, 0.0)),
            Vector((cos(pi/4 + theta) * r1, sin(pi/4 + theta) * r1, 0.0)),
            Vector((cos(pi/2) * r0, sin(pi/2) * r0, 0.0)),
        ]
        faces = [(n, n+1, n+1+len(vertices), n+len(vertices)) for n in range(4)]
        cut_height = self.row_height / (self.cuts + 1)
        vertices.extend([Vector((v.x, v.y, cut_height)) for v in vertices])
        mesh.from_pydata(vertices, [], faces)
        for face in mesh.polygons:
            face.use_smooth = self.use_smooth_shade
        mesh.use_auto_smooth = True
        mesh.auto_smooth_angle = pi
        crease_data = mesh.attributes.new('crease_edge', domain='EDGE', type='FLOAT').data
        for edge in (mesh.edges[4], mesh.edges[8]):
            edge.use_edge_sharp = True
            crease_data[edge.index].value = 1.0
        mesh.update()

        obj = bpy.data.objects.new("Rope", mesh)
        if target_obj:
            # Snap to the target curve so that the curve modifier works as expected
            obj.location = target_obj.location
        else:
            obj.location = context.scene.cursor.location
        context.collection.objects.link(obj)
        context.view_layer.objects.active = obj

        mod = obj.modifiers.new(type='MIRROR', name="")
        mod.use_axis = [True, True, False]
        mod.use_clip = True
        mod.merge_threshold = 1e-5

        mod = obj.modifiers.new(type='ARRAY', name="")
        mod.count = self.cuts + 1
        mod.relative_offset_displace = [0.0, 0.0, 1.0]
        mod.use_merge_vertices = True
        mod.merge_threshold = 1e-5

        mod = obj.modifiers.new(type='SIMPLE_DEFORM', name="")
        mod.deform_method = 'TWIST'
        mod.angle = pi/2
        mod.deform_axis = 'Z'

        mod = obj.modifiers.new(type='ARRAY', name="")
        mod.count = self.rows
        mod.relative_offset_displace = [0.0, 0.0, 1.0]
        mod.use_merge_vertices = True
        mod.merge_threshold = 1e-5

        if target_obj:
            mod = obj.modifiers.new(type='CURVE', name="")
            mod.object = target_obj
            mod.deform_axis = 'POS_Z'

            if target_obj.data.splines.active and target_obj.data.splines.active.use_cyclic_u:
                # Only weld if it's a cyclic curve
                mod = obj.modifiers.new(type='WELD', name="")
                mod.merge_threshold = 1e-5

        mod = obj.modifiers.new(type='SUBSURF', name="")
        mod.levels = self.subdivisions
        mod.render_levels = self.subdivisions

        select_only(context, obj)

        return {'FINISHED'}

classes = (
    GRET_OT_rope_add,
    # GRET_OT_strap_add,
    # GRET_OT_wall_add,
)

def draw_menu(self, context):
    layout = self.layout
    layout.operator_context = 'INVOKE_REGION_WIN'

    layout.separator()
    layout.operator('gret.rope_add', icon='MOD_SCREW', text="Rope")
    # layout.operator('gret.strap_add', icon='EDGESEL', text="Strap")
    # layout.operator('gret.wall_add', icon='MOD_BUILD', text="Wall")

def register(settings, prefs):
    if not prefs.mesh__enable_add_rope:
        return False

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.VIEW3D_MT_mesh_add.append(draw_menu)

def unregister():
    bpy.types.VIEW3D_MT_mesh_add.remove(draw_menu)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
