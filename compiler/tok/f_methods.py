"""Lambdas and the built-in methods on lists and strings.

A lambda is not a first-class value in this compiler: it can only be written
directly as the argument of a list method, where it is compiled inline as a loop.
"""
from .diag import Diag
from .types import BOOL, ERR, F64, INT, NUMS, STR, VOID, is_num, mangle

# name -> (parameter types, result type, C function, wants a source location)
STR_METHODS = {
    "len": ((), INT, "tok_s_len", False),
    "contains": ((STR,), BOOL, "tok_s_contains", False),
    "starts_with": ((STR,), BOOL, "tok_s_starts_with", False),
    "ends_with": ((STR,), BOOL, "tok_s_ends_with", False),
    "trim": ((), STR, "tok_s_trim", False),
    "upper": ((), STR, "tok_s_upper", False),
    "lower": ((), STR, "tok_s_lower", False),
    "lines": ((), ("list", STR), "tok_s_lines", False),
    "split": ((STR,), ("list", STR), "tok_s_split", True),
    "replace": ((STR, STR), STR, "tok_s_replace", True),
    "find": ((STR,), ("opt", INT), "tok_s_find", False),
    "to_int": ((), ("opt", INT), "tok_s_to_int", False),
    "to_f64": ((), ("opt", F64), "tok_s_to_f64", False),
    "chars": ((), ("list", STR), "tok_s_chars", False),
    "words": ((), ("list", STR), "tok_s_words", False),
    "reverse": ((), STR, "tok_s_reverse", False),
    "repeat": ((INT,), STR, "tok_s_repeat", True),
    "first": ((INT,), STR, "tok_s_first", False),
    "last": ((INT,), STR, "tok_s_last", False),
    "drop": ((INT,), STR, "tok_s_drop", False),
}


