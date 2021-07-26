from collections import defaultdict, namedtuple
import bmesh
import bpy

from .helpers import bmesh_blur_vertex_group

class EdgeKey(namedtuple("EdgeKey", ['a', 'b'])):
    @classmethod
    def from_edge(cls, bm_edge):
        return cls(bm_edge.verts[0].index, bm_edge.verts[1].index)
    def __new__(cls, a, b):
        return super().__new__(cls, a, b) if a < b else super().__new__(cls, b, a)
    def other(self, index):
        return self.a if index == self.b else self.b

def dict_vert_verts(edge_keys):
    # input: list of edge-keys, output: dictionary with vertex-vertex connections
    vert_verts = defaultdict(list)
    for ek in edge_keys:
        vert_verts[ek.a].append(ek.b)
        vert_verts[ek.b].append(ek.a)
    return vert_verts

def face_edgekeys(bm_face):
    return [EdgeKey.from_edge(edge) for edge in bm_face.edges]

def dict_edge_faces(bm):
    # input: bmesh, output: dict with the edge-key as key and face-index as value
    edge_faces = dict([[EdgeKey.from_edge(edge), []] for edge in bm.edges if not edge.hide])
    for face in bm.faces:
        if face.hide:
            continue
        for key in face_edgekeys(face):
            edge_faces[key].append(face.index)
    return edge_faces

def dict_face_faces(bm, edge_faces=False):
    # input: bmesh (edge-faces optional), output: dict with face-face connections
    if not edge_faces:
        edge_faces = dict_edge_faces(bm)
    connected_faces = dict([[face.index, []] for face in bm.faces if not face.hide])
    for face in bm.faces:
        if face.hide:
            continue
        for edge_key in face_edgekeys(face):
            for connected_face in edge_faces[edge_key]:
                if connected_face == face.index:
                    continue
                connected_faces[face.index].append(connected_face)
    return connected_faces

def get_connected_input(bm, parallel=False):
    edge_keys = [EdgeKey.from_edge(edge) for edge in bm.edges if edge.select and not edge.hide]
    loops = get_connected_selections(edge_keys)
    if parallel:
        loops = get_parallel_loops(bm, loops)
    return loops

def get_connected_selections(edge_keys):
    # From mesh_looptools.py by Bart Crouch, Vladimir Spivak (cwolf3d)
    # Create connection data
    vert_verts = dict_vert_verts(edge_keys)

    # Find loops consisting of connected selected edges
    loops = []
    while len(vert_verts) > 0:
        loop = [iter(vert_verts.keys()).__next__()]
        growing = True
        flipped = False

        # Extend loop
        while growing:
            # No more connection data for current vertex
            if loop[-1] not in vert_verts:
                if not flipped:
                    loop.reverse()
                    flipped = True
                else:
                    growing = False
            else:
                extended = False
                for i, next_vert in enumerate(vert_verts[loop[-1]]):
                    if next_vert not in loop:
                        vert_verts[loop[-1]].pop(i)
                        if len(vert_verts[loop[-1]]) == 0:
                            del vert_verts[loop[-1]]
                        # Remove connection both ways
                        if next_vert in vert_verts:
                            if len(vert_verts[next_vert]) == 1:
                                del vert_verts[next_vert]
                            else:
                                vert_verts[next_vert].remove(loop[-1])
                        loop.append(next_vert)
                        extended = True
                        break
                if not extended:
                    # Found one end of the loop, continue with next
                    if not flipped:
                        loop.reverse()
                        flipped = True
                    # Found both ends of the loop, stop growing
                    else:
                        growing = False
        # Check if loop is circular
        if loop[0] in vert_verts:
            if loop[-1] in vert_verts[loop[0]]:
                # Circular
                if len(vert_verts[loop[0]]) == 1:
                    del vert_verts[loop[0]]
                else:
                    vert_verts[loop[0]].remove(loop[-1])
                if len(vert_verts[loop[-1]]) == 1:
                    del vert_verts[loop[-1]]
                else:
                    vert_verts[loop[-1]].remove(loop[0])
                loop = [loop, True]
            else:
                # Not circular
                loop = [loop, False]
        else:
            # Not circular
            loop = [loop, False]
        loops.append(loop)
    return loops

