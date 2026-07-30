from ..parser import JsonPathParser as _BaseParser
from ..parser import parse as _parse


class ExtendedJsonPathParser(_BaseParser):
    def parse(self, string, lexer=None):
        return _parse(string, extended=True)
