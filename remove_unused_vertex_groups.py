bl_info = {
    "name": "Remove Unused Vertex Groups",
    "author": "CoDEmanX, greisane",
    "version": (1, 1),
    "blender": (2, 80, 0),
    "location": "Properties Editor > Object Data > Vertex Groups > Specials Menu",
    "description": "Deletes vertex groups with no assigned weight",
    "warning": "",
    "wiki_url": "",
    "category": "Mesh"}

import bpy
from bpy.types import Operator

class OBJECT_OT_vertex_group_remove_unused(Operator):
    bl_idname = "object.vertex_group_remove_unused"
    bl_label = "Remove Unused Vertex Groups"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH'

    def execute(self, context):
        ob = context.object
        ob.update_from_editmode()
        vgroups = ob.vertex_groups

        vgroup_used = {i: False for i, k in enumerate(vgroups)}
        for v in ob.data.vertices:
            for g in v.groups:
                if g.weight > 0.0:
                    vgroup_used[g.group] = True

        if any(m.type == 'MIRROR' and m.use_mirror_vertex_groups for m in ob.modifiers):
            # Don't remove empty groups that the mirror modifier needs
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
            for i, used in vgroup_used.items():
                suffix = vgroups[i].name[-2:]
                if used and suffix in flip_suffix:
                    j = vgroups.find(vgroups[i].name[:-2] + flip_suffix[suffix])
                    if j >= 0:
                        vgroup_used[j] = True

        for i, used in sorted(vgroup_used.items(), reverse=True):
            if not used:
                vgroups.remove(vgroups[i])

        return {'FINISHED'}

def draw_func(self, context):
    self.layout.operator(OBJECT_OT_vertex_group_remove_unused.bl_idname, icon='X')

def register():
    bpy.utils.register_class(OBJECT_OT_vertex_group_remove_unused)
    vertex_group_menu = (bpy.types.MESH_MT_vertex_group_specials if bpy.app.version < (2, 80) else
        bpy.types.MESH_MT_vertex_group_context_menu)
    vertex_group_menu.append(draw_func)

def unregister():
    bpy.utils.unregister_class(OBJECT_OT_vertex_group_remove_unused)
    vertex_group_menu = (bpy.types.MESH_MT_vertex_group_specials if bpy.app.version < (2, 80) else
        bpy.types.MESH_MT_vertex_group_context_menu)
    vertex_group_menu.remove(draw_func)

if __name__ == "__main__":
    register()