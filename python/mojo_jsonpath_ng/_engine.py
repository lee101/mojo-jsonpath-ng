from __future__ import annotations

import ctypes
from dataclasses import dataclass

import numpy as np

from ._lib import lib

FIELD = 1
INDEX = 2
SLICE = 3
LIST_WILDCARD = 4
FIELD_WILDCARD = 5
FILTER = 6

_stats = {"mojo_calls": 0, "python_fallbacks": 0}


def engine_stats():
    return dict(_stats)


def _i64(values):
    values = values or [0]
    return np.ascontiguousarray(values, dtype=np.int64)


def _f64(values):
    values = values or [0.0]
    return np.ascontiguousarray(values, dtype=np.float64)


@dataclass(frozen=True)
class FlatTree:
    objects: list
    parent: np.ndarray
    first: np.ndarray
    next_sibling: np.ndarray
    subtree_end: np.ndarray
    kind: np.ndarray
    key_id: np.ndarray
    item_index: np.ndarray
    value_type: np.ndarray
    value_string: np.ndarray
    value_number: np.ndarray
    child_start: np.ndarray
    child_count: np.ndarray
    child_nodes: np.ndarray
    child_keys: np.ndarray
    keys: list
    key_ids: dict
    exact_float_integers: bool

    def intern(self, value: str) -> int:
        if value not in self.key_ids:
            self.key_ids[value] = len(self.keys)
            self.keys.append(value)
        return self.key_ids[value]


@dataclass(frozen=True)
class PreparedDocument:
    data: object
    flat: FlatTree


def prepare(data):
    tree = flatten(data)
    if tree is None:
        raise TypeError("prepare() requires a JSON object or array")
    return PreparedDocument(data, tree)


def flatten(data) -> FlatTree | None:
    if not isinstance(data, (dict, list)):
        return None
    objects = []
    parent = []
    first = []
    next_sibling = []
    subtree_end = []
    kind = []
    key_id = []
    item_index = []
    value_type = []
    value_string = []
    value_number = []
    child_ordinal = []
    keys = []
    key_ids = {}
    last_child = []
    exact_float_integers = True

    def intern(value):
        if value not in key_ids:
            key_ids[value] = len(keys)
            keys.append(value)
        return key_ids[value]

    stack = [(0, data, -1, -1, -1, -1)]
    while stack:
        event, value, parent_id, field_id, index, ordinal = stack.pop()
        if event:
            subtree_end[parent_id] = len(objects)
            continue

        node = len(objects)
        objects.append(value)
        parent.append(parent_id)
        first.append(-1)
        next_sibling.append(-1)
        subtree_end.append(-1)
        key_id.append(field_id)
        item_index.append(index)
        child_ordinal.append(ordinal)
        last_child.append(-1)

        if parent_id >= 0:
            if first[parent_id] < 0:
                first[parent_id] = node
            else:
                next_sibling[last_child[parent_id]] = node
            last_child[parent_id] = node

        if isinstance(value, dict):
            kind.append(1)
            value_type.append(4)
            value_string.append(-1)
            value_number.append(0.0)
            children = []
            for field, child in value.items():
                if isinstance(field, str):
                    children.append(
                        (child, node, intern(field), -1, len(children))
                    )
        elif isinstance(value, list):
            kind.append(2)
            value_type.append(4)
            value_string.append(-1)
            value_number.append(0.0)
            children = [(child, node, -1, i, i) for i, child in enumerate(value)]
        else:
            kind.append(0)
            if value is None:
                value_type.append(0)
                value_string.append(-1)
                value_number.append(0.0)
            elif isinstance(value, bool):
                value_type.append(1)
                value_string.append(-1)
                value_number.append(float(value))
            elif isinstance(value, (int, float)):
                value_type.append(2)
                value_string.append(-1)
                value_number.append(float(value))
                if isinstance(value, int) and abs(value) > 2**53:
                    exact_float_integers = False
            elif isinstance(value, str):
                value_type.append(3)
                value_string.append(intern(value))
                value_number.append(0.0)
            else:
                value_type.append(5)
                value_string.append(-1)
                value_number.append(0.0)
            children = []

        stack.append((1, None, node, -1, -1, -1))
        for child, child_parent, child_key, child_index, child_position in reversed(children):
            stack.append(
                (0, child, child_parent, child_key, child_index, child_position)
            )

    parent_array = _i64(parent)
    key_array = _i64(key_id)
    node_count = len(objects)
    child_count_array = np.bincount(
        parent_array[1:], minlength=node_count
    ).astype(np.int64, copy=False)
    child_start_array = np.empty(node_count, dtype=np.int64)
    child_start_array[0] = 0
    if node_count > 1:
        np.cumsum(child_count_array[:-1], out=child_start_array[1:])
    adjacency_size = max(1, node_count - 1)
    child_nodes_array = np.empty(adjacency_size, dtype=np.int64)
    if node_count > 1:
        ordinals = np.asarray(child_ordinal[1:], dtype=np.int64)
        slots = child_start_array[parent_array[1:]] + ordinals
        child_nodes_array[slots] = np.arange(1, node_count, dtype=np.int64)
    else:
        child_nodes_array[0] = 0
    child_keys_array = np.ascontiguousarray(
        key_array[child_nodes_array], dtype=np.int64
    )
    return FlatTree(
        objects,
        parent_array,
        _i64(first),
        _i64(next_sibling),
        _i64(subtree_end),
        _i64(kind),
        key_array,
        _i64(item_index),
        _i64(value_type),
        _i64(value_string),
        _f64(value_number),
        child_start_array,
        child_count_array,
        child_nodes_array,
        child_keys_array,
        keys,
        key_ids,
        exact_float_integers,
    )


