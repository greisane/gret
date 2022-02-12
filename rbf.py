from mathutils import Vector
import bmesh
import bpy
import numpy as np

from .math import get_dist

# Based on https://github.com/chadmv/cmt/blob/master/scripts/cmt/rig/meshretarget.py
# Which in turn references http://mathlab.github.io/PyGeM/_modules/pygem/radial.html#RBF

def linear(matrix, radius):
    return matrix

def gaussian(matrix, radius):
    result = np.exp(-(matrix * matrix) / (radius * radius))
    return result

def thin_plate(matrix, radius):
    result = matrix / radius
    result *= matrix
    np.warnings.filterwarnings("ignore")
    result = np.where(result > 0, np.log(result), result)
    np.warnings.filterwarnings("always")
    return result

def multi_quadratic_biharmonic(matrix, radius):
    result = np.sqrt((matrix * matrix) + (radius * radius))
    return result

def inv_multi_quadratic_biharmonic(matrix, radius):
    result = 1.0 / (np.sqrt((matrix * matrix) + (radius * radius)))
    return result

def beckert_wendland_c2_basis(matrix, radius):
    arg = matrix / radius
    first = np.zeros(matrix.shape)
    first = np.where(1 - arg > 0, np.power(1 - arg, 4), first)
    second = (4 * arg) + 1
    result = first * second
    return result

rbf_kernels = {
    'LINEAR': (linear, 1.0),
    'GAUSSIAN': (gaussian, 0.01),
    'PLATE': (thin_plate, 0.001),
    'BIHARMONIC': (multi_quadratic_biharmonic, 0.01),
    'INV_BIHARMONIC': (inv_multi_quadratic_biharmonic, 0.01),
    'C2': (beckert_wendland_c2_basis, 1.0),
}

def get_weight_matrix(src_pts, dst_pts, rbf, radius):
    """Get the weight matrix x in Ax=B."""

    assert src_pts.shape == dst_pts.shape
    num_pts, dim = src_pts.shape
    identity = np.ones((num_pts, 1))
    dist = get_distance_matrix(src_pts, src_pts, rbf, radius)
    # Solve x for Ax=B
    H = np.bmat([
        [dist, identity, src_pts],
        [identity.T, np.zeros((1, 1)), np.zeros((1, dim))],
        [src_pts.T, np.zeros((dim, 1)), np.zeros((dim, dim))],
    ])
    rhs = np.bmat([[dst_pts], [np.zeros((1, dim))], [np.zeros((dim, dim))]])
    weights = None
    try:
        weights = np.linalg.solve(H, rhs)
    except np.linalg.LinAlgError as err:
        # Solving for C2 kernel may throw 'SVD did not converge' sometimes
        if 'Singular matrix' in str(err):
            # While testing the matrix would get close to singular, without a definite solution
            # Can't reproduce it now, however in such a case try an approximation
            Hpinv = np.linalg.pinv(H)
            weights = Hpinv.dot(rhs)
    return weights

def get_distance_matrix(v1, v2, rbf, radius):
    # numpy alternative to scipy.spatial.distance.cdist(v1, v2, 'euclidean')
    matrix = v1[:, np.newaxis, :] - v2[np.newaxis, :, :]
    matrix = np.linalg.norm(matrix, axis=-1)
    return rbf(matrix, radius)

def transform_points(pts, matrix):
    identity = np.ones((len(pts), 1))
    new_pts = np.c_[pts, identity]
    new_pts = np.einsum('ij,aj->ai', matrix, new_pts)
    new_pts = new_pts[:, :-1]
    return new_pts

