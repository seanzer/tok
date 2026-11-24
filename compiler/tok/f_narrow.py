"""Optional narrowing: after a test shows that an optional has a value, it is a plain value.

    if u.email              u.email is a str inside the block
    if x != none and x > 3  the right side of 'and' sees x as a plain value
    if m == none            after a block that always leaves (return or fail),
      return 0              m is a plain value for the rest of the enclosing block
    for u in us where u.email

What can be narrowed is a variable, or a chain of fields that starts at a variable.
Soundness: a narrowed value is read again each time it is used, so the fact must still hold.
An immutable binding never changes. A `var` is narrowed only when nothing in the region the
fact covers assigns to it (or to one of its fields or elements, or pushes onto it); otherwise
it stays optional there and `or` is needed.
"""
from .diag import make_fix

EMPTY = frozenset()


def path_of(n):
    """('u', 'email') for u.email; None for anything that is not a variable or a field chain."""
    parts = []
    while n["k"] == "field":
        parts.append(n["name"])
        n = n["base"]
    if n["k"] != "name":
        return None
    parts.append(n["name"])
    return tuple(reversed(parts))


def root_of(n):
    while n["k"] in ("field", "index"):
        n = n["base"]
    return n["name"] if n["k"] == "name" else None


def assigned_roots(x, out=None):
    """Every variable that the statements may assign to, change in place, or push onto."""
    if out is None:
        out = set()
    if isinstance(x, dict):
        k = x.get("k")
        if k == "assign":
            out.add(root_of(x["target"]))
        elif k == "bind":
            out.add(x["name"])
        elif k == "mcall" and x.get("name") == "push":
            out.add(root_of(x["base"]))
        for v in x.values():
            if isinstance(v, (dict, list, tuple)):
                assigned_roots(v, out)
    elif isinstance(x, (list, tuple)):
        for v in x:
            if isinstance(v, (dict, list, tuple)):
                assigned_roots(v, out)
    return out


def leaves(stmts):
    """True if the block always ends by leaving the function (return or fail)."""
    if not stmts:
        return False
    last = stmts[-1]
    if last["k"] == "return":
        return True
    if last["k"] == "expr" and last["val"]["k"] == "call" and last["val"]["name"] == "fail":
        return True
    if last["k"] == "if" and last["other"] is not None:
        return leaves(last["then"]) and leaves(last["other"])
    return False


class Facts:
    """Pushes a set of narrowed paths for the duration of a `with` block."""

    def __init__(self, comp, paths):
        self.comp, self.paths = comp, paths

    def __enter__(self):
        self.comp.narrowed.append(self.paths)

    def __exit__(self, *a):
        self.comp.narrowed.pop()


class Narrow:
    def setup(self):
        super().setup()
        self.narrowed = []

    def is_narrowed(self, path):
        return path is not None and any(path in s for s in self.narrowed)

    # ---------------------------------------------------------- reading a narrowed value

    def x_name(self, n, want):
        code, ty = super().x_name(n, want)
        if ty[0] == "opt" and self.is_narrowed((n["name"],)):
            n["narrowed"] = True
            return f"{code}.v", ty[1]
        return code, ty

    def x_field(self, n, want):
        code, ty = super().x_field(n, want)
        if ty[0] == "opt" and self.is_narrowed(path_of(n)):
            n["narrowed"] = True
            return f"{code}.v", ty[1]
        return code, ty

    # ---------------------------------------------------------- what a condition proves

    def facts(self, n):
        """(paths that have a value when the condition is true, paths that have one when it is false)."""
        k = n["k"]
        if k in ("name", "field"):
            if n.get("ty", ("error",))[0] == "opt" and path_of(n) is not None:
                return frozenset([path_of(n)]), EMPTY
            return EMPTY, EMPTY
        if k == "un" and n["op"] == "not":
            t, f = self.facts(n["e"])
            return f, t
        if k == "bin":
            op = n["op"]
            if op in ("==", "!="):
                for a, b in ((n["l"], n["r"]), (n["r"], n["l"])):
                    if b["k"] == "none" and a["k"] in ("name", "field") and a.get("ty", ("error",))[0] == "opt" \
                            and path_of(a) is not None:
                        s = frozenset([path_of(a)])
                        return (s, EMPTY) if op == "!=" else (EMPTY, s)
            elif op == "and":
                lt, _ = self.facts(n["l"])
                rt, _ = self.facts(n["r"])
                return lt | rt, EMPTY
            elif op == "or" and n.get("ty") == ("bool",):
                _, lf = self.facts(n["l"])
                _, rf = self.facts(n["r"])
                return EMPTY, lf | rf
        return EMPTY, EMPTY

    def holding(self, paths, region):
        """The facts that still hold everywhere in `region` (statements or expressions)."""
        if not paths:
            return Facts(self, EMPTY)
        changed = None
        keep = set()
        for p in paths:
            v = self.lookup(p[0])
            if v is None:
                continue
            if v["mutable"]:
                if changed is None:
                    changed = assigned_roots(region)
                if p[0] in changed:
                    continue
            keep.add(p)
        return Facts(self, frozenset(keep))

    def facts_after(self, s):
        """What is known after an `if` statement, because the other branch always leaves."""
        if s["k"] != "if" or "facts" not in s:
            return EMPTY
        t, f = s["facts"]
        other = s["other"]
        if leaves(s["then"]):
            if other is None:
                return f
            if len(other) == 1 and other[0]["k"] == "if":
                return f | self.facts_after(other[0])
            return f
        if other is not None and leaves(other):
            return t
        return EMPTY

    # ---------------------------------------------------------- diagnostics

    def known(self, n):
        return n.get("narrowed") and ".".join(path_of(n))

    def or_default(self, n, lc, lt, want):
        l, r = n["l"], n["r"]
        name = self.known(l)
        if name:
            start, end = l.get("pend", l["end"]), r.get("pend", r["end"])
            self.err("E0205", f"'{name}' is known to have a value here, so the default after 'or' is never used", n,
                     make_fix(f"remove the default: delete {end - start} character(s) at {l['line']}:{start}",
                              [(l["line"], start, "", end - start)]))
        return super().or_default(n, lc, lt, want)

    def binop(self, op, lc, lt, rc, rt, n):
        if op in ("==", "!=") and "l" in n:
            for a, at, bt in ((n["l"], lt, rt), (n["r"], rt, lt)):
                name = bt == ("none",) and self.known(a)
                if name:
                    self.err("E0205", f"'{name}' is known to have a value here, so comparing it with none is always "
                                      f"{'true' if op == '!=' else 'false'}", n)
        return super().binop(op, lc, lt, rc, rt, n)

    def cond(self, n):
        try:
            return super().cond(n)
        except Exception as d:
            name = getattr(d, "code", None) == "E0103" and self.known(n)
            if name:
                self.err("E0205", f"'{name}' is known to have a value here, so this test is always true", n)
            raise
