from __future__ import annotations

import ctypes
import os
import shutil
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LIB = os.environ.get("MOJO_JSONPATH_NG_LIB") or os.path.join(
    ROOT, "dist", "libmojo-jsonpath-ng.so"
)
SOURCE = os.path.join(ROOT, "src", "jsonpath.mojo")

I = ctypes.c_int64
P = ctypes.c_void_p


class BuildError(RuntimeError):
    pass


def build(force: bool = False) -> str:
    if os.environ.get("MOJO_JSONPATH_NG_LIB") and os.path.exists(LIB):
        return LIB
    stale = (
        not os.path.exists(LIB)
        or (os.path.exists(SOURCE) and os.path.getmtime(SOURCE) > os.path.getmtime(LIB))
    )
    if force or stale:
        pixi = shutil.which("pixi")
        if not pixi:
            raise BuildError("pixi is required to build the Mojo shared library")
        proc = subprocess.run(
            [pixi, "run", "--manifest-path", os.path.join(ROOT, "pixi.toml"), "build"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=1800,
        )
        if proc.returncode or not os.path.exists(LIB):
            raise BuildError((proc.stderr or proc.stdout).strip()[:4000])
    return LIB


_library = None


def lib():
    global _library
    if _library is None:
        _library = ctypes.CDLL(build())
        fn = _library.mjp_eval
        # The first 22 arguments are addresses, not integers.  c_void_p makes
        # ctypes reject non-pointer-width values instead of silently narrowing
        # them before Mojo receives them.
        fn.argtypes = [P] * 22 + [I, I, P, P, I]
        fn.restype = I
    return _library
