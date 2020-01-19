from math import pi, atan2
import bpy
from bpy.props import BoolProperty, FloatProperty, StringProperty, EnumProperty
from mathutils import Vector, Color
import random

bl_info = {
    "name": "Grass Information to Vertex Color",
    "author": "greisane",
    "description": "Encodes information useful for grass shaders into vertex colors",
    "version": (0, 2),
    "blender": (2, 79, 0),
    "location": "3D View > Quick Search",
    "category": "Mesh"
}
class GrassInformationToVertexColor(bpy.types.Operator):
    bl_idname = "object.grass_information_to_vertex_color"
    bl_label = "Grass Information to Vertex Color"
    bl_context = "objectmode"
    bl_options = {'REGISTER', 'UNDO'}

    items = [('object', "Object", "World object location"),
        ('vertex', "Vertex", "Vertex to pivot difference (unrotated vertex location)"),
        ('distance', "Distance", "Vertex to pivot distance"),
        ('xz_distance', "XY Distance", "Vertex to pivot distance (no Z)"),
        ('height', "Height", "Clamped Z up to maximum height"),
        ('rotation', "Rotation", "Rotation of object around up axis (angle of green arrow)"),
        ('facing', "Facing", "Rotation of vertex normal around up axis"),
        ('bevel_weight', "Bevel weight", "Vertex bevel weight"),
        ('random', "Random", "Random value based on the object's name"),
        ('zero', "Zero", "0.0"),
        ('one', "One", "1.0"),
        ('none', "None", "Leave channel untouched")]
    r_channel = bpy.props.EnumProperty(items=items, name="R Channel", default='object')
    g_channel = bpy.props.EnumProperty(items=items, name="G Channel", default='object')
    b_channel = bpy.props.EnumProperty(items=items, name="B Channel", default='vertex')
    maximum_distance = FloatProperty(name="Maximum distance", default=2.0,
        min=0.001, precision=4, step=1, unit='LENGTH')
    maximum_height = FloatProperty(name="Maximum height", default=1.0,
        min=0.001, precision=4, step=1, unit='LENGTH')
    invert_y = bpy.props.BoolProperty(name="Invert Y", default=True,
        description="Flip Y channel")

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH"

    def execute(self, context):
        scene = context.scene
        clipping = False

        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue
            mesh = obj.data

            # Get or create vertex color data
            scene.objects.active = obj
            obj.select = True
            if mesh.vertex_colors:
                vcol_layer = mesh.vertex_colors.active
            else:
                vcol_layer = mesh.vertex_colors.new()

            def get_element(type, loop_index, component_index):
                if type == 'object':
                    d = obj.location[component_index] / self.maximum_distance + 0.5
                    # if not clipping and d < 0.0 or d > 1.0:
                    #     clipping = True
                    return d
                elif type == 'vertex':
                    vertex_index = mesh.loops[loop_index].vertex_index
                    co = obj.matrix_world * mesh.vertices[vertex_index].co
                    d = (obj.location - co)[component_index] / self.maximum_distance + 0.5
                    # if not clipping and d < 0.0 or d > 1.0:
                    #     clipping = True
                    return d
                elif type == 'distance':
                    vertex_index = mesh.loops[loop_index].vertex_index
                    co = mesh.vertices[vertex_index].co
                    d = co.length / self.maximum_distance
                    # if not clipping and d < 0.0 or d > 1.0:
                    #     clipping = True
                    return d
                elif type == 'xz_distance':
                    vertex_index = mesh.loops[loop_index].vertex_index
                    co = mesh.vertices[vertex_index].co.copy()
                    co.z = 0.0
                    d = co.length / self.maximum_distance
                    # if not clipping and d < 0.0 or d > 1.0:
                    #     clipping = True
                    return d
                elif type == 'height':
                    vertex_index = mesh.loops[loop_index].vertex_index
                    co = mesh.vertices[vertex_index].co
                    h = max(0.0, min(1.0, abs(co.z / self.maximum_height)))
                    return h
                elif type == 'rotation':
                    return obj.rotation_euler.z % pi / pi
                elif type == 'facing':
                    vertex_index = mesh.loops[loop_index].vertex_index
                    n = mesh.vertices[vertex_index].normal
                    # Flatten normal and get the angle it's pointing at
                    n.z = 0.0
                    n.normalize()
                    return atan2(-n.x, n.y) % pi / pi # Rotated 90 deg
                elif type == 'random':
                    return hash(obj.name) % 256 / 256
                elif type == 'bevel_weight':
                    vertex_index = mesh.loops[loop_index].vertex_index
                    return mesh.vertices[vertex_index].bevel_weight
                elif type == 'none':
                    return vcol_layer.data[loop_index].color[component_index]
                elif type == 'zero':
                    return 0.0
                elif type== 'one':
                    return 1.0

            for poly in mesh.polygons:
                for loop_index in poly.loop_indices:
                    c = Color()
                    c.r = get_element(self.r_channel, loop_index, 0)
                    c.g = get_element(self.g_channel, loop_index, 1)
                    c.b = get_element(self.b_channel, loop_index, 2)
                    c.g = c.g if not self.invert_y else 1.0 - c.g

                    vcol_layer.data[loop_index].color[:] = c

        if clipping:
            self.report({'WARNING'}, 'Position clipping, increase maximum distance')

        return {'FINISHED'}

    def invoke(self, context, event):
        return self.execute(context)

def register():
    bpy.utils.register_class(GrassInformationToVertexColor)

def unregister():
    bpy.utils.unregister_class(GrassInformationToVertexColor)

if __name__ == '__main__':
    register()