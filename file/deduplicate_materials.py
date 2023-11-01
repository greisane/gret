import bpy
import re

class GRET_OT_deduplicate_materials(bpy.types.Operator):
    """Squashes duplicate materials (.001, .002) into the original material"""

    bl_idname = 'gret.deduplicate_materials'
    bl_label = "Deduplicate Materials"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        # Find duplicate materials
        # For now, duplicate means they are suffixed with ".001", ".002" while the original exists
        delete_materials = []
        for material in bpy.data.materials:
            if match := re.fullmatch(r"(.*)\.\d\d\d", material.name):
                if original_material := bpy.data.materials.get(match[1]):
                    material.user_remap(original_material)
                    delete_materials.append(material)

        # Delete duplicate materials
        for material in delete_materials:
            bpy.data.materials.remove(material)

        self.report({'INFO'}, f"Deleted {len(delete_materials)} duplicate materials.")
        return {'FINISHED'}

def draw_menu(self, context):
    self.layout.operator(GRET_OT_deduplicate_materials.bl_idname)

def register(settings, prefs):
    if not prefs.file__enable_deduplicate_materials:
        return False

    bpy.utils.register_class(GRET_OT_deduplicate_materials)
    bpy.types.TOPBAR_MT_file_cleanup.append(draw_menu)

def unregister():
    bpy.types.TOPBAR_MT_file_cleanup.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_deduplicate_materials)
