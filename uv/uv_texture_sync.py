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
    """Sync UV maps from the active object to other selected objects.
Existing layers are reordered or renamed. Missing UVs are created and extra layers deleted"""

    bl_idname = 'gret.uv_texture_sync'
    bl_label = "Sync UV Maps"
    bl_options = {'REGISTER', 'UNDO'}

    only_set_active: bpy.props.BoolProperty(
        name="Only Set Active Layer",
        description="Don't change UV layers, only set active index and render state",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

    def execute(self, context):
        src_uv_layers = context.active_object.data.uv_layers
        active_render_name = next((uv.name for uv in src_uv_layers if uv.active_render), "")
        num_moved = num_renamed = num_created = num_removed = 0

        for obj in context.selected_objects:
            if obj.type != 'MESH' or obj == context.active_object:
                continue
            dst_uv_layers = obj.data.uv_layers

            if not self.only_set_active:
                # Add empty missing UV layers. Use default names, will be renamed after reordering
                new_layer_names = []
                while len(dst_uv_layers) < len(src_uv_layers):
                    new_layer = dst_uv_layers.new(name="", do_init=False)
                    new_layer_names.append(new_layer.name)
                    num_created += 1
                # Reorder if names match
                for new_index, src_uv_layer in enumerate(src_uv_layers):
                    old_index = dst_uv_layers.find(src_uv_layer.name)
                    if old_index >= 0 and old_index != new_index:
                        num_moved += dst_uv_layers[old_index].name not in new_layer_names
                        move_uv_layer_to_index(dst_uv_layers, old_index, new_index)
                # Rename to match active object
                for src_uv_layer, dst_uv_layer in zip(src_uv_layers, dst_uv_layers):
                    if src_uv_layer.name != dst_uv_layer.name:
                        num_renamed += dst_uv_layer.name not in new_layer_names
                        dst_uv_layer.name = src_uv_layer.name
                # Remove extra UV layers. Collect names first since deleting will cause memory changes
                extra_uv_layer_names = [uv.name for uv in dst_uv_layers if uv.name not in src_uv_layers]
                for uv_layer_name in reversed(extra_uv_layer_names):
                    dst_uv_layers.remove(dst_uv_layers[uv_layer_name])
                    num_removed += 1

            # Sync active and active render state
            if len(src_uv_layers) == len(dst_uv_layers):
                dst_uv_layers.active_index = src_uv_layers.active_index
            if active_render_name in dst_uv_layers:
                dst_uv_layers[active_render_name].active_render = True

        num_last = num_removed or num_created or num_renamed or (num_moved + 1)
        num_zip = zip((num_moved, num_renamed, num_created, num_removed),
            ("reordered", "renamed {}", "created {}", "removed {}"))
        result = ', '.join(s.format(num) for num, s in num_zip if num)
        if result:
            self.report({'INFO'}, result[0].upper() + result[1:] +
                f" UV layer{'s' if num_last > 1 else ''}.")
        else:
            self.report({'INFO'}, f"No UV layers changed.")

        return {'FINISHED'}

    def invoke(self, context, event):
        self.only_set_active = event.ctrl
        return self.execute(context)

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

# Avoid an ImportError since PanelPatcher currently doesn't capture globals
from bl_ui.properties_data_mesh import draw_attribute_warnings
"""

class UVTexturePanelPatcher(PanelPatcher):
    fallback_func = staticmethod(draw_uv_texture_panel_addon)
    panel_type = getattr(bpy.types, 'DATA_PT_uv_texture', None)

    def visit_Call(self, node):
        super().generic_visit(node)
        if getattr(node.func, 'attr', "") == 'template_list':
            # Modify `col.template_list(...)`
            for kw in node.keywords:
                if kw.arg == 'rows':
                    kw.value.value = 4
        return node

    def visit_Expr(self, node):
        super().generic_visit(node)
        try:
            if node.value.func.id == 'draw_attribute_warnings':
                import ast
                module = ast.parse(uv_texture_panel_addon)
                return module.body + [node]
        except AttributeError:
            pass
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
