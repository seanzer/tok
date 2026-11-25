"""The compiler: the core plus one mixin per language feature."""
from .check import Core
from .f_builtins import Builtins
from .f_dicts import Dicts
from .f_lists import Lists
from .f_methods import Methods
from .f_modules import Modules
from .f_narrow import Narrow
from .f_numeric import Numeric
from .f_optres import OptRes

MIXINS = [Narrow, Builtins, Numeric, Modules, Methods, OptRes, Dicts, Lists]


def make_compiler():
    return type("Compiler", tuple(MIXINS) + (Core,), {})


def compile_file(path, src, need_main=True):
    """Returns (c_code or None, compiler). Errors are on compiler.sorted_errors()."""
    comp = make_compiler()(path, src)
    return comp.compile(need_main), comp
