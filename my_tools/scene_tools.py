from itertools import chain
from mathutils import Matrix, Vector
import bmesh
import bpy
import math
import os
import re
import time
from .math_helpers import (
    get_best_fit_line,
    get_point_dist_to_line,
    get_range_pct,
    get_sq_dist,
)
from .helpers import (
    beep,
    fail_if_invalid_export_path,
    get_export_path,
    get_nice_export_report,
    load_selection,
    log,
    logger,
    remove_extra_data,
    save_selection,
    select_only,
    show_only,
)
from .settings import bake_items

# make_collision TODO:
# - When creating collision from vertices, sometimes the result is offset
# - Multiple objects. Should take away the shape properties and leave only the type
# - Non-AABB boxes
# - Symmetrize for convex isn't good
# - Wall collision should try to decompose into boxes

class SolidPixels:
    """Mimics a pixels array, always returning the same value for all pixels."""
    def __init__(self, size, value=0.0):
        self.size = size
        self.value = value
    def __len__(self):
        return self.size * self.size * 4
    def __getitem__(self, key):
        if isinstance(key, slice):
            return [self.value] * len(range(*key.indices(len(self))))
        return self.value

def remap_materials(objs, src_mat, dst_mat):
    for obj in objs:
        for mat_idx, mat in enumerate(obj.data.materials):
            if mat == src_mat:
                obj.data.materials[mat_idx] = dst_mat

def bake_ao(scn, nodes, links):
    scn.cycles.samples = 128
    bpy.ops.object.bake(type='AO')

def bake_bevel(scn, nodes, links):
    geometry_node = nodes.new(type='ShaderNodeNewGeometry')
    bevel_node = nodes.new(type='ShaderNodeBevel')
    bevel_node.samples = 1
    bevel_node.inputs['Radius'].default_value = 0.1
    cross_node = nodes.new(type='ShaderNodeVectorMath')
    cross_node.operation = 'CROSS_PRODUCT'
    length_node = nodes.new(type='ShaderNodeVectorMath')
    length_node.operation = 'LENGTH'
    emission_node = nodes.new(type='ShaderNodeEmission')
    output_node = nodes.new(type='ShaderNodeOutputMaterial')
    links.new(output_node.inputs['Surface'], emission_node.outputs['Emission'])
    links.new(emission_node.inputs['Color'], length_node.outputs['Value'])
    links.new(length_node.inputs['Vector'], cross_node.outputs['Vector'])
    links.new(cross_node.inputs[0], geometry_node.outputs['Normal'])
    links.new(cross_node.inputs[1], bevel_node.outputs['Normal'])

    scn.cycles.samples = 64
    bpy.ops.object.bake(type='EMIT')

bakers = {
    'AO': bake_ao,
    'BEVEL': bake_bevel,
}

