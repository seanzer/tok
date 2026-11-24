"""Modules (other .tok files, found by qualified use), the fs module, and the math functions."""
import os

from .diag import Diag
from .types import ERR, NUMS, STR, VOID, is_float, is_int, is_num


class Modules:
    def setup(self):
        super().setup()
        self.builtin_mods["fs"] = {
            "read_text": ((STR,), ("res", STR), "tok_fs_read_text", "fs"),
            "write_text": ((STR, STR), ("res", VOID), "tok_fs_write_text", "fs"),
        }
        for name in ("sqrt", "mean", "min", "max", "abs"):
            self.free_fns[name] = getattr(self, "b_" + name)

    # ---------------------------------------------------------- user modules

    def get_module(self, name, n):
        if name in self.builtin_mods:
            self.err("E0110", f"the built-in module '{name}' has no types", n)
        mod = self.mods.get(name)
        if mod is None:
            path = os.path.join(os.path.dirname(os.path.abspath(self.entry.path)), name + ".tok")
            try:
                with open(path, encoding="utf-8") as fh:
                    src = fh.read()
            except (OSError, UnicodeDecodeError):
                known = ", ".join(sorted(self.builtin_mods))
                self.err("E0501", f"unknown name or module '{name}': there is no variable '{name}', no built-in module "
                                  f"'{name}' (built in: {known}), and no file {name}.tok next to "
                                  f"{os.path.basename(self.entry.path)}", n)
            shown = os.path.join(os.path.dirname(self.entry.path), name + ".tok")
            mod = self.add_module(name, shown, src)
        return mod

    def module_call(self, n, want):
        base = n["base"]
        name = base["name"]
        shown = f"{name}.{n['name']}"
        if name in self.builtin_mods:
            fns = self.builtin_mods[name]
            if n["name"] not in fns:
                raise Diag("E0101", f"module '{name}' has no function '{n['name']}' (functions: {', '.join(sorted(fns))})",
                           n["line"], n["name_col"], n["end"])
            return self.builtin_call(n, shown, *fns[n["name"]])
        if name in self.mod.failed:
            for a in n["args"]:
                self.expr(a)
            return "0", ERR
        mod = self.get_module(name, base)
        f = mod.fns.get(n["name"])
        if f is None:
            if n["name"] in mod.failed or mod.broken:
                for a in n["args"]:
                    self.expr(a)
                return "0", ERR
            hint = f" (functions: {', '.join(sorted(mod.fns))})" if mod.fns else ""
            raise Diag("E0101", f"module '{name}' has no function '{n['name']}'{hint}", n["line"], n["name_col"], n["end"])
        return self.call_fn(f, n, shown)

    def builtin_call(self, n, shown, params, ret, cfn, effect):
        if len(n["args"]) != len(params):
            self.err("E0104", f"'{shown}' takes {len(params)} argument(s), found {len(n['args'])}", n)
        args = []
        for an, pt in zip(n["args"], params):
            code, ty = self.val(an, pt)
            args.append(self.coerce(code, ty, pt, an, f"'{shown}' needs {self.type_str(pt)}, found {self.type_str(ty)}"))
        if effect:
            self.need_effect(effect, n, shown)
        self.types.need(ret)
        n["fresh"] = True
        return self.hoist(f"{cfn}({', '.join(args)})", ret), ret

    # ---------------------------------------------------------- math

    def nargs(self, n, counts, usage):
        if len(n["args"]) not in counts:
            self.err("E0104", f"{usage}, found {len(n['args'])} argument(s)", n)

    def b_sqrt(self, n, want):
        self.nargs(n, (1,), "sqrt takes 1 argument")
        w = self.num_want(want)
        code, ty = self.val(n["args"][0], w if w is not None and is_float(w) else ("f64",))
        if ty == ERR:
            return "0", ERR
        if not is_float(ty):
            self.err("E0103" if not is_num(ty) else "E0106", f"sqrt needs f64 or f32, found {self.type_str(ty)}",
                     n["args"][0], self.conv_fix(n["args"][0], ty, ("f64",)) if is_num(ty) else None)
        return (f"sqrt({code})" if ty == ("f64",) else f"sqrtf({code})"), ty

    def b_mean(self, n, want):
        self.nargs(n, (1,), "mean takes 1 argument (a list of f64 or f32)")
        w = self.num_want(want)
        arg = n["args"][0]
        # a list written out with only literals is taken as floats, so mean([1, 2]) works
        all_literals = arg["k"] == "list" and all(self.is_literal(i) for i in arg["items"])
        code, ty = self.val(arg, ("list", w if w is not None and is_float(w) else ("f64",)) if all_literals else None)
        if ty == ERR:
            return "0", ERR
        if ty[0] != "list" or not is_float(ty[1]):
            self.err("E0103", f"mean needs a list of f64 or f32, found {self.type_str(ty)}", n["args"][0])
        e = ty[1]
        ct = NUMS[e[0]][0]
        # an empty list has no mean: the result is an optional
        out_ty = ("opt", e)
        out = self.hoist(f"(({self.c(out_ty)}){{0}})", out_ty)
        i = self.new_tmp()
        self.emit(f"if ({code}.len > 0) {{")
        self.emit(f"  {ct} {out}_s = 0;")
        self.emit(f"  for (int64_t {i} = 0; {i} < {code}.len; {i}++) {out}_s += {code}.data[{i}];")
        self.emit(f"  {out}.has = true;")
        self.emit(f"  {out}.v = {out}_s / ({ct}){code}.len;")
        self.emit("}")
        return out, out_ty

    def minmax(self, n, want, op):
        name = n["name"]
        self.nargs(n, (1, 2), f"{name} takes a list of numbers, or 2 numbers")
        args = n["args"]
        if len(args) == 1:
            w = self.num_want(want)
            code, ty = self.val(args[0], ("list", w) if w is not None else None)
            code = self.hoist(code, ty) if ty != ERR else code
            if ty == ERR:
                return "0", ERR
            if ty[0] != "list" or not is_num(ty[1]):
                self.err("E0103", f"{name} with 1 argument needs a list of numbers, found {self.type_str(ty)}", args[0])
            # an empty list has no smallest or largest element: the result is an optional
            out_ty = ("opt", ty[1])
            out = self.hoist(f"(({self.c(out_ty)}){{0}})", out_ty)
            i = self.new_tmp()
            self.emit(f"if ({code}.len > 0) {{")
            self.emit(f"  {out}.has = true;")
            self.emit(f"  {out}.v = {code}.data[0];")
            self.emit(f"  for (int64_t {i} = 1; {i} < {code}.len; {i}++) "
                      f"if ({code}.data[{i}] {op} {out}.v) {out}.v = {code}.data[{i}];")
            self.emit("}")
            return out, out_ty
        a, b = args
        if self.is_literal(a) and not self.is_literal(b):
            bc, bt = self.val(b, want)
            ac, at = self.val(a, bt)
        else:
            ac, at = self.val(a, want)
            bc, bt = self.val(b, at)
        if at == ERR or bt == ERR:
            return "0", ERR
        if not is_num(at) or not is_num(bt):
            self.err("E0103", f"{name} needs numbers, found {self.type_str(at)} and {self.type_str(bt)}", n)
        if at != bt:
            self.err("E0106", f"{name} needs the same type for both arguments, found {self.type_str(at)} and {self.type_str(bt)}",
                     n, self.mix_fix({"l": a, "r": b}, at, bt))
        ta, tb = self.hoist(ac, at), self.hoist(bc, bt)
        return f"({tb} {op} {ta} ? {tb} : {ta})", at

    def b_min(self, n, want):
        return self.minmax(n, want, "<")

    def b_max(self, n, want):
        return self.minmax(n, want, ">")

    def b_abs(self, n, want):
        self.nargs(n, (1,), "abs takes 1 argument")
        code, ty = self.val(n["args"][0], want)
        if ty == ERR:
            return "0", ERR
        if not is_num(ty) or NUMS[ty[0]][1] == "u":
            self.err("E0103", f"abs needs a signed number, found {self.type_str(ty)}", n["args"][0])
        t = self.hoist(code, ty)
        if ty == ("int",):
            return f"({t} < 0 ? tok_neg_int({t}, {self.loc(n)}) : {t})", ty
        if is_int(ty):
            # like the other operations on sized integers, abs of the smallest value wraps
            ct = NUMS[ty[0]][0]
            return f"({t} < 0 ? ({ct})(0 - (uint64_t){t}) : {t})", ty
        return f"({t} < 0 ? -{t} : {t})", ty
