import bpy

bl_info = {
    "name": "Toggle Wireframe",
    "author": "greisane",
    "description": "Toggles wireframe display for selected objects",
    "version": (0, 1),
    "blender": (2, 80, 0),
    "location": "3D View > Quick Search",
    "category": "Object"
}

class OBJECT_OT_toggle_wireframe(bpy.types.Operator):
    #tooltip
    """Toggles wireframe display for all selected objects"""

    bl_idname = "object.toggle_wireframe"
    bl_label = "Toggle Wireframe"
    bl_context = "objectmode"

    def execute(self, context):
        all_show_wire = all(obj.show_wire for obj in context.selected_objects)

        for obj in context.selected_objects:
            obj.show_wire = not all_show_wire
            obj.show_all_edges = True

        return {'FINISHED'}

class OBJECT_OT_toggle_wireframe_all(bpy.types.Operator):
    #tooltip
    """Toggles wireframe display for all textured meshes"""

    bl_idname = "object.toggle_wireframe_all"
    bl_label = "Toggle Wireframe All"

    def execute(self, context):
        all_show_wire = True

        for obj in context.scene.objects:
            if obj.type == 'MESH' and obj.draw_type == 'TEXTURED':
                all_show_wire &= obj.show_wire
                if not all_show_wire:
                    break

        for obj in context.scene.objects:
            if obj.type == 'MESH' and obj.draw_type == 'TEXTURED':
                obj.show_wire = not all_show_wire
                obj.show_all_edges = True

        return {'FINISHED'}

def register():
    bpy.utils.register_class(OBJECT_OT_toggle_wireframe)
    bpy.utils.register_class(OBJECT_OT_toggle_wireframe_all)

def unregister():
    bpy.utils.unregister_class(OBJECT_OT_toggle_wireframe_all)
    bpy.utils.unregister_class(OBJECT_OT_toggle_wireframe)

if __name__ == '__main__':
    register()