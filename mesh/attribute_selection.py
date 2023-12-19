import bmesh
import bpy
import numpy as np

# Face maps nuked in 4.0 and converted to boolean attributes. There's no way for users to
# retrieve this data. Finally, boolean layers aren't accessible from bmesh and attributes
# can't be read from edit mode. Very cool
# These operators could easily work for edge/vertex selection as well, faces only is for clarity

class GRET_OT_attribute_to_selection(bpy.types.Operator):
    """Set the current face selection from a boolean face attribute"""

    bl_idname = 'gret.attribute_to_selection'
    bl_label = "Select Faces by Values"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return (obj
            and obj.type == 'MESH'
            and obj.data.attributes.active
            and obj.data.attributes.active.domain == 'FACE'
            and obj.data.attributes.active.data_type == 'BOOLEAN')

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data
        attr = mesh.attributes.active

        if bpy.context.mode == 'EDIT_MESH':
            # Need to exit editmode since it's not possible to read boolean attributes
            # Active attribute may change unexpectedly when editmode is toggled (even its domain??)
            attr_name = attr.name
            bpy.ops.mesh.select_all(action='DESELECT')
            bpy.ops.object.editmode_toggle()
            attr = mesh.attributes[attr_name]

        values = np.empty(len(mesh.polygons), dtype=bool)
        attr.data.foreach_get('value', values)
        mesh.vertices.foreach_set('select', np.zeros(len(mesh.vertices), dtype=bool))
        mesh.edges.foreach_set('select', np.zeros(len(mesh.edges), dtype=bool))
        mesh.polygons.foreach_set('select', values)
        bpy.ops.object.editmode_toggle()
        bpy.ops.mesh.select_mode(type='FACE')

        return {'FINISHED'}

class GRET_OT_attribute_from_selection(bpy.types.Operator):
    """Update or create a boolean face attribute from the current edit mode face selection"""

    bl_idname = 'gret.attribute_from_selection'
    bl_label = "Face Selection to Values"
    bl_context = 'mesh_edit'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return context.mode == 'EDIT_MESH' and obj and obj.type == 'MESH'

    def execute(self, context):
        obj = context.active_object
        mesh = obj.data

        # Active attribute may change unexpectedly when editmode is toggled (even its domain??)
        attr = mesh.attributes.active
        attr_name = (attr.name
            if attr and attr.domain == 'FACE' and attr.data_type == 'BOOLEAN'
            else None)

        bpy.ops.object.editmode_toggle()

        attr = (mesh.attributes[attr_name]
            if attr_name
            else mesh.attributes.new("Selection", type='BOOLEAN', domain='FACE'))

        # Unselected faces should be set to False, so bpy.ops.mesh.attribute_set won't do the trick
        values = np.empty(len(mesh.polygons), dtype=bool)
        mesh.polygons.foreach_get('select', values)
        attr.data.foreach_set('value', values)

        bpy.ops.object.editmode_toggle()

        return {'FINISHED'}

def draw_menu(self, context):
    self.layout.operator(GRET_OT_attribute_to_selection.bl_idname)
    self.layout.operator(GRET_OT_attribute_from_selection.bl_idname)

def register(settings, prefs):
    if not prefs.mesh__enable_attribute_selection:
        return False

    bpy.utils.register_class(GRET_OT_attribute_to_selection)
    bpy.utils.register_class(GRET_OT_attribute_from_selection)
    bpy.types.MESH_MT_attribute_context_menu.append(draw_menu)

def unregister():
    bpy.types.MESH_MT_attribute_context_menu.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_attribute_to_selection)
    bpy.utils.unregister_class(GRET_OT_attribute_from_selection)
