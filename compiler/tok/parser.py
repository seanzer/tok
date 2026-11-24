"""Parser: tokens -> AST. Nodes are plain dicts: {"k": kind, "line", "col", "end", ...}."""
from .diag import Diag
from .lexer import KEYWORDS, Tok, lex_fragment

EFFECTS = {"io", "net", "fs", "time", "rand", "py", "err"}
ASSIGN_OPS = {"=", "+=", "-=", "*=", "/=", "%="}


def node(k, tok, **kw):
    d = {"k": k, "line": tok.line, "col": tok.col, "end": tok.end}
    d.update(kw)
    return d


def span(k, first, last, **kw):
    d = {"k": k, "line": first["line"], "col": first["col"], "end": last["end"] if isinstance(last, dict) else last.end}
    d.update(kw)
    return d


class Parser:
    def __init__(self, toks):
        self.toks, self.i = toks, 0
        self.errors = []
        self.failed_names = set()

    @property
    def t(self):
        return self.toks[self.i]

    def peek(self, k=1):
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def at(self, kind, val=None):
        t = self.t
        return t.kind == kind and (val is None or t.val == val)

    def eat(self, kind, val=None):
        if self.at(kind, val):
            self.i += 1
            return self.toks[self.i - 1]
        return None

    def found(self):
        cur = self.t
        if cur.kind == "STR":
            return "a string"
        if cur.kind in ("NEWLINE", "EOF"):
            return "end of line"
        return cur.kind.lower() if cur.val is None else repr(cur.val)

    def expect(self, kind, val=None, what=None):
        t = self.eat(kind, val)
        if t is None:
            cur = self.t
            raise Diag("E0010", f"expected {what or (repr(val) if val else kind.lower())}, found {self.found()}",
                       cur.line, cur.col, max(cur.end, cur.col + 1))
        return t

    # ---------------------------------------------------------- declarations

    def program(self):
        """Parse every declaration, recovering at the next top-level 'fn' or 'type'."""
        decls = []
        while not self.at("EOF"):
            start = self.i
            try:
                decls.append(self.decl())
            except Diag as d:
                self.errors.append(d)
                # remember the name so later uses do not cascade
                if self.toks[start].kind == "NAME" and self.toks[start + 1].kind == "NAME":
                    self.failed_names.add(self.toks[start + 1].val)
                if self.i == start:
                    self.i += 1
                while not self.at("EOF") and not (self.t.kind == "NAME" and self.t.val in ("fn", "type", "error") and self.t.col == 1):
                    self.i += 1
        return decls

    def decl(self):
        if self.at("NAME", "fn"):
            return self.fn_decl()
        if self.at("NAME", "type"):
            return self.type_decl()
        if self.at("NAME", "error") and self.t.col == 1:
            return self.error_decl()
        t = self.t
        raise Diag("E0010", f"expected 'fn', 'type' or 'error' at the top level, found {self.found()}", t.line, t.col, max(t.end, t.col + 1))

    def ident(self, what):
        t = self.expect("NAME", what=what)
        if t.val in KEYWORDS:
            raise Diag("E0012", f"'{t.val}' is a keyword and cannot be used as {what}", t.line, t.col, t.end)
        return t

    def type_expr(self):
        t = self.t
        if self.eat("OP", "["):
            elem = self.type_expr()
            close = self.expect("OP", "]", what="']'")
            ty = {"k": "tlist", "elem": elem, "line": t.line, "col": t.col, "end": close.end}
        else:
            name = self.expect("NAME", what="a type")
            if name.val in KEYWORDS:
                raise Diag("E0010", f"expected a type, found keyword '{name.val}'", name.line, name.col, name.end)
            ty = node("tname", name, name=name.val, mod=None)
            if self.at("OP", ".") and self.peek().kind == "NAME":
                self.i += 1
                inner = self.expect("NAME", what="a type name")
                ty = {"k": "tname", "name": inner.val, "mod": name.val, "line": name.line, "col": name.col, "end": inner.end}
        while self.at("OP", "?"):
            q = self.eat("OP", "?")
            ty = {"k": "topt", "inner": ty, "line": ty["line"], "col": ty["col"], "end": q.end}
        return ty

    def type_decl(self):
        start = self.expect("NAME", "type")
        name = self.ident("a type name")
        self.expect("OP", "=", what="'='")
        self.expect("OP", "{", what="'{' (only record types can be declared)")
        fields = []
        while not self.at("OP", "}"):
            f = self.ident("a field name")
            self.expect("OP", ":", what="':' and a type for the field")
            fields.append((f.val, self.type_expr(), f))
            if not self.eat("OP", ","):
                break
        self.expect("OP", "}", what="'}'")
        self.expect("NEWLINE", what="end of line")
        return node("type", start, name=name.val, name_tok=name, fields=fields)

    def error_decl(self):
        """error NotFound, Timeout   declares error kinds."""
        start = self.expect("NAME", "error")
        names = []
        while True:
            names.append(self.ident("an error kind name"))
            if not self.eat("OP", ","):
                break
        self.expect("NEWLINE", what="end of line")
        return node("error", start, name=names[0].val, name_tok=names[0], names=names)

    def lam(self, first, p):
        """x -> expr, or a handler block when the line ends at '->'."""
        if self.at("NEWLINE"):
            block = self.block()
            last = self.toks[self.i - 1]
            # the statement that holds the handler ends where the block ends
            self.toks.insert(self.i, Tok("NEWLINE", None, last.line, last.col, last.end))
            return {"k": "lam", "param": p.val, "param_tok": p, "body": None, "block": block,
                    "line": first.line, "col": first.col, "end": p.end + 3}
        body = self.expr()
        return {"k": "lam", "param": p.val, "param_tok": p, "body": body, "line": first.line, "col": first.col, "end": body["end"]}

    def fn_decl(self):
        start = self.expect("NAME", "fn", what="'fn'")
        name = self.ident("a function name")
        self.expect("OP", "(", what="'('")
        params = []
        while not self.at("OP", ")"):
            p = self.ident("a parameter name")
            self.expect("OP", ":", what="':' and a type for the parameter")
            params.append((p.val, self.type_expr(), p))
            if not self.eat("OP", ","):
                break
        self.expect("OP", ")", what="')'")
        ret = self.type_expr() if self.eat("OP", "->") else None
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

    # ---------------------------------------------------------- statements

    def block(self):
        self.expect("NEWLINE", what="end of line")
        self.expect("INDENT", what="an indented block")
        stmts = []
        while not self.at("DEDENT"):
            stmts.append(self.stmt())
        self.expect("DEDENT")
        return stmts

    def end_stmt(self):
        self.expect("NEWLINE", what="end of line")

    def stmt(self):
        t = self.t
        if self.eat("NAME", "if"):
            return self.if_rest(t)
        if self.eat("NAME", "for"):
            v = self.ident("a loop variable")
            self.expect("NAME", "in", what="'in'")
            first = self.expr()
            hi = None
            if self.eat("OP", ".."):
                hi = self.expr()
            where = self.expr() if self.eat("NAME", "where") else None
            return node("for", t, var=v.val, var_tok=v, iter=first, hi=hi, where=where, body=self.block())
        if self.eat("NAME", "return"):
            val = None if self.at("NEWLINE") else self.expr()
            self.end_stmt()
            return node("return", t, val=val)
        if self.eat("NAME", "var"):
            name = self.ident("a variable name")
            ann = self.type_expr() if self.eat("OP", ":") else None
            self.expect("OP", "=", what="'='")
            val = self.expr()
            self.end_stmt()
            return node("bind", name, name=name.val, ann=ann, val=val, mutable=True, var_col=t.col)
        if t.kind == "NAME" and t.val not in KEYWORDS:
            nxt = self.peek()
            if nxt.kind == "OP" and nxt.val == "=":
                self.i += 2
                val = self.expr()
                self.end_stmt()
                return node("bind", t, name=t.val, ann=None, val=val, mutable=False)
            if nxt.kind == "OP" and nxt.val == ":":
                self.i += 2
                ann = self.type_expr()
                self.expect("OP", "=", what="'='")
                val = self.expr()
                self.end_stmt()
                return node("bind", t, name=t.val, ann=ann, val=val, mutable=False)
        e = self.expr()
        if self.t.kind == "OP" and self.t.val in ASSIGN_OPS:
            op = self.t
            self.i += 1
            if e["k"] not in ("name", "field", "index"):
                raise Diag("E0016", "the left side of an assignment must be a variable, a field, or a list element",
                           e["line"], e["col"], e["end"])
            val = self.expr()
            self.end_stmt()
            return span("assign", e, val, target=e, op=op.val, val=val)
        self.end_stmt()
        return span("expr", e, e, val=e)

    def if_rest(self, t):
        cond = self.expr()
        then = self.block()
        other = None
        if self.eat("NAME", "else"):
            if self.at("NAME", "if"):
                it = self.eat("NAME", "if")
                other = [self.if_rest(it)]
            else:
                other = self.block()
        return node("if", t, cond=cond, then=then, other=other)

    # ---------------------------------------------------------- expressions

    LEVELS = [("or",), ("and",), ("not",), ("==", "!=", "<", "<=", ">", ">="), ("+", "-"), ("*", "/", "%")]

    def expr(self, level=0):
        if level == len(self.LEVELS):
            return self.unary()
        if self.LEVELS[level] == ("not",):
            t = self.t
            if self.eat("NAME", "not"):
                e = self.expr(level)
                return {"k": "un", "op": "not", "e": e, "line": t.line, "col": t.col, "end": e["end"]}
            return self.expr(level + 1)
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
        if self.eat("OP", "-"):
            e = self.unary()
            return {"k": "un", "op": "-", "e": e, "line": t.line, "col": t.col, "end": e["end"]}
        return self.power()

    def power(self):
        """a ** b binds tighter than a unary minus on its left and groups to the right, as in Python:
        -2 ** 2 is -(2 ** 2), 2 ** 3 ** 2 is 2 ** (3 ** 2), and 2.0 ** -1.0 is allowed."""
        base = self.postfix()
        if self.eat("OP", "**"):
            exp = self.unary()
            return {"k": "bin", "op": "**", "l": base, "r": exp, "line": base["line"], "col": base["col"], "end": exp["end"]}
        return base

    def args(self):
        out = []
        while not self.at("OP", ")"):
            out.append(self.expr())
            if not self.eat("OP", ","):
                break
        return out, self.expect("OP", ")", what="')'")

    def postfix(self):
        e = self.atom()
        while True:
            if self.at("OP", ".") and self.peek().kind == "NAME":
                self.i += 1
                # 'where' is a keyword in 'for x in xs where c' and a method name after a dot.
                name = self.t if self.t.val == "where" else None
                name = self.eat("NAME") if name else self.ident("a field or method name")
                if self.eat("OP", "("):
                    args, close = self.args()
                    e = {"k": "mcall", "base": e, "name": name.val, "args": args, "name_col": name.col,
                         "line": e["line"], "col": e["col"], "end": close.end}
                else:
                    e = {"k": "field", "base": e, "name": name.val, "name_col": name.col,
                         "line": e["line"], "col": e["col"], "end": name.end}
            elif self.at("OP", "["):
                self.i += 1
                idx = self.expr()
                close = self.expect("OP", "]", what="']'")
                e = {"k": "index", "base": e, "idx": idx, "line": e["line"], "col": e["col"], "end": close.end}
            elif self.at("OP", "?"):
                q = self.eat("OP", "?")
                e = {"k": "try", "e": e, "q_col": q.col, "line": e["line"], "col": e["col"], "end": q.end}
            elif self.at("OP", "{") and (e["k"] == "name" or (e["k"] == "field" and e["base"]["k"] == "name")):
                e = self.record_lit(e)
            else:
                return e

    def record_lit(self, head):
        self.expect("OP", "{")
        fields = []
        while not self.at("OP", "}"):
            f = self.ident("a field name")
            self.expect("OP", ":", what="':' and a value for the field")
            fields.append((f.val, self.expr(), f))
            if not self.eat("OP", ","):
                break
        close = self.expect("OP", "}", what="'}'")
        if head["k"] == "name":
            tmod, tname = None, head["name"]
        else:
            tmod, tname = head["base"]["name"], head["name"]
        return {"k": "rec", "tmod": tmod, "tname": tname, "fields": fields, "head_end": head["end"],
                "line": head["line"], "col": head["col"], "end": close.end}

    def string(self, t):
        parts = []
        for p in t.val:
            if p[0] == "s":
                parts.append(("s", p[1]))
                continue
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

    def atom(self):
        t = self.t
        if self.eat("INT"):
            return node("int", t, v=t.val)
        if self.eat("FLOAT"):
            return node("float", t, v=t.val)
        if self.eat("STR"):
            return self.string(t)
        if self.at("OP", "("):
            # (x) -> expr
            if self.peek().kind == "NAME" and self.peek(2).kind == "OP" and self.peek(2).val == ")" \
                    and self.peek(3).kind == "OP" and self.peek(3).val == "->":
                self.i += 1
                p = self.ident("a lambda parameter")
                self.i += 2
                return self.lam(t, p)
            self.i += 1
            e = self.expr()
            close = self.expect("OP", ")", what="')'")
            e = dict(e)
            e["paren"] = True
            e["pcol"], e["pend"] = t.col, close.end
            return e
        if self.eat("OP", "["):
            items = []
            while not self.at("OP", "]"):
                items.append(self.expr())
                if not self.eat("OP", ","):
                    break
            close = self.expect("OP", "]", what="']'")
            return {"k": "list", "items": items, "line": t.line, "col": t.col, "end": close.end}
        if t.kind == "NAME":
            if t.val in ("true", "false"):
                self.i += 1
                return node("bool", t, v=t.val == "true")
            if t.val == "none":
                self.i += 1
                return node("none", t)
            if t.val in KEYWORDS:
                raise Diag("E0010", f"expected an expression, found keyword '{t.val}'", t.line, t.col, t.end)
            self.i += 1
            if self.at("OP", "->"):
                self.i += 1
                return self.lam(t, t)
            if self.eat("OP", "("):
                args, close = self.args()
                n = node("call", t, name=t.val, args=args)
                n["end"] = close.end
                return n
            return node("name", t, name=t.val)
        raise Diag("E0010", f"expected an expression, found {self.found()}", t.line, t.col, max(t.end, t.col + 1))