@dataclass(frozen=True)
class Program:
    ops: np.ndarray
    desc: np.ndarray
    arg0: np.ndarray
    arg1: np.ndarray
    arg2: np.ndarray
    arg3: np.ndarray
    arg4: np.ndarray
    number_arg: np.ndarray
    pool: np.ndarray
    multiplier: int


def compile_path(path, flat: FlatTree) -> Program | None:
    from .ext.filter import Expression, Filter
    from .jsonpath import Child, Descendants, Fields, Index, Root, Slice, This

    instructions = []
    pool = []
    multiplier = 1

    def add_atomic(node, descendant=False):
        nonlocal multiplier
        if isinstance(node, Fields):
            if "*" in node.fields:
                instructions.append((FIELD_WILDCARD, descendant, 0, 0, 0, 0, 0, 0.0))
                return True
            start = len(pool)
            pool.extend(flat.intern(field) for field in node.fields)
            multiplier = min(32, multiplier * max(1, len(node.fields)))
            instructions.append(
                (FIELD, descendant, start, len(node.fields), 0, 0, 0, 0.0)
            )
            return True
        if isinstance(node, Index):
            if any(index < 0 for index in node.indices):
                return False
            start = len(pool)
            pool.extend(node.indices)
            multiplier = min(32, multiplier * max(1, len(node.indices)))
            instructions.append(
                (INDEX, descendant, start, len(node.indices), 0, 0, 0, 0.0)
            )
            return True
        if isinstance(node, Slice):
            if node.start is node.end is node.step is None:
                instructions.append((LIST_WILDCARD, descendant, 0, 0, 0, 0, 0, 0.0))
                return True
            step = 1 if node.step is None else node.step
            if step <= 0:
                return False
            missing = (1 if node.start is None else 0) | (2 if node.end is None else 0)
            instructions.append(
                (
                    SLICE,
                    descendant,
                    0 if node.start is None else node.start,
                    0 if node.end is None else node.end,
                    step,
                    missing,
                    0,
                    0.0,
                )
            )
            return True
        if isinstance(node, Filter) and len(node.expressions) == 1:
            # Python integers are arbitrary precision, while the compact
            # scalar table is float64.  Use the Python evaluator whenever any
            # integer in the document cannot be represented exactly.
            if not flat.exact_float_integers:
                return False
            expression = node.expressions[0]
            if not isinstance(expression, Expression) or expression.op == "=~":
                return False
            target_keys = []

            def target(node):
                if isinstance(node, (This, Root)):
                    return True
                if isinstance(node, Fields) and len(node.fields) == 1 and node.fields[0] != "*":
                    target_keys.append(node.fields[0])
                    return True
                if isinstance(node, Child):
                    return target(node.left) and target(node.right)
                return False

            if not target(expression.target):
                return False
            start = len(pool)
            pool.extend(flat.intern(field) for field in target_keys)
            op = {
                None: 0, "==": 1, "=": 1, "!=": 2, "<": 3,
                "<=": 4, ">": 5, ">=": 6,
            }.get(expression.op)
            if op is None:
                return False
            value = expression.value
            if type(value) is int:
                if abs(value) > 2**53:
                    return False
                constant_type, constant_string, number = 1, -1, float(value)
            elif type(value) is float:
                constant_type, constant_string, number = 2, -1, value
            elif type(value) is str:
                if op not in (1, 2):
                    return False
                constant_type, constant_string, number = 3, flat.intern(value), 0.0
            elif type(value) is bool:
                constant_type, constant_string, number = 4, -1, float(value)
            elif value is None:
                constant_type, constant_string, number = 5, -1, 0.0
            else:
                return False
            instructions.append(
                (
                    FILTER, descendant, start, len(target_keys), op,
                    constant_type, constant_string, number,
                )
            )
            return True
        return False

    def walk(node):
        if isinstance(node, (Root, This)):
            return True
        if isinstance(node, Child):
            return walk(node.left) and walk(node.right)
        if isinstance(node, Descendants):
            if not walk(node.left):
                return False
            before = len(instructions)
            if not add_atomic(node.right, descendant=True):
                return False
            return len(instructions) == before + 1
        return add_atomic(node)

    if not walk(path) or not instructions:
        return None
    columns = list(zip(*instructions))
    return Program(
        *[_i64(list(column)) for column in columns[:7]],
        _f64(list(columns[7])),
        _i64(pool),
        multiplier,
    )


