from collections import namedtuple
from math import floor, sqrt, copysign
from mathutils import Vector, Quaternion, Matrix
from numbers import Number
from numpy.polynomial import polynomial as pl
import numpy as np

ZERO_ANIMWEIGHT_THRESH = 0.00001
DELTA = 0.00001
SMALL_NUMBER = 1e-8
KINDA_SMALL_NUMBER = 1e-4

zero_vector = Vector((0.0, 0.0, 0.0))
half_vector = Vector((0.5, 0.5, 0.5))
one_vector = Vector((1.0, 1.0, 1.0))

saturate = lambda x: min(1.0, max(0.0, x))
saturate2 = lambda x: min(1.0 - SMALL_NUMBER, max(0.0, x))
clamp = lambda x, mn, mx: min(mx, max(mn, x))
grid_snap = lambda x, grid: x if grid == 0.0 else floor((x + (grid * 0.5)) / grid) * grid
equals = lambda a, b, threshold=SMALL_NUMBER: np.max(np.abs(a - b)) <= threshold
lerp = lambda a, b, t: t * b + (1.0 - t) * a
lerp_array = lambda a, b, x: tuple(lerp(a, b, x) for a, b in zip(a, b))
invlerp = lambda a, b, x: (x - a) / (b - a)  # Safe version in get_range_pct
avg = lambda l, f: sum(f(el) for el in l) / len(l)
frac = lambda x: x - int(x)
sigmoid = lambda x: 1.0 / (np.exp(-x) + 1.0)
wrap = lambda x, y: x % y if x >= 0 else (x % y + y) % y
abs_max = lambda x, y: copysign(y, x) if abs(x) >= y else x

class Rect(namedtuple('Rect', 'x0 y0 x1 y1')):
    __slots__ = ()

    @classmethod
    def from_corner(cls, x, y, /, width, height):
        return cls(x, y, x + width, y + height)

    @classmethod
    def from_center(cls, x, y, /, width, height):
        hw, hh = width * 0.5, height * 0.5
        return cls(x - hw, y - hh, x + hw, y + hh)

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def height(self):
        return self.y1 - self.y0

    @property
    def area(self):
        return self.width * self.height

    @property
    def center(self):
        return self.x0 + (self.x1 - self.x0) * 0.5, self.y0 + (self.y1 - self.y0) * 0.5

    @property
    def corners(self):
        return (self.x0, self.y0), (self.x1, self.y0), (self.x0, self.y1), (self.x1, self.y1)

    @property
    def size(self):
        return self.width, self.height

    def with_size(self, width, height, /):
        hw, hh = width * 0.5, height * 0.5
        cx, cy = self.center
        return Rect(cx - hw, cy - hh, cx + hw, cy + hh)

    def contains(self, x, y):
        return self.x0 < x < self.x1 and self.y0 < y < self.y1

    def intersects(self, other):
        return self.x0 <= other.x1 and other.x0 <= self.x1 and self.y0 <= other.y1 and other.y0 <= self.y1

    def expand(self, w, h=None, /):
        h = w if h is None else h
        return Rect(self.x0 - w, self.y0 - h, self.x1 + w, self.y1 + h)

    def move(self, x, y, /):
        return Rect(self.x0 + x, self.y0 + y, self.x1 + x, self.y1 + y)

    def to_screen(self, view2d):
        x0, y0 = view2d.view_to_region(self.x0, self.y0, clip=False)
        x1, y1 = view2d.view_to_region(self.x1, self.y1, clip=False)
        return Rect(x0, y0, x1, y1)

    def to_matrix(self):
        mat = Matrix()
        mat[0][3], mat[1][3], mat[0][0], mat[1][1] = self.x0, self.y0, self.width, self.height
        return mat

    def transform_point(self, x, y, /):
        return x * self.width + self.x0, y * self.height + self.y0

    def inverse_transform_point(self, x, y, /):
        return (x - self.x0) / self.width, (y - self.y0) / self.height

    def __mul__(self, other):
        if isinstance(other, Number):
            return Rect(self[0] * other, self[1] * other, self[2] * other, self[3] * other)
        if not isinstance(other, str):
            if len(other) == 4:
                return Rect(self[0] * other[0], self[1] * other[1], self[2] * other[2], self[3] * other[3])
            elif len(other) == 2:
                return Rect(self[0] * other[0], self[1] * other[1], self[2] * other[0], self[3] * other[1])
            elif len(other) == 1:
                return Rect(self[0] * other[0], self[1] * other[0], self[2] * other[0], self[3] * other[0])
        return NotImplemented

    def __truediv__(self, other):
        if isinstance(other, Number):
            return Rect(self[0] / other, self[1] / other, self[2] / other, self[3] / other)
        if not isinstance(other, str):
            if len(other) == 4:
                return Rect(self[0] / other[0], self[1] / other[1], self[2] / other[2], self[3] / other[3])
            elif len(other) == 2:
                return Rect(self[0] / other[0], self[1] / other[1], self[2] / other[0], self[3] / other[1])
            elif len(other) == 1:
                return Rect(self[0] / other[0], self[1] / other[0], self[2] / other[0], self[3] / other[0])
        return NotImplemented

