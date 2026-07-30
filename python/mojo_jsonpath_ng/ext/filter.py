from __future__ import annotations

import operator
import re

from ..jsonpath import DatumInContext, Index, JSONPath

OPERATOR_MAP = {
    "!=": operator.ne,
    "==": operator.eq,
    "=": operator.eq,
    "<=": operator.le,
    "<": operator.lt,
    ">=": operator.ge,
    ">": operator.gt,
    "=~": lambda a, b: isinstance(a, str) and re.search(b, a) is not None,
}


class Filter(JSONPath):
    def __init__(self, expressions):
        self.expressions = expressions

    def _find(self, datum):
        if not self.expressions:
            return datum
        datum = DatumInContext.wrap(datum)
        if isinstance(datum.value, dict):
            datum = DatumInContext(
                list(datum.value.values()), path=datum.path, context=datum.context
            )
        if not isinstance(datum.value, list):
            return []
        return [
            DatumInContext(value, path=Index(i), context=datum)
            for i, value in enumerate(datum.value)
            if all(expression._find(value) for expression in self.expressions)
        ]

    def update(self, data, val):
        if isinstance(data, list):
            for i, item in enumerate(data):
                if all(expression._find(item) for expression in self.expressions):
                    data[i] = val(data[i], data, i) if callable(val) else val
        return data

    def filter(self, fn, data):
        if isinstance(data, list):
            for match in reversed(self._find(data)):
                if fn(match.value):
                    data.pop(match.path.index)
        return data

    def __repr__(self):
        return f"Filter({self.expressions!r})"

    def __str__(self):
        return f"[?{self.expressions}]"

    def __eq__(self, other):
        return isinstance(other, Filter) and self.expressions == other.expressions


class Expression(JSONPath):
    def __init__(self, target, op, value):
        self.target, self.op, self.value = target, op, value

    def _find(self, datum):
        found = self.target._find(DatumInContext.wrap(datum))
        if not found or self.op is None:
            return found
        matches = []
        for item in found:
            value = item.value
            if type(self.value) is int:
                try:
                    value = int(value)
                except (ValueError, TypeError, OverflowError):
                    continue
            try:
                if OPERATOR_MAP[self.op](value, self.value):
                    matches.append(item)
            except (TypeError, ValueError):
                pass
        return matches

    def __repr__(self):
        return (
            f"Expression({self.target!r})" if self.op is None
            else f"Expression({self.target!r} {self.op} {self.value!r})"
        )

    def __str__(self):
        return str(self.target) if self.op is None else \
            f"{self.target} {self.op} {self.value}"

    def __eq__(self, other):
        return (
            isinstance(other, Expression)
            and (self.target, self.op, self.value) == (other.target, other.op, other.value)
        )
