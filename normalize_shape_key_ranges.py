import bpy

bl_info = {
    "name": "Normalize Shape Key Ranges",
    "description": "Resets min/max of shape keys while keeping the range of motion",
    "author": "greisane",
    "version": (0, 1, 2),
    "blender": (2, 79, 0),
    "location": "Properties Editor > Object Data > Shape Keys > Specials Menu",
    "category": "Mesh"
}

class NormalizeShapeKeyRanges(bpy.types.Operator):
    bl_idname = "object.normalize_shape_key_ranges"
    bl_label = "Normalize Shape Key Ranges"
    bl_context = "objectmode"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.mode == "OBJECT" and context.object.type == "MESH"

    def execute(self, context):
        obj = context.object
        shape_keys = obj.data.shape_keys.key_blocks[:]
        new_values = {'BASIS_MIN' : 1.0}

        # Reset all shape keys before anything else
        for sk in shape_keys:
            new_values[sk.name] = (sk.value - sk.slider_min) / (sk.slider_max - sk.slider_min)
            sk.value = 0.0

        for sk in shape_keys:
            # Create a new shape key from the maximum range of motion
            sk.slider_max = sk.slider_max - sk.slider_min
            sk.value = sk.slider_max
            name = sk.name
            sk.name = '_' + sk.name
            obj.shape_key_add(name)
            sk.value = 0.0

        # Create the new basis
        for sk in shape_keys:
            sk.value = sk.slider_min
        obj.shape_key_add('BASIS_MIN')

        # Delete the original shapes
        for sk in shape_keys:
            obj.shape_key_remove(sk)

        # Finally update the new shapes values so that the result is unchanged
        for sk in obj.data.shape_keys.key_blocks:
            sk.value = new_values[sk.name]

        return {'FINISHED'}

def shape_key_specials_draw(self, context):
    self.layout.operator(NormalizeShapeKeyRanges.bl_idname, icon='X')

def register():
    bpy.utils.register_module(__name__)
    bpy.types.MESH_MT_shape_key_specials.append(shape_key_specials_draw)

def unregister():
    bpy.utils.unregister_module(__name__)
    bpy.types.MESH_MT_shape_key_specials.remove(shape_key_specials_draw)

if __name__ == '__main__':
    register()