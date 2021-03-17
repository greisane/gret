from itertools import dropwhile, chain
from mathutils import Vector
import bmesh
import bpy
import math
import re
from .mesh_helpers import (
    bmesh_blur_vertex_group,
    edit_mesh_elements,
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

class MY_OT_setup_wall(bpy.types.Operator):
    #tooltip
    """Use on flat wall meshes to set up modifiers for boolean openings.
A collection is created where meshes can be added to cut through the walls"""

    bl_idname = 'my_tools.setup_wall'
    bl_label = "Setup Wall"
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

class MY_OT_graft(bpy.types.Operator):
    #tooltip
    """Connect boundaries of selected objects to the active object"""

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
                obj.data.auto_smooth_angle = math.pi
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

class MY_OT_strap_add(bpy.types.Operator):
    #tooltip
    """Construct a strap mesh wrapping around the selected object"""

    bl_idname = 'mesh.strap_add'
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
        subtype='DISTANCE',
        default=1,
        min=0,
    )
    use_smooth_shade: bpy.props.BoolProperty(
        name="Smooth Shade",
        description="Output faces with smooth shading rather than flat shaded",
        default=False,
    )

    def execute(self, context):
        target_obj = context.active_object

        mesh = bpy.data.meshes.new("Strap")
        vertices = [Vector(), Vector((1.0, 0.0, 0.0))]
        edges = [(0, 1)]
        mesh.from_pydata(vertices, edges, [])
        mesh.update()
        obj = bpy.data.objects.new("Strap", mesh)
        obj.location = context.scene.cursor.location
        context.collection.objects.link(obj)
        context.view_layer.objects.active = obj

        vgroup = obj.vertex_groups.new(name="_shrinkwrap")
        vgroup.add(range(len(vertices)), 1.0, 'REPLACE')
        mod = obj.modifiers.new(type='SHRINKWRAP', name="Shrinkwrap")
        mod.wrap_method = 'TARGET_PROJECT' # 'NEAREST_SURFACEPOINT'
        mod.wrap_mode = 'OUTSIDE'
        mod.target = target_obj
        mod.offset = self.thickness + self.offset
        mod.show_in_editmode = True
        mod.show_on_cage = True
        mod.vertex_group = vgroup.name

        mod = obj.modifiers.new(type='SUBSURF', name="Subdivision")
        mod.levels = self.subdivisions
        mod.render_levels = self.subdivisions
        mod.show_in_editmode = True
        mod.show_on_cage = True

        mod = obj.modifiers.new(type='SKIN', name="Skin")
        mod.use_x_symmetry = False
        # Smooth shade looks wrong with no thickness
        mod.use_smooth_shade = False if self.thickness <= 0.0 else self.use_smooth_shade
        mod.show_in_editmode = True
        mod.show_on_cage = True
        for skin_vert in mesh.skin_vertices[0].data:
            skin_vert.radius = (self.thickness, self.width)

        # Ideally there would be a weld modifier here when thickness is 0
        # However it isn't consistent about the resulting normals and the faces get flipped around
        # mod = obj.modifiers.new(type='WELD', name="Weld")

        return {'FINISHED'}

class MY_PT_scene_tools(bpy.types.Panel):
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "My Tools"
    bl_label = "Scene Tools"

    def draw(self, context):
        obj = context.active_object
        layout = self.layout

        col = layout.column(align=True)
        col.label(text="Collision:")
        row = col.row(align=True)
        row.operator('my_tools.make_collision', icon='MESH_CUBE', text="Make")
        row.operator('my_tools.assign_collision', text="Assign")

        col = layout.column(align=True)
        col.label(text="Other Tools:")
        col.operator('my_tools.setup_wall', icon='MOD_BUILD')
        col.operator('my_tools.graft', icon='MOD_BOOLEAN')

classes = (
    MY_OT_deduplicate_materials,
    MY_OT_graft,
    MY_OT_replace_references,
    MY_OT_setup_wall,
    MY_OT_strap_add,
    MY_PT_scene_tools,
)

def mesh_menu_draw_func(self, context):
    layout = self.layout
    layout.operator_context = 'INVOKE_REGION_WIN'

    layout.separator()
    layout.operator("mesh.strap_add", icon='EDGESEL', text="Strap")

def register(settings):
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.VIEW3D_MT_mesh_add.append(mesh_menu_draw_func)

def unregister():
    bpy.types.VIEW3D_MT_mesh_add.remove(mesh_menu_draw_func)

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
