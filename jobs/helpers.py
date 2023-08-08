import bpy

from ..log import logger, log, logd

def clone_obj(context, obj, parent=None, convert_to_mesh=False):
    """Clones or converts a mesh object. Returns a new, visible scene object with an unique mesh."""

    new_obj = new_mesh = None
    try:
        if obj.type == 'MESH':
            new_obj = obj.copy()
            new_obj.name = obj.name + "_"
            new_mesh = obj.data.copy()
            new_obj.data = new_mesh
        elif convert_to_mesh:
            dg = bpy.context.evaluated_depsgraph_get()
            new_mesh = bpy.data.meshes.new_from_object(obj, preserve_all_data_layers=True, depsgraph=dg)
            new_obj = bpy.data.objects.new(obj.name + "_", new_mesh)
        else:
            raise RuntimeError("Unhandled object type")
        assert isinstance(new_mesh, bpy.types.Mesh)
        assert new_mesh.users == 1

        # Move object materials to mesh
        for mat_idx, mat_slot in enumerate(obj.material_slots):
            if mat_slot.link == 'OBJECT':
                new_mesh.materials[mat_idx] = mat_slot.material
                new_obj.material_slots[mat_idx].link = 'DATA'

        # New objects are moved to the scene collection, ensuring they're visible
        context.scene.collection.objects.link(new_obj)
        new_obj.hide_set(False)
        new_obj.hide_viewport = False
        new_obj.hide_select = False
        new_obj.parent = parent
        new_obj.matrix_world = obj.matrix_world
    except Exception as e:
        if new_obj:
            bpy.data.objects.remove(new_obj)
        if new_mesh:
            bpy.data.meshes.remove(new_mesh)
        logd(f"Couldn't clone object {obj.name}: {e}")

    return new_obj
