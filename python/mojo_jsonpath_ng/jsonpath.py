from __future__ import annotations

import re
from typing import Any, Callable

auto_id_field = None
_LAZY = object()


class JSONPath:
    def find(self, data) -> list[DatumInContext]:
        from ._engine import PreparedDocument, accelerated_find

        accelerated = accelerated_find(self, data)
        if accelerated is not None:
            return accelerated
        return self._find(data.data if isinstance(data, PreparedDocument) else data)

    def _find(self, data) -> list[DatumInContext]:
        raise NotImplementedError

    def find_or_create(self, data):
        return self.find(data)

    def update(self, data, val):
        for match in self.find(data):
            replacement = val(match.value, match.context.value, _path_key(match.path)) \
                if callable(val) else val
            match.value = replacement
        return data

    def update_or_create(self, data, val):
        return self.update(data, val)

    def filter(self, fn: Callable[[Any], bool], data):
        matches = self.find(data)
        for match in reversed(matches):
            if fn(match.value) and match.context is not None:
                match.path.filter(lambda _: True, match.context.value)
        return data

    def child(self, child):
        if isinstance(self, (This, Root)):
            return child
        if isinstance(child, This):
            return self
        if isinstance(child, Root):
            return child
        return Child(self, child)

    @staticmethod
    def make_datum(value):
        return value if isinstance(value, DatumInContext) else \
            DatumInContext(value, path=Root(), context=None)


class DatumInContext:
    __slots__ = (
        "__value__", "_path", "_context", "_flat", "_node", "_context_cache"
    )

    @classmethod
    def wrap(cls, data):
        return data if isinstance(data, cls) else cls(data)

    def __init__(self, value, path: JSONPath | None = None,
                 context: DatumInContext | None = None):
        self.__value__ = value
        self._path = path or This()
        self._context = None if context is None else DatumInContext.wrap(context)
        self._flat = None
        self._node = -1
        self._context_cache = None

    @classmethod
    def _from_flat(cls, flat, node, context_cache):
        datum = cls.__new__(cls)
        datum.__value__ = flat.objects[node]
        datum._path = _LAZY
        datum._context = _LAZY
        datum._flat = flat
        datum._node = node
        datum._context_cache = context_cache
        return datum

    @property
    def path(self):
        if self._path is _LAZY:
            if self._node == 0:
                self._path = Root()
            else:
                key = int(self._flat.key_id[self._node])
                self._path = (
                    Fields(self._flat.keys[key])
                    if key >= 0
                    else Index(int(self._flat.item_index[self._node]))
                )
        return self._path

    @path.setter
    def path(self, path):
        self._path = path

    @property
    def context(self):
        if self._context is _LAZY:
            parent = int(self._flat.parent[self._node])
            if parent < 0:
                self._context = None
            else:
                context = self._context_cache.get(parent)
                if context is None:
                    context = DatumInContext._from_flat(
                        self._flat, parent, self._context_cache
                    )
                    self._context_cache[parent] = context
                self._context = context
        return self._context

    @context.setter
    def context(self, context):
        self._context = context

    @property
    def value(self):
        return self.__value__

    @value.setter
    def value(self, value):
        if self.context is not None and self.context.value is not None:
            self.path.update(self.context.value, value)
        self.__value__ = value

    def in_context(self, context, path):
        context = DatumInContext.wrap(context)
        if self.context:
            return DatumInContext(
                self.value, path=self.path,
                context=self.context.in_context(path=path, context=context),
            )
        return DatumInContext(self.value, path=path, context=context)

    @property
    def full_path(self):
        return self.path if self.context is None else self.context.full_path.child(self.path)

    def __repr__(self):
        return (
            f"{type(self).__name__}(value={self.value!r}, path={self.path!r}, "
            f"context={self.context!r})"
        )

    def __eq__(self, other):
        return (
            isinstance(other, DatumInContext)
            and other.value == self.value
            and other.path == self.path
            and other.context == self.context
        )

    def __getattr__(self, name):
        return getattr(self.__value__, name)


class Root(JSONPath):
    def _find(self, data):
        if not isinstance(data, DatumInContext):
            return [DatumInContext(data, path=Root(), context=None)]
        if data.context is None:
            return [DatumInContext(data.value, path=Root(), context=None)]
        return self._find(data.context)

    def update(self, data, val):
        return val(data, None, None) if callable(val) else val

    def filter(self, fn, data):
        return data if fn(data) else None

    def __str__(self):
        return "$"

    def __repr__(self):
        return "Root()"

    def __eq__(self, other):
        return isinstance(other, Root)

    def __hash__(self):
        return hash("$")


class This(JSONPath):
    def _find(self, datum):
        return [DatumInContext.wrap(datum)]

    def update(self, data, val):
        return val(data, None, None) if callable(val) else val

    def filter(self, fn, data):
        return data if fn(data) else None

    def __str__(self):
        return "`this`"

    def __repr__(self):
        return "This()"

    def __eq__(self, other):
        return isinstance(other, This)

    def __hash__(self):
        return hash("this")


