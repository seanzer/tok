"""Type representation and the generated C support code for each type.

Types are tuples:
  ("int",) ("f64",) ("bool",) ("str",) and the other numeric names in NUMS
  ("list", T)  ("opt", T)  ("res", T)  ("rec", module_index, name)
  ("void",) ("none",) ("never",) ("error",)
  ("errv",) an error value: kind + message      ("ekind",) an error kind
"""

INT, F64, BOOL, STR = ("int",), ("f64",), ("bool",), ("str",)
VOID, NONE, NEVER, ERR = ("void",), ("none",), ("never",), ("error",)
# A caught error value (`e` in `call() or e -> ...`) and the kind of an error.
ERRV, EKIND = ("errv",), ("ekind",)

# name -> (C type, kind, bits); kind is "i" signed, "u" unsigned, "f" float
NUMS = {
    "int": ("int64_t", "i", 64),
    "f64": ("double", "f", 64),
    "f32": ("float", "f", 32),
    "i8": ("int8_t", "i", 8),
    "i16": ("int16_t", "i", 16),
    "i32": ("int32_t", "i", 32),
    "u8": ("uint8_t", "u", 8),
    "u16": ("uint16_t", "u", 16),
    "u32": ("uint32_t", "u", 32),
    "u64": ("uint64_t", "u", 64),
}

# Other spellings of the same type. `int` is 64-bit signed; `i64` is the same type, not a second one.
ALIASES = {"i64": "int"}


def is_num(t):
    return t[0] in NUMS


def is_int(t):
    return t[0] in NUMS and NUMS[t[0]][1] in "iu"


def is_float(t):
    return t[0] in NUMS and NUMS[t[0]][1] == "f"


def int_range(t):
    _, kind, bits = NUMS[t[0]]
    if kind == "u":
        return 0, (1 << bits) - 1
    return -(1 << (bits - 1)), (1 << (bits - 1)) - 1


def lossless(src, dst):
    """True if every value of numeric type src is exactly representable in dst."""
    if src == dst:
        return True
    _, sk, sb = NUMS[src[0]]
    _, dk, db = NUMS[dst[0]]
    if sk == "f":
        return dk == "f" and db >= sb
    if dk == "f":
        return sb - (1 if sk == "i" else 0) <= (24 if db == 32 else 53)
    if sk == "u":
        return db > sb if dk == "i" else db >= sb
    return dk == "i" and db >= sb


def comparable(t):
    """Types with an order: numbers and strings. These can be sorted and have a smallest and largest."""
    return t[0] in NUMS or t == STR


def less(t, a, b):
    return f"(strcmp({a}, {b}) < 0)" if t == STR else f"({a} < {b})"


def mangle(t):
    k = t[0]
    if k == "list":
        return "L" + mangle(t[1])
    if k == "opt":
        return "O" + mangle(t[1])
    if k == "res":
        return "E" + mangle(t[1])
    if k == "rec":
        return f"R{t[1]}_{t[2]}"
    return k


def heapy(t, fields_of):
    """True if a value of this type owns a list buffer somewhere inside it."""
    return _heapy(t, fields_of, set())


def _heapy(t, fields_of, seen):
    k = t[0]
    if k == "list":
        return True
    if k in ("opt", "res"):
        return _heapy(t[1], fields_of, seen)
    if k == "rec":
        if t in seen:
            return False
        seen.add(t)
        return any(_heapy(ft, fields_of, seen) for _, ft in fields_of(t))
    return False


