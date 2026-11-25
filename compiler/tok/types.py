"""Type representation and the generated C support code for each type.

Types are tuples:
  ("int",) ("f64",) ("bool",) ("str",) and the other numeric names in NUMS
  ("list", T)  ("dict", K, V)  ("opt", T)  ("res", T)  ("rec", module_index, name)
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


# How each scalar is written into a string builder: (C function, cast)
def _show_num(t):
    _, kind, bits = NUMS[t[0]]
    if kind == "i":
        return "tok_sb_add_i64", "(int64_t)"
    if kind == "u":
        return "tok_sb_add_u64", "(uint64_t)"
    return f"tok_sb_add_f{bits}", ""


def mangle(t):
    k = t[0]
    if k == "list":
        return "L" + mangle(t[1])
    if k == "dict":
        return "D" + mangle(t[1]) + "_" + mangle(t[2])
    if k == "opt":
        return "O" + mangle(t[1])
    if k == "res":
        return "E" + mangle(t[1])
    if k == "rec":
        return f"R{t[1]}_{t[2]}"
    return k


def managed(t, fields_of):
    """True if a value of this type holds a counted reference: a string or a list buffer, anywhere inside it."""
    return _managed(t, fields_of, set())


def _managed(t, fields_of, seen):
    k = t[0]
    if k in ("list", "dict", "str", "errv", "res"):
        return True
    if k == "opt":
        return _managed(t[1], fields_of, seen)
    if k == "rec":
        if t in seen:
            return False
        seen.add(t)
        return any(_managed(ft, fields_of, seen) for _, ft in fields_of(t))
    return False


class TypeGen:
    """Collects every compound type the program uses and emits its C definition and helpers."""

    def __init__(self, fields_of):
        self.fields_of = fields_of
        self.used = []
        self.seen = set()
        self._managed = {}

    def need(self, t):
        if t in self.seen or t[0] in ("void", "none", "never", "error"):
            return
        self.seen.add(t)
        k = t[0]
        if k in ("list", "opt", "res"):
            self.need(t[1])
            self.used.append(t)
        elif k == "dict":
            for part in (t[1], t[2], ("list", t[1]), ("list", t[2]), t[2] if t[2][0] == "opt" else ("opt", t[2])):
                self.need(part)
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

    # ------------------------------------------------------ ownership

    def managed(self, t):
        r = self._managed.get(t)
        if r is None:
            r = self._managed[t] = managed(t, self.fields_of)
        return r

    def retain(self, t, code):
        """A C statement that adds one owner to the value."""
        if not self.managed(t):
            return ""
        if t == STR:
            return f"tok_sretain({code});"
        if t == ERRV:
            return f"tok_sretain({code}.msg);"
        self.need(t)
        return f"tok_retain_{mangle(t)}({code});"

    def release(self, t, code):
        """A C statement that drops one owner; the last one frees the memory."""
        if not self.managed(t):
            return ""
        if t == STR:
            return f"tok_srelease({code});"
        if t == ERRV:
            return f"tok_srelease({code}.msg);"
        self.need(t)
        return f"tok_release_{mangle(t)}({code});"

    def ret(self, t, code):
        """A C expression: the value, with one more owner."""
        if not self.managed(t):
            return code
        if t == STR:
            return f"tok_sret({code})"
        if t == ERRV:
            return f"tok_error_ret({code})"
        self.need(t)
        return f"tok_ret_{mangle(t)}({code})"

    # ------------------------------------------------------ comparing and showing

    def eq(self, t, a, b):
        k = t[0]
        if k in NUMS or k in ("bool", "ekind"):
            return f"({a} == {b})"
        if k == "errv":
            return f"tok_error_eq({a}, {b})"
        if k == "str":
            return f"tok_seq({a}, {b})"
        if k in ("error", "none", "void", "never"):
            return "true"
        self.need(t)
        return f"tok_eq_{mangle(t)}({a}, {b})"

    def show(self, t, code, b, quoted=False):
        """A C statement that writes the text of a value into the string builder `b` (a pointer expression)."""
        k = t[0]
        if k == "str":
            if quoted:
                return f"tok_sb_addc({b}, '\"'); tok_sb_adds({b}, {code}); tok_sb_addc({b}, '\"');"
            return f"tok_sb_adds({b}, {code});"
        if k == "bool":
            return f"tok_sb_add_bool({b}, {code});"
        if k in NUMS:
            fn, cast = _show_num(t)
            return f"{fn}({b}, {cast}({code}));"
        if k in ("error", "none", "void", "never"):
            return f"tok_sb_add({b}, \"none\", 4);"
        if k == "ekind":
            return f"tok_sb_adds({b}, tok_kinds[{code}]);"
        if k == "errv":
            return f"tok_show_error({b}, {code});"
        self.need(t)
        return f"tok_show_{mangle(t)}({b}, {code});"

    def panic_text(self, t, code):
        """A C string (not a Tok string) with the text of an integer, for a panic message."""
        return f"tok_cstr_u64((uint64_t)({code}))" if NUMS[t[0]][1] == "u" else f"tok_cstr_i64((int64_t)({code}))"

    # ------------------------------------------------------ emission

    def emit(self):
        out = []
        # Emitting helpers can register more types (members), so iterate to a fixed point.
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
        if t in done or t[0] not in ("list", "dict", "opt", "res", "rec"):
            return
        done.add(t)
        m, k = mangle(t), t[0]
        if k == "dict":
            # the value is one pointer to a counted block: keys and values in insertion order, and a hash index
            order.append(f"struct tok_T_{m} {{ struct tok_B_{m}* p; }};")
            order.append(f"struct tok_B_{m} {{ int64_t rc; int64_t len; int64_t cap; int64_t nslots; "
                         f"{self.c(t[1])}* keys; {self.c(t[2])}* vals; uint32_t* slots; }};")
            self._define(t[1], done, order)
            self._define(t[2], done, order)
            return
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
        SI = "TOK_UNUSED static inline"
        hs = []
        if k == "dict":
            return self._dict_helpers(t)
        if self.managed(t) and k != "list":
            hs.append((f"{SI} {T} tok_ret_{m}({T} v)", f"{{ tok_retain_{m}(v); return v; }}"))
        if k == "res":
            rel = self.release(t[1], "r.v") if t[1] != VOID else ""
            hs.append((f"{S} void tok_retain_{m}({T} r)", f"{{ tok_sretain(r.err); {self.retain(t[1], 'r.v') if t[1] != VOID else ''} }}"))
            hs.append((f"{S} void tok_release_{m}({T} r)", f"{{ tok_srelease(r.err); {rel} }}"))
            return hs
        if k == "list":
            e = t[1]
            E = self.c(e)
            me = self.managed(e)
            hs.append((f"{SI} void tok_retain_{m}({T} l)", "{ if (l.data) TOK_LH(l.data)->rc++; }"))
            hs.append((f"{SI} {T} tok_ret_{m}({T} l)", "{ if (l.data) TOK_LH(l.data)->rc++; return l; }"))
            drop = f"for (int64_t i = 0; i < l.len; i++) {{ {self.release(e, 'l.data[i]')} }} " if me else ""
            hs.append((f"{S} void tok_free_{m}({T} l)", f"{{ {drop}tok_lfree(l.data); }}"))
            hs.append((f"{SI} void tok_release_{m}({T} l)",
                       f"{{ if (l.data && --TOK_LH(l.data)->rc == 0) tok_free_{m}(l); }}"))
            hs.append((f"{SI} {T} tok_new_{m}(int64_t cap)",
                       f"{{ {T} l; l.len = 0; l.cap = cap > 0 ? cap : 0; "
                       f"l.data = cap > 0 ? ({E}*)tok_lalloc(cap, sizeof({E})) : NULL; return l; }}"))
            # make the buffer this list's own, with room for `extra` more elements
            if me:
                cp = f"for (int64_t i = 0; i < l->len; i++) d[i] = {self.ret(e, 'l->data[i]')};"
            else:
                cp = f"if (l->len > 0) memcpy(d, l->data, (size_t)l->len * sizeof({E}));"
            hs.append((f"{S} void tok_grow_{m}({T}* l, int64_t extra)",
                       f"{{ int64_t need = l->len + extra, cap = l->cap; "
                       f"if (cap < need) {{ cap = cap * 2; if (cap < need) cap = need; if (cap < 4) cap = 4; }} "
                       f"if (!l->data) {{ if (cap > 0) l->data = ({E}*)tok_lalloc(cap, sizeof({E})); }} "
                       f"else if (TOK_LH(l->data)->rc == 1) {{ if (cap != l->cap) l->data = ({E}*)tok_lrealloc(l->data, cap, sizeof({E})); }} "
                       f"else {{ {E}* d = ({E}*)tok_lalloc(cap > 0 ? cap : 1, sizeof({E})); {cp} TOK_LH(l->data)->rc--; l->data = d; }} "
                       f"l->cap = cap; }}"))
            hs.append((f"{SI} void tok_push_{m}({T}* l, {E} v)",
                       f"{{ if (TOK_UNLIKELY(l->len == l->cap || TOK_LH(l->data)->rc != 1)) tok_grow_{m}(l, 1); "
                       f"l->data[l->len++] = v; }}"))
            # before writing an element in place: the buffer must not be shared
            hs.append((f"{SI} void tok_own_{m}({T}* l)",
                       f"{{ if (TOK_UNLIKELY(l->data && TOK_LH(l->data)->rc != 1)) tok_grow_{m}(l, 0); }}"))
            hs.append((f"{SI} {E} tok_at_{m}({T} l, int64_t i, const char* loc)",
                       "{ tok_bounds(i, l.len, loc); return l.data[i]; }"))
            hs.append((f"{S} bool tok_eq_{m}({T} a, {T} b)",
                       f"{{ if (a.len != b.len) return false; if (a.data == b.data) return true; for (int64_t i = 0; i < a.len; i++) "
                       f"if (!{self.eq(e, 'a.data[i]', 'b.data[i]')}) return false; return true; }}"))
            hs.append((f"{S} bool tok_has_{m}({T} l, {E} x)",
                       f"{{ for (int64_t i = 0; i < l.len; i++) if ({self.eq(e, 'l.data[i]', 'x')}) return true; return false; }}"))
            if me:
                cps = f"for (int64_t i = 0; i < b - a; i++) r.data[i] = {self.ret(e, 'l.data[a + i]')};"
            else:
                cps = f"if (b > a) memcpy(r.data, l.data + a, (size_t)(b - a) * sizeof({E}));"
            hs.append((f"{S} {T} tok_slice_{m}({T} l, int64_t a, int64_t b)",
                       f"{{ if (a < 0) a = 0; if (b > l.len) b = l.len; if (b < a) b = a; "
                       f"if (a == 0 && b == l.len) return tok_ret_{m}(l); {T} r = tok_new_{m}(b - a); "
                       f"{cps} r.len = b - a; return r; }}"))
            hs.append((f"{S} {T} tok_first_{m}({T} l, int64_t n)", f"{{ return tok_slice_{m}(l, 0, n < 0 ? 0 : n); }}"))
            hs.append((f"{S} {T} tok_rev_{m}({T} l)",
                       f"{{ {T} r = tok_new_{m}(l.len); for (int64_t i = 0; i < l.len; i++) "
                       f"r.data[i] = {self.ret(e, 'l.data[l.len - 1 - i]')}; r.len = l.len; return r; }}"))
            hs.append((f"{S} int64_t tok_find_{m}({T} l, {E} x)",
                       f"{{ for (int64_t i = 0; i < l.len; i++) if ({self.eq(e, 'l.data[i]', 'x')}) return i; return -1; }}"))
            hs.append((f"{S} {T} tok_uniq_{m}({T} l)",
                       f"{{ {T} r = tok_new_{m}(l.len); for (int64_t i = 0; i < l.len; i++) {{ bool dup = false; "
                       f"for (int64_t j = 0; j < r.len; j++) if ({self.eq(e, 'r.data[j]', 'l.data[i]')}) {{ dup = true; break; }} "
                       f"if (!dup) r.data[r.len++] = {self.ret(e, 'l.data[i]')}; }} return r; }}"))
            if comparable(e):
                # the order of the elements as a list of indexes: a stable merge sort
                self.need(("list", INT))
                hs.append((f"{S} tok_T_Lint tok_order_{m}({T} l)",
                           "{ tok_T_Lint r = tok_new_Lint(l.len); for (int64_t i = 0; i < l.len; i++) r.data[i] = i; "
                           "r.len = l.len; if (l.len < 2) return r; int64_t* tmp = (int64_t*)tok_alloc((size_t)l.len * sizeof(int64_t)); "
                           "for (int64_t w = 1; w < l.len; w *= 2) { for (int64_t lo = 0; lo < l.len; lo += 2 * w) { "
                           "int64_t mid = lo + w < l.len ? lo + w : l.len, hi = lo + 2 * w < l.len ? lo + 2 * w : l.len; "
                           "int64_t i = lo, j = mid, k = lo; while (i < mid && j < hi) { "
                           f"if ({less(e, 'l.data[r.data[j]]', 'l.data[r.data[i]]')}) tmp[k++] = r.data[j++]; "
                           "else tmp[k++] = r.data[i++]; } while (i < mid) tmp[k++] = r.data[i++]; "
                           "while (j < hi) tmp[k++] = r.data[j++]; } "
                           "memcpy(r.data, tmp, (size_t)l.len * sizeof(int64_t)); } tok_free(tmp); return r; }"))
            hs.append((f"{S} void tok_show_{m}(tok_sb* b, {T} l)",
                       f"{{ tok_sb_addc(b, '['); for (int64_t i = 0; i < l.len; i++) {{ if (i) tok_sb_add(b, \", \", 2); "
                       f"{self.show(e, 'l.data[i]', 'b', True)} }} tok_sb_addc(b, ']'); }}"))
        elif k == "opt":
            e = t[1]
            if self.managed(t):
                hs.append((f"{SI} void tok_retain_{m}({T} o)", f"{{ if (o.has) {{ {self.retain(e, 'o.v')} }} }}"))
                hs.append((f"{SI} void tok_release_{m}({T} o)", f"{{ if (o.has) {{ {self.release(e, 'o.v')} }} }}"))
            hs.append((f"{S} bool tok_eq_{m}({T} a, {T} b)",
                       f"{{ if (a.has != b.has) return false; return !a.has || {self.eq(e, 'a.v', 'b.v')}; }}"))
            hs.append((f"{S} void tok_show_{m}(tok_sb* b, {T} o)",
                       f"{{ if (o.has) {{ {self.show(e, 'o.v', 'b')} }} else tok_sb_add(b, \"none\", 4); }}"))
        else:
            fields = self.fields_of(t)
            if self.managed(t):
                rt = " ".join(self.retain(ft, f"r.f_{fn}") for fn, ft in fields if self.managed(ft))
                rl = " ".join(self.release(ft, f"r.f_{fn}") for fn, ft in fields if self.managed(ft))
                hs.append((f"{S} void tok_retain_{m}({T} r)", f"{{ {rt} }}"))
                hs.append((f"{S} void tok_release_{m}({T} r)", f"{{ {rl} }}"))
            eq = " && ".join(self.eq(ft, f"a.f_{fn}", f"b.f_{fn}") for fn, ft in fields) or "true"
            hs.append((f"{S} bool tok_eq_{m}({T} a, {T} b)", f"{{ (void)a; (void)b; return {eq}; }}"))
            parts = []
            for i, (fn, ft) in enumerate(fields):
                label = ("{" if i == 0 else ", ") + fn + ": "
                parts.append(f"tok_sb_add(b, \"{label}\", {len(label)}); {self.show(ft, f'r.f_{fn}', 'b', True)}")
            body = " ".join(parts) + " tok_sb_addc(b, '}');" if fields else "tok_sb_add(b, \"{}\", 2);"
            hs.append((f"{S} void tok_show_{m}(tok_sb* b, {T} r)", f"{{ (void)r; {body} }}"))
        return hs

    def _dict_helpers(self, t):
        m, K, V = mangle(t), t[1], t[2]
        T, B, KC, VC = f"tok_T_{m}", f"struct tok_B_{m}", self.c(t[1]), self.c(t[2])
        S, SI = "TOK_UNUSED static", "TOK_UNUSED static inline"
        LK, LV = ("list", K), ("list", V)
        OV = V if V[0] == "opt" else ("opt", V)
        hash_ = "tok_hash_str(k)" if K == STR else "tok_hash_int((uint64_t)k)"
        shown = '"key \\"%s\\" is not in the dict", k' if K == STR else '"key %lld is not in the dict", (long long)k'
        hs = []
        hs.append((f"{SI} void tok_retain_{m}({T} d)", "{ if (d.p) d.p->rc++; }"))
        hs.append((f"{SI} {T} tok_ret_{m}({T} d)", "{ if (d.p) d.p->rc++; return d; }"))
        hs.append((f"{S} void tok_free_{m}({T} d)",
                   f"{{ for (int64_t i = 0; i < d.p->len; i++) {{ {self.release(K, 'd.p->keys[i]')} {self.release(V, 'd.p->vals[i]')} }} "
                   f"tok_free(d.p->keys); tok_free(d.p->vals); tok_free(d.p->slots); tok_free(d.p); }}"))
        hs.append((f"{SI} void tok_release_{m}({T} d)", f"{{ if (d.p && --d.p->rc == 0) tok_free_{m}(d); }}"))
        hs.append((f"{SI} int64_t tok_len_{m}({T} d)", "{ return d.p ? d.p->len : 0; }"))
        hs.append((f"{S} int64_t tok_find_{m}({T} d, {KC} k)",
                   f"{{ if (!d.p || d.p->len == 0) return -1; uint64_t mask = (uint64_t)d.p->nslots - 1, h = {hash_} & mask; "
                   f"for (;;) {{ uint32_t s = d.p->slots[h]; if (!s) return -1; "
                   f"if ({self.eq(K, 'd.p->keys[s - 1]', 'k')}) return (int64_t)s - 1; h = (h + 1) & mask; }} }}"))
        # rebuild the index for the current capacity: at most half full
        hs.append((f"{S} void tok_index_{m}({B}* b)",
                   f"{{ int64_t n = 8; while (n < b->cap * 2) n *= 2; if (b->slots) tok_free(b->slots); "
                   f"b->slots = (uint32_t*)tok_alloc((size_t)n * sizeof(uint32_t)); memset(b->slots, 0, (size_t)n * sizeof(uint32_t)); "
                   f"b->nslots = n; uint64_t mask = (uint64_t)n - 1; for (int64_t i = 0; i < b->len; i++) {{ {KC} k = b->keys[i]; "
                   f"uint64_t h = {hash_} & mask; while (b->slots[h]) h = (h + 1) & mask; b->slots[h] = (uint32_t)(i + 1); }} }}"))
        # copy-on-write: make the block this dict's own, with room for `extra` more entries
        hs.append((f"{S} void tok_own_{m}({T}* d, int64_t extra)",
                   f"{{ {B}* b = d->p; if (b ? (b->rc == 1 && b->len + extra <= b->cap) : extra == 0) return; "
                   f"int64_t len = b ? b->len : 0, cap = b ? b->cap : 0; "
                   f"if (cap < len + extra) {{ cap *= 2; if (cap < len + extra) cap = len + extra; }} if (cap < 4) cap = 4; "
                   f"{KC}* ks = ({KC}*)tok_alloc((size_t)cap * sizeof({KC})); {VC}* vs = ({VC}*)tok_alloc((size_t)cap * sizeof({VC})); "
                   f"if (b && b->rc == 1) {{ memcpy(ks, b->keys, (size_t)len * sizeof({KC})); memcpy(vs, b->vals, (size_t)len * sizeof({VC})); "
                   f"tok_free(b->keys); tok_free(b->vals); b->keys = ks; b->vals = vs; b->cap = cap; tok_index_{m}(b); return; }} "
                   f"{B}* n = ({B}*)tok_alloc(sizeof({B})); n->rc = 1; n->len = len; n->cap = cap; n->keys = ks; n->vals = vs; "
                   f"n->slots = NULL; n->nslots = 0; "
                   f"for (int64_t i = 0; i < len; i++) {{ ks[i] = {self.ret(K, 'b->keys[i]')}; vs[i] = {self.ret(V, 'b->vals[i]')}; }} "
                   f"tok_index_{m}(n); if (b) b->rc--; d->p = n; }}"))
        # insert or update; takes over the caller's ownership of k and v
        hs.append((f"{S} void tok_set_{m}({T}* d, {KC} k, {VC} v)",
                   f"{{ int64_t i = tok_find_{m}(*d, k); if (i >= 0) {{ tok_own_{m}(d, 0); {VC} old TOK_UNUSED = d->p->vals[i]; "
                   f"d->p->vals[i] = v; {self.release(V, 'old')} {self.release(K, 'k')} return; }} "
                   f"tok_own_{m}(d, 1); {B}* b = d->p; if (TOK_UNLIKELY(b->len >= (int64_t)UINT32_MAX - 1)) tok_panic(\"\", \"dict too large\"); "
                   f"b->keys[b->len] = k; b->vals[b->len] = v; "
                   f"uint64_t mask = (uint64_t)b->nslots - 1, h = {hash_} & mask; while (b->slots[h]) h = (h + 1) & mask; "
                   f"b->slots[h] = (uint32_t)(++b->len); }}"))
        # the place of an existing entry, for d[k] += v and d[k].push(v); a missing key stops the program
        hs.append((f"{S} {VC}* tok_at_{m}({T}* d, {KC} k, const char* loc)",
                   f"{{ int64_t i = tok_find_{m}(*d, k); if (TOK_UNLIKELY(i < 0)) tok_panic(loc, {shown}); "
                   f"tok_own_{m}(d, 0); return &d->p->vals[i]; }}"))
        hs.append((f"{S} {VC} tok_must_{m}({T} d, {KC} k, const char* loc)",
                   f"{{ int64_t i = tok_find_{m}(d, k); if (TOK_UNLIKELY(i < 0)) tok_panic(loc, {shown}); return d.p->vals[i]; }}"))
        got = "d.p->vals[i]" if V[0] == "opt" else f"(({self.c(OV)}){{.has = true, .v = d.p->vals[i]}})"
        hs.append((f"{S} {self.c(OV)} tok_get_{m}({T} d, {KC} k)",
                   f"{{ int64_t i = tok_find_{m}(d, k); if (i < 0) return (({self.c(OV)}){{0}}); return {got}; }}"))
        hs.append((f"{SI} bool tok_has_{m}({T} d, {KC} k)", f"{{ return tok_find_{m}(d, k) >= 0; }}"))
        for name, lt, arr in (("keys", LK, "keys"), ("vals", LV, "vals")):
            ml = mangle(lt)
            hs.append((f"{S} {self.c(lt)} tok_{name}_{m}({T} d)",
                       f"{{ int64_t n = tok_len_{m}(d); {self.c(lt)} r = tok_new_{ml}(n); for (int64_t i = 0; i < n; i++) "
                       f"r.data[i] = {self.ret(lt[1], f'd.p->{arr}[i]')}; r.len = n; return r; }}"))
        hs.append((f"{S} bool tok_eq_{m}({T} a, {T} b)",
                   f"{{ int64_t n = tok_len_{m}(a); if (n != tok_len_{m}(b)) return false; if (a.p == b.p) return true; "
                   f"for (int64_t i = 0; i < n; i++) {{ int64_t j = tok_find_{m}(b, a.p->keys[i]); "
                   f"if (j < 0 || !{self.eq(V, 'a.p->vals[i]', 'b.p->vals[j]')}) return false; }} return true; }}"))
        hs.append((f"{S} void tok_show_{m}(tok_sb* b, {T} d)",
                   f"{{ tok_sb_addc(b, '{{'); int64_t n = tok_len_{m}(d); for (int64_t i = 0; i < n; i++) {{ "
                   f"if (i) tok_sb_add(b, \", \", 2); {self.show(K, 'd.p->keys[i]', 'b', True)} tok_sb_add(b, \": \", 2); "
                   f"{self.show(V, 'd.p->vals[i]', 'b', True)} }} tok_sb_addc(b, '}}'); }}"))
        return hs