class Fields(JSONPath):
    def __init__(self, *fields):
        self.fields = tuple(fields)

    def reified_fields(self, datum):
        if "*" not in self.fields:
            return self.fields
        try:
            return tuple(datum.value.keys())
        except AttributeError:
            return ()

    def _find(self, datum):
        datum = DatumInContext.wrap(datum)
        found = []
        for field in self.reified_fields(datum):
            try:
                if field in datum.value:
                    found.append(DatumInContext(
                        datum.value[field], path=Fields(field), context=datum
                    ))
            except (TypeError, AttributeError):
                pass
        return found

    def find_or_create(self, datum):
        datum = DatumInContext.wrap(datum)
        found = []
        for field in self.fields:
            if field == "*":
                found.extend(self._find(datum))
            else:
                if not isinstance(datum.value, dict):
                    continue
                datum.value.setdefault(field, {})
                found.append(DatumInContext(
                    datum.value[field], path=Fields(field), context=datum
                ))
        return found

    def update(self, data, val):
        if data is None:
            return data
        for field in self.reified_fields(DatumInContext.wrap(data)):
            try:
                if field in data and type(data) is not bool:
                    data[field] = val(data[field], data, field) if callable(val) else val
            except (TypeError, AttributeError):
                pass
        return data

    def update_or_create(self, data, val):
        if isinstance(data, dict):
            for field in self.fields:
                if field != "*":
                    data.setdefault(field, {})
            return self.update(data, val)
        return data

    def filter(self, fn, data):
        if isinstance(data, dict):
            for field in list(self.reified_fields(DatumInContext.wrap(data))):
                if field in data and fn(data[field]):
                    data.pop(field)
        return data

    def __str__(self):
        rendered = []
        for field in self.fields:
            rendered.append(
                field if re.match(r"^[A-Za-z_@][A-Za-z0-9_@-]*$", field)
                else repr(field)
            )
        return ",".join(rendered)

    def __repr__(self):
        return f"Fields({','.join(map(repr, self.fields))})"

    def __eq__(self, other):
        return isinstance(other, Fields) and self.fields == other.fields

    def __hash__(self):
        return hash(self.fields)


class Index(JSONPath):
    def __init__(self, *indices):
        self.indices = tuple(indices)
        self.index = self.indices[0] if len(self.indices) == 1 else None

    def _find(self, datum):
        datum = DatumInContext.wrap(datum)
        found = []
        try:
            length = len(datum.value)
        except (TypeError, AttributeError):
            return found
        for index in self.indices:
            normalized = index if index >= 0 else length + index
            if 0 <= normalized < length:
                try:
                    found.append(DatumInContext(
                        datum.value[index], path=Index(index), context=datum
                    ))
                except (IndexError, KeyError, TypeError):
                    pass
        return found

    def find_or_create(self, datum):
        datum = DatumInContext.wrap(datum)
        if not isinstance(datum.value, list):
            return []
        nonnegative = [i for i in self.indices if i >= 0]
        if nonnegative:
            datum.value.extend({} for _ in range(max(nonnegative) + 1 - len(datum.value)))
        return self._find(datum)

    def update(self, data, val):
        if not isinstance(data, list):
            return data
        values = list(val) if isinstance(val, list) else None
        for position, index in enumerate(self.indices):
            if -len(data) <= index < len(data):
                replacement = values[position] if values is not None else val
                data[index] = replacement(data[index], data, index) \
                    if callable(replacement) else replacement
        return data

    def update_or_create(self, data, val):
        if isinstance(data, list):
            nonnegative = [i for i in self.indices if i >= 0]
            if nonnegative:
                data.extend({} for _ in range(max(nonnegative) + 1 - len(data)))
            return self.update(data, val)
        return data

    def filter(self, fn, data):
        if isinstance(data, list):
            normalized = sorted(
                {i if i >= 0 else len(data) + i for i in self.indices}, reverse=True
            )
            for index in normalized:
                if 0 <= index < len(data) and fn(data[index]):
                    data.pop(index)
        return data

    def __str__(self):
        return f"[{','.join(str(i) for i in self.indices)}]"

    def __repr__(self):
        return f"Index(indices={self.indices!r})"

    def __eq__(self, other):
        return isinstance(other, Index) and sorted(self.indices) == sorted(other.indices)

    def __hash__(self):
        return hash(tuple(sorted(self.indices)))


