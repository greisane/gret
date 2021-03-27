from mathutils import Vector, Quaternion, Euler
from numpy.polynomial import polynomial as pl
import bpy
import math
import numpy as np

def get_sq_dist(a, b):
    """Returns the square distance between two vectors."""
    x, y, z = a.x - b.x, a.y - b.y, a.z - b.z
    return x*x + y*y + z*z

def get_range_pct(min_value, max_value, value):
    """Calculates the percentage along a line from min_value to max_value."""

    divisor = max_value - min_value
    if divisor <= 0.0001:
        return 1.0 if value >= max_value else 0.0
    return (value - min_value) / divisor

def get_point_dist_to_line(point, direction, origin):
    """
    Calculates the distance of a given point in world space to a given line.
    Assumes direction is normalized.
    """
    closest_point = origin + (direction * ((point - origin).dot(direction)))
    return (closest_point - point).length

def get_best_fit_line(points):
    """
    Calculates the best fit line that minimizes distance from the line to each point.
    Returns two vectors: the direction of the line and a point it passes through.
    """
    # https://stackoverflow.com/questions/24747643/3d-linear-regression
    # https://machinelearningmastery.com/calculate-principal-component-analysis-scratch-python/
    import numpy as np
    A = np.array(points)
    M = np.mean(A.T, axis=1)  # Find mean
    C = A - M  # Center around mean
    V = np.cov(C.T)  # Calculate covariance matrix of centered matrix
    U, s, Vh = np.linalg.svd(V)  # Singular value decomposition
    return Vector(U[:,0]), Vector(M)

def fit_curve(points, num_segments, polydeg=3, max_iter=20):
    """
    polydeg: Degree of polygons of parametric curve.
    max_iter: Max. number of iterations.
    """
    # From https://meshlogic.github.io/posts/jupyter/curve-fitting/parametric-curve-fitting/

    n = len(points)
    P = np.array(points)

    def generate_param(P, alpha):
        n = len(P)
        u = np.zeros(n)
        u_sum = 0
        for i in range(1,n):
            u_sum += np.linalg.norm(P[i,:]-P[i-1,:])**alpha
            u[i] = u_sum

        return u/max(u)

    def centripetal_param(P):
        u = generate_param(P, alpha=0.5)
        return u

    def find_min_gss(f, a, b, eps=1e-4):
        """
        Find Minimum by Golden Section Search Method.
        Return x minimizing function f(x) on interval a, b
        """
        R = 0.61803399  # Golden section: 1/phi = 2/(1+sqrt(5))
        # Num of needed iterations to get precision eps: log(eps/|b-a|)/log(R)
        n_iter = int(np.ceil(-2.0780869 * np.log(eps/abs(b-a))))
        c = b - (b-a)*R
        d = a + (b-a)*R

        for _ in range(n_iter):
            if f(c) < f(d):
                b = d
            else:
                a = c
            c = b - (b-a)*R
            d = a + (b-a)*R

        return (b+a)/2

    def iterative_param(P, u, fxcoeff, fycoeff, fzcoeff):
        u_new = u.copy()
        f_u = np.zeros(3)

        # Calculate approx. error s(u) related to point P_i
        def calc_s(u):
            f_u[0] = pl.polyval(u, fxcoeff)
            f_u[1] = pl.polyval(u, fycoeff)
            f_u[2] = pl.polyval(u, fzcoeff)
            s_u = np.linalg.norm(P[i]-f_u)
            return s_u

        # Find new values u that locally minimising the approximation error (excl. fixed end-points)
        for i in range(1, len(u)-1):
            # Find new u_i minimising s(u_i) by Golden search method
            u_new[i] = find_min_gss(calc_s, u[i-1], u[i+1])

            # Sample some values bewteen u[i-1] and u[i+1] to plot graph
            u_samp = np.linspace(u[i-1], u[i+1], 25)
            x = pl.polyval(u_samp, fxcoeff)
            y = pl.polyval(u_samp, fycoeff)
            z = pl.polyval(u_samp, fzcoeff)

            residual = P[i] - np.array([x,y,z]).T
            s_u_samp = [np.linalg.norm(residual[j]) for j in range(len(u_samp))]

        return u_new

    # Options for the approximation method
    w = np.ones(n)  # Set weights for knot points
    w[0] = w[-1] = 1e6
    eps = 1e-3

    # Init variables
    f_u = np.zeros([n,3])
    uu = np.linspace(0,1,100)
    f_uu = np.zeros([len(uu),3])
    S_hist = []

    # Compute the iterative approximation
    for iter_i in range(max_iter):
        # Initial or iterative parametrization
        if iter_i == 0:
            u = centripetal_param(P)
        else:
            u = iterative_param(P, u, fxcoeff, fycoeff, fzcoeff)

        # Compute polynomial approximations and get their coefficients
        fxcoeff = pl.polyfit(u, P[:,0], polydeg, w=w)
        fycoeff = pl.polyfit(u, P[:,1], polydeg, w=w)
        fzcoeff = pl.polyfit(u, P[:,2], polydeg, w=w)

        # Calculate function values f(u)=(fx(u),fy(u),fz(u))
        f_u[:,0] = pl.polyval(u, fxcoeff)
        f_u[:,1] = pl.polyval(u, fycoeff)
        f_u[:,2] = pl.polyval(u, fzcoeff)

        # Calculate fine values for ploting
        f_uu[:,0] = pl.polyval(uu, fxcoeff)
        f_uu[:,1] = pl.polyval(uu, fycoeff)
        f_uu[:,2] = pl.polyval(uu, fzcoeff)

        # Total error of approximation S for iteration i
        S = 0
        for j in range(len(u)):
            S += w[j] * np.linalg.norm(P[j] - f_u[j])

        # Add bar of approx. error
        S_hist.append(S)

        # Stop iterating if change in error is lower than desired condition
        if iter_i > 0:
            S_change = S_hist[iter_i-1] / S_hist[iter_i] - 1
            if S_change < eps:
                break

    step = len(f_uu) // (num_segments + 1) + 1
    return f_uu[::step]

