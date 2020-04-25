from mathutils import Vector, Quaternion, Euler
import bpy
import math

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
