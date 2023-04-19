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

class GRET_OT_vertex_group_remove_unused(bpy.types.Operator):
    """Delete vertex groups with no assigned weights"""

    bl_idname = "gret.vertex_group_remove_unused"
    bl_label = "Remove Unused Vertex Groups"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        # Based on the addon by CoDEmanX, respects vertex groups claimed by mirror modifiers
        obj = context.active_object
        obj.update_from_editmode()
        vgroups = obj.vertex_groups

        vgroup_used = {i: vg.lock_weight for i, vg in enumerate(vgroups)}
        for vert in obj.data.vertices:
            for vg in vert.groups:
                if vg.weight > 0.0:
                    vgroup_used[vg.group] = True

        if any(mod.type == 'MIRROR' and mod.use_mirror_vertex_groups for mod in obj.modifiers):
            # Don't remove empty groups that the mirror modifier needs
            for vg_idx, used in vgroup_used.items():
                suffix = vgroups[vg_idx].name[-2:]
                if used and suffix in flip_suffix:
                    j = vgroups.find(vgroups[vg_idx].name[:-2] + flip_suffix[suffix])
                    if j >= 0:
                        vgroup_used[j] = True

        for vg_idx, used in sorted(vgroup_used.items(), reverse=True):
            if not used:
                vgroups.remove(vgroups[vg_idx])

        return {'FINISHED'}

def draw_menu(self, context):
    self.layout.operator(GRET_OT_vertex_group_remove_unused.bl_idname, icon='X')

def register(settings, prefs):
    if not prefs.mesh__enable_vertex_group_remove_unused:
        return False

    bpy.utils.register_class(GRET_OT_vertex_group_remove_unused)
    bpy.types.MESH_MT_vertex_group_context_menu.append(draw_menu)

def unregister():
    bpy.types.MESH_MT_vertex_group_context_menu.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_vertex_group_remove_unused)
