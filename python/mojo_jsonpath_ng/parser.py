from __future__ import annotations

import ast
import json
import re

from .jsonpath import (
    Child,
    Descendants,
    Fields,
    Index,
    Root,
    Slice,
    This,
    Union,
    Where,
    WhereNot,
)


class JsonPathParserError(Exception):
    pass


class JsonPathLexerError(JsonPathParserError):
    pass


class JsonPathParser:
    def parse(self, string, lexer=None):
        return parse(string)


def parse(string: str, *, extended: bool = False):
    if not isinstance(string, str) or not string.strip():
        raise JsonPathParserError("JSONPath expression must be a non-empty string")
    text = _strip_outer(string.strip())

    split = _find_top_level_dot(text)
    if split is not None:
        left, right, descendant = split
        if not left.strip() or not right.strip():
            raise JsonPathParserError("incomplete child expression")
        operator = Descendants if descendant else Child
        return operator(
            parse(left, extended=extended),
            parse(right, extended=extended),
        )

    split = _find_top_level_word(text, " wherenot ")
    if split is not None:
        left, right = split
        return WhereNot(parse(left, extended=extended), parse(right, extended=extended))
    split = _find_top_level_word(text, " where ")
    if split is not None:
        left, right = split
        return Where(parse(left, extended=extended), parse(right, extended=extended))
    split = _find_top_level_char(text, "|")
    if split is not None:
        left, right = split
        return Union(parse(left, extended=extended), parse(right, extended=extended))
    return _parse_sequence(text, extended)


def _parse_sequence(text, extended):
    i = 0
    expression = None
    descendant = False

    while i < len(text):
        while i < len(text) and text[i].isspace():
            i += 1
        if i >= len(text):
            break
        if text.startswith("..", i):
            descendant = True
            i += 2
            continue
        if text[i] == ".":
            i += 1
            continue

        if text[i] == "[":
            end = _matching(text, i, "[", "]")
            node = _parse_bracket(text[i + 1:end].strip(), extended)
            i = end + 1
        elif text[i] == "$":
            node, i = Root(), i + 1
        elif text[i] == "@":
            node, i = This(), i + 1
        elif text[i] in "'\"":
            value, i = _quoted_at(text, i)
            node = Fields(value)
        elif text[i] == "`":
            end = text.find("`", i + 1)
            if end < 0:
                raise JsonPathParserError("unterminated named operator")
            name = text[i + 1:end]
            if name != "this":
                raise JsonPathParserError(f"unsupported named operator `{name}`")
            node, i = This(), end + 1
        elif text[i] == "(":
            end = _matching(text, i, "(", ")")
            node = parse(text[i + 1:end], extended=extended)
            i = end + 1
        else:
            start = i
            while i < len(text) and text[i] not in ".[]()| \t\r\n":
                i += 1
            token = text[start:i]
            if not token:
                raise JsonPathParserError(f"unexpected character at position {i}")
            node = Fields(token)

        if expression is None:
            expression = node
        elif descendant:
            expression = Descendants(expression, node)
            descendant = False
        else:
            expression = Child(expression, node)

    if expression is None or descendant:
        raise JsonPathParserError("incomplete JSONPath expression")
    return expression


def _parse_bracket(content, extended):
    from .ext.filter import Filter

    if not content:
        raise JsonPathParserError("empty brackets are not a selector")
    if content == "*":
        return Slice()
    if content.startswith("?"):
        if not extended:
            raise JsonPathParserError("filters require mojo_jsonpath_ng.ext.parse")
        predicate = content[1:].strip()
        predicate = _strip_outer(predicate)
        parts = _split_top_level(predicate, "&")
        return Filter([_parse_filter_expression(part.strip(), extended) for part in parts])
    if ":" in content:
        parts = content.split(":")
        if len(parts) not in (2, 3):
            raise JsonPathParserError(f"invalid slice [{content}]")
        values = [None if not part.strip() else _integer(part) for part in parts]
        if len(values) == 2:
            values.append(None)
        if values[2] == 0:
            raise JsonPathParserError("slice step cannot be zero")
        return Slice(*values)

    parts = _split_top_level(content, ",")
    if all(re.fullmatch(r"[+-]?\d+", part.strip()) for part in parts):
        return Index(*(int(part.strip()) for part in parts))
    fields = []
    for part in parts:
        part = part.strip()
        if part[:1] in ("'", '"'):
            try:
                fields.append(ast.literal_eval(part))
            except (ValueError, SyntaxError) as exc:
                raise JsonPathParserError(f"invalid quoted field {part}") from exc
        else:
            fields.append(part)
    if not all(isinstance(field, str) for field in fields):
        raise JsonPathParserError("field selectors must be strings")
    return Fields(*fields)


