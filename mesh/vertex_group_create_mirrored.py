import bpy

flip_suffix = {
    '.l': '.r',
    '.r': '.l',
    '.L': '.R',
    '.R': '.L',
    '_l': '_r',
    '_r': '_l',
    '_L': '_R',
    '_R': '_L',
}

class GRET_OT_vertex_group_create_mirrored(bpy.types.Operator):
    """Create any missing mirror vertex groups. New vertex groups will be empty"""

    bl_idname = "gret.vertex_group_create_mirrored"
    bl_label = "Create Mirrored Vertex Groups"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        obj.update_from_editmode()

        for name in [vg.name for vg in obj.vertex_groups]:
            suffix = name[-2:]
            if suffix in flip_suffix:
                name = name.removesuffix(suffix) + flip_suffix[suffix]
                if name not in obj.vertex_groups:
                    obj.vertex_groups.new(name=name)

        return {'FINISHED'}

def draw_menu(self, context):
    self.layout.operator(GRET_OT_vertex_group_create_mirrored.bl_idname, icon='ARROW_LEFTRIGHT')

def register(settings, prefs):
    if not prefs.mesh__enable_vertex_group_create_mirrored:
        return False

    bpy.utils.register_class(GRET_OT_vertex_group_create_mirrored)
    bpy.types.MESH_MT_vertex_group_context_menu.append(draw_menu)

def unregister():
    bpy.types.MESH_MT_vertex_group_context_menu.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_vertex_group_create_mirrored)
