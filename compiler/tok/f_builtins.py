"""More built-ins: sorting and slicing lists, pieces of strings, numbers as text, and standard input.

Every list method here gives a new list and leaves the original alone, so none of them needs a `var`.
"""
from .diag import Diag
from .types import BOOL, ERR, INT, NUMS, STR, comparable, is_float, is_int, is_num, less, mangle

LINT = ("list", INT)


class Builtins:
    def setup(self):
        super().setup()
        for name in ("sort", "reverse", "drop", "last", "index_of", "unique", "min", "max"):
            self.methods[("list", name)] = getattr(self, "m_list_" + name)
        for kind, (_, k, _) in NUMS.items():
            self.methods[(kind, "fixed" if k == "f" else "to_str")] = self.m_fixed if k == "f" else self.m_to_str
        self.builtin_mods["io"] = {
            "read_line": ((), ("opt", STR), "tok_io_read_line", "io"),
            "read_all": ((), STR, "tok_io_read_all", "io"),
        }

    def no_method(self, n, msg):
        raise Diag("E0109", msg, n["line"], n["name_col"], n["end"])

    def count_arg(self, n):
        code, ty = self.val(n["args"][0], INT)
        return self.hoist(self.coerce(code, ty, INT, n["args"][0], f"'{n['name']}' needs an int count, found {self.type_str(ty)}"), INT)

    # ---------------------------------------------------------- order

    def keys(self, n, bc, bt, what):
        """The values a list is ordered by: its own elements, or the result of a key lambda. Returns (list code, type)."""
        if len(n["args"]) > 1:
            self.margs(n, 1)
        if not n["args"]:
            if not comparable(bt[1]):
                self.no_method(n, f"'{n['name']}' without a key works on lists of numbers or strs, found {self.type_str(bt)}; "
                                  f"give a key, for example xs.{n['name']}(x -> x.{what})")
            return bc, bt
        keys, klt, lam = self.collect(n, bc, bt)
        if klt == ERR:
            return "0", ERR
        if not comparable(klt[1]):
            self.err("E0103", f"the key of '{n['name']}' must be a number or a str, found {self.type_str(klt[1])}", lam["body"])
        return keys, klt

    def m_list_sort(self, n, bc, bt, want):
        keys, klt = self.keys(n, bc, bt, "size")
        if klt == ERR:
            return "0", ERR
        self.types.need(klt)
        perm = self.hoist(f"tok_order_{mangle(klt)}({keys})", LINT, own=True)
        out = self.hoist(f"tok_new_{mangle(bt)}({bc}.len)", bt, own=True)
        i = self.new_tmp()
        self.emit(f"for (int64_t {i} = 0; {i} < {bc}.len; {i}++) "
                  f"{out}.data[{i}] = {self.types.ret(bt[1], f'{bc}.data[{perm}.data[{i}]]')};")
        self.emit(f"{out}.len = {bc}.len;")
        return out, bt

    def extreme(self, n, bc, bt, larger):
        """min / max of a list, optionally by a key. The first of equal elements wins. none for an empty list."""
        keys, klt = self.keys(n, bc, bt, "size")
        if klt == ERR:
            return "0", ERR
        out_ty = ("opt", bt[1])
        out = self.hoist(f"(({self.c(out_ty)}){{0}})", out_ty, own=True)
        best, i = self.new_tmp(), self.new_tmp()
        a, b = (f"{keys}.data[{best}]", f"{keys}.data[{i}]") if larger else (f"{keys}.data[{i}]", f"{keys}.data[{best}]")
        self.emit(f"if ({bc}.len > 0) {{")
        self.emit(f"  int64_t {best} = 0;")
        self.emit(f"  for (int64_t {i} = 1; {i} < {bc}.len; {i}++) if ({less(klt[1], a, b)}) {best} = {i};")
        self.emit(f"  {out}.has = true;")
        self.emit(f"  {out}.v = {self.types.ret(bt[1], f'{bc}.data[{best}]')};")
        self.emit("}")
        return out, out_ty

    def m_list_min(self, n, bc, bt, want):
        return self.extreme(n, bc, bt, False)

    def m_list_max(self, n, bc, bt, want):
        return self.extreme(n, bc, bt, True)

    # ---------------------------------------------------------- pieces

    def m_list_reverse(self, n, bc, bt, want):
        self.margs(n, 0)
        return self.hoist(f"tok_rev_{mangle(bt)}({bc})", bt, own=True), bt

    def m_list_unique(self, n, bc, bt, want):
        self.margs(n, 0)
        return self.hoist(f"tok_uniq_{mangle(bt)}({bc})", bt, own=True), bt

    def m_list_drop(self, n, bc, bt, want):
        self.margs(n, 1)
        k = self.count_arg(n)
        return self.hoist(f"tok_slice_{mangle(bt)}({bc}, {k}, {bc}.len)", bt, own=True), bt

    def m_list_last(self, n, bc, bt, want):
        self.margs(n, 1)
        k = self.count_arg(n)
        return self.hoist(f"tok_slice_{mangle(bt)}({bc}, ({k} < 0 || {k} > {bc}.len) ? ({k} < 0 ? {bc}.len : 0) : {bc}.len - {k}, {bc}.len)", bt, own=True), bt

    def m_list_index_of(self, n, bc, bt, want):
        self.margs(n, 1)
        code, ty = self.val(n["args"][0], bt[1])
        code = self.coerce(code, ty, bt[1], n["args"][0],
                           f"'index_of' on {self.type_str(bt)} needs {self.type_str(bt[1])}, found {self.type_str(ty)}")
        at = self.hoist(f"tok_find_{mangle(bt)}({bc}, {code})", INT)
        ot = ("opt", INT)
        return f"(({self.c(ot)}){{.has = {at} >= 0, .v = {at} < 0 ? 0 : {at}}})", ot

    def joinable(self, bc, bt):
        """[int], [f64], [bool] ... as a [str], for join."""
        lt = ("list", STR)
        out = self.hoist(f"tok_new_{mangle(lt)}({bc}.len)", lt, own=True)
        i = self.new_tmp()
        self.emit(f"for (int64_t {i} = 0; {i} < {bc}.len; {i}++) {{")
        self.emit(f"  tok_sbuf {i}b; tok_sb {i}s; tok_sb_init(&{i}s, &{i}b);")
        self.emit("  " + self.types.show(bt[1], f"{bc}.data[{i}]", f"&{i}s"))
        self.emit(f"  {out}.data[{i}] = tok_sb_str(&{i}s);")
        self.emit(f"  {out}.len = {i} + 1;")
        self.emit("}")
        return out

    # ---------------------------------------------------------- numbers as text

    def m_to_str(self, n, bc, bt, want):
        """n.to_str(base): the digits of an integer in base 2 to 36."""
        self.margs(n, 1)
        base = self.count_arg(n)
        if bt == ("u64",):
            return self.hoist(f"tok_u_to_str({bc}, false, {base}, {self.loc(n)})", STR, own=True), STR
        return self.hoist(f"tok_i_to_str((int64_t)({bc}), {base}, {self.loc(n)})", STR, own=True), STR

    def m_fixed(self, n, bc, bt, want):
        """x.fixed(digits): a float with exactly that many digits after the point."""
        self.margs(n, 1)
        digits = self.count_arg(n)
        return self.hoist(f"tok_f_fixed((double)({bc}), {digits}, {self.loc(n)})", STR, own=True), STR
