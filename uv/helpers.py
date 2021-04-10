from collections import namedtuple
from mathutils import Vector
import bmesh

UVItem = namedtuple('UVItem', ['point', 'links', 'loops'])  # (UVPoint, UVItem list, BMLoop list)
UVPoint = namedtuple('UVPoint', ['uv', 'vert_index'])  # Used to uniquely identify 'welded' loops
UVPoint.__repr__ = lambda self: f"UVPoint(i={self.vert_index}, u={self.uv.x:.3f}, v={self.uv.y:.3f})"

class UVBag(tuple):
    """Represents a set of connected UV vertices."""

    _axis = -1

    def __mul__(self, value):
        return NotImplemented

    def _ensure_bounds(self):
        if self._axis != -1:
            return
        us, vs = [item.point.uv.x for item in self], [item.point.uv.y for item in self]
        self._bounds = bounds = min(us), min(vs), max(us), max(vs)
        self._axis = 1 if (bounds[2] - bounds[0] < bounds[3] - bounds[1]) else 0

    def calc_center(self):
        self._ensure_bounds()
        bounds = self._bounds
        return Vector(((bounds[2] + bounds[0]) / 2, (bounds[3] + bounds[1]) / 2))

    @property
    def axis(self):
        self._ensure_bounds()
        return self._axis

    @property
    def bounds(self):
        self._ensure_bounds()
        return self._bounds

    def to_chain(self):
        ends = []
        for item in self:
            if len(item.links) == 1:
                ends.append(item)
                if len(ends) > 2:
                    # Too many ends, not a chain
                    return UVBag()
            elif len(item.links) != 2:
                # Manifold vert, not a chain
                return UVBag()
        if not ends:
            return UVBag()
        # Pick one end to start on, based on bounds
        current = ends[0] if (ends[0].point.uv[self.axis] < ends[1].point.uv[self.axis]) else ends[1]
        chain = []
        last = None
        while current:
            chain.append(current)
            current = next((it for it in current.links if it is not last), None)
            last = chain[-1] if chain else None
        return UVBag(chain)

def _resolve_bag(point_to_item):
    """Reformat bags into their proper form by resolving the links. Easier to work with."""

    item_lookup = {}
    new_items = []
    for item in point_to_item.values():
        item_lookup[item.point] = new_item = UVItem(item.point, [], item.loops)
        new_items.append(new_item)
    for old_item, new_item in zip(point_to_item.values(), new_items):
        new_item.links[:] = [item_lookup[point] for point in old_item.links]
    return UVBag(new_items)

def get_selection_bags(bm):
    bag_map = {}  # UVPoint to (UVPoint to UVItem)
    bags = []  # List of unique bags
    uv_layer = bm.loops.layers.uv.verify()

    for face in bm.faces:
        if not face.select:
            continue

        for loop in face.loops:
            uv = loop[uv_layer]
            if not uv.select:
                continue
            point = UVPoint(uv.uv.copy().freeze(), loop.vert.index)
            bag = bag_map.get(point)
            if bag:
                bag[point].loops.append(loop)

            for other_loop in (loop.link_loop_next, loop.link_loop_prev):
                other_uv = other_loop[uv_layer]
                if other_uv.select:
                    other_point = UVPoint(other_uv.uv.copy().freeze(), other_loop.vert.index)
                    other_bag = bag_map.get(other_point)
                    if other_bag:
                        other_bag[other_point].links.add(point)
                        if not bag:
                            # This loop joins the adjacent bag
                            bag_map[point] = bag = other_bag
                            assert point not in bag
                            bag[point] = UVItem(point, set([other_point]), [loop])
                        elif bag and bag is not other_bag:
                            # This loop is adjacent to two or more bags, merge them
                            assert bag.keys().isdisjoint(other_bag.keys())
                            bag.update(other_bag)
                            bag[point].links.add(other_point)
                            for other_bag_point in other_bag.keys():
                                bag_map[other_bag_point] = bag
                            bags.remove(other_bag)

            if not bag:
                # Lone loop creates a new bag
                bag_map[point] = bag = {point: UVItem(point, set(), [loop])}
                bags.append(bag)

    return [_resolve_bag(bag) for bag in bags]