def get_parallel_loops(bm, loops):
    """Returns a list of all loops parallel to the input, input included."""
    # From mesh_looptools.py by Bart Crouch, Vladimir Spivak (cwolf3d)

    edge_faces = dict_edge_faces(bm)
    connected_faces = dict_face_faces(bm, edge_faces)
    # Turn vertex loops into edge loops
    edgeloops = []
    for loop in loops:
        edgeloop = [[sorted([loop[0][i], loop[0][i + 1]]) for i in
                    range(len(loop[0]) - 1)], loop[1]]
        if loop[1]:  # Circular
            edgeloop[0].append(sorted([loop[0][-1], loop[0][0]]))
        edgeloops.append(edgeloop[:])
    # Variables to keep track while iterating
    all_edgeloops = []
    has_branches = False

    for loop in edgeloops:
        # Initialise with original loop
        all_edgeloops.append(loop[0])
        newloops = [loop[0]]
        verts_used = []
        for edge in loop[0]:
            if edge[0] not in verts_used:
                verts_used.append(edge[0])
            if edge[1] not in verts_used:
                verts_used.append(edge[1])

        # Find parallel loops
        while len(newloops) > 0:
            side_a = []
            side_b = []
            for i in newloops[-1]:
                i = tuple(i)
                forbidden_side = False
                if i not in edge_faces:
                    # weird input with branches
                    has_branches = True
                    break
                for face in edge_faces[i]:
                    if len(side_a) == 0 and forbidden_side != "a":
                        side_a.append(face)
                        if forbidden_side:
                            break
                        forbidden_side = "a"
                    elif side_a[-1] in connected_faces[face] and forbidden_side != "a":
                        side_a.append(face)
                        if forbidden_side:
                            break
                        forbidden_side = "a"
                    elif len(side_b) == 0 and forbidden_side != "b":
                        side_b.append(face)
                        if forbidden_side:
                            break
                        forbidden_side = "b"
                    elif side_b[-1] in connected_faces[face] and forbidden_side != "b":
                        side_b.append(face)
                        if forbidden_side:
                            break
                        forbidden_side = "b"
            if has_branches:
                # Weird input with branches
                break

            newloops.pop(-1)
            sides = []
            if side_a:
                sides.append(side_a)
            if side_b:
                sides.append(side_b)

            for side in sides:
                extraloop = []
                for fi in side:
                    for key in face_edgekeys(bm.faces[fi]):
                        if key[0] not in verts_used and key[1] not in verts_used:
                            extraloop.append(key)
                            break
                if extraloop:
                    for key in extraloop:
                        for new_vert in key:
                            if new_vert not in verts_used:
                                verts_used.append(new_vert)
                    newloops.append(extraloop)
                    all_edgeloops.append(extraloop)

    # Input contains branches, only return selected loop
    if has_branches:
        return loops

    # Change edgeloops into normal loops
    loops = []
    for edgeloop in all_edgeloops:
        loop = []
        # Grow loop by comparing vertices between consecutive edge-keys
        for i in range(len(edgeloop) - 1):
            for vert in range(2):
                if edgeloop[i][vert] in edgeloop[i + 1]:
                    loop.append(edgeloop[i][vert])
                    break
        if loop:
            # Add starting vertex
            for vert in range(2):
                if edgeloop[0][vert] != loop[0]:
                    loop = [edgeloop[0][vert]] + loop
                    break
            # Add ending vertex
            for vert in range(2):
                if edgeloop[-1][vert] != loop[-1]:
                    loop.append(edgeloop[-1][vert])
                    break
            # Check if loop is circular
            if loop[0] == loop[-1]:
                circular = True
                loop = loop[:-1]
            else:
                circular = False
        loops.append([loop, circular])
    return loops

class GRET_OT_vertex_group_smooth_loops(bpy.types.Operator):
    #tooltip
    """Smooth weights for selected vertex loops"""

    bl_idname = 'gret.vertex_group_smooth_loops'
    bl_label = "Smooth Loops"
    bl_options = {'REGISTER', 'UNDO'}

    input_mode: bpy.props.EnumProperty(
        name="Input",
        items=(
            ('ALL', "Parallel (all)", "Also use non-selected parallel loops as input"),
            ('SELECTED', "Selection", "Only use selected vertices as input"),
        ),
        description="Vertex loop selection",
        default='SELECTED',
    )
    group_select_mode: bpy.props.EnumProperty(
        name="Subset",
        items=(
            ('ACTIVE', "Active Group", "The active vertex group"),
            ('BONE_DEFORM', "Deform Pose Bones", "All vertex groups assigned to deform bones"),
            ('ALL', "All", "All vertex groups"),
        ),
        description="Subset of vertex groups to modify",
        default='ACTIVE',
    )
    distance: bpy.props.FloatProperty(
        name="Distance",
        description="Smoothing distance",
        default=0.1,
        min=0.0,
    )
    power: bpy.props.FloatProperty(
        name="Power",
        description="Smoothing power",
        default=1.0,
        min=1.0,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj and obj.type == 'MESH'
            and context.mode == 'PAINT_WEIGHT' and obj.data.use_paint_mask_vertex)

    def execute(self, context):
        # Can't use bpy.ops.object.vertex_group_smooth because weights bleed over from unselected verts
        # Hiding vertices seemed to prevent that, but the operator doesn't work on solitary edge loops
        obj = context.active_object
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

        loops = get_connected_input(bm, parallel=self.input_mode=='ALL')

        # Get list of vertex groups to work on
        if self.group_select_mode == 'ACTIVE':
            vg_idxs = [obj.vertex_groups.active_index]
        elif self.group_select_mode == 'BONE_DEFORM':
            vg_idxs = []
            armature = obj.find_armature()
            if armature:
                bones = armature.data.bones
                vg_idxs = [vg.index for vg in obj.vertex_groups
                    if vg.name in bones and bones[vg.name].use_deform]
        elif self.group_select_mode == 'ALL':
            vg_idxs = list(range(len(obj.vertex_groups)))

        if vg_idxs:
            for vert_idxs, circular in loops:
                for vert in bm.verts:
                    vert.tag = False
                for vert_idx in vert_idxs:
                    bm.verts[vert_idx].tag = True
                for vg_idx in vg_idxs:
                    bmesh_blur_vertex_group(bm, vg_idx, distance=self.distance, power=self.power,
                        only_tagged=True)

        bm.to_mesh(obj.data)
        bm.free()
        context.area.tag_redraw()

        return{'FINISHED'}

def draw_menu(self, context):
    self.layout.operator(GRET_OT_vertex_group_smooth_loops.bl_idname)

def register(settings):
    bpy.utils.register_class(GRET_OT_vertex_group_smooth_loops)
    bpy.types.VIEW3D_MT_paint_weight.append(draw_menu)

def unregister():
    bpy.types.VIEW3D_MT_paint_weight.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_vertex_group_smooth_loops)
