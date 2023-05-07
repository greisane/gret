import bpy

from ..helpers import flip_name

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

        vg_idx_used = {vg_idx: vg.lock_weight for vg_idx, vg in enumerate(vgroups)}

        for vert in obj.data.vertices:
            for vg in vert.groups:
                if vg.weight > 0.0:
                    vg_idx_used[vg.group] = True

        if any(mod.type == 'MIRROR' and mod.use_mirror_vertex_groups for mod in obj.modifiers):
            # Mark mirror vertex groups as used
            for vg_idx, used in vg_idx_used.items():
                if used:
                    flipped_name = flip_name(vgroups[vg_idx].name, suffix_only=True)
                    if flipped_name:
                        other_vg_idx = vgroups.find(flipped_name)
                        if other_vg_idx >= 0:
                            vg_idx_used[other_vg_idx] = True

        # Delete in reverse to not upset the indices
        for vg_idx in sorted((vg_idx for vg_idx, used in vg_idx_used.items() if not used), reverse=True):
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
