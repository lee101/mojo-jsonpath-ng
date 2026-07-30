# mojo-jsonpath-ng

`mojo-jsonpath-ng` is a standalone Mojo-backed implementation of the
evaluation-heavy part of [`jsonpath-ng`](https://pypi.org/project/jsonpath-ng/).
It exposes the familiar `parse(...).find(...)` API, `DatumInContext` results,
path AST classes, mutation methods, and the extended filter parser. It does
not import or call the upstream package at runtime; `jsonpath-ng` is installed
in the development environment only for parity tests and benchmarks.

The useful fast path is repeated selection over a stable, prepared document.
Recursive descent and selective filters avoid constructing Python context
objects for candidates that do not match. One-shot calls remain supported,
but flattening a Python object graph can cost more than the traversal saves.

## Coverage

Implemented and parity-tested:

- root and current nodes, child paths, and recursive descent;
- named fields, quoted fields, field unions, and field wildcards;
- positive and negative indices, index unions, wildcards, and
  `start:end:step` slices;
- `where`, `wherenot`, and path union;
- extended existence and comparison filters, conjunction with `&`, and
  regular-expression matching;
- `DatumInContext.value`, `.path`, `.context`, and `.full_path`;
- `find`, `find_or_create`, `update`, `update_or_create`, and `filter` for the
  covered AST classes;
- direct construction with `Root`, `Fields`, `Index`, `Slice`, `Child`, and
  `Descendants`.

Linear field/index/slice paths, recursive descent with an atomic selector, and
one-clause scalar filters run in Mojo. Constructs that cannot be represented
exactly by the bytecode use this repository's local Python evaluator. That
includes reverse slices, compound filters, regex filters, unions, `where`,
dictionary filter coercion, and the upstream slice coercion for scalar values.

Not covered are extended arithmetic expressions, sorting extensions, named
operators such as `` `parent` ``, automatic ID fields, and arbitrary custom
Python mapping/sequence classes. Prepared documents must not be mutated:
`prepare()` snapshots topology and scalar comparison data while retaining
references to the original values.

## Install and verify

The repository pins the tested Mojo nightly in `pixi.toml`.

```bash
pixi install
pixi run build
pixi run test
```

The build task produces `dist/libmojo-jsonpath-ng.so`. Set
`MOJO_JSONPATH_NG_LIB` to use an already-built library at another location.

## Usage

```python
from mojo_jsonpath_ng.ext import parse
from mojo_jsonpath_ng import prepare

document = {
    "books": [
        {"title": "Small", "price": 8.5},
        {"title": "Large", "price": 14.0},
    ]
}

query = parse("$.books[?(@.price < 10)].title")
print([match.value for match in query.find(document)])
# ['Small']

# Reuse the flattened representation when querying an immutable document.
prepared = prepare(document)
print([match.value for match in query.find(prepared)])
# ['Small']
```

Programmatic construction mirrors upstream:

```python
from mojo_jsonpath_ng.jsonpath import Fields, Slice

query = Fields("books").child(Slice()).child(Fields("title"))
```

## Benchmarks

Measured with `pixi run bench`, best of five runs, on an Intel Xeon E5-2697 v4
at 2.30 GHz with Python 3.13.14 and jsonpath-ng 1.8.0. Each document contains
100,000 records. “Prepared” excludes the one-time flattening operation for
mojo-jsonpath-ng; “cold” passes the raw document and includes any preprocessing.
Both implementations still create their normal result objects.

| query (100,000 records) | Mojo | jsonpath-ng | speedup |
|---|---:|---:|---:|
| projection, prepared | 61.28 ms | 583.36 ms | 9.52x |
| slice, prepared | 24.24 ms | 191.59 ms | 7.90x |
| recursive descent, prepared | 12.65 ms | 1496.74 ms | 118.34x |
| numeric filter, prepared | 5.77 ms | 347.13 ms | 60.21x |
| numeric filter, cold | 50.12 ms | 490.92 ms | 9.80x |

Prepared traversal uses a contiguous direct-child index. Wildcards and
unit-step slices copy that index with host-width SIMD and scalar tails, while
strided slices address children directly. Result contexts are materialized
only when `.path`, `.context`, or `.full_path` is accessed. Supported cold
filters use a path-guided evaluator instead of flattening unrelated branches.

No GPU or parallel CPU path is provided.

## How it works

Python parses the expression into upstream-shaped AST objects and flattens a
JSON document in preorder. The flattened structure uses contiguous NumPy
arrays for first-child and next-sibling links, a direct-child adjacency index,
subtree boundaries, container kinds, field IDs, list indices, scalar types,
string IDs, and numeric values. Python objects remain in an indexed side table.

The AST compiler emits a compact selector stream. A single Mojo compilation
unit walks candidate buffers for fields, indices, slices, descendant ranges,
and scalar filter predicates. The Python wrapper converts returned node IDs
into lazy `DatumInContext` objects backed by original object references.

All FFI buffers are caller-owned contiguous NumPy arrays with explicitly
checked dtypes, lengths, alignment, and non-null addresses. Their addresses
cross `ctypes` as pointers and are rebuilt as
`UnsafePointer[..., AnyOrigin[mut=True]]` inside the exported Mojo function.
Python retains every array for the duration of the synchronous call; the
shared library does not allocate or retain memory across calls. Values that
cannot be represented exactly by the numeric side table use the Python
evaluator.

## Development

```bash
pixi run build
pixi run test
pixi run bench
```

The 52-test suite compares values, result ordering, full paths, filters, and
mutation behavior directly against the installed upstream jsonpath-ng 1.8.0.
