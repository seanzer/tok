#!/usr/bin/env python3
"""tokc - walking-skeleton compiler for Tok (placeholder name).

Pipeline: source -> tokens -> AST -> check (types + effects) -> C -> cc -> native binary.

This is a bootstrap compiler. It covers a small core of the language so the
whole pipeline exists end to end; see README.md for what is and is not here.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

KEYWORDS = {"fn", "var", "if", "else", "for", "in", "return", "and", "or", "not", "true", "false"}
EFFECTS = {"io", "net", "fs", "time", "rand", "py"}
TWO_CHAR = {"->", "..", "==", "!=", "<=", ">="}
ONE_CHAR = set("()+-*/%<>=!,:")


class Diag(Exception):
    """A structured diagnostic: data first, prose second."""

    def __init__(self, code, message, line, col, end_col=None, fix=None):
        super().__init__(message)
        self.code, self.message, self.line, self.col = code, message, line, col
        self.end_col = end_col if end_col is not None else col + 1
        self.fix = fix  # {"line", "col", "insert", "note"} or None

    def to_json(self, path):
        d = {"code": self.code, "message": self.message, "file": path,
             "line": self.line, "col": self.col, "end_col": self.end_col}
        if self.fix:
            d["fix"] = self.fix
        return d

    def to_text(self, path, src_lines):
        out = [f"{path}:{self.line}:{self.col}: error[{self.code}]: {self.message}"]
        if 1 <= self.line <= len(src_lines):
            text = src_lines[self.line - 1]
            out.append("  " + text)
            out.append("  " + " " * (self.col - 1) + "^" * max(1, self.end_col - self.col))
        if self.fix:
            out.append(f"  fix: {self.fix['note']}")
        return "\n".join(out)


# ---------------------------------------------------------------- lexer

class Tok:
    __slots__ = ("kind", "val", "line", "col", "end")

    def __init__(self, kind, val, line, col, end):
        self.kind, self.val, self.line, self.col, self.end = kind, val, line, col, end

    def __repr__(self):
        return f"{self.kind}:{self.val!r}@{self.line}:{self.col}"


def lex_fragment(text, line, col0=1):
    """Tokenize one line (or one interpolation fragment). Columns are 1-based."""
    toks, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        col = col0 + i
        if c in " \t":
            i += 1
        elif text.startswith("//", i):
            break
        elif c.isdigit():
            j = i
            while j < n and text[j].isdigit():
                j += 1
            toks.append(Tok("INT", int(text[i:j]), line, col, col0 + j))
            i = j
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            toks.append(Tok("NAME", text[i:j], line, col, col0 + j))
            i = j
        elif c == '"':
            parts, buf, j = [], [], i + 1
            while True:
                if j >= n:
                    raise Diag("E0003", "unterminated string literal", line, col, col0 + n)
                ch = text[j]
                if ch == '"':
                    break
                if ch == "\\":
                    esc = text[j + 1] if j + 1 < n else ""
                    if esc not in 'nt"\\{}':
                        raise Diag("E0004", f"unknown escape '\\{esc}'", line, col0 + j, col0 + j + 2)
                    buf.append({"n": "\n", "t": "\t"}.get(esc, esc))
                    j += 2
                elif ch == "{":
                    k = text.find("}", j)
                    if k < 0:
                        raise Diag("E0005", "unclosed '{' in string interpolation", line, col0 + j)
                    if buf:
                        parts.append(("s", "".join(buf)))
                        buf = []
                    parts.append(("e", text[j + 1:k], col0 + j + 1))
                    j = k + 1
                else:
                    buf.append(ch)
                    j += 1
            if buf or not parts:
                parts.append(("s", "".join(buf)))
            toks.append(Tok("STR", parts, line, col, col0 + j + 1))
            i = j + 1
        elif text[i:i + 2] in TWO_CHAR:
            toks.append(Tok("OP", text[i:i + 2], line, col, col + 2))
            i += 2
        elif c in ONE_CHAR:
            toks.append(Tok("OP", c, line, col, col + 1))
            i += 1
        else:
            raise Diag("E0001", f"unexpected character {c!r}", line, col)
    return toks


def lex(src):
    toks, indents = [], [0]
    lines = src.split("\n")
    for ln, text in enumerate(lines, 1):
        line_toks = lex_fragment(text, ln)
        if not line_toks:
            continue
        lead = text[:len(text) - len(text.lstrip(" \t"))]
        if "\t" in lead:
            raise Diag("E0002", "tabs are not allowed for indentation; use spaces", ln, 1, len(lead) + 1)
        ind = len(lead)
        if ind > indents[-1]:
            indents.append(ind)
            toks.append(Tok("INDENT", None, ln, 1, ind + 1))
        while ind < indents[-1]:
            indents.pop()
            toks.append(Tok("DEDENT", None, ln, 1, ind + 1))
        if ind != indents[-1]:
            raise Diag("E0002", "indentation does not match any enclosing block", ln, 1, ind + 1)
        toks.extend(line_toks)
        toks.append(Tok("NEWLINE", None, ln, len(text) + 1, len(text) + 1))
    last = len(lines)
    while len(indents) > 1:
        indents.pop()
        toks.append(Tok("DEDENT", None, last, 1, 1))
    toks.append(Tok("EOF", None, last, 1, 1))
    return toks


# ---------------------------------------------------------------- parser
# AST nodes are plain dicts: {"k": kind, "line":, "col":, "end":, ...}

def node(k, tok, **kw):
    d = {"k": k, "line": tok.line, "col": tok.col, "end": tok.end}
    d.update(kw)
    return d


class Parser:
    def __init__(self, toks):
        self.toks, self.i = toks, 0

    @property
    def t(self):
        return self.toks[self.i]

    def at(self, kind, val=None):
        t = self.t
        return t.kind == kind and (val is None or t.val == val)

    def eat(self, kind, val=None):
        if self.at(kind, val):
            self.i += 1
            return self.toks[self.i - 1]
        return None

    def expect(self, kind, val=None, what=None):
        t = self.eat(kind, val)
        if t is None:
            cur = self.t
            found = cur.kind.lower() if cur.val is None else repr(cur.val if cur.kind != "STR" else "string")
            raise Diag("E0010", f"expected {what or val or kind.lower()}, found {found}", cur.line, cur.col, cur.end)
        return t

    def program(self):
        fns = []
        while not self.at("EOF"):
            fns.append(self.fn_decl())
        return fns

    def type_name(self):
        t = self.expect("NAME", what="a type (int, str, bool)")
        if t.val not in ("int", "str", "bool"):
            raise Diag("E0011", f"unknown type '{t.val}' (this compiler knows int, str, bool)", t.line, t.col, t.end)
        return t.val

    def fn_decl(self):
        start = self.expect("NAME", "fn", what="'fn'")
        name = self.expect("NAME", what="a function name")
        if name.val in KEYWORDS:
            raise Diag("E0012", f"'{name.val}' is a keyword and cannot be a function name", name.line, name.col, name.end)
        self.expect("OP", "(")
        params = []
        while not self.at("OP", ")"):
            p = self.expect("NAME", what="a parameter name")
            self.expect("OP", ":", what="':' and a type for the parameter")
            params.append((p.val, self.type_name()))
            if not self.eat("OP", ","):
                break
        self.expect("OP", ")")
        ret = self.type_name() if self.eat("OP", "->") else None
        effects = []
        if self.eat("OP", "!"):
            while True:
                e = self.expect("NAME", what="an effect name")
                if e.val not in EFFECTS:
                    raise Diag("E0013", f"unknown effect '{e.val}' (known: {', '.join(sorted(EFFECTS))})", e.line, e.col, e.end)
                effects.append(e.val)
                if not self.eat("OP", ","):
                    break
        sig_end = self.toks[self.i - 1].end
        body = self.block()
        return node("fn", start, name=name.val, name_tok=name, params=params, ret=ret,
                    effects=effects, sig_end=sig_end, body=body)

    def block(self):
        self.expect("NEWLINE", what="end of line")
        self.expect("INDENT", what="an indented block")
        stmts = []
        while not self.at("DEDENT"):
            stmts.append(self.stmt())
        self.expect("DEDENT")
        return stmts

    def stmt(self):
        t = self.t
        if self.eat("NAME", "if"):
            return self.if_rest(t)
        if self.eat("NAME", "for"):
            v = self.expect("NAME", what="a loop variable")
            self.expect("NAME", "in", what="'in'")
            lo = self.expr()
            self.expect("OP", "..", what="'..' (ranges are inclusive, like 1..10)")
            hi = self.expr()
            return node("for", t, var=v.val, lo=lo, hi=hi, body=self.block())
        if self.eat("NAME", "return"):
            val = None if self.at("NEWLINE") else self.expr()
            self.expect("NEWLINE", what="end of line")
            return node("return", t, val=val)
        if self.eat("NAME", "var"):
            name = self.expect("NAME", what="a variable name")
            self.expect("OP", "=", what="'='")
            val = self.expr()
            self.expect("NEWLINE", what="end of line")
            return node("bind", name, name=name.val, val=val, mutable=True)
        if t.kind == "NAME" and t.val not in KEYWORDS and self.toks[self.i + 1].kind == "OP" and self.toks[self.i + 1].val == "=":
            self.i += 2
            val = self.expr()
            self.expect("NEWLINE", what="end of line")
            return node("bind", t, name=t.val, val=val, mutable=False)
        e = self.expr()
        self.expect("NEWLINE", what="end of line")
        return node("expr", t, val=e)

    def if_rest(self, t):
        cond = self.expr()
        then = self.block()
        other = None
        if self.at("NAME", "else"):
            et = self.eat("NAME", "else")
            if self.at("NAME", "if"):
                it = self.eat("NAME", "if")
                other = [self.if_rest(it)]
            else:
                other = self.block()
            del et
        return node("if", t, cond=cond, then=then, other=other)

    # precedence climbing
    LEVELS = [("or",), ("and",), ("==", "!=", "<", "<=", ">", ">="), ("+", "-"), ("*", "/", "%")]

    def expr(self, level=0):
        if level == len(self.LEVELS):
            return self.unary()
        left = self.expr(level + 1)
        while self.t.kind in ("OP", "NAME") and self.t.val in self.LEVELS[level]:
            op = self.t
            self.i += 1
            right = self.expr(level + 1)
            left = {"k": "bin", "op": op.val, "l": left, "r": right,
                    "line": left["line"], "col": left["col"], "end": right["end"]}
        return left

    def unary(self):
        t = self.t
        if self.eat("OP", "-") or self.eat("NAME", "not"):
            e = self.unary()
            return {"k": "un", "op": t.val, "e": e, "line": t.line, "col": t.col, "end": e["end"]}
        return self.atom()

    def atom(self):
        t = self.t
        if self.eat("INT"):
            return node("int", t, v=t.val)
        if self.eat("STR"):
            parts = []
            for p in t.val:
                if p[0] == "s":
                    parts.append(("s", p[1]))
                else:
                    sub = lex_fragment(p[1], t.line, p[2])
                    if not sub:
                        raise Diag("E0014", "empty '{}' in string; write \\{ for a literal brace", t.line, p[2] - 1, p[2] + 1)
                    sub.append(Tok("EOF", None, t.line, sub[-1].end, sub[-1].end))
                    sp = Parser(sub)
                    e = sp.expr()
                    if not sp.at("EOF"):
                        raise Diag("E0010", "unexpected text inside string interpolation", sp.t.line, sp.t.col, sp.t.end)
                    parts.append(("e", e))
            return node("str", t, parts=parts)
        if self.eat("OP", "("):
            e = self.expr()
            self.expect("OP", ")")
            return e
        if t.kind == "NAME":
            if t.val in ("true", "false"):
                self.i += 1
                return node("bool", t, v=t.val == "true")
            if t.val in KEYWORDS:
                raise Diag("E0010", f"expected an expression, found keyword '{t.val}'", t.line, t.col, t.end)
            self.i += 1
            if self.eat("OP", "("):
                args = []
                while not self.at("OP", ")"):
                    args.append(self.expr())
                    if not self.eat("OP", ","):
                        break
                close = self.expect("OP", ")")
                n = node("call", t, name=t.val, args=args)
                n["end"] = close.end
                return n
            return node("name", t, name=t.val)
        found = t.kind.lower() if t.val is None else repr(t.val)
        raise Diag("E0010", f"expected an expression, found {found}", t.line, t.col, t.end)


# ---------------------------------------------------------------- check + C emission

C_TYPES = {"int": "int64_t", "str": "const char*", "bool": "bool", None: "void"}

RUNTIME = r'''// Generated by tokc. Do not edit.
#include <stdarg.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// Bootstrap runtime: formatted strings are heap-allocated and never freed.
// The real memory model (value semantics + compile-time RC) is not built yet.
static inline const char* tok_fmt(const char* fmt, ...) {
  va_list a, b;
  va_start(a, fmt);
  va_copy(b, a);
  int n = vsnprintf(NULL, 0, fmt, a);
  va_end(a);
  char* s = (char*)malloc((size_t)n + 1);
  if (!s) { fputs("tok: out of memory\n", stderr); exit(70); }
  vsnprintf(s, (size_t)n + 1, fmt, b);
  va_end(b);
  return s;
}
static inline void tok_div_check(int64_t d) {
  if (d == 0) { fputs("tok: panic: division by zero\n", stderr); exit(70); }
}
static inline int64_t tok_div(int64_t a, int64_t b) { tok_div_check(b); return a / b; }
static inline int64_t tok_mod(int64_t a, int64_t b) { tok_div_check(b); return a % b; }
'''


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
        elif ch == "%" and fmt:
            out.append("%%")
        elif ch == "?":
            out.append("\\?")  # avoid C trigraphs
        elif ord(ch) < 32:
            out.append("\\%03o" % ord(ch))
        else:
            out.append(ch)
    return "".join(out)


def fmt_arg(code, ty):
    if ty == "int":
        return "%lld", f"(long long)({code})"
    if ty == "bool":
        return "%s", f"(({code}) ? \"true\" : \"false\")"
    return "%s", code


class Compiler:
    def __init__(self, fns):
        self.fns = {}
        for f in fns:
            if f["name"] in self.fns or f["name"] == "print":
                t = f["name_tok"]
                raise Diag("E0102", f"function '{f['name']}' is already defined", t.line, t.col, t.end)
            self.fns[f["name"]] = f
        self.order = fns
        self.out = []
        self.cur = None
        self.scopes = []
        self.tmp = 0

    def err(self, code, msg, n, fix=None):
        raise Diag(code, msg, n["line"], n["col"], n["end"], fix)

    def lookup(self, name):
        for s in reversed(self.scopes):
            if name in s:
                return s[name]
        return None

    def need_effect(self, eff, n, callee):
        f = self.cur
        if eff in f["effects"]:
            return
        insert = f", {eff}" if f["effects"] else f" ! {eff}"
        fix = {"line": f["line"], "col": f["sig_end"], "insert": insert,
               "note": f"add '{eff}' to the signature of '{f['name']}': insert \"{insert}\" at {f['line']}:{f['sig_end']}"}
        self.err("E0201", f"'{callee}' needs effect '{eff}', but '{f['name']}' does not declare it", n, fix)

    # expressions return (c_code, type)
    def expr(self, n):
        k = n["k"]
        if k == "int":
            return f"INT64_C({n['v']})", "int"
        if k == "bool":
            return ("true" if n["v"] else "false"), "bool"
        if k == "str":
            parts = n["parts"]
            if all(p[0] == "s" for p in parts):
                return '"' + c_str("".join(p[1] for p in parts)) + '"', "str"
            fmt, args = [], []
            for p in parts:
                if p[0] == "s":
                    fmt.append(c_str(p[1], fmt=True))
                else:
                    code, ty = self.expr(p[1])
                    if ty is None:
                        self.err("E0103", "this call returns nothing, so it cannot go inside a string", p[1])
                    spec, arg = fmt_arg(code, ty)
                    fmt.append(spec)
                    args.append(arg)
            return f'tok_fmt("{"".join(fmt)}", {", ".join(args)})', "str"
        if k == "name":
            v = self.lookup(n["name"])
            if v is None:
                hint = " (functions are called with parentheses)" if n["name"] in self.fns else ""
                self.err("E0101", f"unknown name '{n['name']}'{hint}", n)
            return v["c"], v["ty"]
        if k == "un":
            code, ty = self.expr(n["e"])
            want = "int" if n["op"] == "-" else "bool"
            if ty != want:
                self.err("E0103", f"'{n['op']}' needs {want}, found {ty or 'nothing'}", n["e"])
            return (f"(-{code})" if n["op"] == "-" else f"(!{code})"), want
        if k == "bin":
            return self.binop(n)
        if k == "call":
            return self.call(n)
        raise AssertionError(k)

    def binop(self, n):
        op = n["op"]
        lc, lt = self.expr(n["l"])
        rc, rt = self.expr(n["r"])
        if lt is None or rt is None:
            self.err("E0103", f"'{op}' cannot use a call that returns nothing", n["l"] if lt is None else n["r"])
        if op in ("and", "or"):
            for side, ty in ((n["l"], lt), (n["r"], rt)):
                if ty != "bool":
                    self.err("E0103", f"'{op}' needs bool on both sides, found {ty}", side)
            return f"({lc} {'&&' if op == 'and' else '||'} {rc})", "bool"
        if lt != rt:
            self.err("E0103", f"'{op}' needs the same type on both sides, found {lt} and {rt}", n)
        if op in ("==", "!="):
            if lt == "str":
                return f"(strcmp({lc}, {rc}) {op} 0)", "bool"
            return f"({lc} {op} {rc})", "bool"
        if op == "+" and lt == "str":
            return f'tok_fmt("%s%s", {lc}, {rc})', "str"
        if lt != "int":
            self.err("E0103", f"'{op}' needs int on both sides, found {lt}", n)
        if op in ("<", "<=", ">", ">="):
            return f"({lc} {op} {rc})", "bool"
        if op == "/":
            return f"tok_div({lc}, {rc})", "int"
        if op == "%":
            return f"tok_mod({lc}, {rc})", "int"
        return f"({lc} {op} {rc})", "int"

    def call(self, n):
        name = n["name"]
        args = [self.expr(a) for a in n["args"]]
        if name == "print":
            if len(args) != 1:
                self.err("E0104", f"print takes 1 argument, found {len(args)}", n)
            code, ty = args[0]
            if ty is None:
                self.err("E0103", "this call returns nothing, so there is nothing to print", n["args"][0])
            self.need_effect("io", n, "print")
            if ty == "str":
                return f"(void)puts({code})", None
            spec, arg = fmt_arg(code, ty)
            return f'(void)printf("{spec}\\n", {arg})', None
        f = self.fns.get(name)
        if f is None:
            self.err("E0101", f"unknown function '{name}'", n)
        if len(args) != len(f["params"]):
            self.err("E0104", f"'{name}' takes {len(f['params'])} argument(s), found {len(args)}", n)
        for (code, ty), (pn, pt), an in zip(args, f["params"], n["args"]):
            if ty != pt:
                self.err("E0103", f"argument '{pn}' of '{name}' needs {pt}, found {ty or 'nothing'}", an)
        for eff in f["effects"]:
            self.need_effect(eff, n, name)
        return f"tok_fn_{name}({', '.join(c for c, _ in args)})", f["ret"]

    def emit(self, depth, text):
        self.out.append("  " * depth + text)

    def block(self, stmts, depth, tail_returns=False):
        self.scopes.append({})
        for i, s in enumerate(stmts):
            self.stmt(s, depth, is_tail=tail_returns and i == len(stmts) - 1)
        self.scopes.pop()

    def stmt(self, s, depth, is_tail=False):
        k = s["k"]
        f = self.cur
        if k == "bind":
            name = s["name"]
            code, ty = self.expr(s["val"])
            if ty is None:
                self.err("E0103", "this call returns nothing, so it cannot be bound to a name", s["val"])
            old = self.lookup(name)
            if old is not None:
                if s["mutable"]:
                    self.err("E0302", f"'{name}' is already defined; drop 'var' to assign to it", s)
                if not old["mutable"]:
                    fix = None
                    if old["decl"]:
                        d = old["decl"]
                        fix = {"line": d["line"], "col": d["col"], "insert": "var ",
                               "note": f"declare it mutable: insert \"var \" at {d['line']}:{d['col']}"}
                    self.err("E0301", f"'{name}' is immutable and cannot be reassigned", s, fix)
                if old["ty"] != ty:
                    self.err("E0103", f"'{name}' is {old['ty']}, cannot assign {ty}", s["val"])
                self.emit(depth, f"{old['c']} = {code};")
            else:
                if name in self.fns or name == "print":
                    self.err("E0102", f"'{name}' is a function and cannot be used as a variable name", s)
                cname = f"v_{name}"
                self.scopes[-1][name] = {"c": cname, "ty": ty, "mutable": s["mutable"], "decl": s}
                self.emit(depth, f"{C_TYPES[ty]} {cname} = {code};")
        elif k == "expr":
            code, ty = self.expr(s["val"])
            if is_tail and f["ret"] is not None:
                if ty != f["ret"]:
                    self.err("E0103", f"'{f['name']}' returns {f['ret']}, but its last expression is {ty or 'nothing'}", s["val"])
                self.emit(depth, f"return {code};")
            else:
                self.emit(depth, f"{code};")
        elif k == "return":
            if s["val"] is None:
                if f["ret"] is not None:
                    self.err("E0103", f"'{f['name']}' returns {f['ret']}, but this return has no value", s)
                self.emit(depth, "return;")
            else:
                code, ty = self.expr(s["val"])
                if ty != f["ret"]:
                    self.err("E0103", f"'{f['name']}' returns {f['ret'] or 'nothing'}, found {ty or 'nothing'}", s["val"])
                self.emit(depth, f"return {code};")
        elif k == "if":
            code, ty = self.expr(s["cond"])
            if ty != "bool":
                self.err("E0103", f"an 'if' condition needs bool, found {ty or 'nothing'}", s["cond"])
            self.emit(depth, f"if ({code}) {{")
            self.block(s["then"], depth + 1, tail_returns=is_tail)
            if s["other"] is not None:
                self.emit(depth, "} else {")
                self.block(s["other"], depth + 1, tail_returns=is_tail)
            self.emit(depth, "}")
        elif k == "for":
            lo, lt = self.expr(s["lo"])
            hi, ht = self.expr(s["hi"])
            for side, ty in ((s["lo"], lt), (s["hi"], ht)):
                if ty != "int":
                    self.err("E0103", f"range bounds need int, found {ty or 'nothing'}", side)
            if self.lookup(s["var"]) is not None:
                self.err("E0102", f"loop variable '{s['var']}' is already defined in this function", s)
            self.tmp += 1
            end = f"end_{self.tmp}"
            cname = f"v_{s['var']}"
            self.emit(depth, f"for (int64_t {cname} = {lo}, {end} = {hi}; {cname} <= {end}; {cname}++) {{")
            self.scopes.append({s["var"]: {"c": cname, "ty": "int", "mutable": False, "decl": None}})
            self.block(s["body"], depth + 1)
            self.scopes.pop()
            self.emit(depth, "}")

    @staticmethod
    def always_returns(stmts):
        if not stmts:
            return False
        last = stmts[-1]
        if last["k"] in ("return", "expr"):
            return True
        if last["k"] == "if" and last["other"] is not None:
            return Compiler.always_returns(last["then"]) and Compiler.always_returns(last["other"])
        return False

    def proto(self, f):
        params = ", ".join(f"{C_TYPES[t]} v_{n}" for n, t in f["params"]) or "void"
        return f"static {C_TYPES[f['ret']]} tok_fn_{f['name']}({params})"

    def compile(self, need_main=True):
        main = self.fns.get("main")
        if need_main:
            if main is None:
                raise Diag("E0401", "no 'main' function; add: fn main() ! io", 1, 1)
            if main["params"] or main["ret"] is not None:
                t = main["name_tok"]
                raise Diag("E0402", "'main' takes no parameters and returns nothing in this compiler", t.line, t.col, t.end)
        self.out = [RUNTIME]
        for f in self.order:
            self.out.append(self.proto(f) + ";")
        self.out.append("")
        for f in self.order:
            self.cur = f
            seen = set()
            scope = {}
            for n, t in f["params"]:
                if n in seen:
                    self.err("E0102", f"parameter '{n}' is listed twice", {"line": f["line"], "col": f["col"], "end": f["sig_end"]})
                seen.add(n)
                scope[n] = {"c": f"v_{n}", "ty": t, "mutable": False, "decl": None}
            if f["ret"] is not None and not self.always_returns(f["body"]):
                t = f["name_tok"]
                raise Diag("E0105", f"'{f['name']}' returns {f['ret']}, but not every path ends in a value", t.line, t.col, t.end)
            self.out.append(self.proto(f) + " {")
            self.scopes = [scope]
            self.block(f["body"], 1, tail_returns=True)
            self.out.append("}")
            self.out.append("")
        if main is not None:
            self.out.append("int main(void) {\n  tok_fn_main();\n  return 0;\n}")
        return "\n".join(self.out) + "\n"


def compile_source(src, need_main=True):
    return Compiler(Parser(lex(src)).program()).compile(need_main)


# ---------------------------------------------------------------- driver

def cc_build(c_code, out_path, optimize):
    cc = os.environ.get("CC", "cc")
    with tempfile.TemporaryDirectory() as d:
        c_path = os.path.join(d, "out.c")
        with open(c_path, "w") as fh:
            fh.write(c_code)
        cmd = [cc, "-std=c11", "-O2" if optimize else "-O0", "-o", out_path, c_path]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            sys.stderr.write("tokc: internal error: the generated C did not compile. This is a compiler bug.\n")
            sys.stderr.write(r.stderr)
            sys.exit(70)


def apply_fix(src, fix):
    lines = src.split("\n")
    text = lines[fix["line"] - 1]
    c = fix["col"] - 1
    lines[fix["line"] - 1] = text[:c] + fix["insert"] + text[c:]
    return "\n".join(lines)


USAGE = """usage:
  tokc run <file.tok>              compile fast (-O0, cached) and run
  tokc build <file.tok> [-o out]   compile optimized (-O2) to a native binary
  tokc check <file.tok> [--json]   type and effect check only
  tokc fix <file.tok>              apply suggested fixes until the file checks
  tokc emit-c <file.tok>           print the generated C