class Slice(JSONPath):
    def __init__(self, start=None, end=None, step=None):
        self.start, self.end, self.step = start, end, step

    def _find(self, datum):
        datum = DatumInContext.wrap(datum)
        if datum.value is None:
            return []
        if isinstance(datum.value, (dict, int, float, str, bool)):
            return self._find(DatumInContext(
                [datum.value], path=datum.path, context=datum.context
            ))
        try:
            indices = range(len(datum.value))[self.start:self.end:self.step]
            return [
                DatumInContext(datum.value[i], path=Index(i), context=datum)
                for i in indices
            ]
        except (TypeError, AttributeError):
            return []

    def update(self, data, val):
        for datum in self._find(data):
            datum.value = val(datum.value, data, datum.path.index) \
                if callable(val) else val
        return data

    def filter(self, fn, data):
        if not isinstance(data, list):
            return data
        indices = list(range(len(data))[self.start:self.end:self.step])
        for index in reversed(indices):
            if fn(data[index]):
                data.pop(index)
        return data

    def __str__(self):
        if self.start is self.end is self.step is None:
            return "[*]"
        start = "" if self.start is None else str(self.start)
        end = "" if self.end is None else str(self.end)
        step = "" if self.step is None else f":{self.step}"
        return f"[{start}:{end}{step}]"

    def __repr__(self):
        return f"Slice(start={self.start!r},end={self.end!r},step={self.step!r})"

    def __eq__(self, other):
        return (
            isinstance(other, Slice)
            and (self.start, self.end, self.step) == (other.start, other.end, other.step)
        )

    def __hash__(self):
        return hash((self.start, self.end, self.step))


class Child(JSONPath):
    def __init__(self, left, right):
        self.left, self.right = left, right

    def _find(self, datum):
        return [
            match
            for left_match in self.left._find(datum)
            for match in self.right._find(left_match)
        ]

    def find_or_create(self, datum):
        return [
            match
            for left_match in self.left.find_or_create(datum)
            for match in self.right.find_or_create(left_match)
        ]

    def update(self, data, val):
        for datum in self.left.find(data):
            self.right.update(datum.value, val)
        return data

    def update_or_create(self, data, val):
        for datum in self.left.find_or_create(data):
            self.right.update_or_create(datum.value, val)
        return data

    def filter(self, fn, data):
        for datum in self.left.find(data):
            self.right.filter(fn, datum.value)
        return data

    def __str__(self):
        return f"({self.left}.{self.right})"

    def __repr__(self):
        return f"Child({self.left!r}, {self.right!r})"

    def __eq__(self, other):
        return isinstance(other, Child) and self.left == other.left and self.right == other.right

    def __hash__(self):
        return hash((self.left, self.right))


class Descendants(JSONPath):
    def __init__(self, left, right):
        self.left, self.right = left, right

    def _find(self, datum):
        results = []
        for left_match in self.left._find(datum):
            stack = [left_match]
            while stack:
                current = stack.pop()
                results.extend(self.right._find(current))
                children = []
                if isinstance(current.value, list):
                    children = [
                        DatumInContext(v, path=Index(i), context=current)
                        for i, v in enumerate(current.value)
                    ]
                elif isinstance(current.value, dict):
                    children = [
                        DatumInContext(v, path=Fields(k), context=current)
                        for k, v in current.value.items()
                    ]
                stack.extend(reversed(children))
        return results

    def update(self, data, val):
        for match in self.find(data):
            match.value = val(match.value, match.context.value, _path_key(match.path)) \
                if callable(val) else val
        return data

    def filter(self, fn, data):
        for match in reversed(self.find(data)):
            if match.context is not None and fn(match.value):
                match.path.filter(lambda _: True, match.context.value)
        return data

    def __str__(self):
        return f"({self.left}..{self.right})"

    def __repr__(self):
        return f"Descendants({self.left!r}, {self.right!r})"

    def __eq__(self, other):
        return (
            isinstance(other, Descendants)
            and self.left == other.left and self.right == other.right
        )

    def __hash__(self):
        return hash((self.left, self.right))


class Where(JSONPath):
    def __init__(self, left, right):
        self.left, self.right = left, right

    def _find(self, data):
        return [match for match in self.left._find(data) if self.right._find(match)]

    def __str__(self):
        return f"{self.left} where {self.right}"

    def __repr__(self):
        return f"Where({self.left!r}, {self.right!r})"

    def __eq__(self, other):
        return isinstance(other, Where) and (self.left, self.right) == (other.left, other.right)

    def __hash__(self):
        return hash((self.left, self.right))


class WhereNot(Where):
    def _find(self, data):
        return [match for match in self.left._find(data) if not self.right._find(match)]

    def __str__(self):
        return f"{self.left} wherenot {self.right}"

    def __repr__(self):
        return f"WhereNot({self.left!r}, {self.right!r})"


class Union(JSONPath):
    def __init__(self, left, right):
        self.left, self.right = left, right

    def _find(self, data):
        return self.left._find(data) + self.right._find(data)

    def update(self, data, val):
        self.left.update(data, val)
        self.right.update(data, val)
        return data

    def __str__(self):
        return f"{self.left} | {self.right}"

    def __repr__(self):
        return f"Union({self.left} | {self.right})"

    def __eq__(self, other):
        return isinstance(other, Union) and (self.left, self.right) == (other.left, other.right)

    def __hash__(self):
        return hash((self.left, self.right))


def _path_key(path):
    if isinstance(path, Fields):
        return path.fields[0]
    if isinstance(path, Index):
        return path.indices[0]
    return None
