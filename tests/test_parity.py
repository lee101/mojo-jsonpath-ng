from __future__ import annotations

import copy

import pytest

import jsonpath_ng
from jsonpath_ng.ext import parse as upstream_ext_parse

import mojo_jsonpath_ng as mojo
from mojo_jsonpath_ng.ext import parse as mojo_ext_parse
from mojo_jsonpath_ng.jsonpath import (
    Child,
    DatumInContext,
    Descendants,
    Fields,
    Index,
    Root,
    Slice,
)


@pytest.fixture
def document():
    return {
        "store": {
            "book": [
                {
                    "category": "reference",
                    "author": "Nigel Rees",
                    "title": "Sayings",
                    "price": 8.95,
                    "active": True,
                    "tag": None,
                },
                {
                    "category": "fiction",
                    "author": "Evelyn Waugh",
                    "title": "Sword",
                    "price": 12.99,
                    "active": False,
                    "tag": "classic",
                },
                {
                    "category": "fiction",
                    "author": "Herman Melville",
                    "title": "Moby Dick",
                    "price": 8.99,
                    "active": True,
                    "tag": "classic",
                },
            ],
            "bicycle": {"color": "red", "price": 19.95},
        },
        "expensive": 10,
        "odd key": {"x.y": 7},
        "numbers": [0, 1, 2, 3, 4, 5],
    }


def snapshot(matches):
    return (
        [match.value for match in matches],
        [str(match.full_path) for match in matches],
    )


@pytest.mark.parametrize(
    "expression",
    [
        "$",
        "store",
        "$.store.book[*].author",
        "$.store.book[0].title",
        "$.store.book[0,2].title",
        "$.store.book[0:3:2].author",
        "$.numbers[::-1]",
        "$.numbers[-4:-1]",
        "$.store.*",
        "$['odd key']['x.y']",
        "$..price",
        "$..author",
        "store.book[*]",
        "store.bicycle.color",
        "store['bicycle','book']",
        "store.book[-1].title",
        "store.book[20].title",
    ],
)
def test_core_find_matches_upstream(document, expression):
    expected = jsonpath_ng.parse(expression).find(document)
    actual = mojo.parse(expression).find(document)
    assert snapshot(actual) == snapshot(expected)


@pytest.mark.parametrize(
    "expression",
    [
        "$.store.book[?(@.price < 10)].title",
        "$.store.book[?price >= 9].author",
        "$.store.book[?(@.active == true)].title",
        "$.store.book[?(@.tag == null)].title",
        "$.store.book[?(@.category == 'fiction')].title",
        "$.store.book[?(@.category != 'fiction')].title",
        "$.store.book[?(@.price < 10) & (@.active == true)].author",
        "$.store.book[?(@.title =~ 'Moby')].author",
    ],
)
def test_extended_filters_match_upstream(document, expression):
    expected = upstream_ext_parse(expression).find(document)
    actual = mojo_ext_parse(expression).find(document)
    assert snapshot(actual) == snapshot(expected)


def test_descendant_order_matches_upstream(document):
    assert [m.value for m in mojo.parse("$..price").find(document)] == [
        8.95, 12.99, 8.99, 19.95
    ]


def test_mojo_backend_is_exercised(document):
    before = mojo.engine_stats()["mojo_calls"]
    values = [m.value for m in mojo.parse("$.store.book[*].price").find(document)]
    assert values == [8.95, 12.99, 8.99]
    assert mojo.engine_stats()["mojo_calls"] == before + 1


def test_union_and_where_match_upstream(document):
    for expression in (
        "store.book | expensive",
        "store.* where price",
        "store.* wherenot price",
    ):
        assert snapshot(mojo.parse(expression).find(document)) == snapshot(
            jsonpath_ng.parse(expression).find(document)
        )


def test_extended_existence_filter_matches_upstream(document):
    expression = "$.store.book[?(@.tag)].title"
    assert snapshot(mojo_ext_parse(expression).find(document)) == snapshot(
        upstream_ext_parse(expression).find(document)
    )


