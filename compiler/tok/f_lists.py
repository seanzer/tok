"""Lists: [T] literals, indexing, len, push, and for-in loops."""
from .diag import Diag
from .types import ERR, INT, NONE, VOID, mangle, is_int


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
            return self.hoist(f"tok_new_{mangle(ty)}(0)", ty), ty
        codes = []
        for it in items:
            code, ty = self.val(it, elem)
            if ty == VOID:
                self.err("E0103", "this call returns nothing, so it cannot be a list element", it)
            code = self.store(code, ty, it, False)
            if elem is None:
                if ty == NONE:
                    self.err("E0108", "cannot tell the element type from 'none'; annotate the list, for example 'xs: [int?] = [none]'", it)
                elem = ty
            else:
                code = self.coerce(code, ty, elem, it,
                                   f"list elements must all be {self.type_str(elem)}, found {self.type_str(ty)}")
            codes.append(code)
        ty = ("list", elem)
        if elem == ERR:
            return "0", ERR
        t = self.hoist(f"tok_new_{mangle(ty)}({len(codes)})", ty)
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
        if bt[0] != "list":
            hint = " (use .lines() or .split() to get a list)" if bt[0] == "str" else ""
            self.err("E0103", f"only lists can be indexed, found {self.type_str(bt)}{hint}", n["base"])
        idx = self.index_code(n)
        return self.hoist(f"tok_at_{mangle(bt)}({bc}, {idx}, {self.loc(n)})", bt[1]), bt[1]

    def place_index(self, n):
        bc, bt, v, root = self.place(n["base"])
        if bt == ERR:
            return "0", ERR, v, root
        if bt[0] != "list":
            self.err("E0103", f"only lists can be indexed, found {self.type_str(bt)}", n["base"])
        idx = self.index_code(n)
        return f"{bc}.data[tok_idx({idx}, {bc}.len, {self.loc(n)})]", bt[1], v, root

    def for_list(self, s):
        it = s["iter"]
        code, ty = self.val(it)
        vt = s["var_tok"]
        self.check_new_name(s["var"], {"line": vt.line, "col": vt.col, "end": vt.end}, "loop variable")
        if ty == ERR:
            self.loop_body(s, s["var"], ERR, False)
            return
        if ty[0] != "list":
            hint = " (use .lines() or .split() to get a list)" if ty[0] == "str" else "; a range is written a..b"
            self.err("E0103", f"'for' needs a list or a range, found {self.type_str(ty)}{hint}", it)
        # iterate over the value the list had when the loop started
        t = self.hoist(self.store(code, ty, it, False), ty)
        i = self.new_tmp()
        self.emit(f"for (int64_t {i} = 0; {i} < {t}.len; {i}++) {{")
        self.emit(f"  {self.c(ty[1])} v_{s['var']} TOK_UNUSED = {t}.data[{i}];")
        self.loop_body(s, s["var"], ty[1], False)
        self.emit("}")

    def m_list_len(self, n, bc, bt, want):
        self.margs(n, 0)
        return f"{bc}.len", INT

    def m_list_push(self, n, bc, bt, want):
        self.margs(n, 1)
        base = n["base"]
        if base["k"] not in ("name", "field", "index"):
            self.err("E0301", "push needs a var list to add to", base)
        lv, ty, v, root = self.place(base)
        self.need_mutable(v, root, base, "cannot be added to")
        code = self.stored(n["args"][0], bt[1], True,
                           None)
        self.emit(f"tok_push_{mangle(bt)}(&{lv}, {code});")
        return "", VOID
