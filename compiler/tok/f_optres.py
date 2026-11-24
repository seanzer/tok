"""Optionals (T?, none, `or` defaults) and the error path (`err`, `?`, fail, error kinds, handlers).

Errors are typed by kind. `error NotFound, Timeout` declares kinds; `fail(NotFound, "msg")` raises one;
`call() or e -> ...` binds the error as `e`, an `Error` with `.kind` and `.msg`. The syntax is provisional.
"""
from .diag import Diag, make_fix
from .types import BOOL, EKIND, ERR, ERRV, NEVER, NONE, STR, VOID


class OptRes:
    def setup(self):
        super().setup()
        self.free_fns["fail"] = self.b_fail

    def x_none(self, n, want):
        return "0", NONE

    # ---------------------------------------------------------- or

    def logic(self, n, want):
        l = n["l"]
        if n["op"] == "or":
            # `xs[i] or d`, and a chain of lookups and method calls, `rows[i].split(",")[1].to_int() or d`:
            # under `or` every lookup in the chain is checked, so a missing element gives the default.
            spine, at = [], l
            while at["k"] == "index" or (at["k"] == "mcall" and not self.is_module_call(at)):
                if at["k"] == "index":
                    spine.append(at)
                at = at["base"]
            bt = self.dry_type(l["base"]) if l["k"] == "index" and len(spine) == 1 else None
            direct = bt is not None and bt[0] == "list"
            if spine and not direct and self.dry_type(l) != BOOL:  # a bool chain keeps the logical `or`
                checked = False
                for ix in spine:
                    ix["checked"] = True
                try:
                    lt = self.dry_type(l)
                    checked = lt is not None and lt[0] == "opt"
                    if checked:
                        lc, lt = self.expr(l, want)
                finally:
                    for ix in spine:
                        ix["checked"] = False
                if checked:
                    if lt == ERR:
                        self.expr(n["r"])
                        return "0", ERR
                    return self.or_default(n, lc, lt, want)
        if n["op"] == "or" and l["k"] == "index":
            bc, bt = self.val(l["base"])
            if bt != ERR and bt[0] == "list" and bt[1] != BOOL:
                return self.index_or(n, bc, bt, want)
            if bt == ERR:
                self.expr(l["idx"])
                self.expr(n["r"])
                return "0", ERR
        return super().logic(n, want)

    def is_module_call(self, n):
        return n["base"]["k"] == "name" and self.lookup(n["base"]["name"]) is None

    def dry_type(self, n):
        """The type an expression would have, without emitting code or reporting errors. None if it has an error."""
        tmp, nerr, scopes, narrowed, sinks = self.tmp, len(self.errors), len(self.scopes), len(self.narrowed), len(self.sinks)
        try:
            with self.capture():
                return self.expr(n)[1]
        except Diag:
            return None
        finally:
            self.tmp = tmp
            del self.errors[nerr:], self.scopes[scopes:], self.narrowed[narrowed:], self.sinks[sinks:]

    def opt_call(self, n, bc, bt, want):
        """A method called on an optional runs when there is a value and gives none otherwise: the result is optional."""
        inner = bt[1]
        h = self.methods[(inner[0], n["name"])]
        if n["name"] == "push":
            self.err("E0103", f"cannot push onto {self.type_str(bt)}: it may be none; test it first", n["base"])
        src = self.hoist(bc, bt)
        with self.capture(1) as pre:
            code, ty = h(n, f"{src}.v", inner, want[1] if want is not None and want[0] == "opt" else want)
        if ty == ERR:
            return "0", ERR
        if ty in (VOID, NEVER):
            self.err("E0103", f"'{n['name']}' returns nothing, so it cannot be called on {self.type_str(bt)}; test it first", n)
        out_ty = ty if ty[0] == "opt" else ("opt", ty)
        t = self.new_tmp()
        self.emit(f"{self.c(out_ty)} {t} TOK_UNUSED = (({self.c(out_ty)}){{0}});")
        self.emit(f"if ({src}.has) {{")
        self.buf.extend(pre)
        self.emit(f"  {t} = {code if ty[0] == 'opt' else f'(({self.c(out_ty)}){{.has = true, .v = {code}}})'};")
        self.emit("}")
        return t, out_ty

    def index_or(self, n, bc, bt, want):
        """xs[i] or default: the default is used when i is out of range."""
        l, elem = n["l"], bt[1]
        idx = self.index_code(l)
        l["ty"] = elem
        inside = f"({idx} >= 0 && {idx} < {bc}.len)"
        if elem[0] == "opt":
            t = self.hoist(f"{inside} ? {bc}.data[{idx}] : (({self.c(elem)}){{0}})", elem)
            return self.default(n, f"{t}.has", f"{t}.v", elem[1], want)
        return self.default(n, inside, f"{bc}.data[{idx}]", elem, want)

    def or_default(self, n, lc, lt, want):
        if n["r"]["k"] == "lam":
            if lt[0] == "res":
                return self.handler(n, lc, lt)
            if lt != ERR:
                self.err("E0113", "'or e -> ...' handles the error of a call that can fail; "
                                  f"here the left side is {self.type_str(lt)}, so write the default directly", n["r"])
        if lt[0] == "opt":
            return self.default(n, f"{lc}.has", f"{lc}.v", lt[1], want)
        if lt[0] == "res":
            if lt[1] == VOID:
                self.err("E0103", "this call returns nothing, so there is nothing to give a default for; use '?'", n["l"])
            return self.default(n, f"!{lc}.err", f"{lc}.v", lt[1], want)
        if lt == NONE:
            self.err("E0103", "'none or ...' is always the right side; write the value directly", n["l"])
        self.err("E0103", f"'or' needs bool, an optional, or a call that can fail on its left, found {self.type_str(lt)}", n["l"])

    def default(self, n, has, value, ty, want):
        r = n["r"]
        with self.capture(1) as pre:
            rc, rt = self.val(r, ty, never=True)
            if rt != NEVER:
                rc = self.store(rc, rt, r, False)
                rc = self.coerce(rc, rt, ty, r, f"the default must be {self.type_str(ty)}, found {self.type_str(rt)}")
        if ty == ERR:
            return "0", ERR
        t = self.new_tmp()
        self.emit(f"{self.c(ty)} {t} TOK_UNUSED;")
        self.emit(f"if ({has}) {{")
        self.emit(f"  {t} = {value};")
        self.emit("} else {")
        self.buf.extend(pre)
        if rt != NEVER:
            self.emit(f"  {t} = {rc};")
        self.emit("}")
        return t, ty

    # ---------------------------------------------------------- handlers

    def handler(self, n, lc, lt):
        """call() or e -> expr, or a block after '->': runs only when the call failed, with the error bound to e."""
        lam, ty = n["r"], lt[1]
        pt = lam["param_tok"]
        self.check_new_name(lam["param"], {"line": pt.line, "col": pt.col, "end": pt.end}, "error name")
        t = None
        if ty != VOID and ty != ERR:
            t = self.new_tmp()
            self.emit(f"{self.c(ty)} {t} TOK_UNUSED;")
        self.emit(f"if (!{lc}.err) {{")
        if t:
            self.emit(f"  {t} = {lc}.v;")
        self.emit("} else {")
        self.emit(f"  tok_error v_{lam['param']} TOK_UNUSED = {{ {lc}.err, {lc}.kind }};")
        self.scopes.append({})
        self.define(lam["param"], ERRV)
        try:
            if lam["body"] is None:
                block = lam["block"]
                if ty not in (VOID, ERR) and not self.always_returns(block):
                    self.report(Diag("E0105", f"the handler must give a {self.type_str(ty)} on every path: end each path "
                                              "in a value, 'return' or 'fail'", lam["line"], lam["col"], lam["end"]))
                self.sinks.append((t, ty))
                try:
                    self.block(block, tail=True)
                finally:
                    self.sinks.pop()
            else:
                self.depth += 1
                try:
                    body = lam["body"]
                    code, rt = self.val(body, None if ty == VOID else ty, never=True)
                    if rt != NEVER and ty not in (VOID, ERR):
                        if rt == VOID:
                            self.err("E0103", f"the handler must give a {self.type_str(ty)}, but this call returns nothing", body)
                        code = self.store(code, rt, body, False)
                        code = self.coerce(code, rt, ty, body,
                                           f"the handler must give a {self.type_str(ty)}, found {self.type_str(rt)}")
                        if rt != ERR:
                            self.emit(f"{t} = {code};")
                finally:
                    self.depth -= 1
        finally:
            self.scopes.pop()
            self.emit("}")
        if ty == ERR:
            return "0", ERR
        return (t, ty) if t else ("", VOID)

    # ---------------------------------------------------------- error kinds and values

    def x_name(self, n, want):
        if self.lookup(n["name"]) is None:
            k = self.kind_id(n["name"])
            if k is not None:
                return str(k), EKIND
        return super().x_name(n, want)

    def x_field(self, n, want):
        base = n["base"]
        if base["k"] == "name" and self.lookup(base["name"]) is None and base["name"] not in self.builtin_mods \
                and base["name"] not in self.mod.failed:
            # geometry.OutOfRange: an error kind declared in another module
            try:
                mod = self.get_module(base["name"], base)
            except Diag:
                mod = None
            if mod is not None and n["name"] in mod.errors:
                return str(mod.errors[n["name"]]), EKIND
            return super().x_field(n, want)
        bc, bt = self.val(base)
        if bt == ERRV:
            if n["name"] == "msg":
                return f"{bc}.msg", STR
            if n["name"] == "kind":
                return f"{bc}.kind", EKIND
            raise Diag("E0109", f"Error has no field '{n['name']}' (fields: kind, msg)", n["line"], n["name_col"], n["end"])
        return super().x_field(n, want)

    # ---------------------------------------------------------- errors

    def can_fail(self, n, what):
        sig = self.sig(self.cur)
        if sig["fallible"] or sig["is_main"]:
            return
        self.err("E0202", f"{what}, but '{self.cur['name']}' does not declare 'err' in its signature", n, self.sig_fix("err"))

    def bail(self, msg, kind):
        sig = self.sig(self.cur)
        if sig["is_main"]:
            self.emit(f"tok_main_fail({msg});")
        else:
            self.emit(f"return (({self.c(sig['cret'])}){{.err = {msg}, .kind = {kind}}});")

    def x_try(self, n, want):
        code, ty = self.expr(n["e"], want)
        if ty == ERR:
            return "0", ERR
        if ty[0] != "res":
            why = "an optional has no error to pass up; use 'or' to give a default" if ty[0] == "opt" \
                else "this expression cannot fail"
            self.err("E0204", f"'?' passes an error up to the caller, but {why}", n,
                     make_fix(f"remove \"?\" at {n['line']}:{n['q_col']}", [(n["line"], n["q_col"], "", 1)]))
        self.can_fail(n, "'?' passes the error up to the caller")
        self.emit(f"if ({code}.err) {{")
        self.depth += 1
        self.bail(f"{code}.err", f"{code}.kind")
        self.depth -= 1
        self.emit("}")
        if ty[1] == VOID:
            return "", VOID
        return f"{code}.v", ty[1]

    def b_fail(self, n, want):
        """fail("msg"), fail(Kind, "msg"), fail(Kind), or fail(e) to pass on a caught error."""
        args = n["args"]
        if len(args) not in (1, 2):
            self.err("E0104", f"fail takes a message, an error kind and a message, or a caught error; found {len(args)} "
                              "argument(s)", n)
        code, ty = self.val(args[0], STR)
        if ty == ERR:
            for a in args[1:]:
                self.expr(a)
            return "", NEVER
        if len(args) == 2:
            if ty != EKIND:
                self.err("E0103", f"with 2 arguments fail needs an error kind first, found {self.type_str(ty)}", args[0])
            mc, mt = self.val(args[1], STR)
            mc = self.coerce(mc, mt, STR, args[1], f"fail needs a str message, found {self.type_str(mt)}")
            msg, kind = mc, code
        elif ty == EKIND:
            msg, kind = f"tok_kind_name({code})", code
        elif ty == ERRV:
            msg, kind = f"{code}.msg", f"{code}.kind"
        else:
            msg = self.coerce(code, ty, STR, args[0],
                              f"fail needs a str message, an error kind, or a caught error, found {self.type_str(ty)}")
            kind = "0"
        self.can_fail(n, "'fail' returns an error to the caller")
        self.bail(msg, kind)
        return "", NEVER
