from __future__ import annotations

import numpy as np

from mojo_jsonpath_ng import engine_stats, parse, prepare
from mojo_jsonpath_ng._engine import flatten
from mojo_jsonpath_ng.ext import parse as ext_parse


def test_flattened_tree_topology():
    tree = flatten({"a": [{"x": 1}, {"x": 2}], "b": 3})
    assert tree is not None
    assert len(tree.objects) == 7
    assert tree.parent.tolist() == [-1, 0, 1, 2, 1, 4, 0]
    assert tree.subtree_end.tolist() == [7, 6, 4, 4, 6, 6, 7]


def test_large_wildcard_returns_all_values_in_order():
    data = prepare({"rows": [{"value": i} for i in range(10_003)]})
    before = engine_stats()["mojo_calls"]
    result = parse("$.rows[*].value").find(data)
    assert np.array([m.value for m in result]).tolist() == list(range(10_003))
    assert engine_stats()["mojo_calls"] == before + 1


def test_simd_tails_for_slice_and_field_lookup():
    data = prepare({
        "values": list(range(19)),
        "mapping": {f"k{i}": i for i in range(7)},
    })
    assert [m.value for m in parse("$.values[1:18]").find(data)] == list(range(1, 18))
    assert [m.value for m in parse("$.mapping.k6").find(data)] == [6]


def test_cold_filter_does_not_flatten(monkeypatch):
    import mojo_jsonpath_ng._engine as engine

    monkeypatch.setattr(
        engine, "flatten",
        lambda data: (_ for _ in ()).throw(AssertionError("flatten called")),
    )
    data = {"rows": [{"score": 98}, {"score": 99}, {"score": 100}]}
    assert [
        match.value for match in ext_parse("$.rows[?(@.score >= 99)].score").find(data)
    ] == [99, 100]


def test_lazy_prepared_context_supports_update():
    data = {"rows": [{"value": 1}, {"value": 2}]}
    prepared = prepare(data)
    parse("$.rows[*].value").update(prepared, 7)
    assert data == {"rows": [{"value": 7}, {"value": 7}]}


def test_recursive_descent_on_deep_tree():
    data = {"target": 0}
    cursor = data
    for i in range(1, 100):
        cursor["child"] = {"target": i}
        cursor = cursor["child"]
    assert [m.value for m in parse("$..target").find(data)] == list(range(100))


def test_filter_numeric_boundary():
    data = {"rows": [{"x": x} for x in (-2, -1, 0, 1, 2)]}
    assert [m.value["x"] for m in parse("$.rows[*]").find(data)] == [-2, -1, 0, 1, 2]


def test_prepared_filter_preserves_large_integer_precision():
    boundary = 2**53
    data = prepare({"rows": [{"x": boundary}, {"x": boundary + 1}]})
    before = engine_stats()
    result = ext_parse(f"$.rows[?(@.x == {boundary + 1})].x").find(data)
    assert [match.value for match in result] == [boundary + 1]
    assert engine_stats()["mojo_calls"] == before["mojo_calls"]
    assert engine_stats()["python_fallbacks"] == before["python_fallbacks"] + 1


def test_invalid_native_buffer_is_rejected_before_ffi():
    data = prepare({"rows": [{"x": 1}]})
    object.__setattr__(data.flat, "first", data.flat.first.astype(np.int32))
    with np.testing.assert_raises_regex(TypeError, "first must be"):
        parse("$.rows[*].x").find(data)


def test_native_capacity_failure_is_not_silently_swallowed(monkeypatch):
    import mojo_jsonpath_ng._engine as engine

    class FailedLibrary:
        @staticmethod
        def mjp_eval(*args):
            return -1

    monkeypatch.setattr(engine, "lib", lambda: FailedLibrary())
    with np.testing.assert_raises_regex(RuntimeError, "error code -1"):
        parse("$.rows[*].x").find(prepare({"rows": [{"x": 1}]}))