def test_schema_coercing_wildcard_falls_back_correctly():
    data = {"value": 3, "mapping": {"a": 1}}
    for expression in ("value[*]", "mapping[*]"):
        assert snapshot(mojo.parse(expression).find(data)) == snapshot(
            jsonpath_ng.parse(expression).find(data)
        )


def test_dict_filter_falls_back_correctly():
    data = {"items": {"first": {"x": 1}, "second": {"x": 2}}}
    expression = "$.items[?(@.x >= 2)]"
    assert [m.value for m in mojo_ext_parse(expression).find(data)] == [
        m.value for m in upstream_ext_parse(expression).find(data)
    ]


def test_update_matches_upstream(document):
    upstream_data = copy.deepcopy(document)
    mojo_data = copy.deepcopy(document)
    expression = "$.store.book[*].price"
    expected = jsonpath_ng.parse(expression).update(upstream_data, 0)
    actual = mojo.parse(expression).update(mojo_data, 0)
    assert actual == expected


def test_callable_update_matches_upstream(document):
    upstream_data = copy.deepcopy(document)
    mojo_data = copy.deepcopy(document)

    def discount(value, container, key):
        return round(value * 0.9, 3)

    expression = "$.store.book[*].price"
    jsonpath_ng.parse(expression).update(upstream_data, discount)
    mojo.parse(expression).update(mojo_data, discount)
    assert mojo_data == upstream_data


def test_find_or_create_and_update_or_create_match_upstream():
    for method in ("find_or_create", "update_or_create"):
        upstream_data = {}
        mojo_data = {}
        upstream_path = jsonpath_ng.parse("$.new.value")
        mojo_path = mojo.parse("$.new.value")
        if method == "find_or_create":
            expected = getattr(upstream_path, method)(upstream_data)
            actual = getattr(mojo_path, method)(mojo_data)
            assert snapshot(actual) == snapshot(expected)
        else:
            getattr(upstream_path, method)(upstream_data, 7)
            getattr(mojo_path, method)(mojo_data, 7)
        assert mojo_data == upstream_data


def test_filter_removal_matches_upstream(document):
    upstream_data = copy.deepcopy(document)
    mojo_data = copy.deepcopy(document)
    expression = "$.store.book[*].tag"
    jsonpath_ng.parse(expression).filter(lambda value: value is None, upstream_data)
    mojo.parse(expression).filter(lambda value: value is None, mojo_data)
    assert mojo_data == upstream_data


def test_programmatic_ast_matches_upstream(document):
    ours = Fields("store").child(Fields("book")).child(Slice()).child(Fields("title"))
    theirs = (
        jsonpath_ng.jsonpath.Fields("store")
        .child(jsonpath_ng.jsonpath.Fields("book"))
        .child(jsonpath_ng.jsonpath.Slice())
        .child(jsonpath_ng.jsonpath.Fields("title"))
    )
    assert snapshot(ours.find(document)) == snapshot(theirs.find(document))


def test_datum_context_and_root_behavior(document):
    context = DatumInContext(document["store"], path=Fields("store"),
                             context=DatumInContext(document, path=Root()))
    assert Root().find(context)[0].value is document
    match = Child(Fields("book"), Index(1)).find(context)[0]
    assert match.value["author"] == "Evelyn Waugh"
    assert str(match.full_path) == "((store.book).[1])"


def test_ast_equality_and_hashes():
    assert Fields("a", "b") == Fields("a", "b")
    assert Index(2, 1) == Index(1, 2)
    assert Slice(1, 5, 2) == Slice(1, 5, 2)
    assert Descendants(Root(), Fields("x")) == Descendants(Root(), Fields("x"))
    assert len({Fields("x"), Fields("x")}) == 1


@pytest.mark.parametrize("expression", ["", "$.", "$[", "$.items[::0]"])
def test_invalid_expressions_raise(expression):
    with pytest.raises(Exception):
        mojo.parse(expression)
