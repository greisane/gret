from collections import namedtuple, defaultdict
from itertools import chain, zip_longest
from math import pi
import bpy
import re
import shlex
import time

from .. import prefs
from ..helpers import (
    beep,
    fail_if_invalid_export_path,
    get_context,
    get_export_path,
    get_name_safe,
    get_nice_export_report,
    get_topmost_parent,
    gret_operator_exists,
    intercept,
    load_selection,
    save_selection,
    select_only,
    viewport_reveal_all,
)
from ..log import logger, log, logd
from ..mesh.helpers import (
    apply_modifiers,
    delete_faces_with_no_material,
    encode_shape_keys,
    merge_shape_keys,
    unsubdivide_preserve_uvs,
)
from ..mesh.collision import collision_prefixes, get_collision_objects

def export_fbx(filepath, context, objects):
    select_only(context, objects)
    return bpy.ops.export_scene.fbx(
        filepath=filepath
        , check_existing=False
        , axis_forward='-Z'
        , axis_up='Y'
        , use_selection=True
        , use_active_collection=False
        , global_scale=1.0
        , apply_unit_scale=True
        , apply_scale_options='FBX_SCALE_NONE'
        , object_types={'MESH', 'EMPTY'}
        , use_mesh_modifiers=True
        , use_mesh_modifiers_render=False
        , mesh_smooth_type='EDGE'
        , bake_space_transform=True
        , use_subsurf=False
        , use_mesh_edges=False
        , use_tspace=False
        , use_custom_props=False
        , bake_anim=False
        , path_mode='STRIP'
        , embed_textures=False
        , batch_mode='OFF'
        , use_batch_own_dir=False
    )

def copy_obj(self, obj):
    if obj.type == 'MESH':
        new_obj = obj.copy()
        new_obj.name = obj.name + "_"
        new_data = obj.data.copy()
        new_obj.data = new_data
        new_obj.parent = None
    else:
        dg = bpy.context.evaluated_depsgraph_get()
        new_data = bpy.data.meshes.new_from_object(obj, preserve_all_data_layers=True, depsgraph=dg)
        new_obj = bpy.data.objects.new(obj.name + "_", new_data)
    new_obj.matrix_world = obj.matrix_world
    self.new_objs.append(new_obj)
    assert isinstance(new_data, bpy.types.Mesh)
    assert new_data.users == 1
    self.new_meshes.append(new_data)

    # Move object materials to mesh
    for mat_idx, mat_slot in enumerate(obj.material_slots):
        if mat_slot.link == 'OBJECT':
            new_data.materials[mat_idx] = mat_slot.material

    # New objects are moved to the scene collection, ensuring they're visible
    bpy.context.scene.collection.objects.link(new_obj)
    new_obj.hide_set(False)
    new_obj.hide_viewport = False
    new_obj.hide_select = False
    return new_obj

def restore_saved_object_names(self):
    for n, obj in enumerate(self.saved_object_names.keys()):
        obj.name = f"___{n}"
    for obj, name in self.saved_object_names.items():
        obj.name = name
    self.saved_object_names.clear()

def swap_object_names(self, obj1, obj2):
    assert obj1 not in self.saved_object_names
    assert obj2 not in self.saved_object_names
    name1, name2 = obj1.name, obj2.name
    self.saved_object_names[obj1] = name1
    self.saved_object_names[obj2] = name2
    obj1.name = name2
    obj2.name = name1
    obj1.name = name2

def set_parent_keep_parent_inverse(objs, new_parent):
    for obj in objs:
        m = obj.matrix_parent_inverse.copy()
        obj.parent = new_parent
        obj.matrix_parent_inverse = m

