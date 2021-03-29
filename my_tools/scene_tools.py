from itertools import dropwhile, chain
from math import pi
import bmesh
import bpy
import numpy as np
import re
from .math_helpers import RBF
from .mesh_helpers import (
    bmesh_blur_vertex_group,
    edit_mesh_elements,
    get_mesh_points,
)
from .helpers import (
    link_properties,
    load_selection,
    save_selection,
)

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
        # Find duplicate materials
        # For now, duplicate means they are suffixed with ".001", ".002" while the original exists
        redirects = {}
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
        name="Destination Object",
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
            self.report({'ERROR'}, f"Destination object does not exist.")
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
                            verb = "couldn't be"
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

        if self.num_found == 0:
            self.report({'INFO'}, f"No references found.")
        elif self.dry_run:
            self.report({'INFO'}, f"{num_found} references found, see the console for details.")
        else:
            self.report({'INFO'}, f"{num_found} references found, {num_replaced} replaced.")

        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

class MY_OT_graft(bpy.types.Operator):
    #tooltip
    """Connects boundaries of selected objects to the active object"""

    bl_idname = 'my_tools.graft'
    bl_label = "Graft"
    bl_options = {'REGISTER', 'UNDO'}

    expand: bpy.props.IntProperty(
        name="Expand",
        description="Expand the target area on the active mesh",
        default=0,
        min=0,
    )
    cuts: bpy.props.IntProperty(
        name="Number of Cuts",
        description="Number of cuts",
        default=0,
        min=0,
    )
    transfer_normals: bpy.props.BoolProperty(
        name="Transfer Normals",
        description="Transfer custom normals",
        default=True,
    )
    normal_blend_distance: bpy.props.FloatProperty(
        name="Normal Blend Distance",
        description="Blur boundary normals up to this distance",
        subtype='DISTANCE',
        default=0.0,
        min=0.0,
    )
    normal_blend_power: bpy.props.FloatProperty(
        name="Normal Blend Power",
        description="Adjust the strength of boundary normal blending",
        default=1.0,
        min=1.0,
    )
    transfer_vertex_groups: bpy.props.BoolProperty(
        name="Transfer Vertex Groups",
        description="Transfer vertex groups",
        default=True,
    )
    transfer_uv: bpy.props.BoolProperty(
        name="Transfer UVs",
        description="Transfer UV layers",
        default=False,
    )
    create_mask: bpy.props.BoolProperty(
        name="Create Mask",
        description="Create mask modifiers on the active object to hide the affected faces",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return (len(context.selected_objects) > 1
            and context.active_object
            and context.active_object.type == 'MESH'
            and context.mode == 'OBJECT')

    def new_vgroup(self, obj, name):
        vgroup = obj.vertex_groups.get(name)
        if vgroup:
            vgroup.remove(range(len(obj.data.vertices)))
        else:
            vgroup = obj.vertex_groups.new(name=name)
        return vgroup

    def new_modifier(self, obj, type, name):
        modifier = obj.modifiers.get(name)
        if not modifier or modifier.type != type:
            modifier = obj.modifiers.new(type=type, name=name)
        ctx = {'object': obj}
        bpy.ops.object.modifier_move_to_index(ctx, modifier=modifier.name, index=0)
        return modifier

    def _execute(self, context):
        dst_obj = context.active_object
        dst_mesh = dst_obj.data

        for obj in context.selected_objects[:]:
            if obj.type != 'MESH':
                continue
            if obj == context.active_object:
                continue

            # Initial setup
            obj_to_world = obj.matrix_world.copy()
            world_to_obj = obj.matrix_world.inverted()
            dst_to_obj = world_to_obj @ dst_obj.matrix_world
            obj_to_dst = dst_to_obj.inverted()

            boundary_vg = self.new_vgroup(obj, f"_boundary")
            soft_boundary_vg = self.new_vgroup(obj, f"_boundary_soft")
            bm = bmesh.new()
            bm.from_mesh(obj.data)

            # The source edge loop is currently the mesh boundary. Not doing any validation
            edges1 = [e for e in bm.edges if e.is_boundary]
            for edge in edges1:
                boundary_vg.add([edge.verts[0].index, edge.verts[1].index], 1.0, 'REPLACE')

            # Push the boundary into the destination mesh and get the boolean intersection
            # Use fast since exact solver demands the object is manifold. Might need to close holes
            saved_active_modifiers = []
            for mod in chain(obj.modifiers, dst_obj.modifiers):
                if mod.show_viewport:
                    mod.show_viewport = False
                    saved_active_modifiers.append(mod)
            wrap_mod = obj.modifiers.new(type='SHRINKWRAP', name="Shrinkwrap")
            wrap_mod.wrap_method = 'TARGET_PROJECT' # 'NEAREST_SURFACEPOINT'
            wrap_mod.wrap_mode = 'INSIDE'
            wrap_mod.target = dst_obj
            wrap_mod.vertex_group = boundary_vg.name
            wrap_mod.offset = 0.01
            bool_mod = obj.modifiers.new(type='BOOLEAN', name="Boolean")
            bool_mod.operation = 'INTERSECT'
            bool_mod.solver = 'FAST'
            bool_mod.object = dst_obj
            dg = context.evaluated_depsgraph_get()
            bool_bm = bmesh.new()
            bool_bm.from_object(obj, dg)
            obj.modifiers.remove(bool_mod)
            obj.modifiers.remove(wrap_mod)

            # Because the result of the boolean operation mostly matches the destination geometry,
            # all that's needed is finding those same faces in the original mesh
            intersecting_face_indices = []
            for face in bool_bm.faces:
                p = obj_to_dst @ face.calc_center_median()
                result, closest_point, normal, face_idx = dst_obj.closest_point_on_mesh(p)
                if result:
                    if (dst_mesh.polygons[face_idx].center - p).length_squared <= 0.05:
                        intersecting_face_indices.append(face_idx)

            while saved_active_modifiers:
                saved_active_modifiers.pop().show_viewport = True
            bool_bm.free()

            if not intersecting_face_indices:
                bm.free()
                self.report({'ERROR'}, f"No intersection found between the objects.")
                return

            # The target edge loop is the boundary of the intersection. Recreate it in working bmesh
            edit_mesh_elements(dst_obj, 'FACE', intersecting_face_indices)
            for _ in range(self.expand):
                bpy.ops.mesh.select_more()
            bpy.ops.object.editmode_toggle()
            intersecting_vert_indices = [v.index for v in dst_mesh.vertices if v.select]
            bpy.ops.object.editmode_toggle()
            bpy.ops.mesh.region_to_loop()
            bpy.ops.object.editmode_toggle()
            idx_to_bmvert = {v.index: bm.verts.new(dst_to_obj @ v.co)
                for v in dst_mesh.vertices if v.select}
            bm.verts.index_update()
            edges2 = [bm.edges.new((idx_to_bmvert[e.vertices[0]], idx_to_bmvert[e.vertices[1]]))
                for e in dst_mesh.edges if e.select]
            bm.edges.index_update()

            try:
                ret = bmesh.ops.bridge_loops(bm, edges=edges1+edges2, use_pairs=False,
                    use_cyclic=False, use_merge=False, merge_factor=0.5, twist_offset=0)
                new_faces = ret['faces']
                if self.cuts:
                    ret = bmesh.ops.subdivide_edges(bm, edges=ret['edges'], smooth=1.0,
                        smooth_falloff='LINEAR', cuts=self.cuts)
                    new_faces = list(dropwhile(lambda el: not isinstance(el, bmesh.types.BMFace),
                        ret['geom']))
            except RuntimeError:
                bm.free()
                self.report({'ERROR'}, f"Couldn't bridge edge loops.")
                return
            for face in new_faces:
                face.smooth = True

            # Begin transferring data from the destination mesh
            bm.verts.layers.deform.verify()
            deform_layer = bm.verts.layers.deform.active
            for edge in bm.edges:
                if edge.is_boundary:
                    for vert in edge.verts:
                        vert[deform_layer][boundary_vg.index] = 1.0
                        vert[deform_layer][soft_boundary_vg.index] = 1.0
            if self.transfer_normals:
                bmesh_blur_vertex_group(bm, soft_boundary_vg.index,
                    distance=self.normal_blend_distance,
                    power=self.normal_blend_power)

            # Apply the result
            bm.to_mesh(obj.data)
            bm.free()

            ctx = {'object': obj}
            if self.transfer_normals:
                mod = self.new_modifier(obj, name="transfer normals", type='DATA_TRANSFER')
                mod.object = dst_obj
                mod.vertex_group = soft_boundary_vg.name
                mod.use_object_transform = True
                mod.use_loop_data = True
                mod.data_types_loops = {'CUSTOM_NORMAL'}
                mod.loop_mapping = 'POLYINTERP_NEAREST'
                obj.data.use_auto_smooth = True
                obj.data.auto_smooth_angle = pi
                bpy.ops.mesh.customdata_custom_splitnormals_clear(ctx)
                bpy.ops.object.modifier_apply(ctx, modifier=mod.name)

            if self.transfer_vertex_groups or self.transfer_uv:
                mod = self.new_modifier(obj, name="transfer other", type='DATA_TRANSFER')
                mod.object = dst_obj
                mod.use_object_transform = True
                if self.transfer_vertex_groups:
                    mod.use_vert_data = True
                    mod.data_types_verts = {'VGROUP_WEIGHTS'}
                    mod.vert_mapping = 'EDGEINTERP_NEAREST'
                if self.transfer_uv:
                    mod.use_loop_data = True
                    mod.data_types_loops = {'UV'}  # Automatically turns on use_poly_data
                    mod.loop_mapping = 'POLYINTERP_NEAREST'
                bpy.ops.object.datalayout_transfer(ctx, modifier=mod.name)
                bpy.ops.object.modifier_apply(ctx, modifier=mod.name)

            # If requested, create a mask modifier that will hide the intersection's inner verts
            if self.create_mask:
                mask_vg = self.new_vgroup(dst_obj, f"_mask_{obj.name}")
                intersecting_verts = (dst_mesh.vertices[i] for i in intersecting_vert_indices)
                mask_vg.add([v.index for v in intersecting_verts if not v.select], 1.0, 'REPLACE')
                mask_mod = self.new_modifier(dst_obj, name=mask_vg.name, type='MASK')
                mask_mod.vertex_group = mask_vg.name
                mask_mod.invert_vertex_group = True
                mod_dp = f'modifiers["{mask_mod.name}"]'
                # Can't create a hide_viewport driver for reasons
                link_properties(obj, 'hide_render', dst_obj, mod_dp + '.show_render', invert=True)

        return {'FINISHED'}

    def execute(self, context):
        saved_selection = save_selection()

        try:
            self._execute(context)
        finally:
            # Clean up
            load_selection(saved_selection)

        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout

        layout.prop(self, 'expand')
        layout.prop(self, 'cuts')
        layout.prop(self, 'create_mask')

        layout.separator()
        layout.label(text="Transfer:")
        split = layout.split(factor=0.35)
        col = split.column()
        col.prop(self, 'transfer_normals', text="Normals")
        col.prop(self, 'transfer_vertex_groups', text="Vertex Groups")
        col.prop(self, 'transfer_uv', text="UVs")
        col = split.column()

        sub = col.split()
        sub.enabled = self.transfer_normals
        row = sub.row(align=True)
        row.prop(self, 'normal_blend_distance', text="Dist.")
        row.prop(self, 'normal_blend_power', text="Power")

class MY_OT_retarget_mesh(bpy.types.Operator):
    #tooltip
    """Retarget meshes fit on a source mesh to a modified version of the source mesh.
The meshes are expected to share topology and vertex order"""
    # Note: If vertex order gets messed up, try using an addon like Transfer Vert Order to fix it

    bl_idname = 'my_tools.retarget_mesh'
    bl_label = "Retarget Mesh"
    bl_options = {'REGISTER', 'UNDO'}

    source: bpy.props.StringProperty(
        name="Source",
        description="Source mesh object that the meshes were originally fit to",
    )
    destination: bpy.props.StringProperty(
        name="Destination",
        description="Modified mesh object to retarget to",
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
    stride: bpy.props.IntProperty(
        name="Stride",
        description="Increase vertex sampling stride to reduce accuracy and speed up calculation",
        default=1,
        min=1,
    )
    as_shapekey: bpy.props.BoolProperty(
        name="As Shapekey",
        description="Save the result as a shape key on the mesh",
        default=False,
    )
    use_object_transform: bpy.props.BoolProperty(
        name="Object Transform",
        description="Evaluate all meshes in global space",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT' and context.selected_objects

    def execute(self, context):
        objs = context.selected_objects
        src_obj = bpy.data.objects.get(self.source)
        dst_obj = bpy.data.objects.get(self.destination)
        if not src_obj or src_obj.type != 'MESH' or not dst_obj or dst_obj.type != 'MESH':
            # Don't error here so the user can call up the props dialog
            return {'FINISHED'}
        if len(src_obj.data.vertices) != len(dst_obj.data.vertices):
            self.report({'ERROR'}, "Source and destination meshes must have equal amount of vertices.")
            return {'CANCELLED'}

        rbf_kernels = {
            'LINEAR': RBF.linear,
            'GAUSSIAN': RBF.gaussian,
            'PLATE': RBF.thin_plate,
            'BIHARMONIC': RBF.multi_quadratic_biharmonic,
            'INV_BIHARMONIC': RBF.inv_multi_quadratic_biharmonic,
            'C2': RBF.beckert_wendland_c2_basis,
        }
        rbf = rbf_kernels.get(self.function, RBF.linear)
        src_pts = get_mesh_points(src_obj, self.use_object_transform, self.stride)
        dst_pts = get_mesh_points(dst_obj, self.use_object_transform, self.stride)
        try:
            weights = RBF.get_weight_matrix(src_pts, dst_pts, rbf, self.radius)
        except np.linalg.LinAlgError:
            # Solving for C2 kernel may throw 'SVD did not converge' sometimes
            self.report({'ERROR'}, "Failed to retarget. Try a different function or change the radius.")
            return {'CANCELLED'}

        for obj in objs:
            if obj.type != 'MESH':
                continue
            mesh_pts = get_mesh_points(obj, self.use_object_transform)
            num_mesh_pts = mesh_pts.shape[0]

            dist = RBF.get_distance_matrix(mesh_pts, src_pts, rbf, self.radius)
            identity = np.ones((num_mesh_pts, 1))
            h = np.bmat([[dist, identity, mesh_pts]])
            new_mesh_pts = np.asarray(np.dot(h, weights))
            if self.use_object_transform:
                # Result back to local space
                new_mesh_pts = np.c_[new_mesh_pts, identity]
                new_mesh_pts = np.einsum('ij,aj->ai', obj.matrix_world.inverted(), new_mesh_pts)
                new_mesh_pts = new_mesh_pts[:, :-1]

            if self.as_shapekey:
                # Result to new shape key
                if not obj.data.shape_keys or not obj.data.shape_keys.key_blocks:
                    obj.shape_key_add(name="Basis")
                shape_key = obj.shape_key_add(name=f"Retarget_{dst_obj.name}")
                shape_key.data.foreach_set('co', new_mesh_pts.ravel())
                shape_key.value = 1.0
            elif obj.data.shape_keys and obj.data.shape_keys.key_blocks:
                # There are shape keys, so replace the basis
                # Using bmesh propagates the change, where just setting the coordinates won't
                bm = bmesh.new()
                bm.from_mesh(obj.data)
                for vert, new_pt in zip(bm.verts, new_mesh_pts):
                    vert.co[:] = new_pt
                bm.to_mesh(obj.data)
                bm.free()
            else:
                # Set new coordinates directly
                obj.data.vertices.foreach_set('co', new_mesh_pts.ravel())
            obj.data.update()

        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        layout.prop_search(self, 'source', bpy.data, 'meshes', text="From")
        layout.prop_search(self, 'destination', bpy.data, 'meshes', text="To")
        layout.prop(self, 'function')
        layout.prop(self, 'radius')
        layout.prop(self, 'stride')
        layout.prop(self, 'as_shapekey')

class MY_PT_scene_tools(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "My Tools"
    bl_label = "Scene Tools"

    def draw(self, context):
        obj = context.active_object
        layout = self.layout
        settings = context.scene.my_tools

        col = layout.column(align=True)
        col.label(text="Collision:")
        row = col.row(align=True)
        row.operator('my_tools.make_collision', icon='MESH_CUBE', text="Make")
        row.operator('my_tools.assign_collision', text="Assign")

        col = layout.column(align=True)
        row = col.row(align=False)
        row.label(text="Retarget Mesh:")
        sub = row.row(align=True)
        sub.prop(settings, 'retarget_use_object_transform', icon='ORIENTATION_GLOBAL', text="")
        sub.prop(settings, 'retarget_show_options', icon='SETTINGS', text="")

        if settings.retarget_show_options:
            sub = col.column(align=False)
            sub.prop(settings, 'retarget_function', text="")
            sub.prop(settings, 'retarget_radius')
            sub.prop(settings, 'retarget_stride')
            sub.separator()

        row = col.row(align=True)
        row.prop(settings, 'retarget_src', text="")
        row.label(text="", icon='FORWARD')
        row.prop(settings, 'retarget_dst', text="")

        row = col.row(align=True)
        op1 = row.operator('my_tools.retarget_mesh', icon='CHECKMARK', text="Apply")
        op2 = row.operator('my_tools.retarget_mesh', icon='SHAPEKEY_DATA', text="Save")
        if settings.retarget_src and settings.retarget_dst:
            op1.source = op2.source = settings.retarget_src.name
            op1.destination = op2.destination = settings.retarget_dst.name
            op1.function = op2.function = settings.retarget_function
            op1.radius = op2.radius = settings.retarget_radius
            op1.use_object_transform = op2.use_object_transform = settings.retarget_use_object_transform
            op1.as_shapekey = False
            op2.as_shapekey = True
        else:
            row.active = False

        col = layout.column(align=True)
        col.label(text="Other Tools:")
        col.operator('my_tools.graft', icon='MOD_BOOLEAN')
        col.operator('my_tools.deduplicate_materials', icon='MATERIAL')
        col.operator('my_tools.replace_references', icon='LIBRARY_DATA_OVERRIDE')

classes = (
    MY_OT_deduplicate_materials,
    MY_OT_graft,
    MY_OT_replace_references,
    MY_OT_retarget_mesh,
    MY_PT_scene_tools,
)

def register(settings):
    for cls in classes:
        bpy.utils.register_class(cls)

    # Add persistent settings for mesh retargeting
    settings.add_property('retarget_src', bpy.props.PointerProperty(
        name="Mesh Retarget Source",
        description="Source mesh that the meshes were originally fit to",
        type=bpy.types.Object,
        poll=lambda self, obj: obj and obj.type == 'MESH',
    ))
    settings.add_property('retarget_dst', bpy.props.PointerProperty(
        name="Mesh Retarget Destination",
        description="Modified source mesh to retarget to",
        type=bpy.types.Object,
        poll=lambda self, obj: obj and obj.type == 'MESH' and obj != self.retarget_src and (
            not self.retarget_src or len(obj.data.vertices) == len(self.retarget_src.data.vertices))
    ))
    retarget_props = MY_OT_retarget_mesh.__annotations__
    settings.add_property('retarget_function', retarget_props['function'])
    settings.add_property('retarget_radius', retarget_props['radius'])
    settings.add_property('retarget_stride', retarget_props['stride'])
    settings.add_property('retarget_use_object_transform', retarget_props['use_object_transform'])
    settings.add_property('retarget_show_options', bpy.props.BoolProperty(
        name="Configure",
        description="Show retargeting options",
        default=False,
    ))

def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
