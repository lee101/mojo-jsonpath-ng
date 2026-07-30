from .filter import Expression, Filter
from .parser import ExtendedJsonPathParser


def parse(path):
    return ExtendedJsonPathParser().parse(path)


__all__ = ["Expression", "ExtendedJsonPathParser", "Filter", "parse"]