"""


def main(argv):
    if len(argv) < 2 or argv[0] not in ("run", "build", "check", "fix", "emit-c"):
        sys.stderr.write(USAGE)
        return 64
    cmd, path, rest = argv[0], argv[1], argv[2:]
    try:
        with open(path) as fh:
            src = fh.read()
    except OSError as e:
        sys.stderr.write(f"tokc: cannot read {path}: {e.strerror}\n")
        return 66
    as_json = "--json" in rest

    if cmd == "fix":
        applied = 0
        for _ in range(50):
            try:
                compile_source(src)
                break
            except Diag as d:
                if not d.fix:
                    sys.stderr.write(d.to_text(path, src.split("\n")) + "\n")
                    sys.stderr.write(f"tokc: applied {applied} fix(es); this error has no automatic fix\n")
                    if applied:
                        with open(path, "w") as fh:
                            fh.write(src)
                    return 1
                src = apply_fix(src, d.fix)
                applied += 1
                print(f"fixed {d.code} at {path}:{d.line}: {d.fix['note']}")
        with open(path, "w") as fh:
            fh.write(src)
        print(f"{path}: ok ({applied} fix(es) applied)")
        return 0

    try:
        c_code = compile_source(src)
    except Diag as d:
        if as_json:
            print(json.dumps({"ok": False, "errors": [d.to_json(path)]}))
        else:
            sys.stderr.write(d.to_text(path, src.split("\n")) + "\n")
        return 1

    if cmd == "check":
        print(json.dumps({"ok": True, "errors": []}) if as_json else f"{path}: ok")
        return 0
    if cmd == "emit-c":
        sys.stdout.write(c_code)
        return 0
    if cmd == "build":
        out = rest[rest.index("-o") + 1] if "-o" in rest and rest.index("-o") + 1 < len(rest) else os.path.splitext(os.path.basename(path))[0]
        cc_build(c_code, out, optimize=True)
        print(f"built {out}")
        return 0
    # run: cache the debug binary by a hash of the generated C
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "tok")
    os.makedirs(cache, exist_ok=True)
    exe = os.path.join(cache, hashlib.sha256(c_code.encode()).hexdigest()[:24])
    if not os.path.exists(exe):
        tmp_exe = exe + f".tmp{os.getpid()}"
        cc_build(c_code, tmp_exe, optimize=False)
        os.replace(tmp_exe, exe)
    return subprocess.run([exe]).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
