from gret.log import log

class SolidPixels:
    """Mimics a pixels array, always returning the same value for all pixels."""

    def __init__(self, size, value=0.0):
        self.size = size
        self.value = value
    def __len__(self):
        return self.size * self.size * 4
    def __getitem__(self, key):
        if isinstance(key, slice):
            return [self.value] * len(range(*key.indices(len(self))))
        return self.value

class Node:
    """Fluent interface wrapper for nodes in a Blender node tree."""

    def __init__(self, type, **kwargs):
        self.type = 'ShaderNode' + type
        self.options = kwargs
        self.default_values = {}
        self.links = []  # List of (this_input, other_output, other)
        self._node = None

    def link(self, this_input, other_output, other):
        """Links the other's node output to this node's input.
        If other_output is None, uses any output socket that matches the type of the input socket."""
        self.links.append((this_input, other_output, other))
        return self

    def set(self, this_input, value):
        """Sets the default value of the input."""
        self.default_values[this_input] = value
        return self

    def find_input_socket(self, id_):
        """Find an input socket by its name, index or type."""
        if id_ in {'VALUE', 'VECTOR', 'RGBA', 'SHADER'}:
            return next(s for s in self._node.inputs if s.type == id_)
        return self._node.inputs[id_]

    def find_output_socket(self, id_):
        """Find an output socket by its name, index or type."""
        if id_ in {'VALUE', 'VECTOR', 'RGBA', 'SHADER'}:
            return next(s for s in self._node.outputs if s.type == id_)
        return self._node.outputs[id_]

    def build(self, node_tree, location=(0, 0)):
        if self._node:
            return

        self._node = node_tree.nodes.new(type=self.type)
        self._node.location[:] = location
        # Can't get actual node dimensions until the layout is updated, so take a guess
        node_height = max(len(self._node.inputs), len(self._node.outputs)) * 20.0 + 200.0
        self.branch_height = node_height + 20.0

        for k, v in self.options.items():
            try:
                setattr(self._node, k, v)
            except (AttributeError, TypeError) as e:
                log(f"Couldn't set option {k} for node {self._node.name}: {e}")
        for k, v in self.default_values.items():
            self.find_input_socket(k).default_value = v

        height = 0.0
        for link_idx, (this_input, other_output, other) in enumerate(self.links):
            # Rudimentary arrangement
            other_x = self._node.location.x - 200.0
            other_y = self._node.location.y - height
            other.build(node_tree, (other_x, other_y))
            height += other.branch_height

            this_input_socket = self.find_input_socket(this_input)
            other_output = this_input_socket.type if other_output is None else other_output
            other_output_socket = other.find_output_socket(other_output)
            node_tree.links.new(this_input_socket, other_output_socket)

    def __repr__(self):
        return f"{__class__.__name__}({repr(self.type)})"