class TypeGen:
    """Collects every compound type the program uses and emits its C definition and helpers."""

    def __init__(self, fields_of):
        self.fields_of = fields_of
        self.used = []
        self.seen = set()

    def need(self, t):
        if t in self.seen or t[0] in ("void", "none", "never", "error"):
            return
        self.seen.add(t)
        k = t[0]
        if k in ("list", "opt", "res"):
            self.need(t[1])
            self.used.append(t)
        elif k == "rec":
            for _, ft in self.fields_of(t):
                self.need(ft)
            self.used.append(t)

    # ------------------------------------------------------ C spellings

    def c(self, t):
        k = t[0]
        if k in NUMS:
            return NUMS[k][0]
        if k == "bool":
            return "bool"
        if k == "str":
            return "const char*"
        if k in ("void", "never"):
            return "void"
        if k in ("error", "none"):
            return "int"
        if k == "errv":
            return "tok_error"
        if k == "ekind":
            return "int32_t"
        self.need(t)
        return "tok_T_" + mangle(t)

    def is_heapy(self, t):
        return heapy(t, self.fields_of)

    def copy(self, t, code):
        if self.is_heapy(t):
            self.need(t)
            return f"tok_copy_{mangle(t)}({code})"
        return code

    def eq(self, t, a, b):
        k = t[0]
        if k in NUMS or k in ("bool", "ekind"):
            return f"({a} == {b})"
        if k == "errv":
            return f"tok_error_eq({a}, {b})"
        if k == "str":
            return f"(strcmp({a}, {b}) == 0)"
        if k in ("error", "none", "void", "never"):
            return "true"
        self.need(t)
        return f"tok_eq_{mangle(t)}({a}, {b})"

    def to_str(self, t, code):
        k = t[0]
        if k == "str":
            return code
        if k == "bool":
            return f"(({code}) ? \"true\" : \"false\")"
        if k in NUMS:
            _, kind, bits = NUMS[k]
            if kind == "i":
                return f"tok_str_i64((int64_t)({code}))"
            if kind == "u":
                return f"tok_str_u64((uint64_t)({code}))"
            return f"tok_str_f{bits}({code})"
        if k in ("error", "none", "void", "never"):
            return "\"none\""
        if k == "ekind":
            return f"tok_kind_name({code})"
        if k == "errv":
            return f"tok_error_str({code})"
        self.need(t)
        return f"tok_str_{mangle(t)}({code})"

    def to_repr(self, t, code):
        """Like to_str, but strings are quoted (used inside lists and records)."""
        if t[0] == "str":
            return f"tok_quote({code})"
        return self.to_str(t, code)

    # ------------------------------------------------------ emission

    def emit(self):
        out = []
        # Emitting helpers can register more types (copy/eq/str of members), so iterate to a fixed point.
        bodies, done, order = [], set(), []
        i = 0
        while i < len(self.used):
            t = self.used[i]
            i += 1
            bodies.append(self._helpers(t))
        for t in self.used:
            out.append(f"typedef struct tok_T_{mangle(t)} tok_T_{mangle(t)};")
        for t in self.used:
            self._define(t, done, order)
        out.extend(order)
        protos, defs = [], []
        for ps in bodies:
            for proto, body in ps:
                protos.append(proto + ";")
                defs.append(proto + " " + body)
        return "\n".join(out + [""] + protos + [""] + defs) + "\n"

    def _define(self, t, done, order):
        if t in done or t[0] not in ("list", "opt", "res", "rec"):
            return
        done.add(t)
        m, k = mangle(t), t[0]
        if k == "list":
            order.append(f"struct tok_T_{m} {{ {self.c(t[1])}* data; int64_t len; int64_t cap; }};")
            self._define(t[1], done, order)
        elif k == "opt":
            self._define(t[1], done, order)
            order.append(f"struct tok_T_{m} {{ bool has; {self.c(t[1])} v; }};")
        elif k == "res":
            self._define(t[1], done, order)
            if t[1] == VOID:
                order.append(f"struct tok_T_{m} {{ const char* err; int32_t kind; }};")
            else:
                order.append(f"struct tok_T_{m} {{ const char* err; int32_t kind; {self.c(t[1])} v; }};")
        else:
            fields = self.fields_of(t)
            for _, ft in fields:
                self._define(ft, done, order)
            body = " ".join(f"{self.c(ft)} f_{fn};" for fn, ft in fields) or "char tok_empty;"
            order.append(f"struct tok_T_{m} {{ {body} }};")

    def _helpers(self, t):
        m, k = mangle(t), t[0]
        T = f"tok_T_{m}"
        S = "TOK_UNUSED static"
        hs = []
        if k == "res":
            return hs
        if k == "list":
            e = t[1]
            E = self.c(e)
            hs.append((f"{S} {T} tok_new_{m}(int64_t cap)",
                       f"{{ {T} l; l.len = 0; l.cap = cap; l.data = ({E}*)tok_alloc((size_t)(cap > 0 ? cap : 1) * sizeof({E})); return l; }}"))
            hs.append((f"{S} void tok_push_{m}({T}* l, {E} v)",
                       f"{{ if (l->len == l->cap) {{ int64_t cap = l->cap < 4 ? 4 : l->cap * 2; "
                       f"{E}* d = ({E}*)tok_alloc((size_t)cap * sizeof({E})); "
                       f"if (l->len > 0) memcpy(d, l->data, (size_t)l->len * sizeof({E})); l->data = d; l->cap = cap; }} "
                       f"l->data[l->len++] = v; }}"))
            hs.append((f"{S} {E} tok_at_{m}({T} l, int64_t i, const char* loc)",
                       "{ tok_bounds(i, l.len, loc); return l.data[i]; }"))
            if self.is_heapy(e):
                cp = f"for (int64_t i = 0; i < l.len; i++) r.data[i] = {self.copy(e, 'l.data[i]')};"
            else:
                cp = f"if (l.len > 0) memcpy(r.data, l.data, (size_t)l.len * sizeof({E}));"
            hs.append((f"{S} {T} tok_copy_{m}({T} l)", f"{{ {T} r = tok_new_{m}(l.len); {cp} r.len = l.len; return r; }}"))
            hs.append((f"{S} {T} tok_first_{m}({T} l, int64_t n)",
                       f"{{ if (n < 0) n = 0; if (n > l.len) n = l.len; {T} r = tok_new_{m}(n); "
                       f"if (n > 0) memcpy(r.data, l.data, (size_t)n * sizeof({E})); r.len = n; return r; }}"))
            hs.append((f"{S} bool tok_eq_{m}({T} a, {T} b)",
                       f"{{ if (a.len != b.len) return false; for (int64_t i = 0; i < a.len; i++) "
                       f"if (!{self.eq(e, 'a.data[i]', 'b.data[i]')}) return false; return true; }}"))
            hs.append((f"{S} bool tok_has_{m}({T} l, {E} x)",
                       f"{{ for (int64_t i = 0; i < l.len; i++) if ({self.eq(e, 'l.data[i]', 'x')}) return true; return false; }}"))
            hs.append((f"{S} {T} tok_slice_{m}({T} l, int64_t a, int64_t b)",
                       f"{{ if (a < 0) a = 0; if (b > l.len) b = l.len; if (b < a) b = a; {T} r = tok_new_{m}(b - a); "
                       f"for (int64_t i = 0; i < b - a; i++) r.data[i] = {self.copy(e, 'l.data[a + i]')}; "
                       f"r.len = b - a; return r; }}"))
            hs.append((f"{S} {T} tok_rev_{m}({T} l)",
                       f"{{ {T} r = tok_new_{m}(l.len); for (int64_t i = 0; i < l.len; i++) "
                       f"r.data[i] = {self.copy(e, 'l.data[l.len - 1 - i]')}; r.len = l.len; return r; }}"))
            hs.append((f"{S} int64_t tok_find_{m}({T} l, {E} x)",
                       f"{{ for (int64_t i = 0; i < l.len; i++) if ({self.eq(e, 'l.data[i]', 'x')}) return i; return -1; }}"))
            hs.append((f"{S} {T} tok_uniq_{m}({T} l)",
                       f"{{ {T} r = tok_new_{m}(l.len); for (int64_t i = 0; i < l.len; i++) {{ bool dup = false; "
                       f"for (int64_t j = 0; j < r.len; j++) if ({self.eq(e, 'r.data[j]', 'l.data[i]')}) {{ dup = true; break; }} "
                       f"if (!dup) r.data[r.len++] = {self.copy(e, 'l.data[i]')}; }} return r; }}"))
            if comparable(e):
                # the order of the elements as a list of indexes: a stable merge sort
                self.need(("list", INT))
                hs.append((f"{S} tok_T_Lint tok_order_{m}({T} l)",
                           "{ tok_T_Lint r = tok_new_Lint(l.len); for (int64_t i = 0; i < l.len; i++) r.data[i] = i; "
                           "r.len = l.len; int64_t* tmp = (int64_t*)tok_alloc((size_t)(l.len > 0 ? l.len : 1) * sizeof(int64_t)); "
                           "for (int64_t w = 1; w < l.len; w *= 2) { for (int64_t lo = 0; lo < l.len; lo += 2 * w) { "
                           "int64_t mid = lo + w < l.len ? lo + w : l.len, hi = lo + 2 * w < l.len ? lo + 2 * w : l.len; "
                           "int64_t i = lo, j = mid, k = lo; while (i < mid && j < hi) { "
                           f"if ({less(e, 'l.data[r.data[j]]', 'l.data[r.data[i]]')}) tmp[k++] = r.data[j++]; "
                           "else tmp[k++] = r.data[i++]; } while (i < mid) tmp[k++] = r.data[i++]; "
                           "while (j < hi) tmp[k++] = r.data[j++]; } "
                           "memcpy(r.data, tmp, (size_t)l.len * sizeof(int64_t)); } return r; }"))
            hs.append((f"{S} const char* tok_str_{m}({T} l)",
                       f"{{ const char* s = \"[\"; for (int64_t i = 0; i < l.len; i++) "
                       f"s = tok_fmt(\"%s%s%s\", s, i ? \", \" : \"\", {self.to_repr(e, 'l.data[i]')}); "
                       f"return tok_fmt(\"%s]\", s); }}"))
        elif k == "opt":
            e = t[1]
            if self.is_heapy(t):
                hs.append((f"{S} {T} tok_copy_{m}({T} o)", f"{{ if (o.has) o.v = {self.copy(e, 'o.v')}; return o; }}"))
            hs.append((f"{S} bool tok_eq_{m}({T} a, {T} b)",
                       f"{{ if (a.has != b.has) return false; return !a.has || {self.eq(e, 'a.v', 'b.v')}; }}"))
            hs.append((f"{S} const char* tok_str_{m}({T} o)",
                       f"{{ return o.has ? {self.to_str(e, 'o.v')} : \"none\"; }}"))
        else:
            fields = self.fields_of(t)
            if self.is_heapy(t):
                cp = " ".join(f"r.f_{fn} = {self.copy(ft, 'r.f_' + fn)};" for fn, ft in fields if self.is_heapy(ft))
                hs.append((f"{S} {T} tok_copy_{m}({T} r)", f"{{ {cp} return r; }}"))
            eq = " && ".join(self.eq(ft, f"a.f_{fn}", f"b.f_{fn}") for fn, ft in fields) or "true"
            hs.append((f"{S} bool tok_eq_{m}({T} a, {T} b)", f"{{ (void)a; (void)b; return {eq}; }}"))
            fmt = ", ".join(f"{fn}: %s" for fn, _ in fields)
            args = "".join(", " + self.to_repr(ft, f"r.f_{fn}") for fn, ft in fields)
            hs.append((f"{S} const char* tok_str_{m}({T} r)",
                       f"{{ (void)r; return tok_fmt(\"{{{fmt}}}\"{args}); }}" if fields else "{ (void)r; return \"{}\"; }"))
        return hs