def _scene_export(self, context, job):
    if job.to_collection and job.clean_collection:
        # Clean the target collection first
        log(f"Cleaning target collection")
        for obj in job.export_collection.objects:
            bpy.data.objects.remove(obj, do_unlink=True)

    # Find and clone objects to be exported
    # Original objects that aren't exported will be hidden for render, only for driver purposes
    if not job.selection_only:
        export_objs, job_cls = job.get_export_objects(context, types={'MESH', 'CURVE'})
    elif context.selected_objects:
        export_objs, job_cls = [o for o in context.selected_objects if o.type == 'MESH'], []
    else:
        # Nothing to export
        return

    ExportItem = namedtuple('ExportObject', ['original', 'obj', 'job_collection',
        'col_objs', 'socket_objs'])
    items = []
    groups = defaultdict(list)  # Filepath to list of ExportItems
    for obj in context.scene.objects:
        obj.hide_render = True
    for obj, job_cl in zip_longest(export_objs, job_cls):
        if any(obj.name.startswith(prefix) for prefix in collision_prefixes):
            # Never export collision objects by themselves
            continue
        obj.hide_render = False
        items.append(ExportItem(obj, copy_obj(self, obj), job_cl, [], []))

    # Process individual meshes
    job_tags = job.modifier_tags.split(' ')
    def should_apply_modifier(modifier):
        for tag in re.findall(r"g:(\S+)", modifier.name):
            if tag.startswith('!'):
                # Blacklisted tag
                return tag[1:] not in job_tags
            else:
                return tag in job_tags
        return modifier.show_render

    for item in items:
        log(f"Processing {item.original.name}")
        obj = item.obj
        job_cl = item.job_collection
        ctx = get_context(obj)
        logger.indent += 1

        # Simplify if specified in job collection
        levels = job_cl.subdivision_levels if job_cl else 0
        if levels < 0:
            unsubdivide_preserve_uvs(obj, -levels)

        if job.merge_basis_shape_keys:
            for shape_key_pattern in shlex.split(job.basis_shape_key_pattern):
                try:
                    # Check for A->B format which specifies the target shape key instead of basis
                    shape_key_pattern, target_shape_key_name = shape_key_pattern.split("->")
                    if not target_shape_key_name or target_shape_key_name == "_":
                        remove_shape_keys(obj, [shape_key_pattern])
                    else:
                        merge_shape_keys(obj, [shape_key_pattern], target_shape_key_name)
                except ValueError:
                    merge_shape_keys(obj, [shape_key_pattern])

        # Clear shape keys if they won't be needed later
        if not job.encode_shape_keys:
            obj.shape_key_clear()

        if job.apply_modifiers:
            apply_modifiers(obj, key=should_apply_modifier)

        if job.use_postprocess_script and job.postprocess_script:
            try:
                log(f"Running post-process script {job.postprocess_script.name}")
                global_dict = globals().copy()
                global_dict.update({'ctx': ctx, 'obj': obj})
                exec(job.postprocess_script.as_string(), global_dict, global_dict)
            except:
                raise

        if obj.instance_type != 'NONE' and item.original.children:
            # Instancing is a bit annoying since it relies on hierarchy and matrices get reset
            original_children = item.original.children[:]
            set_parent_keep_parent_inverse(original_children, obj)
            bpy.ops.object.duplicates_make_real(ctx, use_base_parent=True)
            set_parent_keep_parent_inverse(original_children, item.original)
            if obj.children:
                ctx = get_context(active_obj=obj, selected_objs=obj.children)
                bpy.ops.object.join(ctx)
                log(f"Joined {len(obj.children)} instanced objects")

        # Remap materials, any objects or faces with no material won't be exported
        all_none = lambda iterable: all(not el for el in iterable)

        remapped_to_none = False
        for remap in job.remap_materials:
            if remap.source:
                for mat_idx, mat in enumerate(obj.data.materials):
                    if mat and mat == remap.source:
                        logd(f"Remapped material {mat.name} to {get_name_safe(remap.destination)}")
                        obj.data.materials[mat_idx] = remap.destination
                        remapped_to_none = remapped_to_none or not remap.destination
            elif remap.destination and all_none(obj.data.materials):
                logd(f"Added material {get_name_safe(remap.destination)}")
                obj.data.materials.append(remap.destination)

        if all_none(obj.data.materials):
            log(f"Object has no materials and won't be exported")
            logger.indent -= 1
            continue

        if all(not mat for mat in obj.data.materials):
            log(f"Object has no materials and won't be exported")
            logger.indent -= 1
            continue

        if remapped_to_none or any(mat for mat in obj.data.materials):
            delete_faces_with_no_material(obj)
            if not obj.data.polygons:
                log(f"Object has no faces and won't be exported")
                logger.indent -= 1
                continue

        # If enabled, pick up UE4 collision objects
        if job.export_collision:
            item.col_objs.extend(get_collision_objects(context, item.original))
        if item.col_objs:
            log(f"Collected {len(item.col_objs)} collision primitives")

        if job.export_sockets:
            item.socket_objs.extend(o for o in item.original.children if o.type == 'EMPTY')

        # If enabled, move main object to world center while keeping collision relative transforms
        if not job.keep_transforms:
            if item.original.parent:
                pivot_obj = get_topmost_parent(item.original)
                world_to_pivot = pivot_obj.matrix_world.inverted()
                obj.matrix_world = world_to_pivot @ obj.matrix_world
                logd(f"Zero transform for {obj.name} relative to {pivot_obj.name}")
            else:
                world_to_pivot = obj.matrix_world.inverted()
                obj.matrix_world.identity()
                logd(f"Zero transform for {obj.name}")

            for other_obj in chain(item.col_objs, item.socket_objs):
                logd(f"Moving collision/socket {other_obj.name}")
                self.saved_transforms[other_obj] = other_obj.matrix_world.copy()
                other_obj.matrix_world = world_to_pivot @ other_obj.matrix_world

        obj.data.transform(obj.matrix_basis, shape_keys=True)
        obj.matrix_basis.identity()

        if job.encode_shape_keys:
            encode_shape_keys(obj, ["*_UV"])

        obj.shape_key_clear()

        if job.ensure_uv_layers and not obj.data.uv_layers:
            # Optionally ensure UV layer. Zero coords to avoid all kinds of problems
            log("Created empty UV layer")
            for uvloop in obj.data.uv_layers.new(name="UVMap").data:
                uvloop.uv = (0.0, 0.0)

        # It's more intuitive to author masks starting from black, however UE4 defaults to white
        # Invert vertex colors, materials should use OneMinus to get the original value
        if gret_operator_exists("gret.vertex_color_mapping_add"):
            if not obj.data.vertex_colors and not obj.vertex_color_mapping:
                logd("Created default vertex color mapping")
                bpy.ops.gret.vertex_color_mapping_add(ctx)
            bpy.ops.gret.vertex_color_mapping_refresh(ctx, invert=job.invert_vertex_color_mappings)
            bpy.ops.gret.vertex_color_mapping_clear(ctx)
            if len(obj.data.vertex_colors) > 1:
                log(f"More than one vertex color layer, is this intended?",
                    ", ".join(vc.name for vc in obj.data.vertex_colors))

        # Put the objects in a group
        cl = job_cl.get_collection(context) if job_cl else item.original.users_collection[0]
        path_fields = {
            'object': item.original.name,
            'topobject': get_topmost_parent(item.original).name,
            'collection': cl.name,
        }
        filepath = get_export_path(job.scene_export_path, path_fields)
        groups[filepath].append(item)
        logger.indent -= 1

    # Export each file
    for filepath, items in sorted(groups.items()):
        for item in items:
            # If set, ensure prefix for exported materials
            if job.material_name_prefix:
                for mat_slot in item.obj.material_slots:
                    mat = mat_slot.material
                    if mat:
                        if not mat.name.startswith(job.material_name_prefix):
                            self.saved_material_names[mat] = mat.name
                            mat.name = job.material_name_prefix + mat.name

            swap_object_names(self, item.original, item.obj)
            # Rename sockets to lose the .001 .002 suffix while avoiding name collisions
            # Normally it's not possible to have two objects with the same name in Blender
            # That's unwieldy when you want e.g. a socket named "Pivot" in every mesh
            for socket_obj in item.socket_objs:
                name_base = "SOCKET_" + re.sub(r"\.\d\d\d$", "", socket_obj.name)
                name_number = 0
                while True:
                    new_name = name_base if name_number == 0 else f"{name_base}{name_number}"
                    existing_obj = context.scene.objects.get(new_name)
                    if existing_obj and existing_obj in self.saved_object_names:
                        name_number += 1
                    elif existing_obj:
                        swap_object_names(self, existing_obj, socket_obj)
                        break
                    else:
                        self.saved_object_names[socket_obj] = socket_obj.name
                        socket_obj.name = new_name
                        break

        filename = bpy.path.basename(filepath)
        objs = list(chain.from_iterable([item.obj] + item.col_objs + item.socket_objs
            for item in items))

        result = export_fbx(filepath, context, objs)
        if result == {'FINISHED'}:
            log(f"Exported {filename} with {len(objs)} objects")
            self.exported_files.append(filename)
        else:
            log(f"Failed to export {filename}")

        restore_saved_object_names(self)

