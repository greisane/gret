from collections import namedtuple
from mathutils import Vector
import bmesh

from ..math import calc_bounds_2d, calc_center_2d

class UVVector(Vector):
    """Vector with an extra component used to separately identify face corners."""

    __slots__ = 'vert_index'

    def __new__(cls, vert_index, seq):
        assert len(seq) == 2
        self = super().__new__(cls, seq).freeze()
        self.vert_index = vert_index
        return self
    def __eq__(self, other):
        return self.vert_index == other.vert_index and super().__eq__(other)
    def __ne__(self, other):
        return self.vert_index != other.vert_index or super().__ne__(other)
    def __hash__(self):
        return hash((self.vert_index, *self))
    def __repr__(self):
        return f"UVVector(i={self.vert_index}, u={self.x:.3f}, v={self.y:.3f})"

class UVPoint(namedtuple('UVPoint', ['uv', 'links', 'bmloops'])):
    __slots__ = ()

    @property
    def vert(self):
        return self.bmloops[0].vert

class UVBag(tuple):
    """Collection of UV points."""

    def __mul__(self, value):
        return NotImplemented
    def calc_bounds(self):
        return calc_bounds_2d(point.uv for point in self)
    def calc_center(self):
        return calc_center_2d(point.uv for point in self)

class UVBagLoop(UVBag):
    is_closed = False

def get_selection_bags(bm):
    uv2uv2p = {}  # UVVector to (UVVector to UVPoint)
    all_uv2p = []  # List of unique (UVVector to UVPoint)
    uv_layer = bm.loops.layers.uv.verify()

    for face in bm.faces:
        if not face.select:
            continue

        for loop in face.loops:
            loopuv = loop[uv_layer]
            if not loopuv.select:
                continue

            # Check if this face corner belongs to a bag by virtue of being UV 'welded'
            uv = UVVector(loop.vert.index, loopuv.uv)
            uv2p = uv2uv2p.get(uv)
            if uv2p:
                uv2p[uv].bmloops.append(loop)

            for other_loop in (loop.link_loop_next, loop.link_loop_prev):
                other_loopuv = other_loop[uv_layer]
                if not other_loopuv.select:
                    continue

                other_point = UVVector(other_loop.vert.index, other_loopuv.uv)
                other_bag = uv2uv2p.get(other_point)
                if other_bag:
                    other_bag[other_point].links.add(uv)
                    if not uv2p:
                        # This point doesn't have a bag, join the adjacent bag
                        uv2uv2p[uv] = uv2p = other_bag
                        assert uv not in uv2p
                        uv2p[uv] = UVPoint(uv, set([other_point]), [loop])
                    elif uv2p and uv2p is not other_bag:
                        # This point is adjacent to two or more bags, merge them
                        assert uv2p.keys().isdisjoint(other_bag.keys())
                        uv2p.update(other_bag)
                        uv2p[uv].links.add(other_point)
                        for other_bag_point in other_bag.keys():
                            uv2uv2p[other_bag_point] = uv2p
                        all_uv2p.remove(other_bag)

            if not uv2p:
                # Lone point creates a new bag
                uv2uv2p[uv] = uv2p = {uv: UVPoint(uv, set(), [loop])}
                all_uv2p.append(uv2p)

    # Reformat bags into their proper form by resolving the links
    bags = []
    for uv2p in all_uv2p:
        new_uv2p = {}
        new_points = []

        for p in uv2p.values():
            new_uv2p[p.uv] = new_p = UVPoint(p.uv, [], p.bmloops)
            new_points.append(new_p)
        for old_p, new_p in zip(uv2p.values(), new_points):
            new_p.links[:] = [new_uv2p[uv] for uv in old_p.links]

        bags.append(UVBag(new_points))

    return bags

def get_selection_loops(bm):
    bags = get_selection_bags(bm)
    loops = []

    for bag in bags:
        ends = []
        if len(bag) < 2:
            # Too few points
            continue
        if any(len(point.links) > 2 for point in bag):
            # Has manifold points, not a loop
            continue

        ends = [point for point in bag if len(point.links) == 1]
        if len(ends) == 2:
            # Open loop, pick the endpoint to start on based on bounds
            _, _, axis = bag.calc_bounds()
            current = ends[0] if (ends[0].uv[axis] < ends[1].uv[axis]) else ends[1]
            end = None
        else:
            # Closed loop, begin anywhere
            assert not ends
            current = end = bag[0]

        points = []
        prev = None
        while True:
            points.append(current)
            prev, current = current, next((p for p in current.links if p is not prev), None)
            if current is end:
                break
        assert len(points) == len(bag)

        loop = UVBagLoop(points)
        loop.is_closed = end is not None
        loops.append(loop)

    return loops