class Transform:
    __slots__ = ('location', 'rotation', 'scale')

    def __init__(self, location=None, rotation=None, scale=None):
        self.location = location or Vector()
        self.rotation = rotation or Quaternion()
        self.scale = scale or Vector((1.0, 1.0, 1.0))

    def copy(self):
        return Transform(
            self.location.copy(),
            self.rotation.copy(),
            self.scale.copy())

    def to_matrix(self):
        return Matrix.LocRotScale(self.location, self.rotation, self.scale)

    def equals(self, other, tolerance=0.00001):
        return (abs(self.location.x - other.location.x) <= tolerance
            and abs(self.location.y - other.location.y) <= tolerance
            and abs(self.location.z - other.location.z) <= tolerance
            and abs(self.rotation.w - other.rotation.w) <= tolerance
            and abs(self.rotation.x - other.rotation.x) <= tolerance
            and abs(self.rotation.y - other.rotation.y) <= tolerance
            and abs(self.rotation.z - other.rotation.z) <= tolerance
            and abs(self.scale.x - other.scale.x) <= tolerance
            and abs(self.scale.y - other.scale.y) <= tolerance
            and abs(self.scale.z - other.scale.z) <= tolerance)

    def accumulate_with_shortest_rotation(self, delta_atom, blend_weight=1.0):
        """Accumulates another transform with this one, with an optional blending weight.
Rotation is accumulated additively, in the shortest direction."""

        atom = delta_atom * blend_weight

        # To ensure the shortest route, make sure the dot product between the rotations is positive
        if self.rotation.dot(atom.rotation) < 0.0:
            self.rotation -= atom.rotation
        else:
            self.rotation += atom.rotation
        self.location += atom.location
        self.scale += atom.scale

        return self  # Return self for convenience

    @staticmethod
    def blend_from_identity_and_accumulate(final_atom, source_atom, blend_weight=1.0):
        """Blends the identity transform with a weighted source transform \
and accumulates that into a destination transform."""

        delta_location = source_atom.location
        delta_rotation = source_atom.rotation
        delta_scale = source_atom.scale

        # Scale delta by weight
        if blend_weight < 1.0 - ZERO_ANIMWEIGHT_THRESH:
            delta_location = source_atom.location * blend_weight
            delta_scale = zero_vector.lerp(source_atom.scale, blend_weight)
            delta_rotation = source_atom.rotation * blend_weight
            delta_rotation.w = lerp(1.0, source_atom.rotation.w, blend_weight)

        # Add ref pose relative animation to base animation, only if rotation is significant
        if delta_rotation.w * delta_rotation.w < 1.0 - DELTA * DELTA:
            # final_atom.rotation = delta_rotation * final_atom.rotation
            final_atom.rotation.rotate(delta_rotation)

        final_atom.location += delta_location
        final_atom.scale.x *= 1.0 + delta_scale.x
        final_atom.scale.y *= 1.0 + delta_scale.y
        final_atom.scale.z *= 1.0 + delta_scale.z

    def get_safe_scale_reciprocal(self, tolerance=0.00001):
        return Vector((
            0.0 if abs(self.scale.x) <= tolerance else 1.0 / self.scale.x,
            0.0 if abs(self.scale.y) <= tolerance else 1.0 / self.scale.y,
            0.0 if abs(self.scale.z) <= tolerance else 1.0 / self.scale.z))

    def make_additive(self, base_transform):
        self.location -= base_transform.location
        self.rotation.rotate(base_transform.rotation.inverted())
        self.rotation.normalize()
        base_scale = base_transform.get_safe_scale_reciprocal()
        self.scale.x = self.scale.x * base_scale.x - 1.0
        self.scale.y = self.scale.y * base_scale.y - 1.0
        self.scale.z = self.scale.z * base_scale.z - 1.0

    def __eq__(self, other):
        if isinstance(other, Transform):
            return (self.location == other.location
                and self.rotation == other.rotation
                and self.scale == other.scale)
        return NotImplemented

    def __ne__(self, other):
        if isinstance(other, Transform):
            return (self.location != other.location
                or self.rotation != other.rotation
                or self.scale != other.scale)
        return NotImplemented

    def __add__(self, other):
        if isinstance(other, Transform):
            return self.copy().accumulate_with_shortest_rotation(other)
        return NotImplemented

    def __sub__(self, other):
        if isinstance(other, Transform):
            return self.copy().accumulate_with_shortest_rotation(-other)
        return NotImplemented

    def __mul__(self, other):
        if isinstance(other, Number):
            return Transform(self.location * other, self.rotation * other, self.scale * other)
        return NotImplemented

    def __iadd__(self, other):
        if isinstance(other, Transform):
            return self.accumulate_with_shortest_rotation(other)
        return NotImplemented

    def __isub__(self, other):
        if isinstance(other, Transform):
            return self.accumulate_with_shortest_rotation(-other)
        return NotImplemented

    def __imul__(self, other):
        if isinstance(other, Number):
            self.location *= other
            self.rotation *= other
            self.scale *= other
        return NotImplemented

    def __neg__(self):
        return Transform(-self.location, -self.rotation, -self.scale)

    def __pos__(self):
        return Transform(+self.location, +self.rotation, +self.scale)

    def __getitem__(self, key):
        return getattr(self, key)

    def __setitem__(self, key, value):
        setattr(self, key, value)

