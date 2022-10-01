import bpy

from ..patcher import PanelPatcher

def move_uv_layer_last(uv_layers, index):
    saved_active_index = uv_layers.active_index
    uv_layers.active_index = index
    name = uv_layers.active.name
    uv_layers.active.name = uv_layers.active.name + "_"  # Free up the current name
    uv_layers.new(name=name, do_init=True)  # do_init causes the active UV map to be duplicated
    uv_layers.remove(uv_layers[index])
    uv_layers.active_index = saved_active_index

def move_uv_layer_to_index(uv_layers, from_index, to_index):
    assert from_index >= 0 and from_index < len(uv_layers)
    assert to_index >= 0 and to_index < len(uv_layers)
    if from_index == to_index:
        return

    # I'm sure there's a more elegant way
    if from_index <= to_index:
        for _ in range(to_index, from_index, -1):
            move_uv_layer_last(uv_layers, from_index + 1)
        for _ in range(0, len(uv_layers) - to_index):
            move_uv_layer_last(uv_layers, from_index)
    else:
        move_uv_layer_last(uv_layers, from_index)
        for _ in range(to_index, from_index, -1):
            move_uv_layer_last(uv_layers, from_index + 1)
        for _ in range(0, len(uv_layers) - to_index - 1):
            move_uv_layer_last(uv_layers, min(from_index, to_index))

class GRET_OT_uv_texture_move(bpy.types.Operator):
    #tooltip
    """Move the active UV map up/down in the list"""

    bl_idname = 'gret.uv_texture_move'
    bl_label = "Move UV Map"
    bl_options = {'REGISTER', 'UNDO'}

    direction: bpy.props.EnumProperty(
        name="Direction",
        description="Direction in the list to move the UV map",
        items = (
            ('UP', "Up", "Move UV map up"),
            ('DOWN', "Down", "Move UV map down"),
        ),
    )

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        uv_layers = context.active_object.data.uv_layers
        if len(uv_layers) == 1:
            return {'FINISHED'}
        index = uv_layers.active_index

        if self.direction == 'UP' and index > 0:
            move_uv_layer_to_index(uv_layers, index, index - 1)
            uv_layers.active_index -= 1
        elif self.direction == 'DOWN' and index < len(uv_layers) - 1:
            move_uv_layer_to_index(uv_layers, index, index + 1)
            uv_layers.active_index += 1

        return {'FINISHED'}

class GRET_OT_uv_texture_sync(bpy.types.Operator):
    #tooltip
    """Sync UV maps from the active object to other selected objects"""

    bl_idname = 'gret.uv_texture_sync'
    bl_label = "Sync UV Maps"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        src_uv_layers = context.active_object.data.uv_layers
        active_render_name = next((uv.name for uv in src_uv_layers if uv.active_render), None)

        for obj in context.selected_objects:
            if obj.type != 'MESH' or obj == context.active_object:
                continue
            dst_uv_layers = obj.data.uv_layers
            # Remove extra UV layers. Collect names first since deleting will cause memory changes
            extra_uv_layer_names = [uv.name for uv in dst_uv_layers if uv.name not in src_uv_layers]
            for uv_layer_name in reversed(extra_uv_layer_names):
                dst_uv_layers.remove(dst_uv_layers[uv_layer_name])
            # Add empty missing UV layers
            for src_uv_layer in src_uv_layers:
                if src_uv_layer.name not in dst_uv_layers:
                    dst_uv_layers.new(name=src_uv_layer.name, do_init=False)
            # Reorder to match active object
            for src_uv_layer in src_uv_layers:
                index = dst_uv_layers.find(src_uv_layer.name)
                move_uv_layer_last(dst_uv_layers, index)
            # Sync active and active render state
            dst_uv_layers.active_index = src_uv_layers.active_index
            if active_render_name is not None:
                dst_uv_layers[active_render_name].active_render = True

        return {'FINISHED'}

def draw_uv_texture_panel_addon(self, context):
    layout = self.layout
    row = layout.row(align=True)
    op = row.operator('gret.uv_texture_move', icon='TRIA_UP', text="")
    op.direction = 'UP'
    op = row.operator('gret.uv_texture_move', icon='TRIA_DOWN', text="")
    op.direction = 'DOWN'
    row.operator('gret.uv_texture_sync', icon='UV_SYNC_SELECT', text="")

uv_texture_panel_addon = """
col.separator()
op = col.operator('gret.uv_texture_move', icon='TRIA_UP', text="")
op.direction = 'UP'
op = col.operator('gret.uv_texture_move', icon='TRIA_DOWN', text="")
op.direction = 'DOWN'
col.operator('gret.uv_texture_sync', icon='UV_SYNC_SELECT', text="")
"""

class UVTexturePanelPatcher(PanelPatcher):
    fallback_func = staticmethod(draw_uv_texture_panel_addon)
    panel_type = getattr(bpy.types, "DATA_PT_uv_texture", None)

    def visit_Call(self, node):
        super().generic_visit(node)
        # Modify `col.template_list(...)`
        if node.func.attr == "template_list":
            for kw in node.keywords:
                if kw.arg == "rows":
                    kw.value.value = 4
        return node

    def visit_FunctionDef(self, node):
        super().generic_visit(node)
        # Add more buttons at the end
        import ast
        tree_addon = ast.parse(uv_texture_panel_addon)
        node.body += tree_addon.body
        return node

panel_patcher = UVTexturePanelPatcher()

classes = (
    GRET_OT_uv_texture_move,
    GRET_OT_uv_texture_sync,
)

def register(settings, prefs):
    if not prefs.uv__texture_sync:
        return False

    for cls in classes:
        bpy.utils.register_class(cls)

    panel_patcher.patch(debug=False)

def unregister():
    panel_patcher.unpatch()

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
