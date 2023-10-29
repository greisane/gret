from mathutils import Vector
import bmesh
import bpy

from .helpers import get_selection_loops

def calculate_t(bag, knots):
    """Calculate relative positions compared to first knot."""
    # From mesh_looptools.py by Bart Crouch, Vladimir Spivak (cwolf3d)

    tknots = []
    mknots = []
    prev_uv = None
    prev_co = None
    total_length_uv = 0.0
    total_length_me = 0.0
    for k in knots:
        uv, co = bag[k].uv, bag[k].vert.co
        if prev_uv:
            total_length_uv += (uv - prev_uv).length
            total_length_me += (co - prev_co).length
        tknots.append(total_length_uv)
        mknots.append(total_length_me)
        prev_uv = uv
        prev_co = co
    tpoints = [(length / total_length_me) * total_length_uv for length in mknots]

    return tknots, tpoints

def calculate_verts(tknots, tpoints, points, splines):
    """Change the location of the points to their place on the spline."""
    # From mesh_looptools.py by Bart Crouch, Vladimir Spivak (cwolf3d)

    move = []
    for p in points:
        m = tpoints[points.index(p)]
        if m in tknots:
            n = tknots.index(m)
        else:
            t = tknots[:]
            t.append(m)
            t.sort()
            n = t.index(m) - 1
        if n > len(splines) - 1:
            n = len(splines) - 1
        elif n < 0:
            n = 0
        a, d, t, u = splines[n]
        move.append([p, ((m - t) / u) * d + a])
    return move

def calculate_linear_splines(bag, tknots, knots):
    """Calculates linear splines through all given knots."""
    # From mesh_looptools.py by Bart Crouch, Vladimir Spivak (cwolf3d)

    splines = []
    for i in range(len(knots) - 1):
        a = bag[knots[i]].uv
        b = bag[knots[i + 1]].uv
        d = b - a
        t = tknots[i]
        u = tknots[i + 1] - t
        splines.append([a, d, t, u])  # [locStart, locDif, tStart, tDif]
    return splines

class GRET_OT_relax_loops(bpy.types.Operator):
    """Relax selected edge loops to their respective mesh length."""

    bl_idname = 'gret.relax_loops'
    bl_label = "Relax Loops"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj and obj.type == 'MESH' and obj.mode == 'EDIT' and obj.data.uv_layers
            and context.area.type == 'IMAGE_EDITOR'
            and not context.scene.tool_settings.use_uv_select_sync
            and context.scene.tool_settings.uv_select_mode in {'EDGE', 'VERTEX'})

    def execute(self, context):
        obj = context.active_object
        bm = bmesh.from_edit_mesh(obj.data)
        uv_layer = bm.loops.layers.uv.verify()

        for loop in get_selection_loops(bm):
            indices = list(range(len(loop)))
            if loop.is_closed:
                indices.append(indices[0])

            # Calculate splines and new positions
            tknots, tpoints = calculate_t(loop, indices)
            splines = calculate_linear_splines(loop, tknots, indices)
            move = calculate_verts(tknots, tpoints, indices[:-1], splines)

            for index, uv in move:
                for bmloop in loop[index].bmloops:
                    bmloop[uv_layer].uv = uv

        bmesh.update_edit_mesh(obj.data)
        return {'FINISHED'}

def draw_menu(self, context):
    self.layout.separator()
    self.layout.operator(GRET_OT_relax_loops.bl_idname)

def register(settings, prefs):
    if not prefs.uv__enable_relax_loops:
        return False

    bpy.utils.register_class(GRET_OT_relax_loops)
    bpy.types.IMAGE_MT_uvs.append(draw_menu)

def unregister():
    bpy.types.IMAGE_MT_uvs.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_relax_loops)
