import bpy
import re

class GRET_OT_deduplicate_materials(bpy.types.Operator):
    #tooltip
    """Deletes duplicate materials and fixes meshes referencing them"""

    bl_idname = 'gret.deduplicate_materials'
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

def draw_menu(self, context):
    self.layout.operator(GRET_OT_deduplicate_materials.bl_idname)

def register(settings):
    bpy.utils.register_class(GRET_OT_deduplicate_materials)
    bpy.types.TOPBAR_MT_file_cleanup.append(draw_menu)

def unregister():
    bpy.types.TOPBAR_MT_file_cleanup.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_deduplicate_materials)
