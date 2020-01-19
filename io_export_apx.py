bl_info = {
    "name": "HairWorks Exporter (.apx)",
    "author": "greisane",
    "description": "Export hair data to HairWorks file (.apx)",
    "version": (0, 1),
    "blender": (2, 80, 0),
    "location": "File > Export > HairWorks (.apx)",
    "category": "Import-Export",
}

import xml.etree.cElementTree as ET
import xml.dom.minidom as minidom
from math import pi, inf
from datetime import datetime, timezone
from itertools import chain
import bpy
from bpy_extras.io_utils import ExportHelper
from bpy.props import BoolProperty, FloatProperty, StringProperty, EnumProperty
from bpy.types import Operator
from mathutils import Vector, Matrix

# From fbx_utils.py
# Scale/unit mess. FBX can store the 'reference' unit of a file in its UnitScaleFactor property
# (1.0 meaning centimeter, afaik). We use that to reflect user's default unit as set in Blender with scale_length.
# However, we always get values in BU (i.e. meters), so we have to reverse-apply that scale in global matrix...
# Note that when no default unit is available, we assume 'meters' (and hence scale by 100).
def units_blender_to_fbx_factor(scene):
    return 100.0 if (scene.unit_settings.system == 'NONE') else (100.0 * scene.unit_settings.scale_length)

sqr_length = lambda v: v.x * v.x + v.y * v.y + v.z * v.z
def find_hair_rooted_at(hairs, co, sqr_threshold=0.003):
    """Returns the hair at the exact location or the closest hair within distance"""
    nearest_hair, distance = None, inf
    for hair in hairs:
        d = sqr_length(hair.hair_keys[0].co - co)
        if d <= 0.001:
            return hair
        elif d <= sqr_threshold and d < distance:
            nearest_hair, distance = hair, d
    return nearest_hair

def prettify(elem):
    """Return a pretty-printed XML string for the Element."""
    rough_string = ET.tostring(elem, "utf-8")
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="\t")

vec_to_string = lambda v: " ".join(repr(n) for n in v)
mat_to_string = lambda m: " ".join(vec_to_string(v) for v in m)

def add_value(elem, name, type, value=None, null=False):
    sub_elem = ET.SubElement(elem, "value", name=name, type=type)

    if null:
        sub_elem.set("null", "1")

    if type in ("U8", "U32", "I32", "F32"):
        sub_elem.text = repr(value or 0)
    elif type in ("Vec2", "Vec3", "Vec4"):
        sub_elem.text = vec_to_string(value)
    elif type in ("Bool"):
        sub_elem.text = "true" if value else "false"
    elif type in ("String"):
        sub_elem.text = value or ""
    else:
        raise ValueError("Invalid value type %s" % type)

    return sub_elem

def add_array(elem, name, type, iterable):
    # FurViewer.exe will crash if a numeric array doesn't have trailing whitespace
    l = iterable if isinstance(iterable, list) else list(iterable)
    sub_elem = ET.SubElement(elem, "array", name=name, size=repr(len(l)), type=type)

    if type in ("U8", "U32", "I32"):
        sub_elem.text = " ".join(repr(n) for n in l) + " "
    elif type in ("Vec2", "Vec3", "Vec4"):
        sub_elem.text = ", ".join(vec_to_string(v) for v in l) + " "
    elif type in ("Mat44"):
        sub_elem.text = ", ".join(mat_to_string(m) for m in l) + " "
    elif type in ("String"):
        for s in l:
            ET.SubElement(sub_elem, "value", type="String").text = s
    elif type in ("Struct"):
        pass
    else:
        raise ValueError("Invalid array type %s" % type)

    return sub_elem

def mesh_triangulate(mesh):
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.triangulate(bm, faces=bm.faces)
    bm.to_mesh(mesh)
    bm.free()

