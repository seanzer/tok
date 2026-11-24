"""Optionals (T?, none, `or` defaults) and the single error path (`err`, `?`, fail)."""
from .diag import make_fix
from .types import BOOL, ERR, NEVER, NONE, STR, VOID


class OptRes:
    def setup(self):
        super().setup()
        self.free_fns["fail"] = self.b_fail

    def x_none(self, n, want):
        return "0", NONE

    # ---------------------------------------------------------- or

    def logic(self, n, want):
        l = n["l"]
        if n["op"] == "or" and l["k"] == "index":
            bc, bt = self.val(l["base"])
            if bt != ERR and bt[0] == "list" and bt[1] != BOOL:
                return self.index_or(n, bc, bt, want)
            if bt == ERR:
                self.expr(l["idx"])
                self.expr(n["r"])
                return "0", ERR
        return super().logic(n, want)

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

    # ---------------------------------------------------------- errors

    def can_fail(self, n, what):
        sig = self.sig(self.cur)
        if sig["fallible"] or sig["is_main"]:
            return
        self.err("E0202", f"{what}, but '{self.cur['name']}' does not declare 'err' in its signature", n, self.sig_fix("err"))

    def bail(self, msg):
        sig = self.sig(self.cur)
        if sig["is_main"]:
            self.emit(f"tok_main_fail({msg});")
        else:
            self.emit(f"return (({self.c(sig['cret'])}){{.err = {msg}}});")

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
        self.bail(f"{code}.err")
        self.depth -= 1
        self.emit("}")
        if ty[1] == VOID:
            return "", VOID
        return f"{code}.v", ty[1]

    def b_fail(self, n, want):
        if len(n["args"]) != 1:
            self.err("E0104", f"fail takes 1 argument (the error message), found {len(n['args'])}", n)
        code, ty = self.val(n["args"][0], STR)
        code = self.coerce(code, ty, STR, n["args"][0], f"fail needs a str message, found {self.type_str(ty)}")
        self.can_fail(n, "'fail' returns an error to the caller")
        self.bail(code)
        return "", NEVER
