"""mojo-jsonpath-ng against jsonpath-ng 1.8.0 on identical documents."""

from __future__ import annotations

import math
import os
import platform
import sys
import time

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "python"),
)

import jsonpath_ng  # noqa: E402
from jsonpath_ng.ext import parse as upstream_ext_parse  # noqa: E402

import mojo_jsonpath_ng as mojo  # noqa: E402
from mojo_jsonpath_ng.ext import parse as mojo_ext_parse  # noqa: E402


def timeit(fn, repeat=5):
    best = math.inf
    for _ in range(repeat):
        start = time.perf_counter()
        result = fn()
        elapsed = time.perf_counter() - start
        if not isinstance(result, list):
            raise RuntimeError("benchmark query did not return matches")
        best = min(best, elapsed)
    return best


def machine():
    model = platform.processor()
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("model name"):
                    model = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    return model or platform.machine()


def main():
    count = 100_000
    records = [
        {
            "id": i,
            "score": (i * 37) % 1000 / 10,
            "active": i % 7 == 0,
            "meta": {"target": i} if i % 20 == 0 else {"other": i},
        }
        for i in range(count)
    ]
    document = {"records": records, "version": 1}
    prepared = mojo.prepare(document)

    core_cases = [
        ("projection, prepared", "$.records[*].score", prepared, document),
        ("slice, prepared", "$.records[1000:90000:3].id", prepared, document),
        ("recursive descent, prepared", "$..target", prepared, document),
    ]
    filter_cases = [
        (
            "numeric filter, prepared",
            "$.records[?(@.score >= 99)].id",
            prepared,
            document,
        ),
        (
            "numeric filter, cold",
            "$.records[?(@.score >= 99)].id",
            document,
            document,
        ),
    ]

    rows = []
    for name, expression, mojo_data, upstream_data in core_cases:
        ours = mojo.parse(expression)
        theirs = jsonpath_ng.parse(expression)
        ours.find(mojo_data)
        mojo_time = timeit(lambda: ours.find(mojo_data))
        upstream_time = timeit(lambda: theirs.find(upstream_data))
        rows.append((name, mojo_time, upstream_time))

    for name, expression, mojo_data, upstream_data in filter_cases:
        ours = mojo_ext_parse(expression)
        theirs = upstream_ext_parse(expression)
        ours.find(mojo_data)
        mojo_time = timeit(lambda: ours.find(mojo_data))
        upstream_time = timeit(lambda: theirs.find(upstream_data))
        rows.append((name, mojo_time, upstream_time))

    print(f"Machine: {machine()}")
    print(f"Python: {platform.python_version()}; jsonpath-ng: {jsonpath_ng.__version__}")
    print()
    print("| query (100,000 records) | Mojo | jsonpath-ng | speedup |")
    print("|---|---:|---:|---:|")
    for name, mojo_time, upstream_time in rows:
        speedup = upstream_time / mojo_time
        print(
            f"| {name} | {mojo_time * 1e3:.2f} ms | "
            f"{upstream_time * 1e3:.2f} ms | {speedup:.2f}x |"
        )


if __name__ == "__main__":
    main()