class ExportHairWorks(bpy.types.Operator, ExportHelper):
    bl_idname = "export_scene.apx"
    bl_label = "Export APX"
    bl_options = {'UNDO', 'PRESET'}

    filename_ext = '.apx'
    filter_glob: StringProperty(
        default="*.apx",
        options={'HIDDEN'},
    )
    root_threshold: FloatProperty(
        name="Hair distance threshold",
        min=0.0,
        max=1000.0,
        default=0.01,
        unit='LENGTH',
    )

    def execute(self, context):
        scene = context.scene

        cos = []
        index = 0
        end_indices = []
        indices = []
        uvs = []
        bone_indices = []
        bone_weights = []

        armature = None
        bones = []

        for obj in context.selected_objects:
            if not obj.parent or obj.parent.type != 'ARMATURE':
                self.report({'ERROR'}, "Mesh must be parented to an armature.")
                return {'CANCELLED'}
            if armature and obj.parent.data != armature:
                self.report({'ERROR'}, "Meshes must be parented to the same armature.")
                return {'CANCELLED'}
            if not obj.data.uv_layers.active:
                self.report({'ERROR'}, "Mesh must have an active UV map.")
                return {'CANCELLED'}

            particle_systems = [m.particle_system for m in obj.modifiers if
                m.type == 'PARTICLE_SYSTEM' and m.show_viewport]
            if not particle_systems:
                self.report({'ERROR'}, "Mesh has no visible particle systems.")
                return {'CANCELLED'}

            points_per_hair = 0
            for system in particle_systems:
                if system.settings.type != 'HAIR':
                    self.report({'ERROR'}, "The particle system is not a hair particle system.")
                    return {'CANCELLED'}
                # if system.settings.emit_from != 'VERT' or system.settings.use_emit_random:
                #     self.report({'ERROR'}, 'The particle system should emit uniformly from vertices.')
                #     return {'CANCELLED'}
                points_per_hair = points_per_hair or system.settings.hair_step
                if system.settings.hair_step != points_per_hair:
                    self.report({'ERROR'}, "Particle systems should have the same number of keys per hair.")
                    return {'CANCELLED'}
            points_per_hair += 1

            if not armature:
                armature = obj.parent.data
                # Export all bones for now
                bones = armature.bones[:]

            # Apply and triangulate the mesh
            mesh = obj.to_mesh(context.scene, system.settings.use_modifier_stack,
                calc_tessface=False, settings='PREVIEW')
            mesh_triangulate(mesh)

            # List of points in each hair. The hair roots double as vertices for the growth mesh
            # When cutting hair down to the root in blender the curves are completely removed
            # HairWorks doesn't like that and instead treats 0 length hairs as a bald spot
            # I'd like to skip exporting hidden hairs, but hair.is_visible seems to always
            # return True. It's probably not actually related to viewport visibility
            # Maybe it's a bug since hair visibility doesn't even get saved
            first_non_none = lambda it: next(filter(lambda x: x is not None, it), None)
            hairs_exported = 0
            sqr_threshold = self.root_threshold ** 2
            for vertex in mesh.vertices:
                hair = first_non_none(find_hair_rooted_at(ps.particles,
                    vertex.co, sqr_threshold) for ps in particle_systems)
                if hair:
                    hairs_exported += 1
                    # Use the vertex coord instead of the hair root to make
                    # sure that HairWorks won't omit the hair
                    cos.append(vertex.co)
                    cos += [hair_key.co for hair_key in hair.hair_keys[1:]]
                else:
                    cos += [vertex.co for _ in range(points_per_hair)]
                index += points_per_hair
                end_indices.append(index - 1)

            total_hairs = sum(len(ps.particles) for ps in particle_systems)
            if hairs_exported < total_hairs:
                self.report({'WARNING'}, "Only %d out of %d hairs exported. " \
                    "Make sure the hairs are rooted on vertices of the mesh." %
                    (hairs_exported, total_hairs))

            # Mesh data
            uv_layer = mesh.uv_layers.active.data
            flipped_y = lambda xy: (xy.x, 1.0 - xy.y)

            for face in mesh.polygons:
                indices += face.vertices
                uvs += [flipped_y(uv_layer[li].uv) for li in face.loop_indices]

            # Skinning data
            for vertex_index, vertex in enumerate(mesh.vertices):
                # Export exactly 4 influences per vert
                for group in vertex.groups:
                    group_name = obj.vertex_groups[group.group].name
                    bone_index = next((i for i, b in enumerate(bones) if group_name == b.name), -1)
                    if bone_index >= 0:
                        bone_indices.append(bone_index)
                        bone_weights.append(group.weight)
                        if len(bone_indices) % 4 == 0:
                            break
                while len(bone_indices) < (vertex_index + 1) * 4:
                    bone_indices.append(0)
                    bone_weights.append(0.0)

        # Write header
        root = ET.Element("NvParameters", numObjects="4", version="1.0")

        xml_obj = ET.SubElement(root, "value", name="", type="Ref", className="HairWorksInfo",
            version="1.1", checksum="")
        struct = ET.SubElement(xml_obj, "struct", name="")

        add_value(struct, "fileVersion", "String", "1.1.2")
        add_value(struct, "toolVersion", "String", "Blender %s" % bpy.app.version_string)
        add_value(struct, "sourcePath", "String")
        add_value(struct, "authorName", "String")
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S %Z")
        add_value(struct, "lastModified", "String", now)

        xml_obj = ET.SubElement(root, "value", name="", type="Ref", className="HairSceneDescriptor",
            version="1.1", checksum="")
        struct = ET.SubElement(xml_obj, "struct", name="")
        add_value(struct, "densityTexture", "String")
        add_value(struct, "rootColorTexture", "String")
        add_value(struct, "tipColorTexture", "String")
        add_value(struct, "widthTexture", "String")
        add_value(struct, "rootWidthTexture", "String", null=True)
        add_value(struct, "tipWidthTexture", "String", null=True)
        add_value(struct, "stiffnessTexture", "String")
        add_value(struct, "rootStiffnessTexture", "String")
        add_value(struct, "clumpScaleTexture", "String")
        add_value(struct, "clumpRoundnessTexture", "String")
        add_value(struct, "clumpNoiseTexture", "String", null=True)
        add_value(struct, "waveScaletexture", "String")
        add_value(struct, "waveFreqTexture", "String")
        add_value(struct, "strandTexture", "String")
        add_value(struct, "lengthTexture", "String")
        add_value(struct, "specularTexture", "String")

        xml_obj = ET.SubElement(root, "value", name="", type="Ref", className="HairAssetDescriptor",
            version="1.1", checksum="")
        struct = ET.SubElement(xml_obj, "struct", name="")

        add_value(struct, "numGuideHairs", "U32", len(end_indices))
        add_value(struct, "numVertices", "U32", len(cos))
        add_array(struct, "vertices", "Vec3", cos)
        add_array(struct, "endIndices", "U32", end_indices)

        add_value(struct, "numFaces", "U32", len(mesh.polygons))
        add_array(struct, "faceIndices", "U32", indices)
        add_array(struct, "faceUVs", "Vec2", uvs)

        add_value(struct, "numBones", "U32", len(bones))
        add_array(struct, "boneIndices", "Vec4", zip(*[iter(bone_indices)]*4))
        add_array(struct, "boneWeights", "Vec4", zip(*[iter(bone_weights)]*4))

        # boneNames is a dump of null-terminated bone names
        string_to_chars = lambda s: (ord(c) for c in s + "\0")
        it = chain.from_iterable(string_to_chars(b.name) for b in bones)
        add_array(struct, "boneNames", "U8", it)

        # Bone names as strings
        add_array(struct, "boneNameList", "String", (b.name for b in bones))

        # Write bindPoses
        # Try to stay in sync with native fbx export
        apply_unit_scale = True
        global_scale = 1.0
        global_matrix = Matrix()
        unit_scale = units_blender_to_fbx_factor(scene) if apply_unit_scale else 100.0
        global_matrix = Matrix.Scale(unit_scale * global_scale, 4) * global_matrix
        # mtx4_z90 = Matrix.Rotation(pi / 2.0, 4, 'X')
        # global_matrix = mtx4_z90

        matrices = []
        for bone in bones:
            # Use the same calculation as in write_sub_deformer_skin to compute the global
            # transform of the bone for the bind pose.
            matrix = obj.parent.matrix_world * bone.matrix_local
            # matrix = global_matrix * matrix
            matrix.transpose()
            matrices.append(matrix)

        add_array(struct, "bindPoses", "Mat44", matrices)

        # Write each bone"s parent index
        def get_bone_parent_index(bone):
            return armature.bones.find(bone.parent.name) if bone.parent else -1

        it = (get_bone_parent_index(b) for b in bones)
        add_array(struct, "boneParents", "I32", it)

        # Unsupported miscellaneous stuff
        add_value(struct, "numBoneSpheres", "U32", 0)
        bone_spheres = add_array(struct, "boneSpheres", "Struct", [])
        bone_spheres.set("structElements", "boneSphereIndex(I32),boneSphereRadius(F32)," \
            "boneSphereLocalPos(Vec3)")

        add_value(struct, "numBoneCapsules", "U32", 0)
        add_array(struct, "boneCapsuleIndices", "U32", [])

        add_value(struct, "numPinConstraints", "U32", 0)
        pins = add_array(struct, "pinConstraints", "Struct", [])
        pins.set("structElements", "boneSphereIndex(I32),boneSphereRadius(F32)," \
            "boneSphereLocalPos(Vec3),pinStiffness(F32),influenceFallOff(F32)," \
            "useDynamicPin(Bool),doLra(Bool),useStiffnessPin(Bool),influenceFallOffCurve(Vec4)")

        add_value(struct, "sceneUnit", "F32", 1.0)
        add_value(struct, "upAxis", "U32", 2)
        add_value(struct, "handedness", "U32", 1)

        # HairInstanceDescriptor defaults
        xml_obj = ET.SubElement(root, "value", name="", type="Ref", className="HairInstanceDescriptor",
            version="1.1", checksum="")
        struct = ET.SubElement(xml_obj, "struct", name="")
        array = ET.SubElement(struct, "array", name="materials", size="4", type="Struct")

        for _ in range(4):
            struct = ET.SubElement(array, "struct")
            add_value(struct, "name", "String", "", null=True)
            add_value(struct, "densityTextureChan", "U32", 0)
            add_value(struct, "widthTextureChan", "U32", 0)
            add_value(struct, "rootWidthTextureChan", "U32", 0)
            add_value(struct, "tipWidthTextureChan", "U32", 0)
            add_value(struct, "clumpScaleTextureChan", "U32", 0)
            add_value(struct, "clumpNoiseTextureChan", "U32", 0)
            add_value(struct, "clumpRoundnessTextureChan", "U32", 0)
            add_value(struct, "waveScaleTextureChan", "U32", 0)
            add_value(struct, "waveFreqTextureChan", "U32", 0)
            add_value(struct, "lengthTextureChan", "U32", 0)
            add_value(struct, "stiffnessTextureChan", "U32", 0)
            add_value(struct, "rootStiffnessTextureChan", "U32", 0)
            add_value(struct, "splineMultiplier", "U32", 4)
            add_value(struct, "assetType", "U32", 0)
            add_value(struct, "assetPriority", "U32", 0)
            add_value(struct, "assetGroup", "U32", 0)
            add_value(struct, "width", "F32", 1.0)
            add_value(struct, "widthNoise", "F32", 0)
            add_value(struct, "clumpNoise", "F32", 0)
            add_value(struct, "clumpNumSubclumps", "U32", 0)
            add_value(struct, "clumpRoundness", "F32", 1.0)
            add_value(struct, "clumpScale", "F32", 0)
            add_value(struct, "clumpPerVertex", "Bool", False)
            add_value(struct, "density", "F32", 1.0)
            add_value(struct, "lengthNoise", "F32", 1.0)
            add_value(struct, "lengthScale", "F32", 1.0)
            add_value(struct, "widthRootScale", "F32", 1.0)
            add_value(struct, "widthTipScale", "F32", 0.1)
            add_value(struct, "waveRootStraighten", "F32", 0)
            add_value(struct, "waveScale", "F32", 0.0)
            add_value(struct, "waveScaleNoise", "F32", 0.5)
            add_value(struct, "waveFreq", "F32", 3.0)
            add_value(struct, "waveFreqNoise", "F32", 0.5)
            add_value(struct, "waveScaleStrand", "F32", 1.0)
            add_value(struct, "waveScaleClump", "F32", 0.0)
            add_value(struct, "enableDistanceLOD", "Bool", True)
            add_value(struct, "distanceLODStart", "F32", 5.0)
            add_value(struct, "distanceLODEnd", "F32", 10.0)
            add_value(struct, "distanceLODFadeStart", "F32", 1000.0)
            add_value(struct, "distanceLODDensity", "F32", 0.0)
            add_value(struct, "distanceLODWidth", "F32", 1.0)
            add_value(struct, "enableDetailLOD", "Bool", True)
            add_value(struct, "detailLODStart", "F32", 2.0)
            add_value(struct, "detailLODEnd", "F32", 1.0)
            add_value(struct, "detailLODDensity", "F32", 1.0)
            add_value(struct, "detailLODWidth", "F32", 1.0)
            add_value(struct, "colorizeLODOption", "U32", 0)
            add_value(struct, "useViewfrustrumCulling", "Bool", True)
            add_value(struct, "useBackfaceCulling", "Bool", False)
            add_value(struct, "backfaceCullingThreshold", "F32", -0.2)
            add_value(struct, "usePixelDensity", "Bool", False)
            add_value(struct, "alpha", "F32", 0.0)
            add_value(struct, "strandBlendScale", "F32", 1.0)
            add_value(struct, "baseColor", "Vec4", "0 0 0 0")
            add_value(struct, "diffuseBlend", "F32", 0.5)
            add_value(struct, "diffuseScale", "F32", 0.0)
            add_value(struct, "diffuseHairNormalWeight", "F32", 0.0)
            add_value(struct, "diffuseBoneIndex", "U32", 0)
            add_value(struct, "diffuseBoneLocalPos", "Vec3", (0, 0, 0))
            add_value(struct, "diffuseNoiseFreqU", "F32", 0.0)
            add_value(struct, "diffuseNoiseFreqV", "F32", 0.0)
            add_value(struct, "diffuseNoiseScale", "F32", 0.0)
            add_value(struct, "diffuseNoiseGain", "F32", 0.0)
            add_value(struct, "textureBrightness", "F32", 0.0)
            add_value(struct, "diffuseColor", "Vec4", (0, 0, 0, 0))
            add_value(struct, "rootColor", "Vec4", (1, 1, 1, 1))
            add_value(struct, "tipColor", "Vec4", (1, 1, 1, 1))
            add_value(struct, "glintStrength", "F32", 0.0)
            add_value(struct, "glintCount", "F32", 256.0)
            add_value(struct, "glintExponent", "F32", 2.0)
            add_value(struct, "rootAlphaFalloff", "F32", 0.0)
            add_value(struct, "rootTipColorWeight", "F32", 0.5)
            add_value(struct, "rootTipColorFalloff", "F32", 1.0)
            add_value(struct, "shadowSigma", "F32", 0.2)
            add_value(struct, "specularColor", "Vec4", (1, 1, 1, 1))
            add_value(struct, "specularPrimary", "F32", 0.1)
            add_value(struct, "specularNoiseScale", "F32", 0.0)
            add_value(struct, "specularEnvScale", "F32", 0.25)
            add_value(struct, "specularPrimaryBreakup", "F32", 0)
            add_value(struct, "specularSecondary", "F32", 0.05)
            add_value(struct, "specularSecondaryOffset", "F32", 0.1)
            add_value(struct, "specularPowerPrimary", "F32", 100.0)
            add_value(struct, "specularPowerSecondary", "F32", 20.0)
            add_value(struct, "strandBlendMode", "U32", 0)
            add_value(struct, "useTextures", "Bool", False)
            add_value(struct, "useShadows", "Bool", False)
            add_value(struct, "shadowDensityScale", "F32", 0.5)
            add_value(struct, "castShadows", "Bool", True)
            add_value(struct, "receiveShadows", "Bool", True)
            add_value(struct, "backStopRadius", "F32", 0.0)
            add_value(struct, "bendStiffness", "F32", 0.0)
            add_value(struct, "interactionStiffness", "F32", 0.0)
            add_value(struct, "pinStiffness", "F32", 1.0)
            add_value(struct, "collisionOffset", "F32", 0.0)
            add_value(struct, "useCollision", "Bool", False)
            add_value(struct, "useDynamicPin", "Bool", False)
            add_value(struct, "damping", "F32", 0.0)
            add_value(struct, "friction", "F32", 0.0)
            add_value(struct, "massScale", "F32", 10.0)
            add_value(struct, "gravity", "Vec3", (0, 0, -1))
            add_value(struct, "inertiaScale", "F32", 1.0)
            add_value(struct, "inertiaLimit", "F32", 1000.0)
            add_value(struct, "rootStiffness", "F32", 0.5)
            add_value(struct, "tipStiffness", "F32", 0.0)
            add_value(struct, "simulate", "Bool", True)
            add_value(struct, "stiffness", "F32", 0.5)
            add_value(struct, "stiffnessStrength", "F32", 1.0)
            add_value(struct, "stiffnessDamping", "F32", 0.0)
            add_value(struct, "stiffnessCurve", "Vec4", (1, 1, 1, 1))
            add_value(struct, "stiffnessStrengthCurve", "Vec4", (1, 1, 1, 1))
            add_value(struct, "stiffnessDampingCurve", "Vec4", (1, 1, 1, 1))
            add_value(struct, "bendStiffnessCurve", "Vec4", (1, 1, 1, 1))
            add_value(struct, "interactionStiffnessCurve", "Vec4", (1, 1, 1, 1))
            add_value(struct, "wind", "Vec3", (0, 0, 0))
            add_value(struct, "windNoise", "F32", 0.0)
            add_value(struct, "visualizeBones", "Bool", False)
            add_value(struct, "visualizeBoundingBox", "Bool", False)
            add_value(struct, "visualizeCapsules", "Bool", False)
            add_value(struct, "visualizeControlVertices", "Bool", False)
            add_value(struct, "visualizeCullSphere", "Bool", False)
            add_value(struct, "visualizeDiffuseBone", "Bool", False)
            add_value(struct, "visualizeFrames", "Bool", False)
            add_value(struct, "visualizeGrowthMesh", "Bool", False)
            add_value(struct, "visualizeGuideHairs", "Bool", False)
            add_value(struct, "visualizeHairInteractions", "Bool", False)
            add_value(struct, "visualizeHairSkips", "U32", 0)
            add_value(struct, "visualizeLocalPos", "Bool", False)
            add_value(struct, "visualizePinConstraints", "Bool", False)
            add_value(struct, "visualizeShadingNormals", "Bool", False)
            add_value(struct, "visualizeSkinnedGuideHairs", "Bool", False)
            add_value(struct, "drawRenderHairs", "Bool", True)
            add_value(struct, "enable", "Bool", True)

        with open(self.filepath, 'w', encoding='utf-8') as f:
            f.write("<!DOCTYPE NvParameters>\n")
            f.write(prettify(root))

        bpy.data.meshes.remove(mesh)

        return {'FINISHED'}

def menu_func(self, context):
    self.layout.operator(ExportHairWorks.bl_idname, text="HairWorks (.apx)");

def register():
    bpy.utils.register_class(ExportHairWorks);
    bpy.types.TOPBAR_MT_file_export.append(menu_func);

def unregister():
    bpy.utils.unregister_class(ExportHairWorks);
    bpy.types.TOPBAR_MT_file_export.remove(menu_func);

if __name__ == "__main__":
    register();
    bpy.ops.export_scene.apx("INVOKE_DEFAULT");