def _parse_filter_expression(text, extended):
    from .ext.filter import Expression

    text = _strip_outer(text.strip())
    for op in ("=~", "==", "!=", "<=", ">=", "=", "<", ">"):
        split = _find_top_level_operator(text, op)
        if split is not None:
            left, right = split
            return Expression(parse(left.strip(), extended=extended), op, _literal(right))
    return Expression(parse(text.strip(), extended=extended), None, None)


def _literal(text):
    text = text.strip()
    aliases = {"true": True, "false": False}
    if text.lower() in aliases:
        return aliases[text.lower()]
    if text.lower() == "null":
        return "null"
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        try:
            return ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return text


def _integer(value):
    try:
        return int(value.strip())
    except ValueError as exc:
        raise JsonPathParserError(f"invalid slice integer {value!r}") from exc


def _quoted_at(text, start):
    quote = text[start]
    i = start + 1
    escaped = False
    while i < len(text):
        if text[i] == quote and not escaped:
            token = text[start:i + 1]
            try:
                return ast.literal_eval(token), i + 1
            except (ValueError, SyntaxError) as exc:
                raise JsonPathParserError(f"invalid quoted field {token}") from exc
        escaped = text[i] == "\\" and not escaped
        if text[i] != "\\":
            escaped = False
        i += 1
    raise JsonPathParserError("unterminated quoted field")


def _matching(text, start, opening, closing):
    depth = 0
    quote = None
    escaped = False
    for i in range(start, len(text)):
        char = text[i]
        if quote:
            if char == quote and not escaped:
                quote = None
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            continue
        if char in "'\"":
            quote = char
        elif char == opening:
            depth += 1
        elif char == closing:
            depth -= 1
            if depth == 0:
                return i
    raise JsonPathParserError(f"unterminated {opening}")


def _strip_outer(text):
    while text.startswith("("):
        try:
            end = _matching(text, 0, "(", ")")
        except JsonPathParserError:
            return text
        if end != len(text) - 1:
            return text
        text = text[1:-1].strip()
    return text


def _scan_top_level(text):
    square = round_depth = 0
    quote = None
    escaped = False
    for i, char in enumerate(text):
        if quote:
            if char == quote and not escaped:
                quote = None
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            continue
        if char in "'\"":
            quote = char
        elif char == "[":
            square += 1
        elif char == "]":
            square -= 1
        elif char == "(":
            round_depth += 1
        elif char == ")":
            round_depth -= 1
        yield i, char, square, round_depth, quote


def _find_top_level_char(text, wanted):
    for i, char, square, round_depth, quote in _scan_top_level(text):
        if char == wanted and square == 0 and round_depth == 0 and quote is None:
            return text[:i], text[i + 1:]
    return None


def _find_top_level_dot(text):
    for i, char, square, round_depth, quote in _scan_top_level(text):
        if char != "." or square != 0 or round_depth != 0 or quote is not None:
            continue
        descendant = i + 1 < len(text) and text[i + 1] == "."
        return text[:i], text[i + (2 if descendant else 1):], descendant
    return None


def _find_top_level_word(text, wanted):
    lower = text.lower()
    for i, _, square, round_depth, quote in _scan_top_level(text):
        if (
            square == 0 and round_depth == 0 and quote is None
            and lower.startswith(wanted, i)
        ):
            return text[:i], text[i + len(wanted):]
    return None


def _find_top_level_operator(text, operator):
    for i, _, square, round_depth, quote in _scan_top_level(text):
        if (
            square == 0 and round_depth == 0 and quote is None
            and text.startswith(operator, i)
        ):
            return text[:i], text[i + len(operator):]
    return None


def _split_top_level(text, separator):
    parts = []
    start = 0
    for i, char, square, round_depth, quote in _scan_top_level(text):
        if char == separator and square == 0 and round_depth == 0 and quote is None:
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return parts
