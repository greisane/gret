import bpy
import numpy as np

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
    try:
        weights = np.linalg.solve(H, rhs)
    except np.linalg.LinAlgError as err:
        if 'Singular matrix' in str(err):
            # While testing the matrix would get close to singular, without a definite solution
            # Can't reproduce it now, however in such a case try an approximation
            Hpinv = np.linalg.pinv(H)
            weights = Hpinv.dot(rhs)
        else:
            raise
    return weights

def get_distance_matrix(v1, v2, rbf, radius):
    # numpy alternative to scipy.spatial.distance.cdist(v1, v2, 'euclidean')
    matrix = v1[:, np.newaxis, :] - v2[np.newaxis, :, :]
    matrix = np.linalg.norm(matrix, axis=-1)
    return rbf(matrix, radius)

def get_mesh_points(obj, use_object_transform=False, stride=1):
    """Return vertex coordinates of a mesh as a numpy array with shape (?, 3)."""
    # Moving the mesh seems to be faster. See https://blender.stackexchange.com/questions/139511

    mesh = obj.data
    if use_object_transform:
        mesh = mesh.copy()
        mesh.transform(obj.matrix_world)

    points = np.zeros(len(mesh.vertices)*3, dtype=np.float)
    mesh.vertices.foreach_get('co', points)
    points = points.reshape((-1, 3))[::stride]

    if use_object_transform:
        bpy.data.meshes.remove(mesh)
    return points
