from io import StringIO
import bpy
import csv

from .. import prefs
from ..patcher import PanelPatcher

def dump_shape_key_info(sk):
    return [sk.name, "1" if sk.mute else "0", sk.value, sk.slider_min, sk.slider_max]

def load_shape_key_info(obj, fields):
    name, mute, value, slider_min, slider_max = fields[:5]
    sk = obj.data.shape_keys.key_blocks.get(name)
    if not sk:
        return
    if prefs.mesh__shape_key_presets_load_minmax:
        sk.slider_min = float(slider_min)
        sk.slider_max = float(slider_max)
    sk.mute = mute == "1"
    sk.value = float(value)

class GRET_PG_shape_key_preset(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(
        name="Name",
        description="Name of this preset",
    )
    data: bpy.props.StringProperty(
        name="Data",
        options={'HIDDEN'},
    )

class GRET_OT_shape_key_preset(bpy.types.Operator):
    """Load shape key preset. Ctrl-Click to save or discard"""

    bl_idname = 'gret.shape_key_preset'
    bl_label = "Shape Key Preset"
    bl_context = 'objectmode'
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty(options={'HIDDEN'})
    load: bpy.props.BoolProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.active_object and hasattr(context.active_object.data, 'shape_key_storage')

    def execute(self, context):
        obj = context.active_object
        slots = obj.data.shape_key_storage

        # For human readability, store info as comma separated values instead of using save_properties
        if self.load:
            # Load from slot
            data = StringIO(slots[self.index].data if self.index < len(slots) else "")

            for row in csv.reader(data):
                load_shape_key_info(obj, row)
        elif self.index >= len(slots) or not slots[self.index].data:
            # Save to slot
            data = StringIO()
            writer = csv.writer(data)
            for sk in obj.data.shape_keys.key_blocks:
                writer.writerow(dump_shape_key_info(sk))

            while self.index >= len(slots):
                slots.add()
            slots[self.index].data = data.getvalue()
        else:
            # Clear slot
            if self.index < len(slots):
                slots[self.index].data = ""

            # Trim empty slots
            while slots and not slots[-1].data:
                slots.remove(len(slots) - 1)

        return {'FINISHED'}

    def invoke(self, context, event):
        self.load = not event.ctrl
        return self.execute(context)

class GRET_OT_shape_key_clear(bpy.types.Operator):
    """Clear weights for all shape keys. Ctrl-Click to mute all instead"""

    bl_idname = 'gret.shape_key_clear'
    bl_label = "Clear Shape Keys"
    bl_context = 'objectmode'
    bl_options = {'REGISTER', 'UNDO'}

    mute: bpy.props.BoolProperty(
        name="Mute",
        description="Mute shape keys instead of clearing weights",
    )

    @classmethod
    def poll(cls, context):
        return context.active_object and hasattr(context.active_object.data, 'shape_key_storage')

    def execute(self, context):
        obj = context.active_object

        if not self.mute:
            # Can't set value to 0.0 myself when slider_min is greater, so call the native operator
            bpy.ops.object.shape_key_clear()
        else:
            for sk in obj.data.shape_keys.key_blocks:
                sk.mute = True

        return {'FINISHED'}

    def invoke(self, context, event):
        self.mute = event.ctrl
        return self.execute(context)

def draw_shape_key_panel_addon(self, context):
    layout = self.layout
    obj = context.active_object
    slots = getattr(obj.data, 'shape_key_storage', None)

    if slots is not None and obj.data.shape_keys:
        box = layout.box()
        sub = box.split(factor=0.2)
        sub.label(text="Slots")
        row = sub.row(align=True)
        for slot_idx in range(prefs.mesh__shape_key_presets_num_slots):
            has_data = slot_idx < len(slots) and bool(slots[slot_idx].data)
            text = chr(ord('A') + min(slot_idx, 25))
            op = row.operator('gret.shape_key_preset', text=text, depress=has_data)
            op.index = slot_idx
        row.separator()
        row.operator('gret.shape_key_clear', text="", icon='X')

shape_key_panel_slots_addon = """
slots = getattr(ob.data, 'shape_key_storage', None)
if slots is not None:
    subsub = sub.row(align=True)
    subsub.scale_x = 0.6
    for slot_idx in range({num_slots}):
        has_data = slot_idx < len(slots) and bool(slots[slot_idx].data)
        text = chr(ord('A') + min(slot_idx, 25))
        op = subsub.operator('gret.shape_key_preset', text=text, depress=has_data)
        op.index = slot_idx
    sub.separator()
"""

class ShapeKeyPanelPatcher(PanelPatcher):
    fallback_func = staticmethod(draw_shape_key_panel_addon)
    panel_type = getattr(bpy.types, 'DATA_PT_shape_keys', None)
    num_slots = 5

    def visit_Call(self, node):
        super().generic_visit(node)
        # Modify `split = layout.split(factor=0.4)`
        if node.func.attr == 'split':
            for kw in node.keywords:
                if kw.arg == 'factor':
                    kw.value.value = 0.25
        # Modify `sub.operator('object.shape_key_clear', icon='X', text='')`
        if node.func.attr == 'operator':
            for arg in node.args:
                if arg.value == 'object.shape_key_clear':
                    arg.value = 'gret.shape_key_clear'
        return node

    def visit_Expr(self, node):
        super().generic_visit(node)
        # Add slot selector after `sub.label()`
        if node.value.func.attr == 'label':
            import ast
            tree_addon = ast.parse(shape_key_panel_slots_addon.format(num_slots=self.num_slots))
            return [node, *tree_addon.body]
        return node

panel_patcher = ShapeKeyPanelPatcher()

classes = (
    GRET_OT_shape_key_clear,
    GRET_OT_shape_key_preset,
    GRET_PG_shape_key_preset,
)

def register(settings, prefs):
    if not prefs.mesh__enable_shape_key_presets:
        return False

    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Mesh.shape_key_storage = bpy.props.CollectionProperty(
        type=GRET_PG_shape_key_preset,
    )
    bpy.types.Lattice.shape_key_storage = bpy.props.CollectionProperty(
        type=GRET_PG_shape_key_preset,
    )
    panel_patcher.num_slots = prefs.mesh__shape_key_presets_num_slots
    panel_patcher.patch(debug=False)

def unregister():
    panel_patcher.unpatch()
    del bpy.types.Mesh.shape_key_storage
    del bpy.types.Lattice.shape_key_storage

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