def _address(array):
    return array.ctypes.data_as(ctypes.c_void_p)


def _validate_buffer(array, dtype, minimum, name):
    if (
        not isinstance(array, np.ndarray)
        or array.dtype != np.dtype(dtype)
        or array.ndim != 1
        or not array.flags.c_contiguous
        or array.size < minimum
        or not array.flags.aligned
        or array.ctypes.data == 0
    ):
        raise TypeError(
            f"{name} must be an aligned, contiguous one-dimensional "
            f"{np.dtype(dtype)} NumPy array with at least {minimum} elements"
        )


def accelerated_find(path, data):
    from .jsonpath import DatumInContext

    if isinstance(data, DatumInContext):
        _stats["python_fallbacks"] += 1
        return None
    if not isinstance(data, PreparedDocument) and _contains_filter(path):
        _stats["python_fallbacks"] += 1
        return _cold_filter_find(path, data)
    flat = data.flat if isinstance(data, PreparedDocument) else flatten(data)
    if flat is None:
        _stats["python_fallbacks"] += 1
        return None
    program = compile_path(path, flat)
    if program is None:
        _stats["python_fallbacks"] += 1
        return None

    capacity = max(1, len(flat.objects) * program.multiplier)
    work_a = np.empty(capacity, dtype=np.int64)
    work_b = np.empty(capacity, dtype=np.int64)
    node_count = len(flat.objects)
    step_count = len(program.ops)
    for name in (
        "first", "next_sibling", "subtree_end", "kind", "key_id",
        "item_index", "value_type", "value_string", "child_start",
        "child_count",
    ):
        _validate_buffer(getattr(flat, name), np.int64, node_count, name)
    _validate_buffer(flat.value_number, np.float64, node_count, "value_number")
    adjacency_count = max(1, node_count - 1)
    _validate_buffer(flat.child_nodes, np.int64, adjacency_count, "child_nodes")
    _validate_buffer(flat.child_keys, np.int64, adjacency_count, "child_keys")
    for name in ("ops", "desc", "arg0", "arg1", "arg2", "arg3", "arg4"):
        _validate_buffer(getattr(program, name), np.int64, step_count, name)
    _validate_buffer(program.number_arg, np.float64, step_count, "number_arg")
    _validate_buffer(program.pool, np.int64, 1, "pool")
    count = lib().mjp_eval(
        _address(flat.first),
        _address(flat.next_sibling),
        _address(flat.subtree_end),
        _address(flat.kind),
        _address(flat.key_id),
        _address(flat.item_index),
        _address(flat.value_type),
        _address(flat.value_string),
        _address(flat.value_number),
        _address(program.ops),
        _address(program.desc),
        _address(program.arg0),
        _address(program.arg1),
        _address(program.arg2),
        _address(program.arg3),
        _address(program.arg4),
        _address(program.number_arg),
        _address(program.pool),
        _address(flat.child_start),
        _address(flat.child_count),
        _address(flat.child_nodes),
        _address(flat.child_keys),
        step_count,
        node_count,
        _address(work_a),
        _address(work_b),
        capacity,
    )
    if count == -2:
        _stats["python_fallbacks"] += 1
        return None
    if count < 0:
        raise RuntimeError(f"Mojo evaluator failed with error code {count}")
    if count > capacity:
        raise RuntimeError(
            f"Mojo evaluator returned {count} results for capacity {capacity}"
        )
    _stats["mojo_calls"] += 1
    result_buffer = work_a if len(program.ops) % 2 == 0 else work_b
    return _contexts(flat, result_buffer[:count])