def get_mesh_points(obj, matrix=None, shape_key=None, mask=None, stride=1, x_mirror=None):
    """Return vertex coordinates of a mesh as a numpy array with shape (?, 3)."""
    # Moving the mesh seems to be faster. See https://blender.stackexchange.com/questions/139511

    assert obj.type == 'MESH'
    mesh = obj.data

    if matrix is not None:
        mesh = mesh.copy()
        mesh.transform(matrix)

    shape_key = mesh.shape_keys.key_blocks[shape_key] if shape_key else None
    points = np.zeros(len(mesh.vertices)*3, dtype=np.float)
    if shape_key and shape_key.vertex_group:
        bm = bmesh.new()
        bm.from_mesh(mesh)
        bm.verts.layers.shape.verify()
        base_shape_layer = bm.verts.layers.shape[shape_key.relative_key.name]
        shape_layer = bm.verts.layers.shape[shape_key.name]
        deform_layer = bm.verts.layers.deform.verify()
        vertex_group_index = obj.vertex_groups[shape_key.vertex_group].index
        for vert_idx, vert in enumerate(bm.verts):
            w = vert[deform_layer].get(vertex_group_index, 0.0)
            points[vert_idx*3:vert_idx*3+3] = vert[base_shape_layer].lerp(vert[shape_layer], w)
        bm.free()
    else:
        vertices = mesh.vertices if shape_key is None else shape_key.data
        vertices.foreach_get('co', points)

    points = points.reshape((-1, 3))
    if mask is not None:
        points = points[mask]
    points = points[::stride]

    if isinstance(x_mirror, list):
        if not x_mirror:
            x_mirror[:] = np.ravel(np.where(points[:,0] > 1e-4))
        points = np.append(points, points[x_mirror] * [-1, 1, 1], axis=0)

    if matrix is not None:
        bpy.data.meshes.remove(mesh)

    return points

def set_mesh_points(obj, new_pts, matrix=None, shape_key_name=None):
    assert obj.type == 'MESH'
    mesh = obj.data

    if matrix is not None:
        new_pts = transform_points(new_pts, matrix)

    if shape_key_name is not None:
        # Result to new shape key
        if not mesh.shape_keys or not mesh.shape_keys.key_blocks:
            obj.shape_key_add(name="Basis")
        shape_key = obj.shape_key_add(name=shape_key_name)
        shape_key.data.foreach_set('co', new_pts.ravel())
        shape_key.value = 1.0
    elif mesh.shape_keys and mesh.shape_keys.key_blocks:
        # There are shape keys, so replace the basis
        # Using bmesh propagates the change, where just setting the coordinates won't
        bm = bmesh.new()
        bm.from_mesh(mesh)
        for vert, new_pt in zip(bm.verts, new_pts):
            vert.co[:] = new_pt
        bm.to_mesh(mesh)
        bm.free()
    else:
        # Set new coordinates directly
        mesh.vertices.foreach_set('co', new_pts.ravel())

def get_armature_points(obj, matrix=None):
    """Return head and tail coordinates of armature bones as a numpy array with shape (?, 3)."""

    assert obj.type == 'ARMATURE'
    armature = obj.data
    bones = armature.edit_bones if obj.mode == 'EDIT' else armature.bones

    cos = []
    if matrix is None:
        for bone in bones:
            cos.append(bone.head)
            cos.append(bone.tail)
    else:
        for bone in bones:
            cos.append(matrix @ bone.head)
            cos.append(matrix @ bone.tail)

    points = np.array(cos)
    return points

def set_armature_points(obj, new_pts, matrix=None, only_selected=False,
    lock_length=False, lock_direction=False):
    assert obj.type == 'ARMATURE' and obj.mode == 'EDIT'

    if matrix is not None:
        new_pts = transform_points(new_pts, matrix)

    index = 0
    for bone in obj.data.edit_bones:
        new_head, new_tail = new_pts[index], new_pts[index+1]
        index += 2

        if lock_length or lock_direction:
            length = bone.length if lock_length else get_dist(new_head, new_tail)
            direction = (bone.vector if lock_direction else Vector(new_tail - new_head)).normalized()
            center = (new_head + new_tail) / 2
            new_head = center + direction * (length * -0.5)
            new_tail = center + direction * (length * 0.5)

        if not only_selected or bone.select_head:
            bone.head[:] = new_head
        if not only_selected or bone.select_tail:
            bone.tail[:] = new_tail