class RBF:
    """Radial basis function kernels and helpers."""
    # Based on https://github.com/chadmv/cmt/blob/master/scripts/cmt/rig/meshretarget.py
    # Which in turn references http://mathlab.github.io/PyGeM/_modules/pygem/radial.html#RBF

    @classmethod
    def linear(cls, matrix, radius):
        return matrix

    @classmethod
    def gaussian(cls, matrix, radius):
        result = np.exp(-(matrix * matrix) / (radius * radius))
        return result

    @classmethod
    def thin_plate(cls, matrix, radius):
        result = matrix / radius
        result *= matrix
        np.warnings.filterwarnings("ignore")
        result = np.where(result > 0, np.log(result), result)
        np.warnings.filterwarnings("always")
        return result

    @classmethod
    def multi_quadratic_biharmonic(cls, matrix, radius):
        result = np.sqrt((matrix * matrix) + (radius * radius))
        return result

    @classmethod
    def inv_multi_quadratic_biharmonic(cls, matrix, radius):
        result = 1.0 / (np.sqrt((matrix * matrix) + (radius * radius)))
        return result

    @classmethod
    def beckert_wendland_c2_basis(cls, matrix, radius):
        arg = matrix / radius
        first = np.zeros(matrix.shape)
        first = np.where(1 - arg > 0, np.power(1 - arg, 4), first)
        second = (4 * arg) + 1
        result = first * second
        return result

    @classmethod
    def get_weight_matrix(cls, src_pts, dst_pts, rbf, radius):
        """Get the weight matrix x in Ax=B."""

        assert src_pts.shape == dst_pts.shape
        num_pts, dim = src_pts.shape
        identity = np.ones((num_pts, 1))
        dist = cls.get_distance_matrix(src_pts, src_pts, rbf, radius)
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

    @classmethod
    def get_distance_matrix(cls, v1, v2, rbf, radius):
        # numpy alternative to scipy.spatial.distance.cdist(v1, v2, 'euclidean')
        matrix = v1[:, np.newaxis, :] - v2[np.newaxis, :, :]
        matrix = np.linalg.norm(matrix, axis=-1)
        return rbf(matrix, radius)