def _contexts(flat: FlatTree, node_ids):
    from .jsonpath import DatumInContext

    cache = {}
    return [
        DatumInContext._from_flat(flat, node, cache)
        for node in node_ids.tolist()
    ]


def _contains_filter(path):
    from .ext.filter import Filter
    from .jsonpath import Child, Descendants, Union, Where

    if isinstance(path, Filter):
        return True
    if isinstance(path, (Child, Descendants, Union, Where)):
        return _contains_filter(path.left) or _contains_filter(path.right)
    return False


def _cold_filter_find(path, data):
    from .ext.filter import Filter, OPERATOR_MAP
    from .jsonpath import Child, DatumInContext, Fields, Index, Root, This

    atoms = []

    def linearize(node):
        if isinstance(node, (Root, This)):
            return True
        if isinstance(node, Child):
            return linearize(node.left) and linearize(node.right)
        atoms.append(node)
        return True

    if not linearize(path):
        return None
    filter_positions = [
        position for position, atom in enumerate(atoms)
        if isinstance(atom, Filter)
    ]
    if len(filter_positions) != 1:
        return None
    filter_position = filter_positions[0]
    filter_node = atoms[filter_position]
    if not filter_node.expressions:
        return None

    target_paths = []
    for expression in filter_node.expressions:
        if expression.op == "=~" or expression.op not in OPERATOR_MAP:
            return None
        target_atoms = []

        def target(node):
            if isinstance(node, (Root, This)):
                return True
            if isinstance(node, Child):
                return target(node.left) and target(node.right)
            if isinstance(node, Fields) and len(node.fields) == 1:
                target_atoms.append(node.fields[0])
                return True
            return False

        if not target(expression.target):
            return None
        target_paths.append((target_atoms, expression.op, expression.value))

    root_context = DatumInContext(data, path=Root(), context=None)
    container = data
    container_context = root_context
    for atom in atoms[:filter_position]:
        if not isinstance(atom, Fields) or len(atom.fields) != 1:
            return None
        field = atom.fields[0]
        if not isinstance(container, dict) or field not in container:
            return []
        container = container[field]
        container_context = DatumInContext(
            container, path=Fields(field), context=container_context
        )
    if not isinstance(container, list):
        return None

    def matches(candidate):
        for fields, op, wanted in target_paths:
            actual = candidate
            found = True
            for field in fields:
                if not isinstance(actual, dict) or field not in actual:
                    found = False
                    break
                actual = actual[field]
            if not found:
                return False
            if op is None:
                continue
            if type(wanted) is int:
                try:
                    actual = int(actual)
                except (ValueError, TypeError, OverflowError):
                    return False
            try:
                if not OPERATOR_MAP[op](actual, wanted):
                    return False
            except (TypeError, ValueError):
                return False
        return True

    suffix = atoms[filter_position + 1:]
    if any(
        not isinstance(atom, Fields) or len(atom.fields) != 1
        for atom in suffix
    ):
        return None
    results = []
    for index, candidate in enumerate(container):
        if not matches(candidate):
            continue
        value = candidate
        context = DatumInContext(
            candidate, path=Index(index), context=container_context
        )
        for atom in suffix:
            field = atom.fields[0]
            if not isinstance(value, dict) or field not in value:
                break
            value = value[field]
            context = DatumInContext(
                value, path=Fields(field), context=context
            )
        else:
            results.append(context)
    return results