class MY_OT_bake(bpy.types.Operator):
    #tooltip
    """Export textures for the selected objects' materials, based on the selected bakers"""

    bl_idname = 'my_tools.bake'
    bl_label = "Bake"
    bl_options = {'REGISTER'}

    export_path: bpy.props.StringProperty(
        name="Bake Export Path",
        description="""Export path for the baked texture.
{file} = Name of this .blend file without extension.
{material} = Name of the material being baked.""",
        default="//export/T_{material}.png",
        subtype='FILE_PATH',
    )
    size: bpy.props.IntProperty(
        name="Texture Size",
        description="Size of the exported texture",
        default=256,
        min=8,
    )
    r: bpy.props.EnumProperty(
        name="Texture R Source",
        description="Type of mask to bake into the texture's red channel",
        items=bake_items,
    )
    g: bpy.props.EnumProperty(
        name="Texture G Source",
        description="Type of mask to bake into the texture's green channel",
        items=bake_items,
    )
    b: bpy.props.EnumProperty(
        name="Texture B Source",
        description="Type of mask to bake into the texture's blue channel",
        items=bake_items,
    )

    def new_image(self, name):
        image = bpy.data.images.new(name=name, width=self.size, height=self.size)
        self.new_images.append(image)

        image.alpha_mode = 'NONE'
        return image

    def new_bake_material(self, image):
        mat = bpy.data.materials.new(name=image.name)
        self.new_materials.append(mat)

        mat.use_nodes = True
        mat.node_tree.nodes.clear()
        image_node = mat.node_tree.nodes.new(type='ShaderNodeTexImage')
        image_node.image = image
        return mat

    @classmethod
    def poll(cls, context):
        return context.selected_objects and context.mode == 'OBJECT'

    def _execute(self, context):
        # External baking is broken in Blender
        # See https://developer.blender.org/T57143 and https://developer.blender.org/D4162

        material_groups = {}  # Material to object list

        # Collect relevant materials
        for obj in context.selected_objects:
            for mat in obj.data.materials:
                material_groups[mat] = []

        if not material_groups:
            self.report({'ERROR'}, f"Selected objects have no materials assigned.")
            return {'CANCELLED'}

        # Now collect all the objects relevant to each material
        for obj in context.scene.objects:
            for mat in obj.data.materials:
                objs = material_groups.get(mat)
                if objs is not None:
                    objs.append(obj)

        # Setup common to all bakers
        context.scene.render.engine = 'CYCLES'
        context.scene.render.bake.margin = self.size // 128

        # For each material, bake all objects that contribute to it and export the result
        for mat, objs in material_groups.items():
            path_fields = {
                'material': mat.name,
            }
            filepath = get_export_path(self.export_path, path_fields)
            filename = bpy.path.basename(filepath)
            if filepath in self.exported_files:
                log(f"Skipping {mat.name} as it would overwrite a file that was just exported")

            log(f"Baking {mat.name} with {len(objs)} contributing objects")
            logger.log_indent += 1

            show_only(context, objs)
            select_only(context, objs)

            bake_pixels = [SolidPixels(self.size)] * 3 + [SolidPixels(self.size, value=1.0)]
            bake_srcs = [self.r, self.g, self.b]
            for bake_src in bake_srcs:
                if bake_src == 'NONE':
                    continue

                # Avoid doing extra work and bake only once for all channels with the same source
                channel_idxs = [idx for idx, src in enumerate(bake_srcs) if src == bake_src]
                channel_names = ''
                for channel_idx in channel_idxs:
                    bake_srcs[channel_idx] = 'NONE'
                    channel_names += ('R', 'G', 'B')[channel_idx]
                log(f"Baking {bake_src} for channel {channel_names}")
                bake_img = self.new_image(f"_{mat.name}_{bake_src}")
                bake_mat = self.new_bake_material(bake_img)

                remap_materials(objs, mat, bake_mat)
                bakers[bake_src](context.scene, bake_mat.node_tree.nodes, bake_mat.node_tree.links)
                remap_materials(objs, bake_mat, mat)

                # Store the result
                pixels = bake_img.pixels[:]
                for channel_idx in channel_idxs:
                    bake_pixels[channel_idx] = pixels

            log(f"Exporting {filename}")
            pack_img = self.new_image(f"_{mat.name}")
            pack_img.pixels[:] = chain.from_iterable(
                zip(*(pxls[channel_idx::4] for channel_idx, pxls in enumerate(bake_pixels))))
            pack_img.filepath_raw = filepath
            pack_img.file_format = 'PNG'
            pack_img.save()
            self.exported_files.append(filepath)

            logger.log_indent -= 1

    def execute(self, context):
        try:
            fail_if_invalid_export_path(self.export_path, ['material'])
        except Exception as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}

        saved_selection = save_selection()
        saved_render_engine = context.scene.render.engine
        saved_render_bake_margin = context.scene.render.bake.margin  # Don't mistake for bake_margin
        saved_cycles_samples = context.scene.cycles.samples
        saved_use_global_undo = context.preferences.edit.use_global_undo
        context.preferences.edit.use_global_undo = False
        self.exported_files = []
        self.new_materials = []
        self.new_images = []
        logger.start_logging()

        try:
            start_time = time.time()
            self._execute(context)
            # Finished without errors
            elapsed = time.time() - start_time
            self.report({'INFO'}, get_nice_export_report(self.exported_files, elapsed))
            beep(pitch=3, num=1)
        finally:
            # Clean up
            while self.new_materials:
                bpy.data.materials.remove(self.new_materials.pop())
            while self.new_images:
                bpy.data.images.remove(self.new_images.pop())

            load_selection(saved_selection)
            context.scene.render.engine = saved_render_engine
            context.scene.render.bake.margin = saved_render_bake_margin
            context.scene.cycles.samples = saved_cycles_samples
            context.preferences.edit.use_global_undo = saved_use_global_undo
            logger.end_logging()

        return {'FINISHED'}

