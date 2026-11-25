"""Lists: [T] literals, indexing, len, push, and for-in loops."""
from .diag import Diag
from .types import ERR, INT, NONE, STR, VOID, mangle, is_int


class Lists:
    def setup(self):
        super().setup()
        self.methods[("list", "len")] = self.m_list_len
        self.methods[("list", "push")] = self.m_list_push

    def x_list(self, n, want):
        if want is not None and want[0] == "opt":
            want = want[1]
        elem = want[1] if want is not None and want[0] == "list" else None
        items = n["items"]
        if not items:
            if elem is None:
                self.err("E0108", "cannot tell the element type of '[]'; annotate it, for example 'xs: [int] = []'", n)
            ty = ("list", elem)
            return f"(({self.c(ty)}){{0}})", ty  # an empty list has no buffer
        vals = []
        for it in items:
            code, ty = self.val(it, elem)
            if ty == VOID:
                self.err("E0103", "this call returns nothing, so it cannot be a list element", it)
            if elem is None:
                if ty == NONE:
                    self.err("E0108", "cannot tell the element type from 'none'; annotate the list, for example 'xs: [int?] = [none]'", it)
                elem = ty
            vals.append((code, ty, it))
        ty = ("list", elem)
        # every element is evaluated before any is taken, so an early exit in a later one leaves nothing half-owned
        codes = [self.coerce(self.take(code, vt), vt, elem, it,
                             f"list elements must all be {self.type_str(elem)}, found {self.type_str(vt)}")
                 for code, vt, it in vals]
        if elem == ERR:
            return "0", ERR
        t = self.hoist(f"tok_new_{mangle(ty)}({len(codes)})", ty, own=True)
        for i, code in enumerate(codes):
            self.emit(f"{t}.data[{i}] = {code};")
        self.emit(f"{t}.len = {len(codes)};")
        return t, ty

    def index_code(self, n):
        code, ty = self.val(n["idx"], None if self.is_literal(n["idx"]) else INT)
        if ty == ERR:
            return self.hoist("0", INT)
        if not is_int(ty):
            self.err("E0103", f"a list index needs an integer, found {self.type_str(ty)}", n["idx"])
        if ty == ("u64",):
            # anything above the int range is out of bounds for every list
            t = self.hoist(code, ty)
            code = f"({t} > (uint64_t)INT64_MAX ? (int64_t)-1 : (int64_t){t})"
        elif ty != INT:
            code = f"((int64_t){code})"
        return self.hoist(code, INT)

    def x_index(self, n, want):
        bc, bt = self.val(n["base"])
        if bt == ERR:
            self.expr(n["idx"])
            return "0", ERR
        checked = n.get("checked")  # under `or`: a missing element is none instead of stopping the program
        has = ""
        if checked and bt[0] == "opt" and (bt[1] == STR or bt[1][0] in ("list", "dict")):
            # the base is itself a checked lookup that may have found nothing
            src = self.hoist(bc, bt)
            has, bc, bt = f"{src}.has && ", f"{src}.v", bt[1]
        if bt[0] == "dict":
            return self.dict_index(n, bc, bt, has)
        if bt == STR:
            idx = self.index_code(n)
            if checked:
                ot = ("opt", STR)
                code = f"tok_s_at_opt({bc}, {idx})"
                return self.hoist(f"{has}true ? {code} : (({self.c(ot)}){{0}})" if has else code, ot, own=True), ot
            return self.hoist(f"tok_s_at({bc}, {idx}, {self.loc(n)})", STR, own=True), STR
        if bt[0] != "list":
            self.err("E0103", f"only lists, dicts and strs can be indexed, found {self.type_str(bt)}", n["base"])
        idx = self.index_code(n)
        if checked:
            elem = bt[1]
            inside = f"({has}{idx} >= 0 && {idx} < {bc}.len)"
            if elem[0] == "opt":
                return self.hoist(f"{inside} ? {bc}.data[{idx}] : (({self.c(elem)}){{0}})", elem), elem
            ot = ("opt", elem)
            return self.hoist(f"{inside} ? (({self.c(ot)}){{.has = true, .v = {bc}.data[{idx}]}}) : (({self.c(ot)}){{0}})", ot), ot
        return self.hoist(f"tok_at_{mangle(bt)}({bc}, {idx}, {self.loc(n)})", bt[1]), bt[1]

    def place_index(self, n):
        bc, bt, v, root = self.place(n["base"])
        if bt == ERR:
            return "0", ERR, v, root
        if bt[0] == "dict":
            return self.dict_place(n, bc, bt, v, root)
        if bt[0] != "list":
            why = "a str cannot be changed in place; build a new one" if bt == STR else \
                f"only lists and dicts can be indexed, found {self.type_str(bt)}"
            self.err("E0103", why, n["base"])
        idx = self.index_code(n)
        # copy-on-write: before an element is written, the buffer must belong to this list alone
        self.preps.append(f"tok_own_{mangle(bt)}(&{bc});")
        return f"{bc}.data[tok_idx({idx}, {bc}.len, {self.loc(n)})]", bt[1], v, root

    def for_list(self, s):
        it = s["iter"]
        code, ty = self.val(it)
        vt = s["var_tok"]
        self.check_new_name(s["var"], {"line": vt.line, "col": vt.col, "end": vt.end}, "loop variable")
        if ty == ERR:
            self.loop_body(s, s["var"], ERR)
            return
        if ty[0] == "dict":
            if s.get("var2"):
                t2 = s["var2_tok"]
                if s["var2"] == s["var"]:
                    raise Diag("E0302", f"'{s['var']}' is used for both the key and the value", t2.line, t2.col, t2.end)
                self.check_new_name(s["var2"], {"line": t2.line, "col": t2.col, "end": t2.end}, "loop variable")
            return self.for_dict(s, code, ty)
        if s.get("var2"):
            self.err("E0103", f"'for k, v in' needs a dict, found {self.type_str(ty)}", it)
        if ty[0] != "list":
            hint = " (use .chars(), .words(), .lines() or .split() to get a list)" if ty[0] == "str" else "; a range is written a..b"
            self.err("E0103", f"'for' needs a list, a dict or a range, found {self.type_str(ty)}{hint}", it)
        # iterate over the value the list had when the loop started: the loop holds its own reference to the
        # buffer, so a change to the list inside the body goes to a copy
        t = self.stash(self.take(code, ty), ty)
        self.flush()
        self.scopes.append({"$" + t: {"c": t, "ty": ty, "mutable": False, "decl": None, "own": True}})
        i = self.new_tmp()
        self.emit(f"for (int64_t {i} = 0; {i} < {t}.len; {i}++) {{")
        self.emit(f"  {self.c(ty[1])} v_{s['var']} TOK_UNUSED = {t}.data[{i}];")
        self.loop_body(s, s["var"], ty[1])
        self.emit("}")
        self.emit(self.types.release(ty, t))
        self.scopes.pop()
        self.dead = False

    def m_list_len(self, n, bc, bt, want):
        self.margs(n, 0)
        return f"{bc}.len", INT

    def m_list_push(self, n, bc, bt, want):
        self.margs(n, 1)
        base = n["base"]
        if base["k"] not in ("name", "field", "index"):
            self.err("E0301", "push needs a var list to add to", base)
        outer, self.preps = self.preps, []
        try:
            lv, ty, v, root = self.place(base)
            preps = self.preps
        finally:
            self.preps = outer
        self.need_mutable(v, root, base, "cannot be added to")
        code = self.stored(n["args"][0], bt[1])
        if self.managed(bt[1]) and base["k"] != "name":
            code = self.settle(code, bt[1])  # owned before the list is made unique: it can never end up inside itself
        for p in preps:
            self.emit(p)
        self.emit(f"tok_push_{mangle(bt)}(&{lv}, {code});")
        return "", VOID
