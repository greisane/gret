import bpy
from bpy.props import BoolProperty, FloatVectorProperty
from mathutils import Vector, Color

bl_info = {
    "name": "Make Plane Cross Section",
    "author": "greisane",
    "description": "Intersects the selected objects with a given plane",
    "version": (0, 1),
    "blender": (2, 79, 0),
    "location": "3D View > Quick Search",
    "category": "Mesh"
}

def color_match(col1, col2, tol=0.001):
    '''
    Return true if vector col1 is within tol of vector col2
    '''
    def vector(col):
        # sanitize range (-2, 3, 5) = (0, 1, 1)
        return Vector([max(0, min(1, c)) for c in col])

    d = vector(col1) - vector(col2)
    return d.length <= tol

class MakePlaneCrossSection(bpy.types.Operator):
    bl_idname = "object.make_plane_cross_section"
    bl_label = "Make Plane Cross Section"
    bl_context = "objectmode"
    bl_options = {'REGISTER', 'UNDO'}

    location = FloatVectorProperty(
        name="Location",
        subtype='TRANSLATION',
        default=(0.0, 0.0, 0.25),
        description='Plane location'
    )
    rotation = FloatVectorProperty(
        name="Rotation",
        subtype='EULER',
        default=(0.0, 0.0, 0.0),
        description='Plane rotation'
    )
    color = FloatVectorProperty(
        name="Color",
        subtype='COLOR',
        default=(1.0, 0.0, 0.0),
        min=0.0, max=1.0,
        description='Plane vertex color'
    )
    delete_previous = bpy.props.BoolProperty(
        name="Delete previous",
        default=True,
        description="Deletes already existing faces matching the plane color"
    )

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == "MESH"

    def execute(self, context):
        scene = context.scene
        objs = [obj for obj in context.selected_objects if obj.type == 'MESH']

        for obj in objs:
            mesh = obj.data

            if self.delete_previous:
                scene.objects.active = obj
                # Clean up previous cross sections (all faces matching the color)
                if mesh.vertex_colors:
                    vcol_layer = mesh.vertex_colors.active
                    for poly in mesh.polygons:
                        poly.select = False
                        for loop_index in poly.loop_indices:
                            if color_match(self.color, vcol_layer.data[loop_index].color):
                                poly.select = True
                                break
                bpy.ops.object.mode_set(mode='EDIT')
                bpy.ops.mesh.select_mode(type='FACE')
                bpy.ops.mesh.delete(type='FACE')
                bpy.ops.object.mode_set(mode='OBJECT')

            # Just make a big plane, easier than fitting it to the target
            bpy.ops.mesh.primitive_plane_add(radius=100.0,
                location=self.location,
                rotation=self.rotation
            )
            plane_obj = bpy.context.object

            # Intersect with original object
            bool_mod = plane_obj.modifiers.new(type='BOOLEAN', name="Intersect")
            bool_mod.object = obj
            bool_mod.operation = 'INTERSECT'
            bpy.ops.object.modifier_apply(modifier=bool_mod.name)

            # Enter edit mode to clean up the result
            # Luckily normals should already be correct and facing up
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.ops.mesh.remove_doubles()
            bpy.ops.mesh.dissolve_limited()
            bpy.ops.object.mode_set(mode='OBJECT')

            # Give it vertex colors
            plane_mesh = plane_obj.data
            if plane_mesh.vertex_colors:
                vcol_layer = plane_mesh.vertex_colors.active
            else:
                vcol_layer = plane_mesh.vertex_colors.new()

            for poly in plane_mesh.polygons:
                for loop_index in poly.loop_indices:
                    vcol_layer.data[loop_index].color[:] = self.color

            # Finally join with original mesh
            plane_obj.select = True
            obj.select = True
            scene.objects.active = obj
            bpy.ops.object.join()

        return {'FINISHED'}

    def invoke(self, context, event):
        return self.execute(context)

def register():
    bpy.utils.register_class(MakePlaneCrossSection)

def unregister():
    bpy.utils.unregister_class(MakePlaneCrossSection)

if __name__ == '__main__':
    register()