def calc_bounds(points):
    xs, ys, zs = zip(*points)
    x0, y0, z0, x1, y1, z1 = min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)
    return Vector((x0, y0, z0)), Vector((x1, y1, z1))

def calc_bounds_2d(points):
    xs, ys = zip(*points)
    x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
    axis = 1 if (x1 - x0 < y1 - y0) else 0
    return Vector((x0, y0)), Vector((x1, y1)), axis

def calc_center(points):
    return sum(points, Vector()) / len(points)

def calc_center_2d(points):
    return sum(points, Vector((0.0, 0.0))) / len(points)

def get_dist_sq(a, b):
    """Returns the square distance between two 3D vectors."""
    x, y, z = a[0] - b[0], a[1] - b[1], a[2] - b[2]
    return x*x + y*y + z*z

def get_dist(a, b):
    """Returns the distance between two vectors."""
    return sqrt(get_dist_sq(a, b))

def get_direction_safe(a, b):
    """Returns the direction from one 3D position to another, or zero vector if they are too close."""
    x, y, z = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    k = sqrt(x*x + y*y + z*z)
    if k <= SMALL_NUMBER:
        return Vector()
    return Vector((x/k, y/k, z/k))

def normalized(a, axis=-1, order=2):
    # From https://stackoverflow.com/a/21032099
    l2 = np.atleast_1d(np.linalg.norm(a, order, axis))
    l2[l2 == 0] = 1
    return a / np.expand_dims(l2, axis)

def get_range_pct(min_value, max_value, value):
    """Calculates the percentage along a line from min_value to max_value."""

    divisor = max_value - min_value
    if abs(divisor) <= SMALL_NUMBER:
        return 1.0 if value >= max_value else 0.0
    return (value - min_value) / divisor

def get_point_dist_to_line(point, direction, origin):
    """
    Calculates the distance of a given point in world space to a given line.
    Assumes direction is normalized.
    """
    return sqrt(get_point_dist_to_line_sq(point, direction, origin))

def get_point_dist_to_line_sq(point, direction, origin):
    """
    Calculates the square distance of a given point in world space to a given line.
    Assumes direction is normalized.
    """
    closest_point = origin + direction * (point - origin).dot(direction)
    return (closest_point - point).length_squared

def calc_best_fit_line(points):
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

def calc_fit_curve(points, num_segments, polydeg=3, max_iter=20):
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

def reverse_morton3(x):
    x &= 0x09249249
    x = (x ^ (x >> 2)) & 0x030c30c3
    x = (x ^ (x >> 4)) & 0x0300f00f
    x = (x ^ (x >> 8)) & 0xff0000ff
    x = (x ^ (x >> 16)) & 0x000003ff
    return x

def zagzig(x):
    return (x >> 1) ^ -(x & 1)
