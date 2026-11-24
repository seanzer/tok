"""Sized numeric types: explicit conversions and the fixes offered when types are mixed.

There are no implicit conversions between numeric types. A conversion is written as a
call to the target type, for example f64(n) or u8(x).
"""
from .diag import make_fix
from .types import ALIASES, ERR, NUMS, int_range, is_float, is_int, is_num, lossless


class Numeric:
    def setup(self):
        super().setup()
        for name in list(NUMS) + list(ALIASES):
            self.free_fns[name] = self.b_convert

    def b_convert(self, n, want):
        dst = (ALIASES.get(n["name"], n["name"]),)
        if len(n["args"]) != 1:
            self.err("E0104", f"{dst[0]}(...) converts 1 number, found {len(n['args'])} argument(s)", n)
        arg = n["args"][0]
        # A literal is typed by the conversion itself, so u8(300) is a compile error.
        literal_ok = self.is_literal(arg) and not (is_int(dst) and (arg if arg["k"] != "un" else arg["e"])["k"] == "float")
        code, src = self.val(arg, dst if literal_ok else None)
        if src == ERR:
            return "0", ERR
        if not is_num(src):
            hint = "; use .to_int() or .to_f64() to parse text" if src == ("str",) else ""
            self.err("E0103", f"{dst[0]}(...) converts a number, found {self.type_str(src)}{hint}", arg)
        if src == dst:
            return code, dst
        return self.convert(code, src, dst, n), dst

    def convert(self, code, src, dst, n):
        sct, sk, sb = NUMS[src[0]]
        dct, dk, db = NUMS[dst[0]]
        if lossless(src, dst) or dk == "f":
            return f"(({dct}){code})"
        t = self.hoist(code, src)
        if sk == "f":
            # truncates toward zero; NaN and out-of-range values stop the program
            d = t if src == ("f64",) else self.hoist(f"(double){t}", ("f64",))
            lo, hi = int_range(dst)
            low = f"{d} >= {float(lo)!r}" if db == 64 and dk == "i" else f"{d} > {float(lo - 1)!r}"
            self.emit(f"if (!({low} && {d} < {float(hi + 1)!r})) "
                      f"tok_panic({self.loc(n)}, \"%s does not fit in {dst[0]}\", tok_str_f64({d}));")
            return f"(({dct}){t})"
        slo, shi = int_range(src)
        dlo, dhi = int_range(dst)
        checks = []
        if dlo > slo:
            checks.append(f"{t} < {self.c_int(dlo, src)}")
        if dhi < shi:
            checks.append(f"{t} > {self.c_int(dhi, src)}")
        shown = self.types.to_str(src, t)
        self.emit(f"if ({' || '.join(checks)}) tok_panic({self.loc(n)}, \"%s does not fit in {dst[0]}\", {shown});")
        return f"(({dct}){t})"

    # ---------------------------------------------------------- fixes

    def wrap_fix(self, n, ty, want):
        # Exact conversions only, plus integer -> f64: the widest float is the only sensible
        # target there, though 64-bit integers beyond 2^53 are rounded.
        rounds = is_int(ty) and want == ("f64",) and not lossless(ty, want)
        if n["k"] == "lam" or not (lossless(ty, want) or rounds):
            return None
        note = " (integers beyond 2^53 are rounded)" if rounds else ""
        return make_fix(f"convert to {want[0]}{note}: wrap the expression at {n['line']}:{n['col']} in {want[0]}(...)",
                        [(n["line"], n["col"], f"{want[0]}("), (n["line"], n["end"], ")")])

    def conv_fix(self, n, ty, want):
        if want[0] == "opt":
            want = want[1]
        if not (is_num(ty) and is_num(want)) or "line" not in n:
            return None
        return self.wrap_fix(n, ty, want)

    def mix_fix(self, n, lt, rt):
        """Convert whichever side can be converted without losing information."""
        return self.wrap_fix(n["l"], lt, rt) or self.wrap_fix(n["r"], rt, lt)
