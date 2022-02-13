from io import StringIO
import bpy
import csv

max_shape_key_slots = 5

def dump_shape_key_info(sk):
    return [sk.name, "1" if sk.mute else "0", sk.value, sk.slider_min, sk.slider_max,
        sk.vertex_group, sk.relative_key.name, sk.interpolation]

def load_shape_key_info(obj, fields):
    name, mute, value, slider_min, slider_max, vertex_group, relative_key, interpolation = fields
    sk = obj.data.shape_keys.key_blocks.get(name)
    if not sk:
        return
    sk.mute = mute == "1"
    sk.slider_min = float(slider_min)
    sk.slider_max = float(slider_max)
    sk.value = float(value)
    sk.vertex_group = vertex_group
    sk.relative_key = obj.data.shape_keys.key_blocks.get(relative_key)
    sk.interpolation = interpolation

class GRET_PG_shape_key_storage(bpy.types.PropertyGroup):
    name: bpy.props.StringProperty(
        name="Name",
        description="Name of this storage slot",
    )
    data: bpy.props.StringProperty(
        name="Data",
        options={'HIDDEN'},
    )

class GRET_OT_shape_key_store(bpy.types.Operator):
    #tooltip
    """Load shape key values stored in this slot. Ctrl-click to save or discard"""

    bl_idname = 'gret.shape_key_store'
    bl_label = "Store Shape Keys"
    bl_context = 'objectmode'
    bl_options = {'REGISTER', 'UNDO'}

    index: bpy.props.IntProperty(options={'HIDDEN'})
    load: bpy.props.BoolProperty(options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        return context.active_object and context.active_object.type == 'MESH'

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

def draw_panel_extra(self, context):
    layout = self.layout
    obj = context.active_object
    slots = obj.data.shape_key_storage

    if obj.type == 'MESH' and obj.data.shape_keys:
        box = layout.box()
        sub = box.split(factor=0.2)
        sub.label(text="Slots")
        row = sub.row(align=True)
        for slot_idx in range(max_shape_key_slots):
            has_data = slot_idx < len(slots) and bool(slots[slot_idx].data)
            text = chr(ord('A') + min(slot_idx, 25))
            op = row.operator('gret.shape_key_store', text=text, depress=has_data)
            op.index = slot_idx

classes = (
    GRET_OT_shape_key_store,
    GRET_PG_shape_key_storage,
)

def register(settings):
    for cls in classes:
        bpy.utils.register_class(cls)

    bpy.types.Mesh.shape_key_storage = bpy.props.CollectionProperty(
        type=GRET_PG_shape_key_storage,
    )
    bpy.types.DATA_PT_shape_keys.append(draw_panel_extra)

def unregister():
    bpy.types.DATA_PT_shape_keys.remove(draw_panel_extra)
    del bpy.types.Mesh.shape_keys_store

    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
