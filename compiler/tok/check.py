"""Core of the checker and C emitter.

One pass per function: type and effect check each statement and emit C for it.
Expressions return (c_code, type). Anything with a side effect, or that can
panic, is hoisted into a C temporary in source order, so the C expression that
comes back is always safe to place anywhere.

Ownership. Strings and list buffers are reference counted. The C code an
expression returns is either *borrowed* (a variable, a field or element of one,
a literal: someone else keeps it alive for the rest of the statement) or an
*owned temporary* (the result of a call or a constructor), which is listed in
the current pool and released when the statement ends. Whoever stores a value
calls `take`: an owned temporary is moved out of the pool, a borrowed value gets
one more owner. Variables own their value and release it when their scope ends.
Function parameters and loop variables are borrowed.

Language features live in mixins (lists, optionals and errors, methods,
modules, fixed-width numbers) that add `x_<kind>` expression handlers,
`s_<kind>` statement handlers, and entries in the builtin tables.
"""
import os
import re

from .diag import Diag, make_fix
from .lexer import Tok, lex
from .parser import Parser
from .types import (ALIASES, BOOL, EKIND, ERR, ERRV, F64, INT, NEVER, NONE, NUMS, STR, VOID, TypeGen, int_range, is_float, is_int,
                    is_num, lossless, mangle)
from . import runtime, runtime_mt, runtime_net

BUILTIN_KINDS = ("Failed", "FsError", "NetError")  # the kinds of fail("msg"), the fs module, the net module