class MY_OT_deduplicate_materials(bpy.types.Operator):
    #tooltip
    """Deletes duplicate materials and fixes meshes that reference them"""

    bl_idname = 'my_tools.deduplicate_materials'
    bl_label = "Deduplicate Materials"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        redirects = {}
        # Find duplicate materials
        # For now, duplicate means they are suffixed with ".001", ".002" and the original exists
        for mat in bpy.data.materials:
            match = re.match(r"^(.*)\.\d\d\d$", mat.name)
            if match:
                original_name, = match.groups(0)
                original = bpy.data.materials.get(original_name)
                if original:
                    redirects[mat] = original
        # Replace references in existing meshes
        for me in bpy.data.meshes:
            for idx, mat in enumerate(me.materials):
                me.materials[idx] = redirects.get(mat, mat)
        # Delete duplicate materials
        for mat in redirects.keys():
            bpy.data.materials.remove(mat, do_unlink=True)

        self.report({'INFO'}, f"Deleted {len(redirects)} duplicate materials.")
        return {'FINISHED'}

class MY_OT_replace_references(bpy.types.Operator):
    #tooltip
    """Replaces references to an object with a different object. Use with care.
Currently only handles objects and modifiers, and no nested properties"""

    bl_idname = 'my_tools.replace_references'
    bl_label = "Replace References"
    bl_options = {'REGISTER', 'UNDO'}

    def get_obj_name_items(self, context):
        return [(o.name, o.name, "") for o in bpy.data.objects]

    dry_run: bpy.props.BoolProperty(
        name="Dry Run",
        description="List the names of the properties that would be affected without making changes",
        default=True,
    )
    src_obj_name: bpy.props.EnumProperty(
        items=get_obj_name_items,
        name="Source Object",
        description="Object to be replaced",
    )
    dst_obj_name: bpy.props.EnumProperty(
        items=get_obj_name_items,
        name="Target Object",
        description="Object to be used in its place",
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        src_obj = bpy.data.objects.get(self.src_obj_name)
        if not src_obj:
            self.report({'ERROR'}, f"Source object does not exist.")
            return {'CANCELLED'}
        dst_obj = bpy.data.objects.get(self.dst_obj_name)
        if not dst_obj:
            self.report({'ERROR'}, f"Target object does not exist.")
            return {'CANCELLED'}
        if src_obj == dst_obj:
            self.report({'ERROR'}, f"Source and destination objects are the same.")
            return {'CANCELLED'}

        num_found = 0
        num_replaced = 0
        def replace_pointer_properties(obj, path=""):
            nonlocal num_found, num_replaced
            for prop in obj.bl_rna.properties:
                if prop.type != 'POINTER':
                    continue
                if obj.is_property_readonly(prop.identifier):
                    continue
                if getattr(obj, prop.identifier) == src_obj:
                    path = " -> ".join(s for s in [path, obj.name, prop.identifier] if s)
                    verb = "would be" if self.dry_run else "was"
                    if not self.dry_run:
                        try:
                            setattr(obj, prop.identifier, dst_obj)
                            num_replaced += 1
                        except:
                            verb = "failed to be"
                    print(f"{path} {verb} replaced")
                    num_found += 1

        print(f"Searching for '{src_obj.name}' to replace with '{dst_obj.name}'")
        for obj in bpy.data.objects:
            if obj.library:
                # Linked objects are not handled currently, though it might just work
                continue
            replace_pointer_properties(obj)
            for mo in obj.modifiers:
                replace_pointer_properties(mo, path=obj.name)

        if self.dry_run:
            self.report({'INFO'}, f"{num_found} references found, see the console for details.")
        else:
            self.report({'INFO'}, f"{num_found} references found, {num_replaced} replaced.")

        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

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
        return context.object and context.mode == 'OBJECT'

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
        default=0.2,
        min=0.001,
    )
    offset: bpy.props.FloatProperty(
        name="Offset",
        description="Offset the thickness from the center",
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
        min=0.001,
    )
    box_height: bpy.props.FloatProperty(
        name="Height",
        description="Box height",
        min=0.001,
    )
    box_depth: bpy.props.FloatProperty(
        name="Depth",
        description="Box depth",
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
        min=0.001,
    )
    cyl_diameter2: bpy.props.FloatProperty(
        name="Diameter 2",
        description="Second cylinder diameter",
        min=0.001,
    )
    cyl_height: bpy.props.FloatProperty(
        name="Height",
        description="Cylinder height",
        min=0.001,
    )

    # Capsule settings
    cap_diameter: bpy.props.FloatProperty(
        name="Diameter",
        description="Capsule diameter",
        min=0.001,
    )
    cap_depth: bpy.props.FloatProperty(
        name="Depth",
        description="Capsule depth",
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

class MY_OT_setup_wall(bpy.types.Operator):
    #tooltip
    """Use on flat wall meshes to set up modifiers for boolean openings.
A collection is created where meshes can be added to cut through the walls."""

    bl_idname = 'my_tools.setup_wall'
    bl_label = "Setup Wall"
    bl_options = {'REGISTER', 'UNDO'}

    thickness: bpy.props.FloatProperty(
        name="Thickness",
        description="Wall thickness",
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
        default="black",
    )

    @classmethod
    def poll(cls, context):
        return context.selected_objects and context.mode == 'OBJECT'

    def execute(self, context):
        # Ensure collection exists
        if self.bool_collection_name in bpy.data.collections:
            bool_collection = bpy.data.collections[self.bool_collection_name]
        else:
            bool_collection = bpy.data.collections.new(self.bool_collection_name)
            context.scene.collection.children.link(bool_collection)
        if bpy.app.version >= (2, 91):
            bool_collection.color_tag = 'COLOR_08'

        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue

            # Ensure vertex group exists
            if self.back_vgroup_name not in obj.vertex_groups:
                obj.vertex_groups.new(name=self.back_vgroup_name)

            obj.modifiers.clear()

            # Solidify is necessary for boolean to work on planes
            mo = obj.modifiers.new(type='SOLIDIFY', name="pre cut")
            mo.show_expanded = False
            mo.thickness = 0.0001
            mo.offset = 0.0
            mo.use_rim = False
            mo.shell_vertex_group = self.back_vgroup_name
            mo.rim_vertex_group = self.back_vgroup_name

            # Boolean cuts out the openings
            mo = obj.modifiers.new(type='BOOLEAN', name="cut")
            mo.show_expanded = True  # Don't hide, user may want to change FAST for EXACT
            mo.operation = 'DIFFERENCE'
            mo.operand_type = 'COLLECTION'
            mo.collection = bool_collection
            mo.solver = 'FAST'

            # Undo the previous solidify
            mo = obj.modifiers.new(type='MASK', name="post cut mask")
            mo.show_expanded = False
            mo.vertex_group = self.back_vgroup_name
            mo.invert_vertex_group = True
            mo = obj.modifiers.new(type='WELD', name="post cut weld")
            mo.merge_threshold = 0.1

            # Clear the target vertex group
            mo = obj.modifiers.new(type='VERTEX_WEIGHT_EDIT', name="clear vg")
            mo.show_expanded = False
            mo.vertex_group = self.back_vgroup_name
            mo.use_remove = True
            mo.remove_threshold = 1.0

            # Finally make the backside
            mo = obj.modifiers.new(type='SOLIDIFY', name="solid")
            mo.show_expanded = False
            mo.thickness = self.thickness
            mo.offset = -1.0
            mo.use_even_offset = False  # Even thickness may cause degenerate faces to explode
            mo.use_rim = False
            mo.shell_vertex_group = self.back_vgroup_name

            # Collapse UVs for the backside
            mo = obj.modifiers.new(type='UV_WARP', name="no back uv")
            mo.show_expanded = False
            mo.vertex_group = self.back_vgroup_name
            mo.scale[0] = 0.0
            mo.scale[1] = 0.0

        return {'FINISHED'}

class MY_PT_scene_tools(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "My Tools"
    bl_label = "Scene Tools"

    def draw(self, context):
        me = context.scene.my_tools
        obj = context.active_object
        layout = self.layout

        layout.operator('my_tools.setup_wall')

        col = layout.column(align=True)
        col.operator('my_tools.make_collision', icon='MESH_CUBE')
        col.operator('my_tools.assign_collision')

        col = layout.column(align=True)
        col.label(text="Bake Textures:")
        row = col.row(align=True)
        row.prop(me, 'bake_r', icon='COLOR_RED', text="")
        row.prop(me, 'bake_g', icon='COLOR_GREEN', text="")
        row.prop(me, 'bake_b', icon='COLOR_BLUE', text="")
        row.prop(me, 'bake_size', text="")
        col.prop(me, 'bake_export_path', text="")
        op = col.operator('my_tools.bake', icon='RENDER_STILL')
        op.export_path = me.bake_export_path
        op.size = me.bake_size
        op.r = me.bake_r
        op.g = me.bake_g
        op.b = me.bake_b

classes = (
    MY_OT_assign_collision,
    MY_OT_bake,
    MY_OT_deduplicate_materials,
    MY_OT_make_collision,
    MY_OT_replace_references,
    MY_OT_setup_wall,
    MY_PT_scene_tools,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
