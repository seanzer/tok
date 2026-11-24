"""Core of the checker and C emitter.

One pass per function: type and effect check each statement and emit C for it.
Expressions return (c_code, type). Anything with a side effect, or that can
panic, is hoisted into a C temporary in source order, so the C expression that
comes back is always safe to place anywhere.

Language features live in mixins (lists, optionals and errors, methods,
modules, fixed-width numbers) that add `x_<kind>` expression handlers,
`s_<kind>` statement handlers, and entries in the builtin tables.
"""
import os

from .diag import Diag, make_fix
from .lexer import lex
from .parser import Parser
from .types import (ALIASES, BOOL, EKIND, ERR, ERRV, F64, INT, NEVER, NONE, NUMS, STR, VOID, TypeGen, int_range, is_float, is_int,
                    is_num, lossless, mangle)
from . import runtime


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


RESERVED_TYPES = {"bool": ("bool",), "str": ("str",), "Error": ERRV, "ErrorKind": EKIND}


class Module:
    def __init__(self, name, idx, path, src):
        self.name, self.idx, self.path, self.src = name, idx, path, src
        self.fns, self.types, self.failed = {}, {}, set()
        self.errors = {}  # declared error kinds: name -> index in the program's kind table
        self.broken = False  # had lex or parse errors


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
        self.sinks = [None]  # where the last expression of a block goes: None = the function result
        self.kinds = ["Failed", "FsError"]  # every error kind in the program; the first two are built in
        self.setup()
        for t in (("list", STR), ("opt", INT), ("opt", F64), ("res", STR), ("res", VOID)):
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
        for d in decls:
            d["mod"] = mod
            if d["k"] == "error":
                for t in d["names"]:
                    if t.val in mod.fns or t.val in mod.types or t.val in mod.errors or t.val in self.free_fns \
                            or t.val in self.kinds[:2] or t.val in RESERVED_TYPES:
                        self.report(Diag("E0102", f"'{t.val}' is already defined", t.line, t.col, t.end), mod)
                        continue
                    mod.errors[t.val] = len(self.kinds)
                    self.kinds.append(t.val)
                continue
            t = d["name_tok"]
            table = mod.fns if d["k"] == "fn" else mod.types
            if d["name"] in mod.fns or d["name"] in mod.types or d["name"] in self.free_fns or d["name"] in mod.errors or \
                    d["name"] in self.kinds[:2] or \
                    (d["k"] == "type" and (d["name"] in NUMS or d["name"] in ALIASES or d["name"] in RESERVED_TYPES)):
                self.report(Diag("E0102", f"'{d['name']}' is already defined", t.line, t.col, t.end), mod)
                continue
            table[d["name"]] = d
            if d["k"] == "fn":
                d["c"] = f"tok_f{mod.idx}_{d['name']}"
                self.queue.append(d)
        for d in mod.types.values():
            self.check_record(d)
        return mod

    def type_str(self, t):
        k = t[0]
        if k == "list":
            return f"[{self.type_str(t[1])}]"
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
        if k == "tlist":
            return ("list", self.resolve_type(tn["elem"], mod))
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
            f["sig"] = {"params": params, "ret": ret, "effects": [e for e in f["effects"] if e != "err"],
                        "fallible": fallible, "is_main": is_main,
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

    def hoist(self, code, ty):
        t = self.new_tmp()
        self.emit(f"{self.c(ty)} {t} TOK_UNUSED = {code};")
        return t

    def loc(self, n):
        return '"' + c_str(f"{os.path.basename(self.mod.path)}:{n['line']}") + '"'

    def lookup(self, name):
        for s in reversed(self.scopes):
            if name in s:
                return s[name]
        return None

    def define(self, name, ty, mutable=False, decl=None, volatile=False):
        self.scopes[-1][name] = {"c": f"v_{name}", "ty": ty, "mutable": mutable, "decl": decl, "volatile": volatile}

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
        return self.kinds.index(name) if name in self.kinds[:2] else None

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

    def fresh(self, n):
        """True if the value is newly built and shares no buffer with any binding."""
        k = n["k"]
        if k == "list":
            return all(self.fresh(i) or not self.types.is_heapy(i.get("ty", ERR)) for i in n["items"])
        if k == "rec":
            return all(self.fresh(e) or not self.types.is_heapy(e.get("ty", ERR)) for _, e, _ in n["fields"])
        return bool(n.get("fresh"))

    def var_rooted(self, n):
        k = n["k"]
        if k == "name":
            v = self.lookup(n["name"])
            return v is not None and (v["mutable"] or v["volatile"])
        if k in ("field", "index"):
            return self.var_rooted(n["base"])
        if k == "bin" and n["op"] == "or":
            return self.var_rooted(n["l"]) or self.var_rooted(n["r"])
        return False

    def store(self, code, ty, n, into_var):
        """Value semantics: copy when a list buffer would otherwise be shared with mutable storage."""
        if not self.types.is_heapy(ty) or self.fresh(n):
            return code
        if into_var or self.var_rooted(n):
            return self.types.copy(ty, code)
        return code

    def stored(self, n, want, into_var=False, msg=None):
        code, ty = self.val(n, want)
        code = self.store(code, ty, n, into_var)
        return self.coerce(code, ty, want, n, msg)

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
            return '"' + c_str("".join(p[1] for p in parts)) + '"', STR
        fmt, args = [], []
        for p in parts:
            if p[0] == "s":
                fmt.append(c_str(p[1], fmt=True))
            else:
                code, ty = self.val(p[1])
                if ty == VOID:
                    self.err("E0103", "this call returns nothing, so it cannot go inside a string", p[1])
                fmt.append("%s")
                args.append(self.types.to_str(ty, code))
        return self.hoist(f'tok_fmt("{"".join(fmt)}", {", ".join(args)})', STR), STR

    def x_name(self, n, want):
        v = self.lookup(n["name"])
        if v is None:
            if n["name"] in self.mod.failed:
                return "0", ERR
            hint = " (functions are called with parentheses)" if n["name"] in self.mod.fns or n["name"] in self.free_fns else ""
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
        arith = op in ("+", "-", "*", "/", "%")
        l, r = n["l"], n["r"]
        if self.is_literal(l) and not self.is_literal(r):
            rc, rt = self.val(r, want if arith else None)
            self.float_literal_with_int(op, l, r, rt)
            lc, lt = self.val(l, rt if is_num(rt) else None)
        else:
            lc, lt = self.val(l, want if arith else None)
            if self.is_literal(r):
                self.float_literal_with_int(op, r, l, lt)
            rc, rt = self.val(r, lt if is_num(lt) or r["k"] in ("none", "list") else None)
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
        with self.capture(1) as pre, self.holding(proven, n["r"]):
            rc, rt = self.val(n["r"])
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
                    return self.hoist(f"tok_concat({lc}, {rc})", STR), STR
                if op in ("<", "<=", ">", ">="):
                    return f"(strcmp({lc}, {rc}) {op} 0)", BOOL
            if is_num(lt):
                if op in ("<", "<=", ">", ">="):
                    return f"({lc} {op} {rc})", BOOL
                return self.arith(op, lc, rc, lt, n), lt
            what = "numbers" if op in ("-", "*", "/", "%") else "numbers or strings"
            self.err("E0103", f"'{op}' needs {what}, found {self.type_str(lt)}", n)
        if is_num(lt) and is_num(rt):
            self.err("E0106", f"'{op}' needs the same type on both sides, found {self.type_str(lt)} and {self.type_str(rt)}",
                     n, self.mix_fix(n, lt, rt))
        self.err("E0103", f"'{op}' needs the same type on both sides, found {self.type_str(lt)} and {self.type_str(rt)}", n)

    def mix_fix(self, n, lt, rt):
        return None

    def arith(self, op, a, b, ty, n):
        ct, kind, _ = NUMS[ty[0]]
        if kind == "f":
            if op == "%":
                self.err("E0103", f"'%' needs integers, found {ty[0]}", n)
            return f"({a} {op} {b})"
        return self.hoist(self.int_op(op, a, b, ty, n), ty)

    def int_op(self, op, a, b, ty, n):
        """Integer arithmetic as one C expression. `int` checks for overflow; the sized types wrap."""
        if op in ("/", "%"):
            return f"tok_{'div' if op == '/' else 'mod'}_{ty[0]}({a}, {b}, {self.loc(n)})"
        if ty == INT:
            return f"tok_{ {'+': 'add', '-': 'sub', '*': 'mul'}[op] }_int({a}, {b}, {self.loc(n)})"
        return f"(({NUMS[ty[0]][0]})((uint64_t)({a}) {op} (uint64_t)({b})))"

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
        args = []
        for an, (pn, pt) in zip(n["args"], sig["params"]):
            code, ty = self.val(an, pt)
            code = self.store(code, ty, an, False)
            args.append(self.coerce(code, ty, pt, an,
                                    f"argument '{pn}' of '{shown}' needs {self.type_str(pt)}, found {self.type_str(ty)}"))
        for eff in sig["effects"]:
            self.need_effect(eff, n, shown)
        call = f"{f['c']}({', '.join(args)})"
        if sig["cret"] == VOID:
            self.emit(call + ";")
            return "", VOID
        return self.hoist(call, sig["cret"]), sig["cret"]

    def sig_fix(self, eff):
        f = self.cur
        insert = f", {eff}" if f["effects"] else f" ! {eff}"
        return make_fix(f"add '{eff}' to the signature of '{f['name']}': insert \"{insert}\" at {f['line']}:{f['sig_end']}",
                        [(f["line"], f["sig_end"], insert)])

    def need_effect(self, eff, n, callee):
        f = self.cur
        if eff in f["effects"]:
            return
        self.err("E0201", f"'{callee}' needs effect '{eff}', but '{f['name']}' does not declare it", n, self.sig_fix(eff))

    def b_print(self, n, want):
        if len(n["args"]) != 1:
            self.err("E0104", f"print takes 1 argument, found {len(n['args'])}", n)
        code, ty = self.val(n["args"][0])
        if ty == VOID:
            self.err("E0103", "this call returns nothing, so there is nothing to print", n["args"][0])
        self.need_effect("io", n, "print")
        self.emit(f"puts({self.types.to_str(ty, code)});")
        return "", VOID

    def x_mcall(self, n, want):
        base = n["base"]
        if base["k"] == "name" and self.lookup(base["name"]) is None:
            return self.module_call(n, want)
        bc, bt = self.val(base)
        if bt == ERR:
            for a in n["args"]:
                if a["k"] != "lam":
                    self.expr(a)
            return "0", ERR
        h = self.methods.get((bt[0], n["name"]))
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
        given, inits = set(), []
        for fname, e, ftok in n["fields"]:
            if fname not in fields:
                raise Diag("E0111", f"{self.type_str(ty)} has no field '{fname}' (fields: {', '.join(fields)})",
                           ftok.line, ftok.col, ftok.end)
            if fname in given:
                raise Diag("E0111", f"field '{fname}' is given twice", ftok.line, ftok.col, ftok.end)
            given.add(fname)
            ft = fields[fname]
            code = self.stored(e, ft, False, None)
            inits.append(f".f_{fname} = {code}")
        missing = [fn for fn, ft in fields.items() if fn not in given and ft[0] != "opt" and ft != ERR]
        if missing:
            self.err("E0111", f"{self.type_str(ty)} is missing field(s): {', '.join(missing)} "
                              f"(only optional fields may be left out)", n)
        return (f"(({self.c(ty)}){{{', '.join(inits)}}})" if inits else f"(({self.c(ty)}){{0}})"), ty

    # ---------------------------------------------------------- statements

    def block(self, stmts, tail=False):
        self.scopes.append({})
        self.depth += 1
        pushed = 0
        for i, s in enumerate(stmts):
            try:
                self.stmt(s, tail and i == len(stmts) - 1)
            except Diag as d:
                self.report(d)
            # `if x == none` + return: x has a value in the statements that follow
            after = self.facts_after(s)
            if after:
                self.holding(after, stmts[i + 1:]).__enter__()
                pushed += 1
        del self.narrowed[len(self.narrowed) - pushed:]
        self.depth -= 1
        self.scopes.pop()

    def stmt(self, s, is_tail=False):
        h = getattr(self, "s_" + s["k"], None)
        if h is None:
            self.err("E0900", "this statement is not supported by this compiler yet", s)
        h(s, is_tail)

    def s_bind(self, s, is_tail):
        name = s["name"]
        old = self.lookup(name)
        if old is not None:
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
            code = self.store(code, ty, s["val"], s["mutable"])
            if want is not None:
                code = self.coerce(code, ty, want, s["val"],
                                   f"'{name}' is declared {self.type_str(want)}, but the value is {self.type_str(ty)}")
                ty = want
            elif ty == NONE:
                self.err("E0108", f"cannot tell what type 'none' is here; annotate it, for example '{name}: int? = none'", s["val"])
        except Diag:
            self.define(name, want or ERR, s["mutable"], s)
            raise
        self.define(name, ty, s["mutable"], s)
        if ty != ERR:
            self.emit(f"{self.c(ty)} v_{name} TOK_UNUSED = {code};")

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
        lv, ty, v, root = self.place(target)
        self.need_mutable(v, root, target, "cannot be reassigned" if target["k"] == "name" else "cannot be changed in place")
        if ty == ERR:
            self.expr(val)
            return
        if op == "=":
            what = f"'{root}'" if target["k"] == "name" else "this location"
            code, vt = self.val(val, ty)
            code = self.store(code, vt, val, True)
            code = self.coerce(code, vt, ty, val, f"{what} is {self.type_str(ty)}, cannot assign {self.type_str(vt)}")
        else:
            rc, rt = self.val(val, ty if is_num(ty) else None)
            code, _ = self.binop(op[0], lv, ty, rc, rt, {**s, "l": target, "r": val})
        self.emit(f"{lv} = {code};")

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
            code = self.store(code, ty, s["val"], False)
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
        code = self.store(code, ty, n, False) if n["k"] != "name" else code
        code = self.coerce(code, ty, sig["ret"], n, msg)
        if sig["ret"] == ERR or ty == ERR:
            return
        if sig["fallible"]:
            self.emit(f"return (({self.c(sig['cret'])}){{.v = {code}}});")
        else:
            self.emit(f"return {code};")

    def emit_return_void(self):
        sig = self.sig(self.cur)
        self.emit(f"return (({self.c(sig['cret'])}){{0}});" if sig["fallible"] else "return;")

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
        self.emit(f"if ({code}) {{")
        with self.holding(when_true, s["then"]):
            self.block(s["then"], is_tail)
        if s["other"] is not None:
            self.emit("} else {")
            with self.holding(when_false, s["other"]):
                self.block(s["other"], is_tail)
        self.emit("}")

    def s_for(self, s, is_tail):
        if s["hi"] is None:
            return self.for_list(s)
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
        tlo, thi = self.hoist(lc, ty), self.hoist(hc, ty)
        v = f"v_{s['var']}"
        self.emit(f"for ({self.c(ty)} {v} = {tlo}; {v} <= {thi}; {v}++) {{")
        self.loop_body(s, s["var"], ty, False)
        self.emit(f"  if ({v} == {thi}) break;")
        self.emit("}")

    def loop_body(self, s, var, ty, volatile):
        self.scopes.append({})
        self.define(var, ty, False, None, volatile)
        if s["where"] is not None:
            self.depth += 1
            try:
                c = self.cond(s["where"])
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
        self.scopes.pop()

    def for_list(self, s):
        self.err("E0900", "looping over a list is not supported by this compiler yet", s)

    # ---------------------------------------------------------- functions and program

    @staticmethod
    def always_returns(stmts):
        if not stmts:
            return False
        last = stmts[-1]
        if last["k"] in ("return", "expr"):
            return True
        if last["k"] == "if" and last["other"] is not None:
            return Core.always_returns(last["then"]) and Core.always_returns(last["other"])
        return False

    def proto(self, f):
        sig = self.sig(f)
        params = ", ".join(f"{self.c(t)} v_{n} TOK_UNUSED" for n, t in sig["params"]) or "void"
        return f"static {self.c(sig['cret'])} {f['c']}({params})"

    def compile_fn(self, f):
        self.mod, self.cur = f["mod"], f
        sig = self.sig(f)
        self.buf, self.depth, self.tmp = [], 0, 0
        self.sinks = [None]
        scope, t = {}, f["name_tok"]
        for (pname, pty), (_, _, ptok) in zip(sig["params"], f["params"]):
            if pname in scope:
                self.report(Diag("E0102", f"parameter '{pname}' is listed twice", ptok.line, ptok.col, ptok.end))
            scope[pname] = {"c": f"v_{pname}", "ty": pty, "mutable": False, "decl": None, "volatile": False}
        if sig["ret"] != VOID and not self.always_returns(f["body"]):
            self.report(Diag("E0105", f"'{f['name']}' returns {self.type_str(sig['ret'])}, but not every path ends in a value",
                             t.line, t.col, t.end))
        self.scopes = [scope]
        self.block(f["body"], tail=True)
        if sig["ret"] == VOID and sig["fallible"]:
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
                self.report(Diag("E0401", "no 'main' function; add: fn main() ! io", 1, 1), self.entry)
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
        out = [runtime.PART1, self.runtime_errors(), self.runtime_numeric(), self.types.emit(), runtime.PART2, "\n".join(protos), "", "\n".join(bodies)]
        if main is not None:
            arg = "tok_args(argc, argv)" if takes_args else ""
            out.append(f"int main(int argc, char** argv) {{\n  (void)argc;\n  (void)argv;\n  {main['c']}({arg});\n  return 0;\n}}")
        return "\n".join(out) + "\n"

    def runtime_errors(self):
        names = ", ".join('"' + c_str(k) + '"' for k in self.kinds)
        return (f"static const char* const tok_kinds[] = {{ {names} }};\n"
                "#define TOK_KIND_FS 1\n"
                "TOK_UNUSED static const char* tok_kind_name(int32_t k) { return tok_kinds[k]; }\n"
                "TOK_UNUSED static const char* tok_error_str(tok_error e) "
                "{ return tok_fmt(\"%s: %s\", tok_kinds[e.kind], e.msg); }\n"
                "TOK_UNUSED static bool tok_error_eq(tok_error a, tok_error b) "
                "{ return a.kind == b.kind && strcmp(a.msg, b.msg) == 0; }\n")

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
