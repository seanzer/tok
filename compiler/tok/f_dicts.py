"""Dicts: {K: V} with str or integer keys. Values like lists: a copy is independent (copy-on-write), == compares
contents, and entries keep the order they were inserted in."""
from .diag import Diag
from .types import BOOL, ERR, INT, NONE, STR, VOID, mangle, is_int


def key_ok(t):
    return t == STR or is_int(t)


class Dicts:
    def setup(self):
        super().setup()
        for name in ("len", "has", "keys", "values"):
            self.methods[("dict", name)] = getattr(self, "m_dict_" + name)

    def dict_key_type(self, kt, n):
        if kt != ERR and not key_ok(kt):
            self.err("E0110", f"a dict key must be a str or an integer, found {self.type_str(kt)}", n)

    def x_dict(self, n, want):
        if want is not None and want[0] == "opt":
            want = want[1]
        kt, vt = (want[1], want[2]) if want is not None and want[0] == "dict" else (None, None)
        if not n["keys"]:
            if kt is None:
                self.err("E0108", "cannot tell the key and value types of '{}'; annotate it, for example "
                                  "'d: {str: int} = {}'", n)
            ty = ("dict", kt, vt)
            return f"(({self.c(ty)}){{0}})", ty  # an empty dict has no block
        pairs = []
        for kn, vn in zip(n["keys"], n["vals"]):
            kc, kty = self.val(kn, kt)
            if kt is None:
                self.dict_key_type(kty, kn)
                kt = kty
            vc, vty = self.val(vn, vt)
            if vty == VOID:
                self.err("E0103", "this call returns nothing, so it cannot be a dict value", vn)
            if vt is None:
                if vty == NONE:
                    self.err("E0108", "cannot tell the value type from 'none'; annotate the dict, for example "
                                      "'d: {str: int?} = {...}'", vn)
                vt = vty
            pairs.append((kc, kty, kn, vc, vty, vn))
        ty = ("dict", kt, vt)
        if ERR in (kt, vt):
            return "0", ERR
        # every entry is evaluated before any is taken, so an early exit in a later one leaves nothing half-owned
        codes = [(self.coerce(self.take(kc, kty), kty, kt, kn, f"dict keys must all be {self.type_str(kt)}, found {self.type_str(kty)}"),
                  self.coerce(self.take(vc, vty), vty, vt, vn, f"dict values must all be {self.type_str(vt)}, found {self.type_str(vty)}"))
                 for kc, kty, kn, vc, vty, vn in pairs]
        t = self.hoist(f"(({self.c(ty)}){{0}})", ty, own=True)
        for kc, vc in codes:
            self.emit(f"tok_set_{mangle(ty)}(&{t}, {kc}, {vc});")
        return t, ty

    def dict_key(self, n, bt):
        """The key of d[k], as a C temporary that lives to the end of the statement."""
        code, ty = self.val(n["idx"], bt[1])
        code = self.coerce(code, ty, bt[1], n["idx"], f"this dict has {self.type_str(bt[1])} keys, found {self.type_str(ty)}")
        return self.hoist(code, bt[1])

    def dict_index(self, n, bc, bt, has):
        """d[k] is an optional: none when the key is missing."""
        key = self.dict_key(n, bt)
        if n.get("must"):
            # the entry is about to be changed in place (d[k].push(v)): it has to exist
            return self.hoist(f"tok_must_{mangle(bt)}({bc}, {key}, {self.loc(n)})", bt[2]), bt[2]
        ot = bt[2] if bt[2][0] == "opt" else ("opt", bt[2])
        code = f"tok_get_{mangle(bt)}({bc}, {key})"
        if has:
            code = f"({has}true) ? {code} : (({self.c(ot)}){{0}})"
        return self.hoist(code, ot), ot

    def dict_place(self, n, bc, bt, v, root):
        """An existing entry as a place, for d[k] += v, d[k].push(v), d[k].field = v."""
        key = self.dict_key(n, bt)
        return f"(*tok_at_{mangle(bt)}(&{bc}, {key}, {self.loc(n)}))", bt[2], v, root

    def dict_insert(self, target, val, s):
        """d[k] = v: insert or update."""
        outer, self.preps = self.preps, []
        try:
            bc, bt, v, root = self.place(target["base"])
            preps = self.preps
        finally:
            self.preps = outer
        self.need_mutable(v, root, target, "cannot be changed in place")
        key = self.settle(self.stored(target["idx"], bt[1], f"this dict has {self.type_str(bt[1])} keys"), bt[1])
        code = self.settle(self.stored(val, bt[2], f"this dict has {self.type_str(bt[2])} values"), bt[2])
        for p in preps:
            self.emit(p)
        self.emit(f"tok_set_{mangle(bt)}(&{bc}, {key}, {code});")

    def for_dict(self, s, code, ty):
        """for k in d / for k, v in d: over the entries the dict had when the loop started, in insertion order."""
        t = self.stash(self.take(code, ty), ty)
        self.flush()
        self.scopes.append({"$" + t: {"c": t, "ty": ty, "mutable": False, "decl": None, "own": True}})
        i = self.new_tmp()
        self.emit(f"for (int64_t {i} = 0; {i} < tok_len_{mangle(ty)}({t}); {i}++) {{")
        self.emit(f"  {self.c(ty[1])} v_{s['var']} TOK_UNUSED = {t}.p->keys[{i}];")
        extra = None
        if s.get("var2"):
            self.emit(f"  {self.c(ty[2])} v_{s['var2']} TOK_UNUSED = {t}.p->vals[{i}];")
            extra = (s["var2"], ty[2])
        self.loop_body(s, s["var"], ty[1], extra=extra)
        self.emit("}")
        self.emit(self.types.release(ty, t))
        self.scopes.pop()
        self.dead = False

    def m_dict_len(self, n, bc, bt, want):
        self.margs(n, 0)
        return f"tok_len_{mangle(bt)}({bc})", INT

    def m_dict_has(self, n, bc, bt, want):
        self.margs(n, 1)
        a = n["args"][0]
        code, ty = self.val(a, bt[1])
        code = self.coerce(code, ty, bt[1], a, f"this dict has {self.type_str(bt[1])} keys, found {self.type_str(ty)}")
        return f"tok_has_{mangle(bt)}({bc}, {code})", BOOL

    def m_dict_keys(self, n, bc, bt, want):
        self.margs(n, 0)
        lt = ("list", bt[1])
        return self.hoist(f"tok_keys_{mangle(bt)}({bc})", lt, own=True), lt

    def m_dict_values(self, n, bc, bt, want):
        self.margs(n, 0)
        lt = ("list", bt[2])
        return self.hoist(f"tok_vals_{mangle(bt)}({bc})", lt, own=True), lt