def scene_export(self, context, job):
    assert job.what == 'SCENE'

    # Check addon availability and export path
    try:
        field_names = ['object', 'topobject', 'collection']
        fail_if_invalid_export_path(job.scene_export_path, field_names)
    except Exception as e:
        self.report({'ERROR'}, str(e))
        return {'CANCELLED'}

    saved_selection = save_selection()
    viewport_reveal_all()
    saved_use_global_undo = context.preferences.edit.use_global_undo
    context.preferences.edit.use_global_undo = False
    self.exported_files = []
    self.new_objs = []
    self.new_meshes = []
    self.saved_object_names = {}
    self.saved_material_names = {}
    self.saved_transforms = {}
    logger.start_logging()
    log(f"Beginning scene export job '{job.name}'")

    try:
        start_time = time.time()
        _scene_export(self, context, job)
        # Finished without errors
        elapsed = time.time() - start_time
        self.report({'INFO'}, get_nice_export_report(self.exported_files, elapsed))
        log("Job complete")
        if prefs.jobs__beep_on_finish:
            beep(pitch=2, num=1)
    finally:
        # Clean up
        restore_saved_object_names(self)
        while self.new_objs:
            bpy.data.objects.remove(self.new_objs.pop())
        while self.new_meshes:
            bpy.data.meshes.remove(self.new_meshes.pop())
        for mat, name in self.saved_material_names.items():
            mat.name = name
        for obj, matrix_world in self.saved_transforms.items():
            obj.matrix_world = matrix_world
        del self.saved_object_names
        del self.saved_material_names
        del self.saved_transforms

        load_selection(saved_selection)
        context.preferences.edit.use_global_undo = saved_use_global_undo
        logger.end_logging()

    return {'FINISHED'}
