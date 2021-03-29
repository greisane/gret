import bpy

class GRET_OT_replace_references(bpy.types.Operator):
    #tooltip
    """Replaces references to an object with a different object. Use with care.
Currently only handles objects and modifiers, and no nested properties"""

    bl_idname = 'gret.replace_references'
    bl_label = "Replace References"
    bl_options = {'REGISTER', 'UNDO'}

    def get_obj_name_items(self, context):
        return [(o.name, o.name, "") for o in bpy.data.objects]

    dry_run: bpy.props.BoolProperty(
        name="Dry Run",
        description="List the names of the properties that would be affected without making changes",
        default=True,
    )
    src_obj_name: bpy.props.EnumProperty(
        items=get_obj_name_items,
        name="Source Object",
        description="Object to be replaced",
    )
    dst_obj_name: bpy.props.EnumProperty(
        items=get_obj_name_items,
        name="Destination Object",
        description="Object to be used in its place",
    )

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        src_obj = bpy.data.objects.get(self.src_obj_name)
        if not src_obj:
            self.report({'ERROR'}, f"Source object does not exist.")
            return {'CANCELLED'}
        dst_obj = bpy.data.objects.get(self.dst_obj_name)
        if not dst_obj:
            self.report({'ERROR'}, f"Destination object does not exist.")
            return {'CANCELLED'}
        if src_obj == dst_obj:
            self.report({'ERROR'}, f"Source and destination objects are the same.")
            return {'CANCELLED'}

        num_found = 0
        num_replaced = 0
        def replace_pointer_properties(obj, path=""):
            nonlocal num_found, num_replaced
            for prop in obj.bl_rna.properties:
                if prop.type != 'POINTER':
                    continue
                if obj.is_property_readonly(prop.identifier):
                    continue
                if getattr(obj, prop.identifier) == src_obj:
                    path = " -> ".join(s for s in [path, obj.name, prop.identifier] if s)
                    verb = "would be" if self.dry_run else "was"
                    if not self.dry_run:
                        try:
                            setattr(obj, prop.identifier, dst_obj)
                            num_replaced += 1
                        except:
                            verb = "couldn't be"
                    print(f"{path} {verb} replaced")
                    num_found += 1

        print(f"Searching for '{src_obj.name}' to replace with '{dst_obj.name}'")
        for obj in bpy.data.objects:
            if obj.library:
                # Linked objects are not handled currently, though it might just work
                continue
            replace_pointer_properties(obj)
            for mo in obj.modifiers:
                replace_pointer_properties(mo, path=obj.name)

        if num_found == 0:
            self.report({'INFO'}, f"No references found.")
        elif self.dry_run:
            self.report({'INFO'}, f"{num_found} references found, see the console for details.")
        else:
            self.report({'INFO'}, f"{num_found} references found, {num_replaced} replaced.")

        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

def draw_menu(self, context):
    self.layout.operator(GRET_OT_replace_references.bl_idname)

def register(settings):
    bpy.utils.register_class(GRET_OT_replace_references)
    bpy.types.TOPBAR_MT_file_cleanup.append(draw_menu)

def unregister():
    bpy.types.TOPBAR_MT_file_cleanup.remove(draw_menu)
    bpy.utils.unregister_class(GRET_OT_replace_references)
