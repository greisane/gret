from collections import namedtuple
from math import floor, sqrt
from mathutils import Vector, Matrix
from numpy.polynomial import polynomial as pl
import numpy as np

SMALL_NUMBER = 1e-8
KINDA_SMALL_NUMBER = 1e-4

saturate = lambda x: min(1.0, max(0.0, x))
saturate2 = lambda x: min(1.0 - SMALL_NUMBER, max(0.0, x))
clamp = lambda x, mn, mx: min(mx, max(mn, x))
grid_snap = lambda x, grid: x if grid == 0.0 else floor((x + (grid * 0.5)) / grid) * grid
equals = lambda a, b, threshold=SMALL_NUMBER: abs(a - b) <= threshold
lerp = lambda a, b, t: t * b + (1.0 - t) * a
avg = lambda l, f: sum(f(el) for el in l) / len(l)
frac = lambda x: x - int(x)

class Rect(namedtuple("Rect", ["x0", "y0", "x1", "y1"])):
    @classmethod
    def from_size(self, x, y, width, height):
        return Rect(x, y, x + width, y + height)

    @property
    def width(self):
        return self.x1 - self.x0

    @property
    def height(self):
        return self.y1 - self.y0

    @property
    def center(self):
        return self.x0 + (self.x1 - self.x0) * 0.5, self.y0 + (self.y1 - self.y0) * 0.5

    @property
    def corners(self):
        return (self.x0, self.y0), (self.x1, self.y0), (self.x0, self.y1), (self.x1, self.y1)

    def contains(self, x, y):
        return self.x0 < x < self.x1 and self.y0 < y < self.y1

    def intersects(self, other):
        return self.x0 <= other.x1 and other.x0 <= self.x1 and self.y0 <= other.y1 and other.y0 <= self.y1

    def expand(self, w):
        return Rect(self.x0 - w, self.y0 - w, self.x1 + w, self.y1 + w)

    def resize(self, width, height):
        w, h = width * 0.5, height * 0.5
        cx, cy = self.center
        return Rect(cx - w, cy - h, cx + w, cy + h)

    def move(self, x, y):
        return Rect(self.x0 + x, self.y0 + y, self.x1 + x, self.y1 + y)

    def to_screen(self, view2d):
        x0, y0 = view2d.view_to_region(self.x0, self.y0, clip=False)
        x1, y1 = view2d.view_to_region(self.x1, self.y1, clip=False)
        return Rect(x0, y0, x1, y1)

    def to_trs_matrix(self):
        m = Matrix()
        m[0][3], m[1][3], m[0][0], m[1][1] = self.x0, self.y0, self.width, self.height
        return m

    def transform_point(self, x, y):
        return x * self.width + self.x0, y * self.height + self.y0

    def inverse_transform_point(self, x, y):
        return (x - self.x0) / self.width, (y - self.y0) / self.height

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