class Methods:
    def setup(self):
        super().setup()
        for name in STR_METHODS:
            self.methods[("str", name)] = self.m_str
        for name in ("map", "where", "any", "all", "first", "contains", "join", "sum"):
            self.methods[("list", name)] = getattr(self, "m_list_" + name)

    def m_str(self, n, bc, bt, want):
        params, ret, cfn, wants_loc = STR_METHODS[n["name"]]
        self.margs(n, len(params))
        args = [bc]
        for an, pt in zip(n["args"], params):
            code, ty = self.val(an, pt)
            args.append(self.coerce(code, ty, pt, an, f"'{n['name']}' needs {self.type_str(pt)}, found {self.type_str(ty)}"))
        if wants_loc:
            args.append(self.loc(n))
        self.types.need(ret)
        n["fresh"] = True
        return self.hoist(f"{cfn}({', '.join(args)})", ret), ret

    # ---------------------------------------------------------- lambdas

    def lam_body(self, n, bt, as_cond=False):
        """Compile the lambda argument of a list method. Returns (captured lines, code, type, lambda node)."""
        self.margs(n, 1)
        lam = n["args"][0]
        if lam["k"] != "lam":
            self.err("E0113", f"'{n['name']}' needs a lambda, for example xs.{n['name']}(x -> ...)", lam)
        if lam["body"] is None:
            self.err("E0113", f"'{n['name']}' needs a one-line lambda; a block after '->' is only for 'call() or e ->'", lam)
        pt = lam["param_tok"]
        self.check_new_name(lam["param"], {"line": pt.line, "col": pt.col, "end": pt.end}, "lambda parameter")
        with self.capture(1) as pre:
            self.scopes.append({})
            self.define(lam["param"], bt[1], False, None, self.var_rooted(n["base"]))
            try:
                if as_cond:
                    code, ty = self.cond(lam["body"]), BOOL
                else:
                    code, ty = self.val(lam["body"])
                    if ty == VOID:
                        self.err("E0103", "the lambda returns nothing, so there is nothing to collect", lam["body"])
                    code = self.store(code, ty, lam["body"], False)
            finally:
                self.scopes.pop()
        return pre, code, ty, lam

    def loop(self, bc, bt, lam, pre):
        i = self.new_tmp()
        self.emit(f"for (int64_t {i} = 0; {i} < {bc}.len; {i}++) {{")
        self.emit(f"  {self.c(bt[1])} v_{lam['param']} TOK_UNUSED = {bc}.data[{i}];")
        self.buf.extend(pre)
        return i

    def m_list_map(self, n, bc, bt, want):
        pre, code, ty, lam = self.lam_body(n, bt)
        if ty == ERR:
            return "0", ERR
        out_ty = ("list", ty)
        out = self.hoist(f"tok_new_{mangle(out_ty)}({bc}.len)", out_ty)
        i = self.loop(bc, bt, lam, pre)
        self.emit(f"  {out}.data[{i}] = {code};")
        self.emit("}")
        self.emit(f"{out}.len = {bc}.len;")
        n["fresh"] = not self.types.is_heapy(ty) or self.fresh(lam["body"])
        return out, out_ty

    def m_list_where(self, n, bc, bt, want):
        pre, code, _, lam = self.lam_body(n, bt, as_cond=True)
        out = self.hoist(f"tok_new_{mangle(bt)}(0)", bt)
        self.loop(bc, bt, lam, pre)
        elem = f"v_{lam['param']}"
        owned = self.var_rooted(n["base"]) and self.types.is_heapy(bt[1])
        if owned:
            elem = self.types.copy(bt[1], elem)
        self.emit(f"  if ({code}) tok_push_{mangle(bt)}(&{out}, {elem});")
        self.emit("}")
        n["fresh"] = owned or not self.types.is_heapy(bt[1])
        return out, bt

    def quantifier(self, n, bc, bt, is_any):
        pre, code, _, lam = self.lam_body(n, bt, as_cond=True)
        out = self.hoist("false" if is_any else "true", BOOL)
        self.loop(bc, bt, lam, pre)
        if is_any:
            self.emit(f"  if ({code}) {{ {out} = true; break; }}")
        else:
            self.emit(f"  if (!({code})) {{ {out} = false; break; }}")
        self.emit("}")
        return out, BOOL

    def m_list_any(self, n, bc, bt, want):
        return self.quantifier(n, bc, bt, True)

    def m_list_all(self, n, bc, bt, want):
        return self.quantifier(n, bc, bt, False)

    # ---------------------------------------------------------- other list methods

    def m_list_first(self, n, bc, bt, want):
        self.margs(n, 1)
        code, ty = self.val(n["args"][0], INT)
        code = self.coerce(code, ty, INT, n["args"][0], f"'first' needs an int count, found {self.type_str(ty)}")
        out = self.hoist(f"tok_first_{mangle(bt)}({bc}, {code})", bt)
        n["fresh"] = not self.types.is_heapy(bt[1])
        if self.var_rooted(n["base"]) and self.types.is_heapy(bt[1]):
            out, n["fresh"] = self.hoist(self.types.copy(bt, out), bt), True
        return out, bt

    def m_list_contains(self, n, bc, bt, want):
        self.margs(n, 1)
        code, ty = self.val(n["args"][0], bt[1])
        code = self.coerce(code, ty, bt[1], n["args"][0],
                           f"'contains' on {self.type_str(bt)} needs {self.type_str(bt[1])}, found {self.type_str(ty)}")
        return self.hoist(f"tok_has_{mangle(bt)}({bc}, {code})", BOOL), BOOL

    def m_list_join(self, n, bc, bt, want):
        self.margs(n, 1)
        if bt[1] != STR:
            if not (is_num(bt[1]) or bt[1] == BOOL):
                raise Diag("E0109", f"'join' works on lists of strs, numbers or bools, found {self.type_str(bt)}; map the "
                                    f"elements to strings first, for example xs.map(x -> \"\\{{x\\}}\")".replace("\\{{", "{").replace("\\}}", "}"),
                           n["line"], n["name_col"], n["end"])
            bc = self.joinable(bc, bt)
        code, ty = self.val(n["args"][0], STR)
        code = self.coerce(code, ty, STR, n["args"][0], f"'join' needs a str separator, found {self.type_str(ty)}")
        return self.hoist(f"tok_s_join({bc}, {code})", STR), STR

    def m_list_sum(self, n, bc, bt, want):
        self.margs(n, 0)
        e = bt[1]
        if not is_num(e):
            raise Diag("E0109", f"'sum' works on lists of numbers, found {self.type_str(bt)}", n["line"], n["name_col"], n["end"])
        acc = self.hoist(f"(({NUMS[e[0]][0]})0)", e)
        i = self.new_tmp()
        item = f"{bc}.data[{i}]"
        step = f"({acc} + {item})" if NUMS[e[0]][1] == "f" else self.int_op("+", acc, item, e, n)
        self.emit(f"for (int64_t {i} = 0; {i} < {bc}.len; {i}++) {acc} = {step};")
        return acc, e