def c_str(s, fmt=False):
    out = []
    for ch in s:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "%" and fmt:
            out.append("%%")
        elif ch == "?":
            out.append("\\?")  # avoid C trigraphs
        elif ord(ch) < 32 or ord(ch) == 127:
            out.append("\\%03o" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


LIT_CODE = re.compile(r"tok_q\d+\.s$")

RESERVED_TYPES = {"bool": ("bool",), "str": ("str",), "Error": ERRV, "ErrorKind": EKIND}


class Module:
    def __init__(self, name, idx, path, src):
        self.name, self.idx, self.path, self.src = name, idx, path, src
        self.fns, self.types, self.failed = {}, {}, set()
        self.errors = {}  # declared error kinds: name -> index in the program's kind table
        self.broken = False  # had lex or parse errors


def has_block(x):
    """True if a statement contains a handler written as a block: the one place where statements, and so
    assignments, run in the middle of an expression."""
    if isinstance(x, dict):
        if x.get("k") == "lam" and x.get("block") is not None:
            return True
        return any(has_block(v) for v in x.values() if isinstance(v, (dict, list, tuple)))
    if isinstance(x, (list, tuple)):
        return any(has_block(v) for v in x if isinstance(v, (dict, list, tuple)))
    return False


def mentions(x, name):
    """True if the expression reads the variable `name` anywhere."""
    if isinstance(x, dict):
        if x.get("k") == "name" and x.get("name") == name:
            return True
        return any(mentions(v, name) for v in x.values() if isinstance(v, (dict, list, tuple)))
    if isinstance(x, (list, tuple)):
        return any(mentions(v, name) for v in x if isinstance(v, (dict, list, tuple)))
    return False


def count_mentions(x, name):
    """How many times the variable `name` is read in this piece of the program."""
    if isinstance(x, dict):
        if x.get("k") == "name" and x.get("name") == name:
            return 1
        return sum(count_mentions(v, name) for v in x.values() if isinstance(v, (dict, list, tuple)))
    if isinstance(x, (list, tuple)):
        return sum(count_mentions(v, name) for v in x if isinstance(v, (dict, list, tuple)))
    return 0


def is_name(n, name):
    return isinstance(n, dict) and n.get("k") == "name" and n.get("name") == name


def consumed_params(f):
    """Function summary: the parameters this function takes over instead of borrowing. A parameter is consumed when
    its last use, among the statements of the function body, is to be stored in a variable or returned as it is:
    `var ys = xs`, `ys = xs`, `return xs`, or `xs` as the final expression. The caller then hands over its own
    reference (or a new one), and a caller holding the only reference lets the function change the value in place."""
    body, out = f["body"], set()
    for pname, _, _ in f["params"]:
        last = None
        for i, s in enumerate(body):
            if mentions(s, pname):
                last = i
        if last is None:
            continue
        s = body[last]
        if count_mentions(s, pname) != 1:
            continue
        k = s["k"]
        if (k in ("bind", "return") and is_name(s.get("val"), pname)) \
                or (k == "assign" and s["op"] == "=" and is_name(s["val"], pname) and s["target"]["k"] == "name") \
                or (k == "expr" and last == len(body) - 1 and is_name(s["val"], pname)):
            out.add(pname)
    return out


class Region:
    """A stretch of generated code that runs conditionally or repeatedly: it gets its own pool of owned temporaries,
    which must be flushed before the region ends."""

    def __init__(self, comp):
        self.comp = comp

    def __enter__(self):
        self.comp.pools.append([])

    def __exit__(self, *a):
        self.comp.pools.pop()


class Capture:
    def __init__(self, comp, deeper=0):
        self.comp, self.deeper, self.lines = comp, deeper, []

    def __enter__(self):
        self.saved = self.comp.buf
        self.comp.buf = self.lines
        self.comp.depth += self.deeper
        return self.lines

    def __exit__(self, *a):
        self.comp.buf = self.saved
        self.comp.depth -= self.deeper


class Core:
    def __init__(self, entry_path, src):
        self.errors = []
        self.mods, self.modlist, self.queue = {}, [], []
        self.types = TypeGen(self.fields_of)
        self.free_fns = {"print": self.b_print}
        self.methods = {}
        self.builtin_mods = {}
        self.mod = self.cur = None
        self.buf, self.depth, self.scopes, self.tmp = [], 0, [], 0
        self.pools = [[]]  # owned temporaries: one list per region, innermost last; entries are (c lvalue, type)
        self.cautious = False  # the statement has a handler block: hoisted borrowed values are retained
        self.dead = False  # the statement just emitted left the function
        self.bails = 0  # how many early exits have been emitted so far in this function
        self.preps = []  # C statements to run just before a store through an index
        self.giving = None  # (name, C name) of the variable in `x = f(x)` while f's arguments are compiled
        self.top, self.top_pools, self.dry = None, 0, 0
        self.lits = {}  # string literal text -> name of its static object
        self.sinks = [None]  # where the last expression of a block goes: None = the function result
        self.loops = []  # enclosing loops, innermost last: what `break` and `continue` must release and where they go
        self.kinds = list(BUILTIN_KINDS)  # every error kind in the program; the built-in ones come first
        self.net = False  # the program calls the net module
        self.script_names = set()  # variables bound by top-level statements (the script form)
        self.aux = []  # extra C functions: the bodies of par for loops and the entry points of spawned tasks
        self.setup()
        for t in (("list", STR), ("list", INT), ("opt", INT), ("opt", F64), ("opt", STR), ("res", STR), ("res", VOID)):
            self.types.need(t)
        self.entry = self.add_module("", entry_path, src)

    def setup(self):
        """Mixins extend this to register builtins."""

    # ---------------------------------------------------------- diagnostics

    def report(self, d, mod=None):
        if d.file is None:
            d.file = (mod or self.mod or self.entry).path
        key = (d.file, d.line, d.col, d.code)
        if key not in {(e.file, e.line, e.col, e.code) for e in self.errors}:
            self.errors.append(d)

    def err(self, code, msg, n, fix=None):
        raise Diag(code, msg, n["line"], n["col"], n["end"], fix)

    def sorted_errors(self):
        entry = self.entry.path
        return sorted(self.errors, key=lambda e: (e.file != entry, e.file, e.line, e.col))

    # ---------------------------------------------------------- modules and declarations

    def add_module(self, name, path, src):
        mod = Module(name, len(self.modlist), path, src)
        self.modlist.append(mod)
        self.mods[name] = mod
        try:
            toks = lex(src)
        except Diag as d:
            mod.broken = True
            self.report(d, mod)
            return mod
        p = Parser(toks)
        decls = p.program()
        for d in p.errors:
            mod.broken = True
            self.report(d, mod)
        mod.failed = p.failed_names
        if p.script:
            decls = decls + self.script_main(mod, decls, p.script)
        for d in decls:
            d["mod"] = mod
            if d["k"] == "error":
                for t in d["names"]:
                    if t.val in mod.fns or t.val in mod.types or t.val in mod.errors or t.val in self.free_fns \
                            or t.val in BUILTIN_KINDS or t.val in RESERVED_TYPES:
                        self.report(Diag("E0102", f"'{t.val}' is already defined", t.line, t.col, t.end), mod)
                        continue
                    mod.errors[t.val] = len(self.kinds)
                    self.kinds.append(t.val)
                continue
            t = d["name_tok"]
            table = mod.fns if d["k"] == "fn" else mod.types
            if d["name"] in mod.fns or d["name"] in mod.types or d["name"] in self.free_fns or d["name"] in mod.errors or \
                    d["name"] in BUILTIN_KINDS or \
                    (d["k"] == "type" and (d["name"] in NUMS or d["name"] in ALIASES or d["name"] in RESERVED_TYPES
                                           or d["name"] in ("chan", "task", "atomic"))):
                self.report(Diag("E0102", f"'{d['name']}' is already defined", t.line, t.col, t.end), mod)
                continue
            table[d["name"]] = d
            if d["k"] == "fn":
                d["c"] = f"tok_f{mod.idx}_{d['name']}"
                self.queue.append(d)
        for d in mod.types.values():
            self.check_record(d)
        return mod

    def script_main(self, mod, decls, stmts):
        """Top-level statements are the body of the program: an implicit `main` that takes `args` and whose effects
        are inferred. Only the entry file may have them, and not together with `fn main`."""
        first = stmts[0]
        at = (first["line"], first["col"], first["end"])
        if mod.name != "":
            self.report(Diag("E0404", f"module '{mod.name}' has statements at the top level; a module can only hold "
                                      "functions, types and error kinds", *at), mod)
            return []
        for d in decls:
            if d["k"] == "fn" and d["name"] == "main":
                self.report(Diag("E0403", "this file has both top-level statements and 'fn main'; move the statements "
                                          f"into main (line {d['line']}), or delete 'fn main' and keep its body at the "
                                          "top level", *at), mod)
                return []
        tok = Tok("NAME", "main", *at)
        str_t = {"k": "tname", "name": "str", "mod": None, "line": at[0], "col": at[1], "end": at[2]}
        args_t = {"k": "tlist", "elem": str_t, "line": at[0], "col": at[1], "end": at[2]}
        self.script_names = {"args"}
        for s in stmts:
            if s["k"] == "bind":
                self.script_names.add(s["name"])
            elif s["k"] == "multi":
                self.script_names.update(t["name"] for t in s["targets"] if t["k"] == "name")
        return [{"k": "fn", "name": "main", "name_tok": tok, "params": [("args", args_t, tok)], "ret": None, "effects": [],
                 "sig_end": at[2], "body": stmts, "script": True, "line": at[0], "col": at[1], "end": at[2]}]

    def type_str(self, t):
        k = t[0]
        if k == "task":
            return f"task[{self.type_str(t[1])}{', err' if t[2] else ''}]"
        if k in ("chan", "atomic"):
            return f"{k}[{self.type_str(t[1])}]"
        if k == "list":
            return f"[{self.type_str(t[1])}]"
        if k == "dict":
            return f"{{{self.type_str(t[1])}: {self.type_str(t[2])}}}"
        if k == "opt":
            return f"{self.type_str(t[1])}?"
        if k == "res":
            return self.type_str(t[1])
        if k == "rec":
            m = self.modlist[t[1]]
            return t[2] if m is (self.mod or self.entry) else f"{m.name}.{t[2]}"
        return {"void": "nothing", "never": "nothing", "error": "?", "errv": "Error", "ekind": "ErrorKind"}.get(k, k)

    def resolve_type(self, tn, mod):
        k = tn["k"]
        if k == "thandle":
            return self.handle_type(tn, self.resolve_type(tn["inner"], mod))
        if k == "tlist":
            return ("list", self.resolve_type(tn["elem"], mod))
        if k == "tdict":
            kt, vt = self.resolve_type(tn["key"], mod), self.resolve_type(tn["val"], mod)
            self.dict_key_type(kt, tn["key"])
            return ERR if ERR in (kt, vt) else ("dict", kt, vt)
        if k == "topt":
            inner = self.resolve_type(tn["inner"], mod)
            if inner[0] == "opt":
                self.err("E0110", "an optional of an optional is not allowed", tn)
            return ("opt", inner)
        name = tn["name"]
        if tn["mod"] is not None:
            mod = self.get_module(tn["mod"], tn)
            if mod is None:
                return ERR
        elif name in NUMS or name in ALIASES:
            return (ALIASES.get(name, name),)
        elif name in RESERVED_TYPES:
            return RESERVED_TYPES[name]
        if name in mod.types:
            return ("rec", mod.idx, name)
        if name in mod.failed or mod.broken:
            return ERR
        self.err("E0110", f"unknown type '{name}'", tn)

    def get_module(self, name, n):
        self.err("E0900", "modules are not supported by this compiler yet", n)

    def handle_type(self, tn, inner):
        self.err("E0900", "channels, tasks and atomics are not supported by this compiler yet", tn)

    def fields_of(self, t):
        decl = self.modlist[t[1]].types[t[2]]
        if "rfields" not in decl:
            decl["rfields"] = []  # guards against re-entry
            out, seen = [], set()
            for fname, ftn, ftok in decl["fields"]:
                if fname in seen:
                    self.report(Diag("E0102", f"field '{fname}' is listed twice", ftok.line, ftok.col, ftok.end), decl["mod"])
                    continue
                seen.add(fname)
                try:
                    ft = self.resolve_type(ftn, decl["mod"])
                except Diag as d:
                    self.report(d, decl["mod"])
                    ft = ERR
                out.append((fname, ft))
            decl["rfields"] = out
        return decl["rfields"]

    def check_record(self, decl):
        """A record that contains itself (not through a list) would have infinite size."""
        me = ("rec", decl["mod"].idx, decl["name"])
        seen = set()

        def reaches(t):
            if t[0] == "opt":
                return reaches(t[1])
            if t[0] != "rec":
                return False
            if t == me:
                return True
            if t in seen:
                return False
            seen.add(t)
            return any(reaches(ft) for _, ft in self.fields_of(t))

        if any(reaches(ft) for _, ft in self.fields_of(me)):
            t = decl["name_tok"]
            self.report(Diag("E0112", f"record '{decl['name']}' contains itself, so it would have infinite size; "
                                      f"put the inner value in a list", t.line, t.col, t.end), decl["mod"])
            decl["rfields"] = [(fn, ERR if reaches(ft) else ft) for fn, ft in decl["rfields"]]

    def sig(self, f):
        if "sig" not in f:
            mod = f["mod"]
            params = []
            for pname, ptn, _ in f["params"]:
                try:
                    params.append((pname, self.resolve_type(ptn, mod)))
                except Diag as d:
                    self.report(d, mod)
                    params.append((pname, ERR))
            ret = VOID
            if f["ret"] is not None:
                try:
                    ret = self.resolve_type(f["ret"], mod)
                except Diag as d:
                    self.report(d, mod)
                    ret = ERR
            is_main = mod is self.entry and f["name"] == "main"
            fallible = "err" in f["effects"] and not is_main
            owned = set() if is_main else consumed_params(f)
            f["sig"] = {"params": params, "ret": ret, "effects": [e for e in f["effects"] if e != "err"],
                        "fallible": fallible, "is_main": is_main,
                        "owned": [n in owned and t != ERR and self.managed(t) for n, t in params],
                        "cret": ("res", ret) if fallible else ret}
        return f["sig"]

    # ---------------------------------------------------------- emission helpers

    def emit(self, text):
        self.buf.append("  " * self.depth + text)

    def capture(self, deeper=0):
        return Capture(self, deeper)

    def new_tmp(self):
        self.tmp += 1
        return f"t{self.tmp}"

    def c(self, t):
        return self.types.c(t)

    def region(self):
        return Region(self)

    def managed(self, ty):
        return self.types.managed(ty)

    def lit(self, text):
        """A string literal: a static object, never counted or freed."""
        name = self.lits.get(text)
        if name is None:
            name = self.lits[text] = f"tok_q{len(self.lits)}"
        return f"{name}.s"

    def own(self, code, ty):
        """Record an owned temporary; it is released at the end of the statement unless something takes it."""
        if self.managed(ty):
            self.pools[-1].append((code, ty))
        return code

    def disown(self, code):
        """Take an owned temporary out of the current pool. False if it is not there."""
        pool = self.pools[-1]
        for i, (c, _) in enumerate(pool):
            if c == code:
                del pool[i]
                return True
        return False

    def hoist(self, code, ty, own=False):
        """Evaluate now, into a C temporary. `own`: the code makes a new value that this statement must release."""
        t = self.new_tmp()
        if self.cautious and not own and self.managed(ty):
            code, own = self.types.ret(ty, code), True
        self.emit(f"{self.c(ty)} {t} TOK_UNUSED = {code};")
        if own:
            self.own(t, ty)
        return t

    def stash(self, code, ty):
        """Evaluate now into a C temporary that nothing tracks: the caller is about to store it."""
        t = self.new_tmp()
        self.emit(f"{self.c(ty)} {t} = {code};")
        return t

    def take(self, code, ty):
        """The value as something the caller will store and own: a move if it is an owned temporary, else one more owner."""
        if not self.managed(ty) or self.disown(code):
            return code
        if ty == STR and LIT_CODE.match(code):
            return code
        return self.types.ret(ty, code)

    def movable(self, n, code):
        """The owned variable this name refers to, if this is the last time the function reads it: the value can be
        handed over as it is. Only for a statement directly in the function body, outside any loop or branch."""
        if n["k"] != "name" or self.depth != 1 or len(self.pools) != self.top_pools or self.dry:
            return None
        v = self.lookup(n["name"])
        if v is None or not v.get("own") or v["c"] != code:
            return None
        stmts, i = self.top
        if count_mentions(stmts[i], n["name"]) != 1 or any(mentions(s, n["name"]) for s in stmts[i + 1:]):
            return None
        return v

    def take_node(self, n, code, ty):
        """`take` for the value of expression `n`: a variable read for the last time moves instead of being shared."""
        if self.managed(ty):
            v = self.movable(n, code)
            if v is not None:
                v["own"] = False
                return code
        return self.take(code, ty)

    def settle(self, code, ty):
        """Evaluate an owned value now, into a C temporary that no pool tracks: the caller stores it."""
        t = self.new_tmp()
        self.emit(f"{self.c(ty)} {t} TOK_UNUSED = {code};")
        return t

    def flush(self):
        """Release what is left of the current pool."""
        pool = self.pools[-1]
        for code, ty in reversed(pool):
            self.emit(self.types.release(ty, code))
        del pool[:]

    def cleanup_lines(self, skip=None):
        """C statements that release everything this function holds right now."""
        lines = []
        for pool in reversed(self.pools):
            for code, ty in reversed(pool):
                # a list that is still being filled has no type yet: `collect` fills the line in afterwards
                lines.append(self.types.release(ty, code) if ty is not None else f"@@{code}@@")
        for scope in reversed(self.scopes):
            for v in reversed(list(scope.values())):
                if v.get("own") and v is not skip:
                    lines.append(self.types.release(v["ty"], v["c"]))
        return lines

    def leave(self, value, skip=None):
        """Return from the function: `value` is the C expression to return, or None."""
        self.bails += 1
        sig = self.sig(self.cur)
        lines = self.cleanup_lines(skip)
        if value is not None and lines:
            r = self.new_tmp()
            self.emit(f"{'int' if sig['is_main'] else self.c(sig['cret'])} {r} = {value};")
            value = r
        for line in lines:
            self.emit(line)
        self.emit(f"return {value};" if value is not None else "return;")

    def loc(self, n):
        return '"' + c_str(f"{os.path.basename(self.mod.path)}:{n['line']}") + '"'

    def lookup(self, name):
        for s in reversed(self.scopes):
            if name in s:
                return s[name]
        return None

    def define(self, name, ty, mutable=False, decl=None, own=False):
        self.scopes[-1][name] = {"c": f"v_{name}", "ty": ty, "mutable": mutable, "decl": decl,
                                 "own": own and ty != ERR and self.managed(ty)}

    def check_new_name(self, name, n, what="variable"):
        if self.lookup(name) is not None:
            self.err("E0102", f"{what} '{name}' is already defined in this function", n)
        if name in self.mod.fns or name in self.free_fns:
            self.err("E0102", f"'{name}' is a function and cannot be used as a variable name", n)
        if self.kind_id(name) is not None:
            self.err("E0102", f"'{name}' is an error kind and cannot be used as a variable name", n)

    def kind_id(self, name, mod=None):
        """The index of an error kind visible by this bare name, or None."""
        mod = mod or self.mod
        if mod is not None and name in mod.errors:
            return mod.errors[name]
        return self.kinds.index(name) if name in BUILTIN_KINDS else None

    # ---------------------------------------------------------- expressions

    def expr(self, n, want=None):
        h = getattr(self, "x_" + n["k"], None)
        if h is None:
            self.err("E0900", "this expression is not supported by this compiler yet", n)
        code, ty = h(n, want)
        n["ty"] = ty
        return code, ty

    def val(self, n, want=None, never=False):
        """An expression used as a value: fallible calls must already be handled."""
        code, ty = self.expr(n, want)
        if ty[0] == "res":
            self.err("E0203", "this call can fail; handle it with '?' (pass the error up) or 'or' (use a default)", n,
                     make_fix(f"propagate the error: insert \"?\" at {n['line']}:{n['end']}", [(n["line"], n["end"], "?")]))
        if ty == NEVER and not never:
            self.err("E0103", "'fail' does not produce a value and cannot be used here", n)
        return code, ty

    def coerce(self, code, ty, want, n, msg=None):
        if ty == ERR or want == ERR:
            return code
        if ty == want:
            return code
        if want[0] == "opt":
            if ty == NONE:
                return f"(({self.c(want)}){{0}})"
            if ty == want[1]:
                return f"(({self.c(want)}){{.has = true, .v = {code}}})"
        if is_num(ty) and is_num(want):
            self.err("E0106", msg or f"expected {self.type_str(want)}, found {self.type_str(ty)}", n, self.conv_fix(n, ty, want))
        self.err("E0103", msg or f"expected {self.type_str(want)}, found {self.type_str(ty)}", n)

    def conv_fix(self, n, ty, want):
        """A conversion is suggested only when it cannot lose information."""
        return None

    def stored(self, n, want, msg=None):
        """An expression as a value that the caller stores and owns."""
        code, ty = self.val(n, want)
        return self.coerce(self.take_node(n, code, ty), ty, want, n, msg)

    def cond(self, n):
        code, ty = self.val(n)
        if ty == BOOL or ty == ERR:
            return code
        if ty[0] == "opt":
            return f"{code}.has"
        self.err("E0103", f"a condition needs bool or an optional, found {self.type_str(ty)}", n)

    # literals

    @staticmethod
    def is_literal(n):
        return n["k"] in ("int", "float") or (n["k"] == "un" and n["op"] == "-" and n["e"]["k"] in ("int", "float")
                                              and not n["e"].get("paren"))

    @staticmethod
    def num_want(want):
        if want is not None and want[0] == "opt":
            want = want[1]
        return want if want is not None and is_num(want) else None

    def c_int(self, v, ty):
        ct = NUMS[ty[0]][0]
        if v > 9223372036854775807:
            return f"(({ct})UINT64_C({v}))"
        if v == -9223372036854775808:
            return f"(({ct})(-INT64_C(9223372036854775807) - 1))"
        return f"(({ct})INT64_C({v}))"

    def lit_int(self, n, v, want):
        w = self.num_want(want)
        ty = w if w is not None else INT
        if is_float(ty):
            return self.lit_float(n, float(v), repr(float(v)), want)
        lo, hi = int_range(ty)
        if not lo <= v <= hi:
            self.err("E0107", f"{v} does not fit in {ty[0]} ({lo} to {hi})", n)
        return self.c_int(v, ty), ty

    def lit_float(self, n, v, text, want):
        w = self.num_want(want)
        ty = w if w is not None and is_float(w) else F64
        if w is not None and is_int(w):
            self.err("E0106", f"expected {w[0]}, found a float literal", n)
        if v != v or v in (float("inf"), float("-inf")):
            self.err("E0107", f"{text} is too large for {ty[0]}", n)
        if ty == ("f32",) and abs(v) > 3.4028234663852886e38:
            self.err("E0107", f"{text} is too large for f32", n)
        code = repr(v)
        if "." not in code and "e" not in code and "n" not in code:
            code += ".0"
        return (f"({code})" if ty == F64 else f"(({NUMS[ty[0]][0]}){code})"), ty

    def x_int(self, n, want):
        return self.lit_int(n, n["v"], want)

    def x_float(self, n, want):
        return self.lit_float(n, float(n["v"]), n["v"], want)

    def x_bool(self, n, want):
        return ("true" if n["v"] else "false"), BOOL

    def x_str(self, n, want):
        parts = n["parts"]
        if all(p[0] == "s" for p in parts):
            return self.lit("".join(p[1] for p in parts)), STR
        return self.build_str(self.str_items(n)), STR

    def str_items(self, n):
        """The pieces of an interpolated string, with every expression already evaluated:
        ("s", text) or ("v", code, type)."""
        items = []
        for p in n["parts"]:
            if p[0] == "s":
                if p[1]:
                    items.append(("s", p[1]))
            else:
                code, ty = self.val(p[1])
                if ty == VOID:
                    self.err("E0103", "this call returns nothing, so it cannot go inside a string", p[1])
                items.append(("v", code, ty))
        n["ty"] = STR
        return items

    def sb_new(self):
        """A string builder that starts in a buffer on the stack. Nothing that can leave the function may run
        between here and the call that finishes it."""
        b = self.new_tmp()
        self.emit(f"tok_sbuf {b}b; tok_sb {b}; tok_sb_init(&{b}, &{b}b);")
        return b

    def sb_add(self, b, items):
        for it in items:
            if it[0] == "s":
                self.emit(f'tok_sb_add(&{b}, "{c_str(it[1])}", {len(it[1].encode("utf-8"))});')
            elif it[2] != ERR:
                self.emit(self.types.show(it[2], it[1], f"&{b}"))

    def build_str(self, items):
        b = self.sb_new()
        self.sb_add(b, items)
        return self.hoist(f"tok_sb_str(&{b})", STR, own=True)

    def x_name(self, n, want):
        v = self.lookup(n["name"])
        if v is None:
            if n["name"] in self.mod.failed:
                return "0", ERR
            hint = " (functions are called with parentheses)" if n["name"] in self.mod.fns or n["name"] in self.free_fns else ""
            if n["name"] in self.script_names and self.mod is self.entry and not self.cur.get("script"):
                hint = " (a top-level variable is not visible inside a function; pass it as an argument)"
            self.err("E0101", f"unknown name '{n['name']}'{hint}", n)
        return v["c"], v["ty"]

    def x_lam(self, n, want):
        self.err("E0113", "a lambda can only be passed directly to a list method such as map or where", n)

    def x_un(self, n, want):
        if n["op"] == "-":
            if self.is_literal(n):
                e = n["e"]
                if e["k"] == "int":
                    return self.lit_int(n, -e["v"], want)
                return self.lit_float(n, -float(e["v"]), "-" + e["v"], want)
            code, ty = self.val(n["e"], want)
            if ty == ERR:
                return "0", ERR
            if is_float(ty):
                return f"(-{code})", ty
            if ty == INT:
                return self.hoist(f"tok_neg_int({code}, {self.loc(n)})", ty), ty
            if is_int(ty) and NUMS[ty[0]][1] == "i":
                return f"(({NUMS[ty[0]][0]})(0 - (uint64_t)({code})))", ty
            self.err("E0103", f"'-' needs a signed number, found {self.type_str(ty)}", n["e"])
        code, ty = self.val(n["e"])
        if ty == ERR:
            return "0", ERR
        if ty != BOOL:
            self.err("E0103", f"'not' needs bool, found {self.type_str(ty)}", n["e"])
        return f"(!{code})", BOOL

    def x_bin(self, n, want):
        op = n["op"]
        if op in ("and", "or"):
            return self.logic(n, want)
        arith = op in ("+", "-", "*", "/", "%", "**")
        l, r = n["l"], n["r"]
        if self.is_literal(l) and not self.is_literal(r):
            rc, rt = self.val(r, want if arith else None)
            self.float_literal_with_int(op, l, r, rt)
            lc, lt = self.val(l, rt if is_num(rt) else None)
        else:
            lc, lt = self.val(l, want if arith else None)
            if self.is_literal(r):
                self.float_literal_with_int(op, r, l, lt)
            rc, rt = self.val(r, lt if is_num(lt) or r["k"] in ("none", "list", "dict") else None)
        return self.binop(op, lc, lt, rc, rt, n)

    def float_literal_with_int(self, op, lit, other, ty):
        """n * 1.5 with an integer n: the literal cannot become an integer, so the fix converts n."""
        inner = lit["e"] if lit["k"] == "un" else lit
        if inner["k"] == "float" and is_int(ty):
            self.err("E0106", f"'{op}' needs the same type on both sides, found {self.type_str(ty)} and a float literal",
                     other, self.conv_fix(other, ty, F64))

    def logic(self, n, want):
        op = n["op"]
        lc, lt = self.expr(n["l"], want) if op == "or" else self.val(n["l"])
        if op == "or" and lt != BOOL and lt != ERR:
            return self.or_default(n, lc, lt, want)
        if lt[0] == "res":
            self.val(n["l"])
        if lt == ERR:
            return "0", ERR
        if lt != BOOL:
            self.err("E0103", f"'{op}' needs bool on both sides, found {self.type_str(lt)}", n["l"])
        # the right side only runs when the left side was true ('and') or false ('or')
        proven = self.facts(n["l"])[0 if op == "and" else 1]
        with self.region(), self.capture(1) as pre, self.holding(proven, n["r"]):
            rc, rt = self.val(n["r"])
            if self.pools[-1]:
                # the right side made values that must be released on this path only
                rc = self.hoist(rc, BOOL) if rt == BOOL else rc
                self.flush()
        if rt == ERR:
            return "0", ERR
        if rt != BOOL:
            self.err("E0103", f"'{op}' needs bool on both sides, found {self.type_str(rt)}", n["r"])
        cop = "&&" if op == "and" else "||"
        if not pre:
            return f"({lc} {cop} {rc})", BOOL
        # the right side has side effects or can panic: keep it short-circuited
        t = self.hoist(lc, BOOL)
        self.emit(f"if ({'' if op == 'and' else '!'}{t}) {{")
        self.buf.extend(pre)
        self.emit(f"  {t} = {rc};")
        self.emit("}")
        return t, BOOL

    def or_default(self, n, lc, lt, want):
        self.err("E0103", f"'or' needs bool on both sides, found {self.type_str(lt)}", n["l"])

    def binop(self, op, lc, lt, rc, rt, n):
        if lt == ERR or rt == ERR:
            return "0", ERR
        for side, ty in ((n.get("l", n), lt), (n.get("r", n), rt)):
            if ty == VOID:
                self.err("E0103", f"'{op}' cannot use a call that returns nothing", side)
        if op in ("==", "!="):
            neg = "!" if op == "!=" else ""
            for a, at, bt in ((lc, lt, rt), (rc, rt, lt)):
                if at[0] == "opt" and bt == NONE:
                    return f"({'' if neg else '!'}{a}.has)", BOOL
            if lt == NONE and rt == NONE:
                return ("false" if neg else "true"), BOOL
            if lt == rt:
                return f"({neg}{self.types.eq(lt, lc, rc)})", BOOL
        elif lt == rt:
            if lt == STR:
                if op == "+":
                    return self.hoist(f"tok_sconcat({lc}, {rc})", STR, own=True), STR
                if op in ("<", "<=", ">", ">="):
                    return f"(strcmp({lc}, {rc}) {op} 0)", BOOL
            if is_num(lt):
                if op in ("<", "<=", ">", ">="):
                    return f"({lc} {op} {rc})", BOOL
                return self.arith(op, lc, rc, lt, n), lt
            what = "numbers" if op in ("-", "*", "/", "%", "**") else "numbers or strings"
            self.err("E0103", f"'{op}' needs {what}, found {self.type_str(lt)}", n)
        if is_num(lt) and is_num(rt):
            self.err("E0106", f"'{op}' needs the same type on both sides, found {self.type_str(lt)} and {self.type_str(rt)}",
                     n, self.mix_fix(n, lt, rt))
        self.err("E0103", f"'{op}' needs the same type on both sides, found {self.type_str(lt)} and {self.type_str(rt)}", n)

    def mix_fix(self, n, lt, rt):
        return None

    def arith(self, op, a, b, ty, n):
        ct, kind, _ = NUMS[ty[0]]
        if op == "**":
            return self.power(a, b, ty, n)
        if kind == "f":
            if op == "%":
                self.err("E0103", f"'%' needs integers, found {ty[0]}", n)
            return f"({a} {op} {b})"
        return self.hoist(self.int_op(op, a, b, ty, n), ty)

    def power(self, a, b, ty, n):
        """a ** b. `int` stops the program on overflow or a negative exponent; sized integers wrap."""
        ct, kind, _ = NUMS[ty[0]]
        if kind == "f":
            return f"pow({a}, {b})" if ty == F64 else f"powf({a}, {b})"
        if ty == INT:
            return self.hoist(f"tok_pow_int({a}, {b}, {self.loc(n)})", ty)
        e = self.hoist(b, ty)
        if kind == "i":
            self.emit(f"if ({e} < 0) tok_panic({self.loc(n)}, \"negative exponent %lld: an integer power needs an "
                      f"exponent of 0 or more\", (long long){e});")
        return self.hoist(f"(({ct})tok_pow_wrap((uint64_t)({a}), (uint64_t){e}))", ty)

    def int_op(self, op, a, b, ty, n):
        """Integer arithmetic as one C expression. `int` checks for overflow; the sized types wrap."""
        if op in ("/", "%"):
            return f"tok_{'div' if op == '/' else 'mod'}_{ty[0]}({a}, {b}, {self.loc(n)})"
        if ty == INT:
            return f"tok_{ {'+': 'add', '-': 'sub', '*': 'mul'}[op] }_int({a}, {b}, {self.loc(n)})"
        return f"(({NUMS[ty[0]][0]})((uint64_t)({a}) {op} (uint64_t)({b})))"

    # conditional expression

    def x_ifx(self, n, want):
        """if c then a else b: only the chosen branch runs. Both branches have one type; a branch may be `none`
        (the result is then optional) or may leave with fail(...)."""
        cond = self.cond(n["cond"])
        when_true, when_false = self.facts(n["cond"])
        out = self.new_tmp()
        branches = []
        ty = None
        for part, facts in ((n["then"], when_true), (n["other"], when_false)):
            w = want if ty is None or ty in (NONE, NEVER, ERR) else ty
            with self.region(), self.capture(1) as pre, self.holding(facts, part):
                code, bt = self.val(part, w, never=True)
                if bt == VOID:
                    self.err("E0103", "a branch of a conditional expression must give a value; this call returns nothing "
                                      "(use the 'if' statement to choose between actions)", part)
                if bt not in (NEVER, ERR):
                    code = self.stash(self.take(code, bt), bt) if self.pools[-1] or self.managed(bt) else code
                self.flush()
            branches.append([pre, code, bt, part])
            if ty is None or ty in (NEVER, ERR) or (ty == NONE and bt not in (NEVER, ERR)):
                if ty == NONE and bt != NONE:
                    ty = bt if bt[0] == "opt" else ("opt", bt)
                else:
                    ty = bt
            elif bt == NONE and ty[0] != "opt":
                ty = ("opt", ty)
        if ty == ERR or any(b[2] == ERR for b in branches):
            return "0", ERR
        if ty == NONE:
            self.err("E0108", "cannot tell what type 'none' is here: both branches are none", n)
        if ty == NEVER:
            self.err("E0103", "both branches of this conditional expression fail, so it has no value; use the 'if' statement", n)
        for b in branches:
            if b[2] != NEVER:
                b[1] = self.coerce(b[1], b[2], ty, b[3], f"both branches of a conditional expression must have one type: "
                                   f"the other is {self.type_str(ty)}, this one is {self.type_str(b[2])}")
        (pa, ca, ta, _), (pb, cb, tb, _) = branches
        if not pa and not pb and NEVER not in (ta, tb) and not self.managed(ty):
            return f"({cond} ? {ca} : {cb})", ty
        if self.pools[-1]:
            cond = self.hoist(cond, BOOL)
        self.emit(f"{self.c(ty)} {out} TOK_UNUSED;")
        self.emit(f"if ({cond}) {{")
        self.buf.extend(pa)
        if ta != NEVER:
            self.emit(f"  {out} = {ca};")
        self.emit("} else {")
        self.buf.extend(pb)
        if tb != NEVER:
            self.emit(f"  {out} = {cb};")
        self.emit("}")
        return self.own(out, ty), ty

    # calls

    def x_call(self, n, want):
        name = n["name"]
        if self.lookup(name) is not None:
            self.err("E0101", f"'{name}' is a variable, not a function", n)
        if name in self.free_fns:
            return self.free_fns[name](n, want)
        f = self.mod.fns.get(name)
        if f is None:
            if name in self.mod.failed:
                for a in n["args"]:
                    self.expr(a)
                return "0", ERR
            hint = f"; to build a record write {name} {{ ... }}" if name in self.mod.types else ""
            self.err("E0101", f"unknown function '{name}'{hint}", n)
        return self.call_fn(f, n, name)

    def call_fn(self, f, n, shown):
        sig = self.sig(f)
        if len(n["args"]) != len(sig["params"]):
            self.err("E0104", f"'{shown}' takes {len(sig['params'])} argument(s), found {len(n['args'])}", n)
        vals = [self.val(an, pt) for an, (pn, pt) in zip(n["args"], sig["params"])]
        args = []
        for an, (pn, pt), (code, ty), owned in zip(n["args"], sig["params"], vals, sig["owned"]):
            # Arguments are borrowed: the caller keeps them alive for the call. A parameter the function consumes
            # (see consumed_params) gets a reference of its own instead, handed over without counting when it can be.
            if owned and ty != ERR:
                if self.giving is not None and is_name(an, self.giving[0]) and code == self.giving[1]:
                    self.giving = None  # x = f(x): x gives up its value, and is assigned the result
                else:
                    code = self.take_node(an, code, ty)
            args.append(self.coerce(code, ty, pt, an,
                                    f"argument '{pn}' of '{shown}' needs {self.type_str(pt)}, found {self.type_str(ty)}"))
        for eff in sig["effects"]:
            self.need_effect(eff, n, shown)
        call = f"{f['c']}({', '.join(args)})"
        if sig["cret"] == VOID:
            self.emit(call + ";")
            return "", VOID
        return self.hoist(call, sig["cret"], own=True), sig["cret"]

    def sig_fix(self, eff):
        f = self.cur
        insert = f", {eff}" if f["effects"] else f" ! {eff}"
        return make_fix(f"add '{eff}' to the signature of '{f['name']}': insert \"{insert}\" at {f['line']}:{f['sig_end']}",
                        [(f["line"], f["sig_end"], insert)])

    def need_effect(self, eff, n, callee):
        f = self.cur
        if eff in f["effects"] or f.get("script"):  # the effects of top-level statements are inferred
            return
        self.err("E0201", f"'{callee}' needs effect '{eff}', but '{f['name']}' does not declare it", n, self.sig_fix(eff))

    def b_print(self, n, want):
        if len(n["args"]) != 1:
            self.err("E0104", f"print takes 1 argument, found {len(n['args'])}", n)
        arg = n["args"][0]
        if arg["k"] == "str" and not all(p[0] == "s" for p in arg["parts"]):
            # an interpolated string is written straight to the output: no string is built on the heap
            items = self.str_items(arg)
            self.need_effect("io", n, "print")
            b = self.sb_new()
            self.sb_add(b, items)
            self.emit(f"tok_sb_print(&{b});")
            return "", VOID
        code, ty = self.val(arg)
        if ty == VOID:
            self.err("E0103", "this call returns nothing, so there is nothing to print", arg)
        self.need_effect("io", n, "print")
        if ty == STR:
            self.emit(f"tok_print({code});")
        elif ty != ERR:
            b = self.sb_new()
            self.emit(self.types.show(ty, code, f"&{b}"))
            self.emit(f"tok_sb_print(&{b});")
        return "", VOID

    def x_mcall(self, n, want):
        base = n["base"]
        if base["k"] == "name" and self.lookup(base["name"]) is None:
            return self.module_call(n, want)
        # xs.push(v) changes its base in place, so a dict entry on the way to it must exist: d[k].push(v)
        chain, at = [], base
        while n["name"] == "push" and at["k"] in ("field", "index"):
            if at["k"] == "index":
                chain.append(at)
            at = at["base"]
        for ix in chain:
            ix["must"] = True
        try:
            bc, bt = self.val(base)
        finally:
            for ix in chain:
                ix["must"] = False
        if bt == ERR:
            for a in n["args"]:
                if a["k"] != "lam":
                    self.expr(a)
            return "0", ERR
        if self.cautious and bt[0] == "list" and n["name"] not in ("push", "len"):
            bc = self.hoist(bc, bt)  # a handler block in this statement may change the list while it is being walked
        h = self.methods.get((bt[0], n["name"]))
        if h is None and bt[0] == "opt" and (bt[1][0], n["name"]) in self.methods:
            return self.opt_call(n, bc, bt, want)
        if h is None:
            known = sorted(m for k, m in self.methods if k == bt[0])
            hint = f" (methods on {self.type_str(bt)}: {', '.join(known)})" if known else ""
            raise Diag("E0109", f"{self.type_str(bt)} has no method '{n['name']}'{hint}", n["line"], n["name_col"], n["end"])
        return h(n, bc, bt, want)

    def module_call(self, n, want):
        self.err("E0900", "modules are not supported by this compiler yet", n)

    def margs(self, n, count):
        if len(n["args"]) != count:
            self.err("E0104", f"'{n['name']}' takes {count} argument(s), found {len(n['args'])}", n)

    # records

    def x_field(self, n, want):
        base = n["base"]
        if base["k"] == "name" and self.lookup(base["name"]) is None and base["name"] not in self.mod.failed:
            self.err("E0101", f"unknown name '{base['name']}' (a module function is called with parentheses)", base)
        bc, bt = self.val(base)
        if bt == ERR:
            return "0", ERR
        if bt[0] != "rec":
            hint = " (methods are called with parentheses)" if (bt[0], n["name"]) in self.methods else ""
            raise Diag("E0109", f"{self.type_str(bt)} has no field '{n['name']}'{hint}", n["line"], n["name_col"], n["end"])
        for fname, ft in self.fields_of(bt):
            if fname == n["name"]:
                return f"{bc}.f_{fname}", ft
        names = ", ".join(fn for fn, _ in self.fields_of(bt))
        raise Diag("E0109", f"{self.type_str(bt)} has no field '{n['name']}' (fields: {names})", n["line"], n["name_col"], n["end"])

    def x_rec(self, n, want):
        tn = {"k": "tname", "name": n["tname"], "mod": n["tmod"], "line": n["line"], "col": n["col"], "end": n["head_end"]}
        ty = self.resolve_type(tn, self.mod)
        if ty == ERR:
            for _, e, _ in n["fields"]:
                self.expr(e)
            return "0", ERR
        if ty[0] != "rec":
            self.err("E0110", f"'{n['tname']}' is not a record type", tn)
        fields = dict(self.fields_of(ty))
        given, vals = set(), []
        for fname, e, ftok in n["fields"]:
            if fname not in fields:
                raise Diag("E0111", f"{self.type_str(ty)} has no field '{fname}' (fields: {', '.join(fields)})",
                           ftok.line, ftok.col, ftok.end)
            if fname in given:
                raise Diag("E0111", f"field '{fname}' is given twice", ftok.line, ftok.col, ftok.end)
            given.add(fname)
            ft = fields[fname]
            code, ety = self.val(e, ft)
            vals.append((fname, code, ety, e, ft))
        missing = [fn for fn, ft in fields.items() if fn not in given and ft[0] != "opt" and ft != ERR]
        if missing:
            self.err("E0111", f"{self.type_str(ty)} is missing field(s): {', '.join(missing)} "
                              f"(only optional fields may be left out)", n)
        # every field is evaluated before any is taken, so an early exit in a later field leaves nothing half-owned
        inits = [f".f_{fname} = {self.coerce(self.take(code, ety), ety, ft, e)}" for fname, code, ety, e, ft in vals]
        code = f"(({self.c(ty)}){{{', '.join(inits)}}})" if inits else f"(({self.c(ty)}){{0}})"
        return (self.hoist(code, ty, own=True) if self.managed(ty) else code), ty

    # ---------------------------------------------------------- statements

    def block(self, stmts, tail=False, cut=None):
        """`cut` = (i, k): from statement i on, the facts at self.narrowed[k] no longer hold (see s_while)."""
        self.scopes.append({})
        self.pools.append([])
        self.depth += 1
        pushed = 0
        self.dead = False
        for i, s in enumerate(stmts):
            if cut is not None and i == cut[0]:
                self.narrowed[cut[1]] = frozenset()
            if self.depth == 1:
                self.top, self.top_pools = (stmts, i), len(self.pools)
            cautious, self.cautious, self.dead = self.cautious, has_block(s), False
            try:
                self.stmt(s, tail and i == len(stmts) - 1)
            except Diag as d:
                self.report(d)
            if self.dead:
                del self.pools[-1][:]
            else:
                self.flush()
            self.cautious = cautious
            # `if x == none` + return: x has a value in the statements that follow
            after = self.facts_after(s)
            if after:
                self.holding(after, stmts[i + 1:]).__enter__()
                pushed += 1
        del self.narrowed[len(self.narrowed) - pushed:]
        if not self.dead:
            self.scope_exit()
        self.dead = False
        self.depth -= 1
        self.pools.pop()
        self.scopes.pop()

    def scope_exit(self):
        """Variables release what they own when their scope ends."""
        for v in reversed(list(self.scopes[-1].values())):
            if v.get("own"):
                self.emit(self.types.release(v["ty"], v["c"]))

    def stmt(self, s, is_tail=False):
        h = getattr(self, "s_" + s["k"], None)
        if h is None:
            self.err("E0900", "this statement is not supported by this compiler yet", s)
        h(s, is_tail)

    def s_bind(self, s, is_tail):
        name = s["name"]
        old = self.lookup(name)
        if old is not None:
            if s.get("let"):
                self.err("E0302", f"'{name}' is already defined; drop 'let' to assign to it", s,
                         make_fix(f"remove \"let \" at {s['line']}:{s['var_col']}",
                                  [(s["line"], s["var_col"], "", s["col"] - s["var_col"])]))
            if s["mutable"]:
                self.err("E0302", f"'{name}' is already defined; drop 'var' to assign to it", s,
                         make_fix(f"remove \"var \" at {s['line']}:{s['var_col']}",
                                  [(s["line"], s["var_col"], "", s["col"] - s["var_col"])]))
            if s["ann"] is not None:
                self.err("E0302", f"'{name}' is already defined; drop the type annotation to assign to it", s)
            target = {"k": "name", "name": name, "line": s["line"], "col": s["col"], "end": s["end"]}
            return self.assign(target, "=", s["val"], s)
        self.check_new_name(name, s)
        want = None
        try:
            if s["ann"] is not None:
                want = self.resolve_type(s["ann"], self.mod)
            code, ty = self.val(s["val"], want)
            if ty == VOID:
                self.err("E0103", "this call returns nothing, so it cannot be bound to a name", s["val"])
            code = self.take_node(s["val"], code, ty)
            if want is not None:
                code = self.coerce(code, ty, want, s["val"],
                                   f"'{name}' is declared {self.type_str(want)}, but the value is {self.type_str(ty)}")
                ty = want
            elif ty == NONE:
                self.err("E0108", f"cannot tell what type 'none' is here; annotate it, for example '{name}: int? = none'", s["val"])
        except Diag:
            self.define(name, want or ERR, s["mutable"], s)
            raise
        self.define(name, ty, s["mutable"], s, own=True)
        if ty != ERR:
            self.emit(f"{self.c(ty)} v_{name} TOK_UNUSED = {code};")

    def x_raw(self, n, want):
        """A value that is already in a C temporary, owned (see s_multi)."""
        return self.own(n["code"], n["ty"]), n["ty"]

    def s_multi(self, s, is_tail):
        """a, b = x, y: every value on the right is computed first, then the stores happen left to right.
        There is no tuple: the two sides are matched one to one by position."""
        targets, vals, mode = s["targets"], s["vals"], s["mode"]
        new = [t["k"] == "name" and self.lookup(t["name"]) is None for t in targets]
        try:
            if len(targets) != len(vals):
                hint = " (a function returns one value; there are no tuples)" if len(vals) == 1 else ""
                self.err("E0018", f"{len(targets)} names on the left but {len(vals)} value{'s' if len(vals) != 1 else ''} "
                                  f"on the right{hint}", s)
            seen = set()
            for t, is_new in zip(targets, new):
                if t["k"] == "name":
                    if t["name"] in seen:
                        self.err("E0302", f"'{t['name']}' appears twice on the left", t)
                    seen.add(t["name"])
                    if mode and not is_new:
                        self.err("E0302", f"'{t['name']}' is already defined; drop '{mode}' to assign to it", t,
                                 make_fix(f"remove \"{mode} \" at {s['line']}:{s['var_col']}",
                                          [(s["line"], s["var_col"], "", s["col"] - s["var_col"])]))
            raws = []
            for t, v, is_new in zip(targets, vals, new):
                want = None
                if not is_new:
                    want = self.lookup(t["name"])["ty"] if t["k"] == "name" else self.dry_type(t)
                    if want == ERR:
                        want = None
                code, ty = self.val(v, want)
                if ty == VOID:
                    self.err("E0103", "this call returns nothing, so it cannot be assigned", v)
                tmp = self.settle(self.take(code, ty), ty)
                raws.append({"k": "raw", "code": tmp, "ty": ty, "line": v["line"], "col": v["col"], "end": v["end"]})
        except Diag:
            for t, is_new in zip(targets, new):
                if is_new and self.lookup(t["name"]) is None:
                    self.define(t["name"], ERR, mode == "var", t)
            raise
        first = None
        for t, raw, is_new in zip(targets, raws, new):
            try:
                if is_new:
                    self.s_bind({"k": "bind", "name": t["name"], "ann": None, "val": raw, "mutable": mode == "var",
                                 "line": t["line"], "col": t["col"], "end": t["end"]}, False)
                else:
                    self.assign(t, "=", raw, s)
            except Diag as d:
                first = first or d
        if first:
            raise first

    def place(self, n):
        """An assignable location: (c lvalue, type, root variable info, root name)."""
        k = n["k"]
        if k == "name":
            v = self.lookup(n["name"])
            if v is None:
                self.err("E0101", f"unknown name '{n['name']}'", n)
            return v["c"], v["ty"], v, n["name"]
        if k == "field":
            bc, bt, v, root = self.place(n["base"])
            if bt == ERR:
                return "0", ERR, v, root
            if bt[0] == "rec":
                for fname, ft in self.fields_of(bt):
                    if fname == n["name"]:
                        return f"{bc}.f_{fname}", ft, v, root
            raise Diag("E0109", f"{self.type_str(bt)} has no field '{n['name']}'", n["line"], n["name_col"], n["end"])
        if k == "index":
            return self.place_index(n)
        self.err("E0016", "this is not something that can be assigned to", n)

    def place_index(self, n):
        self.err("E0900", "lists are not supported by this compiler yet", n)

    def need_mutable(self, v, root, n, what):
        if v["mutable"]:
            return
        d = v["decl"]
        if d is not None:
            fix = make_fix(f"declare it mutable: insert \"var \" at {d['line']}:{d['col']}", [(d["line"], d["col"], "var ")])
            self.err("E0301", f"'{root}' is immutable and {what}", n, fix)
        self.err("E0301", f"'{root}' is immutable and {what} (parameters and loop variables are read-only; "
                          f"copy it into a var first)", n)

    def assign(self, target, op, val, s):
        if op == "=" and target["k"] == "index":
            bt = self.dry_type(target["base"])
            if bt is not None and bt[0] == "opt" and target["base"]["k"] == "index":
                bt = bt[1]  # d[a][b] = v: as a value d[a] is optional, as a place it is the entry itself
            if bt is not None and bt[0] == "dict":
                return self.dict_insert(target, val, s)
        outer, self.preps = self.preps, []
        try:
            lv, ty, v, root = self.place(target)
            preps = self.preps
        finally:
            self.preps = outer
        self.need_mutable(v, root, target, "cannot be reassigned" if target["k"] == "name" else "cannot be changed in place")
        if ty == ERR:
            self.expr(val)
            return
        if ty == STR and self.append(target, lv, op, val, preps):
            return
        gave = False
        if op == "=":
            what = f"'{root}'" if target["k"] == "name" else "this location"
            if target["k"] == "name" and val["k"] == "call" and v.get("own") and lv == v["c"] and not self.dry \
                    and count_mentions(val, root) == 1 and any(is_name(a, root) for a in val["args"]):
                self.giving = (root, lv)
            armed = self.giving is not None
            try:
                code, vt = self.val(val, ty)
            finally:
                gave, self.giving = armed and self.giving is None, None
            code = self.take_node(val, code, vt)
            code = self.coerce(code, vt, ty, val, f"{what} is {self.type_str(ty)}, cannot assign {self.type_str(vt)}")
        else:
            rc, rt = self.val(val, ty if is_num(ty) else None)
            code, rty = self.binop(op[0], lv, ty, rc, rt, {**s, "l": target, "r": val})
            code = self.take(code, rty)
        # The new value is complete, and owned, before the target is made unique and before the old value is let go:
        # a value can never be stored inside itself.
        if self.managed(ty) and target["k"] != "name":
            code = self.settle(code, ty)
        for p in preps:
            self.emit(p)
        if not self.managed(ty):
            self.emit(f"{lv} = {code};")
        elif gave:
            self.emit(f"{lv} = {code};")  # the old value went into the call
        elif target["k"] == "name":
            old = self.new_tmp()
            self.emit(f"{{ {self.c(ty)} {old} = {lv}; {lv} = {code}; {self.types.release(ty, old)} }}")
        else:
            at = self.new_tmp()
            self.emit(f"{{ {self.c(ty)}* {at} = &{lv}; {self.c(ty)} {at}v = *{at}; *{at} = {code}; "
                      f"{self.types.release(ty, at + 'v')} }}")

    def append(self, target, lv, op, val, preps):
        """s = s + a + b, s += a, s = "{s}...": add to the string in place instead of building a new one each time.
        Only when no other piece reads s. Returns False if the statement is not of that shape."""
        if target["k"] != "name":
            return False
        name = target["name"]
        pieces = []
        if op == "+=":
            pieces = [val]
        elif op == "=" and val["k"] == "str" and val["parts"] and val["parts"][0][0] != "s" \
                and val["parts"][0][1]["k"] == "name" and val["parts"][0][1]["name"] == name:
            pieces = [{**val, "parts": val["parts"][1:]}]
        elif op == "=":
            at = val
            while at["k"] == "bin" and at["op"] == "+":
                pieces.insert(0, at["r"])
                at = at["l"]
            if not pieces or at["k"] != "name" or at["name"] != name:
                return False
        else:
            return False
        if any(mentions(p, name) for p in pieces) or self.dry_type(val) != STR:
            return False
        items = []
        for p in pieces:
            if p["k"] == "str":
                items.extend(self.str_items(p))
            else:
                code, ty = self.val(p, STR)
                if ty != STR:
                    return False  # not reached: the dry run above typed the whole expression as str
                items.append(("v", code, ty))
        val["ty"] = STR
        for p in preps:
            self.emit(p)
        b = self.new_tmp()
        self.emit(f"tok_sb {b}; tok_sb_take(&{b}, {lv});")
        self.sb_add(b, items)
        self.emit(f"{lv} = tok_sb_str(&{b});")
        return True

    def s_assign(self, s, is_tail):
        self.assign(s["target"], s["op"], s["val"], s)

    def s_expr(self, s, is_tail):
        sink = self.sinks[-1] if is_tail else None
        if sink is not None:
            # the last expression of a handler block is the value of the handler
            var, want = sink
            code, ty = self.val(s["val"], None if want == VOID else want, never=True)
            if ty == NEVER or want == VOID:
                return
            if ty == VOID:
                self.err("E0103", f"the handler must end in a value of type {self.type_str(want)}, "
                                  f"but this call returns nothing", s["val"])
            code = self.take(code, ty)
            code = self.coerce(code, ty, want, s["val"],
                               f"the handler must end in a value of type {self.type_str(want)}, found {self.type_str(ty)}")
            if want != ERR and ty != ERR:
                self.emit(f"{var} = {code};")
            return
        code, ty = self.val(s["val"], self.sig(self.cur)["ret"] if is_tail else None, never=True)
        if ty == NEVER:
            return
        if is_tail and self.sig(self.cur)["ret"] != VOID:
            f = self.cur
            self.emit_return(code, ty, s["val"],
                             f"'{f['name']}' returns {self.type_str(self.sig(f)['ret'])}, but its last expression is {self.type_str(ty)}")
        elif code and ty != VOID:
            self.emit(f"(void)({code});")

    def emit_return(self, code, ty, n, msg):
        sig = self.sig(self.cur)
        moved = None
        if n["k"] == "name" and self.managed(ty):
            v = self.lookup(n["name"])
            if v is not None and v.get("own") and v["c"] == code:
                moved = v  # returning a local: it moves out, no count changes
        if moved is None:
            code = self.take(code, ty)
        code = self.coerce(code, ty, sig["ret"], n, msg)
        if sig["ret"] == ERR or ty == ERR:
            return
        self.leave(f"(({self.c(sig['cret'])}){{.v = {code}}})" if sig["fallible"] else code, moved)
        self.dead = True

    def emit_return_void(self):
        sig = self.sig(self.cur)
        self.leave("0" if sig["is_main"] else f"(({self.c(sig['cret'])}){{0}})" if sig["fallible"] else None)
        self.dead = True

    def s_return(self, s, is_tail):
        f, sig = self.cur, self.sig(self.cur)
        if s["val"] is None:
            if sig["ret"] != VOID:
                self.err("E0103", f"'{f['name']}' returns {self.type_str(sig['ret'])}, but this return has no value", s)
            return self.emit_return_void()
        if sig["ret"] == VOID:
            self.expr(s["val"])
            self.err("E0103", f"'{f['name']}' returns nothing, but this return has a value", s["val"])
        code, ty = self.val(s["val"], sig["ret"], never=True)
        if ty == NEVER:
            return
        self.emit_return(code, ty, s["val"], f"'{f['name']}' returns {self.type_str(sig['ret'])}, found {self.type_str(ty)}")

    def s_if(self, s, is_tail):
        code = self.cond(s["cond"])
        when_true, when_false = s["facts"] = self.facts(s["cond"])
        if self.pools[-1]:
            code = self.hoist(code, BOOL)
            self.flush()
        self.emit(f"if ({code}) {{")
        with self.holding(when_true, s["then"]):
            self.block(s["then"], is_tail)
        if s["other"] is not None:
            self.emit("} else {")
            with self.holding(when_false, s["other"]):
                self.block(s["other"], is_tail)
        self.emit("}")
        self.dead = False

    def s_for(self, s, is_tail):
        if s["hi"] is None:
            return self.for_list(s)
        tlo, thi, ty = self.range_bounds(s)
        self.flush()
        v = f"v_{s['var']}"
        self.emit(f"for ({self.c(ty)} {v} = {tlo}; {v} <= {thi}; {v}++) {{")
        # `continue` must still reach the test below (the loop variable may not step past the largest value)
        label = f"tok_next{self.new_tmp()}"
        if self.loop_body(s, s["var"], ty, label):
            self.emit(f"  {label}: ;")
        self.emit(f"  if ({v} == {thi}) break;")
        self.emit("}")
        self.dead = False

    def range_bounds(self, s):
        """Evaluate the two ends of `a..b`: (C temporary of a, C temporary of b, their integer type)."""
        lo, hi = s["iter"], s["hi"]
        if self.is_literal(lo) and not self.is_literal(hi):
            hc, ht = self.val(hi)
            lc, lt = self.val(lo, ht if is_num(ht) else None)
        else:
            lc, lt = self.val(lo)
            hc, ht = self.val(hi, lt if is_num(lt) else None)
        for side, ty in ((lo, lt), (hi, ht)):
            if ty != ERR and not is_int(ty):
                self.err("E0103", f"range bounds need an integer type, found {self.type_str(ty)}", side)
        if lt != ht and ERR not in (lt, ht):
            self.err("E0106", f"range bounds need the same type, found {self.type_str(lt)} and {self.type_str(ht)}",
                     {**s, "col": lo["col"], "end": hi["end"]}, self.mix_fix({"l": lo, "r": hi}, lt, ht))
        self.check_new_name(s["var"], {"line": s["line"], "col": s["var_tok"].col, "end": s["var_tok"].end}, "loop variable")
        ty = lt if lt != ERR else ht
        if ty == ERR:
            ty = INT
        return self.hoist(lc, ty), self.hoist(hc, ty), ty

    def s_while(self, s, is_tail):
        self.emit("while (1) {")
        self.depth += 1
        with self.region():
            try:
                c = self.cond(s["cond"])
                if self.pools[-1]:
                    c = self.hoist(c, BOOL)
                    self.flush()
            except Diag as d:
                self.report(d)
                c = "0"
        self.emit(f"if (!({c})) break;")
        self.depth -= 1
        self.scopes.append({})
        self.loops.append({"scopes": len(self.scopes), "pools": len(self.pools), "label": None, "used": False})
        try:
            # `while line` ... `line = io.read_line()`: what the condition proved holds in the body up to the first
            # statement that assigns one of the tested variables
            proven, body = self.facts(s["cond"])[0], s["body"]
            roots = {p[0] for p in proven}
            k = next((i for i, st in enumerate(body) if roots & self.assigned(st)), len(body))
            with self.holding(proven, body[:k]):
                self.block(body, cut=(k, len(self.narrowed) - 1))
        finally:
            self.loops.pop()
            self.scopes.pop()
        self.emit("}")
        self.dead = False

    def assigned(self, stmt):
        return set()

    def jump(self, s, word):
        """break / continue: release what the loop body holds at this point, then go."""
        if not self.loops:
            self.err("E0017", f"'{word}' is only allowed inside a 'for' or 'while' loop", s)
        loop = self.loops[-1]
        if word == "break" and loop.get("par"):
            self.err("E0603", "'break' cannot leave a 'par for': its iterations run at the same time, so there is no "
                              "'rest of the loop' to skip", s, self.par_fix(loop["par"]))
        for pool in reversed(self.pools[loop["pools"]:]):
            for code, ty in reversed(pool):
                self.emit(self.types.release(ty, code) if ty is not None else f"@@{code}@@")
        for scope in reversed(self.scopes[loop["scopes"]:]):
            for v in reversed(list(scope.values())):
                if v.get("own"):
                    self.emit(self.types.release(v["ty"], v["c"]))
        if word == "continue" and loop["label"]:
            loop["used"] = True
            self.emit(f"goto {loop['label']};")
        else:
            self.emit(f"{word};")
        self.dead = True

    def s_break(self, s, is_tail):
        self.jump(s, "break")

    def s_continue(self, s, is_tail):
        self.jump(s, "continue")

    def loop_body(self, s, var, ty, label=None, extra=None):
        """The loop variable is borrowed from what is being walked. Returns True if the body used `label`
        (a `continue` in a range loop)."""
        self.scopes.append({})
        self.define(var, ty, False, None)
        if extra:
            self.define(extra[0], extra[1], False, None)
        loop = {"scopes": len(self.scopes), "pools": len(self.pools), "label": label, "used": False}
        self.loops.append(loop)
        try:
            self.loop_stmts(s)
        finally:
            self.loops.pop()
            self.scopes.pop()
        return loop["used"]

    def loop_stmts(self, s):
        if s["where"] is not None:
            self.depth += 1
            with self.region():
                try:
                    c = self.cond(s["where"])
                    if self.pools[-1]:
                        c = self.hoist(c, BOOL)
                        self.flush()
                except Diag as d:
                    self.report(d)
                    c = "0"
            self.emit(f"if ({c}) {{")
            with self.holding(self.facts(s["where"])[0], s["body"]):
                self.block(s["body"])
            self.emit("}")
            self.depth -= 1
        else:
            self.block(s["body"])

    def for_list(self, s):
        self.err("E0900", "looping over a list is not supported by this compiler yet", s)

    # ---------------------------------------------------------- functions and program

    @staticmethod
    def always_returns(stmts, in_loop=False):
        """True if the block ends in a value, a return or a fail on every path (and, for a handler inside a loop,
        a break or continue)."""
        if not stmts:
            return False
        last = stmts[-1]
        if last["k"] in ("return", "expr") or (in_loop and last["k"] in ("break", "continue")):
            return True
        if last["k"] == "if" and last["other"] is not None:
            return Core.always_returns(last["then"], in_loop) and Core.always_returns(last["other"], in_loop)
        return False

    def proto(self, f):
        sig = self.sig(f)
        params = ", ".join(f"{self.c(t)} v_{n} TOK_UNUSED" for n, t in sig["params"]) or "void"
        return f"static {'int' if sig['is_main'] else self.c(sig['cret'])} {f['c']}({params})"

    def compile_fn(self, f):
        self.mod, self.cur = f["mod"], f
        sig = self.sig(f)
        self.buf, self.depth, self.tmp = [], 0, 0
        self.pools, self.cautious, self.dead, self.bails, self.preps = [[]], False, False, 0, []
        self.sinks, self.loops = [None], []
        scope, t = {}, f["name_tok"]
        for (pname, pty), (_, _, ptok) in zip(sig["params"], f["params"]):
            if pname in scope:
                self.report(Diag("E0102", f"parameter '{pname}' is listed twice", ptok.line, ptok.col, ptok.end))
            scope[pname] = {"c": f"v_{pname}", "ty": pty, "mutable": False, "decl": None, "own": False}
        for (pname, _), owned in zip(sig["params"], sig["owned"]):
            scope[pname]["own"] = owned
        if sig["ret"] != VOID and not self.always_returns(f["body"]):
            self.report(Diag("E0105", f"'{f['name']}' returns {self.type_str(sig['ret'])}, but not every path ends in a value",
                             t.line, t.col, t.end))
        self.scopes = [scope]
        self.block(f["body"], tail=True)
        if sig["is_main"]:
            self.emit("  return 0;")
        elif sig["ret"] == VOID and sig["fallible"]:
            self.emit(f"  return (({self.c(sig['cret'])}){{0}});")
        f["c_body"] = self.buf

    def compile(self, need_main=True):
        done = 0
        while done < len(self.queue):
            self.compile_fn(self.queue[done])
            done += 1
        self.mod = self.cur = None
        main = self.entry.fns.get("main")
        takes_args = False
        if main is None:
            if need_main and not self.entry.broken:
                self.report(Diag("E0401", "the program has nothing to run: write statements at the top level, or add "
                                          "fn main() ! io", 1, 1), self.entry)
        else:
            sig, t = self.sig(main), main["name_tok"]
            takes_args = len(sig["params"]) == 1 and sig["params"][0][1] == ("list", STR)
            if sig["ret"] != VOID or (sig["params"] and not takes_args):
                self.report(Diag("E0402", "'main' takes no parameters, or one '[str]' parameter for the program "
                                          "arguments, and returns nothing", t.line, t.col, t.end), self.entry)
        if self.errors:
            return None
        protos = [self.proto(f) + ";" for f in self.queue]
        bodies = []
        for f in self.queue:
            bodies.append(self.proto(f) + " {")
            bodies.extend(f["c_body"])
            bodies.append("}")
            bodies.append("")
        lits = "\n".join(f'TOK_LIT({name}, "{c_str(text)}");' for text, name in self.lits.items())
        types = self.types.emit()
        mt = self.types.mt  # the program uses threads: only then is the threads runtime part of it
        net = self.net  # the program calls the net module: only then is the socket code part of it
        out = [runtime_mt.PRELUDE if mt else "", runtime_net.PRELUDE if net else "", runtime.PART1,
               runtime_mt.RUNTIME if mt else "", self.runtime_errors(),
               self.runtime_numeric(), types, runtime.PART2, runtime_net.RUNTIME if net else "", lits, "", "\n".join(protos), "", "\n".join(self.aux), "",
               "\n".join(bodies)]
        if main is not None:
            call = (f"  tok_T_Lstr args = tok_args(argc, argv);\n  int rc = {main['c']}(args);\n  tok_release_Lstr(args);\n"
                    if takes_args else f"  int rc = {main['c']}();\n")
            if mt:
                call = "  tok_mt_init();\n" + call + "  tok_mt_finish();\n"
            out.append("int main(int argc, char** argv) {\n  (void)argc;\n  (void)argv;\n" + call +
                       "#ifdef TOK_LEAK_CHECK\n  if (tok_live != 0) {\n    fflush(stdout);\n"
                       "    fprintf(stderr, \"tok: leak check: %lld block(s) still allocated at exit\\n\", (long long)tok_live);\n"
                       "    return 71;\n  }\n#endif\n  return rc;\n}")
        return "\n".join(out) + "\n"

    def runtime_errors(self):
        lits = "\n".join(f'TOK_LIT(tok_k{i}, "{c_str(k)}");' for i, k in enumerate(self.kinds))
        names = ", ".join(f"tok_k{i}.s" for i in range(len(self.kinds)))
        return (f"{lits}\nstatic const char* const tok_kinds[] = {{ {names} }};\n"
                "#define TOK_KIND_FS 1\n#define TOK_KIND_NET 2\n"
                "TOK_UNUSED static void tok_show_error(tok_sb* b, tok_error e) "
                "{ tok_sb_adds(b, tok_kinds[e.kind]); tok_sb_add(b, \": \", 2); tok_sb_adds(b, e.msg); }\n"
                "TOK_UNUSED static bool tok_error_eq(tok_error a, tok_error b) "
                "{ return a.kind == b.kind && tok_seq(a.msg, b.msg); }\n")

    def runtime_numeric(self):
        out = []
        for name, (ct, kind, _) in NUMS.items():
            if kind == "f":
                continue
            for fn, op, neg in (("div", "/", "tok_neg_int(a, loc)" if name == "int" else f"({ct})(0 - (uint64_t)a)"),
                                ("mod", "%", f"({ct})0")):
                guard = f" if (b == ({ct})-1) return {neg};" if kind == "i" else ""
                out.append(f"TOK_UNUSED static {ct} tok_{fn}_{name}({ct} a, {ct} b, const char* loc) "
                           f"{{ if (b == 0) tok_panic(loc, \"division by zero\");{guard} return ({ct})(a {op} b); }}")
        return "\n".join(out) + "\n"
