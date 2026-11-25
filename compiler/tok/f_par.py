"""Concurrency: `par for`, tasks (`spawn`, `wait`), channels and atomics. The syntax is provisional.

    par for i in 0..n - 1          iterations run at the same time, in chunks, on the thread pool
      out[i] = f(xs[i])            a body may write an outer list only at the loop index,
      total += xs[i]               and an outer number only as a reduction: += -= *= or v = min(v, e) / max(v, e)
    ys = par for x in xs -> f(x)   the results as a list, in order

    t = spawn work(a, b)           a task: the call runs on the pool; t is task[T] (task[T, err] if it can fail)
    v = t.wait()                   its value, once; if `work` can fail, wait() can fail: t.wait()?
    ch = chan[int](8)              a bounded channel: ch.send(v) blocks when full, ch.recv() gives none when closed
    for v in ch                    and empty; ch.close()
    a = atomic(0)                  atomic[int] or atomic[bool]: load, store, add, swap, compare_swap

Memory. Reference counts are not atomic, so no counted buffer may be changed, or have its count changed, by two
threads. Three rules give that:
  - what a `par for` body reads is pinned for the length of the loop (see runtime_mt.py): the caller keeps its
    references, and no thread touches a count;
  - a value that leaves its thread (an argument of a spawned call, a value sent on a channel, a result of the
    `par for ... ->` form) is isolated first: a buffer with a single owner moves as it is, a shared one is copied;
  - the checker rejects any other write to outer state from a `par for` body.
Channels, tasks and atomics are handles: their cells are counted atomically and are shared on purpose.
"""
from .diag import Diag, make_fix
from .f_narrow import root_of
from .types import BOOL, ERR, INT, NUMS, STR, VOID, is_handle, is_int, is_num, mangle


def walk(x, fn):
    """Call fn on every AST node (a dict with a kind) inside x, x included."""
    if isinstance(x, dict):
        if "k" in x:
            fn(x)
        for v in x.values():
            if isinstance(v, (dict, list, tuple)):
                walk(v, fn)
    elif isinstance(x, (list, tuple)):
        for v in x:
            if isinstance(v, (dict, list, tuple)):
                walk(v, fn)


def is_var(n, name):
    return isinstance(n, dict) and n.get("k") == "name" and n.get("name") == name


def count_names(x, name):
    out = [0]

    def see(n):
        if n["k"] == "name" and n["name"] == name:
            out[0] += 1
    walk(x, see)
    return out[0]


RED_OPS = {"+=": "add", "-=": "add", "*=": "mul"}


class Par:
    def setup(self):
        super().setup()
        self.free_fns["atomic"] = self.b_atomic
        self.par_n = 0
        for name in ("wait", "done"):
            self.methods[("task", name)] = getattr(self, "m_task_" + name)
        for name in ("send", "recv", "close"):
            self.methods[("chan", name)] = getattr(self, "m_chan_" + name)
        for name in ("load", "store", "add", "swap", "compare_swap"):
            self.methods[("atomic", name)] = getattr(self, "m_atomic_" + name)

    # ---------------------------------------------------------- types

    def handle_type(self, tn, inner):
        if inner == ERR:
            return ERR
        name = tn["name"]
        if name == "task":
            return ("task", inner, tn["fails"])
        if name == "chan":
            self.chan_elem(inner, tn["inner"])
            return ("chan", inner)
        if inner not in (INT, BOOL):
            self.err("E0110", f"an atomic holds an int or a bool, found {self.type_str(inner)}", tn["inner"])
        return ("atomic", inner)

    def chan_elem(self, t, n):
        if t[0] == "opt":
            self.err("E0110", "a channel cannot carry an optional: recv() gives none when the channel is closed and empty", n)
        if t in (VOID, ("none",)):
            self.err("E0110", "a channel needs a type of value to carry", n)

    # ---------------------------------------------------------- par for: what the body may do

    def par_fix(self, s):
        if s["k"] != "parfor":
            return None
        return make_fix(f"run the loop on one thread: remove \"par \" at {s['line']}:{s['col']}",
                        [(s["line"], s["col"], "", s["for_col"] - s["col"])])

    def par_scan(self, s, body):
        """Check a par for body against the rules and sort the outer variables it names.
        Returns (captured names in order, {list written at the loop index}, {reduction variable: kind})."""
        var, fix = s["var"], self.par_fix(s)
        is_range = s["hi"] is not None

        def refuse(n, what):
            raise Diag("E0603", f"{what} cannot be used inside 'par for': the iterations run at the same time on "
                                "several threads, so one of them cannot end the function", n["line"], n["col"], n["end"], fix)

        def leaving(n):
            if n["k"] == "return":
                refuse(n, "'return'")
            if n["k"] == "try":
                refuse({**n, "col": n["q_col"], "end": n["q_col"] + 1}, "'?'")
            if n["k"] == "call" and n["name"] == "fail":
                refuse(n, "'fail'")
        walk(body, leaving)

        names = []

        def see(n):
            if n["k"] in ("name", "bind") and n["name"] != var and n["name"] not in names \
                    and self.lookup(n["name"]) is not None:
                names.append(n["name"])
        walk(body, see)
        walk(s.get("where"), see)

        outs, reds, red_uses = set(), {}, {}
        allowed = ("A 'par for' body may change an outer list only as 'xs[i] = ...' at the loop index of a range, and an "
                   "outer number only as a reduction: 'total += e', '-=', '*=', 'v = min(v, e)' or 'v = max(v, e)'")

        def racy(n, root, why):
            raise Diag("E0601", f"'{root}' is defined outside this 'par for' and {why}: the iterations run at the same "
                                f"time, so they would race. {allowed}", n["line"], n["col"], n["end"], fix)

        def reduction(n, root, op, val):
            """The kind of reduction `root op val` is, or None."""
            if op in RED_OPS:
                return None if count_names(val, root) else RED_OPS[op]
            if op == "=" and val["k"] == "call" and val["name"] in ("min", "max") and len(val["args"]) == 2:
                a, b = val["args"]
                if is_var(a, root) and not count_names(b, root):
                    return val["name"]
                if is_var(b, root) and not count_names(a, root):
                    return val["name"]
            return None

        def writes(n):
            k = n["k"]
            if k == "assign":
                target, op, val = n["target"], n["op"], n["val"]
            elif k == "bind":
                target, op, val = {"k": "name", "name": n["name"]}, "=", n["val"]
            elif k == "multi":
                for t in n["targets"]:
                    root = root_of(t)
                    if root in names:
                        racy(n, root, "is assigned here")
                return
            elif k == "mcall" and n["name"] == "push":
                root = root_of(n["base"])
                if root in names:
                    raise Diag("E0601", f"'{root}' is defined outside this 'par for' and is pushed onto here: the iterations "
                                        "run at the same time, so they would race. To collect one result per element use "
                                        "the expression form, 'ys = par for x in xs -> ...'",
                               n["line"], n["col"], n["end"], fix)
                return
            else:
                return
            root = root_of(target)
            if root not in names:
                return
            at = target
            while at["k"] == "field":
                at = at["base"]
            if at["k"] == "index" and at["base"]["k"] == "name":
                if s["k"] == "parfor" and is_range and is_var(at["idx"], var):
                    outs.add(root)
                    return
                racy(n, root, f"is written here at an index that is not the loop variable '{var}' of a range"
                     if is_range else "is written here, but only a 'par for' over a range ('par for i in a..b') has an "
                     "index to write at")
            kind = reduction(n, root, op, val) if target["k"] == "name" and s["k"] == "parfor" else None
            if kind is None:
                racy(n, root, "is assigned here")
            if reds.setdefault(root, kind) != kind:
                racy(n, root, f"is combined in two different ways here ({reds[root]} and {kind})")
            red_uses[root] = red_uses.get(root, 0) + (1 if kind in ("add", "mul") and k == "assign" else
                                                      2 if k == "assign" else 1)
        walk(body, writes)

        whole = [body, s.get("where")]
        for root in outs:
            at_index = [0]

            def indexed(n, root=root):
                if n["k"] == "index" and is_var(n["base"], root) and is_var(n["idx"], var):
                    at_index[0] += 1
            walk(whole, indexed)
            if at_index[0] != count_names(whole, root):
                n = self.par_stray(whole, root, var)
                raise Diag("E0601", f"'{root}' is written by this 'par for', so its body may use it only as '{root}[{var}]': "
                                    "another iteration may be writing any other element at the same time",
                           n["line"], n["col"], n["end"], fix)
            v = self.lookup(root)
            if v["ty"] != ERR and (v["ty"][0] != "list" or self.managed(v["ty"][1])):
                what = (f"a list of {self.type_str(v['ty'][1])}" if v["ty"][0] == "list" else self.type_str(v["ty"]))
                raise Diag("E0602", f"a 'par for' body can write the elements of a list of numbers, bools or records of them; "
                                    f"'{root}' is {what}. Use the expression form to build such a list: "
                                    f"'ys = par for x in xs -> ...'", s["line"], s["col"], s["end"], fix)
        for root, kind in reds.items():
            v = self.lookup(root)
            if count_names(whole, root) != red_uses[root]:
                n = self.par_stray(whole, root, None)
                raise Diag("E0601", f"'{root}' is combined across the iterations of this 'par for', so the body cannot read "
                                    "it anywhere else: each thread only has its own partial result",
                           n["line"], n["col"], n["end"], fix)
            if v["ty"] != ERR and not is_num(v["ty"]):
                raise Diag("E0602", f"a 'par for' can combine numbers across its iterations; '{root}' is "
                                    f"{self.type_str(v['ty'])}", s["line"], s["col"], s["end"], fix)
        return names, outs, reds

    @staticmethod
    def par_stray(whole, root, var):
        """A use of `root` that is not `root[var]` (or, without var, any use that is not an assignment target)."""
        ok, found = set(), []

        def good(n):
            if var is not None and n["k"] == "index" and is_var(n["base"], root) and is_var(n["idx"], var):
                ok.add(id(n["base"]))
            if var is None and n["k"] == "assign" and is_var(n["target"], root):
                ok.add(id(n["target"]))
        walk(whole, good)

        def stray(n):
            if is_var(n, root) and id(n) not in ok and "line" in n:
                found.append(n)
        walk(whole, stray)
        # for a reduction, the argument of min/max is fine too: report the last other use
        return found[-1] if found else whole[0][0] if isinstance(whole[0], list) else whole[0]

    # ---------------------------------------------------------- par for: code

    def add_aux(self, lines):
        if not self.dry:  # a dry run only asks for a type
            self.aux.extend(lines)

    def par_count(self, s):
        """Evaluate what the loop runs over, in the caller. Returns (C code of the iteration count, element type,
        C expression of the loop variable inside the chunk function, fields for the context struct)."""
        if s["hi"] is not None:
            tlo, thi, ty = self.range_bounds(s)
            vt = s["var_tok"]
            self.check_new_name(s["var"], {"line": vt.line, "col": vt.col, "end": vt.end}, "loop variable")
            n = self.hoist(f"{thi} < {tlo} ? 0 : tok_par_count((uint64_t){thi} - (uint64_t){tlo}, {self.loc(s)})", INT)
            return n, ty, f"(({self.c(ty)})((uint64_t)tok_lo + (uint64_t)tok_k))", [(self.c(ty), "tok_lo", tlo)], None
        code, ty = self.val(s["iter"])
        vt = s["var_tok"]
        self.check_new_name(s["var"], {"line": vt.line, "col": vt.col, "end": vt.end}, "loop variable")
        if ty == ERR:
            return "0", ERR, "0", [], None
        if ty[0] != "list":
            self.err("E0103", f"'par for' needs a list or a range, found {self.type_str(ty)}", s["iter"])
        # the caller holds its own reference to the list for the whole loop, like `for` does
        t = self.hoist(self.take(code, ty), ty, own=True)
        return f"{t}.len", ty[1], "tok_it.data[tok_k]", [(self.c(ty), "tok_it", t)], (ty, t)

    def par_emit(self, s, n, fields, locals_, lines, label, names, outs, reds, pins, tail, after):
        """Write the chunk function and the code that runs it. `fields`: (C type, name, initial value) of the context;
        `locals_`: extra declarations at the top of the chunk function; `tail`: lines after its loop."""
        self.types.mt = True
        self.par_n += 1
        k = self.par_n
        red_ty = [(r, self.lookup(r)) for r in reds]
        fields = list(fields)
        decls = [f"{ct} {name} TOK_UNUSED = tok_cx->{name};" for ct, name, _ in fields]
        for name in names:
            if name in reds:
                continue
            v = self.lookup(name)
            if v["ty"] == ERR:
                continue
            fields.append((self.c(v["ty"]), v["c"], v["c"]))
            decls.append(f"{self.c(v['ty'])} {v['c']} TOK_UNUSED = tok_cx->{v['c']};")
            if name not in outs and self.managed(v["ty"]) and not is_handle(v["ty"]):
                pins.append((v["ty"], v["c"]))
        defs = []
        if reds:
            defs.append(f"typedef struct {{ {' '.join(f'{self.c(v['ty'])} {v['c']};' for _, v in red_ty)} }} tok_pr{k};")
            fields.append((f"tok_pr{k}*", "tok_red", "NULL"))
            for r, v in red_ty:
                ct = self.c(v["ty"])
                init = {"add": f"({ct})0", "mul": f"({ct})1"}.get(reds[r], f"tok_cx->tok_start_{v['c']}")
                if reds[r] in ("min", "max"):
                    fields.append((ct, f"tok_start_{v['c']}", v["c"]))
                decls.append(f"{ct} {v['c']} = {init};")
        defs.append(f"typedef struct {{ {' '.join(f'{ct} {name};' for ct, name, _ in fields)} }} tok_pc{k};")
        fn = [f"static void tok_pf{k}(void* tok_cxp, int64_t tok_a, int64_t tok_b, int64_t tok_ci) {{",
              f"  tok_pc{k}* tok_cx = (tok_pc{k}*)tok_cxp;", "  (void)tok_ci;"]
        fn += ["  " + d for d in decls + locals_]
        fn.append("  for (int64_t tok_k = tok_a; tok_k < tok_b; tok_k++) {")
        fn += lines
        if label:
            fn.append(f"    {label}: ;")
        fn.append("  }")
        fn += ["  " + line for line in tail]
        for r, v in red_ty:
            fn.append(f"  tok_cx->tok_red[tok_ci].{v['c']} = {v['c']};")
        fn.append("}")
        self.add_aux(defs + fn + [""])

        cx, pl = self.new_tmp(), self.new_tmp()
        self.emit(f"if ({n} > 0) {{")
        for name in sorted(outs):
            v = self.lookup(name)
            if v["ty"] != ERR:
                self.emit(f"  tok_own_{mangle(v['ty'])}(&{v['c']});")  # the workers write this buffer: it must not be shared
        inits = ", ".join(f".{name} = {init}" for _, name, init in fields)
        self.emit(f"  tok_pc{k} {cx} = {{ {inits} }};")
        self.emit(f"  tok_plan {pl} = tok_par_plan({n});")
        if reds:
            self.emit(f"  {cx}.tok_red = (tok_pr{k}*)tok_alloc((size_t){pl}.nchunks * sizeof(tok_pr{k}));")
        pin = " ".join(self.types.pin(ty, c) for ty, c in pins)
        unpin = " ".join(self.types.unpin(ty, c) for ty, c in pins)
        if pin:
            self.emit(f"  if ({pl}.par) {{ {pin} }}")
        self.emit(f"  tok_par_run(&{pl}, tok_pf{k}, &{cx});")
        if unpin:
            self.emit(f"  if ({pl}.par) {{ {unpin} }}")
        if reds:
            # partial results are combined in chunk order, so the result does not depend on the number of threads
            c = self.new_tmp()
            self.emit(f"  for (int64_t {c} = 0; {c} < {pl}.nchunks; {c}++) {{")
            self.depth += 2
            for r, v in red_ty:
                part, ty = f"{cx}.tok_red[{c}].{v['c']}", v["ty"]
                if reds[r] in ("min", "max"):
                    self.emit(f"if ({part} {'<' if reds[r] == 'min' else '>'} {v['c']}) {v['c']} = {part};")
                else:
                    op = "+" if reds[r] == "add" else "*"
                    code = f"({v['c']} {op} {part})" if NUMS[ty[0]][1] == "f" else self.int_op(op, v["c"], part, ty, s)
                    self.emit(f"{v['c']} = {code};")
            self.depth -= 2
            self.emit("  }")
            self.emit(f"  tok_free({cx}.tok_red);")
        for line in after:
            self.emit("  " + line)
        self.emit("}")

    def s_parfor(self, s, is_tail):
        with self.region():
            self.s_parfor_in(s)
            self.flush()

    def s_parfor_in(self, s):
        n, ety, var_code, fields, it = self.par_count(s)
        names, outs, reds = self.par_scan(s, s["body"])
        pins = [it] if it and self.managed(it[0]) else []
        label = f"tok_next{self.new_tmp()}"
        loops, self.loops = self.loops, []
        try:
            with self.region(), self.capture(2) as lines:
                self.emit(f"{self.c(ety)} v_{s['var']} TOK_UNUSED = {var_code};")
                self.scopes.append({})
                self.define(s["var"], ety, False, None)
                loop = {"scopes": len(self.scopes), "pools": len(self.pools), "label": label, "used": False, "par": s}
                self.loops.append(loop)
                try:
                    self.loop_stmts(s)
                finally:
                    self.scopes.pop()
        finally:
            self.loops = loops
        self.dead = False
        if ety == ERR:
            return
        self.par_emit(s, n, fields, [], lines, label if loop["used"] else None, names, outs, reds, pins, [], [])

    def x_parmap(self, n, want):
        """par for x in xs -> expr: a new list with the value of expr for each element, in order."""
        count, ety, var_code, fields, it = self.par_count(n)
        names, outs, reds = self.par_scan(n, n["body"])
        pins = [it] if it and self.managed(it[0]) else []
        with self.region(), self.capture(2) as lines:
            self.emit(f"{self.c(ety)} v_{n['var']} TOK_UNUSED = {var_code};")
            self.scopes.append({})
            self.define(n["var"], ety, False, None)
            try:
                code, ty = self.val(n["body"])
                if ty == VOID:
                    self.err("E0103", "this call returns nothing, so there is no result to collect; use the statement form "
                                      "of 'par for' to run it for each element", n["body"])
                if ty != ERR:
                    # a result leaves the thread that made it: it must not share a buffer with what the body read
                    self.emit(f"tok_res.data[tok_k] = {self.types.iso(ty, self.take(code, ty))};")
                self.flush()
            finally:
                self.scopes.pop()
        if ty == ERR or ety == ERR:
            return "0", ERR
        out_ty = ("list", ty)
        out = self.new_tmp()
        self.emit(f"{self.c(out_ty)} {out} TOK_UNUSED = tok_new_{mangle(out_ty)}({count});")
        fields.append((self.c(out_ty), "tok_res", out))
        self.par_emit(n, count, fields, [], lines, None, names, outs, reds, pins, [], [f"{out}.len = {count};"])
        return self.own(out, out_ty), out_ty

    # ---------------------------------------------------------- tasks

    def x_spawn(self, n, want):
        call = n["call"]
        f = shown = None
        if call["k"] == "call" and self.lookup(call["name"]) is None and call["name"] not in self.free_fns:
            f, shown = self.mod.fns.get(call["name"]), call["name"]
            if f is None:
                self.err("E0101", f"unknown function '{call['name']}'", call)
        elif call["k"] == "mcall" and call["base"]["k"] == "name" and self.lookup(call["base"]["name"]) is None \
                and call["base"]["name"] not in self.builtin_mods:
            mod = self.get_module(call["base"]["name"], call["base"])
            f, shown = mod.fns.get(call["name"]), f"{call['base']['name']}.{call['name']}"
            if f is None:
                self.err("E0101", f"module '{mod.name}' has no function '{call['name']}'", call)
        if f is None:
            self.err("E0604", "'spawn' starts a call to a function of the program as a task, for example "
                              "'t = spawn work(x)'; wrap anything else in a function", call)
        sig = self.sig(f)
        if sig["is_main"]:
            self.err("E0604", "'main' cannot be spawned", call)
        if len(call["args"]) != len(sig["params"]):
            self.err("E0104", f"'{shown}' takes {len(sig['params'])} argument(s), found {len(call['args'])}", call)
        vals = [self.val(an, pt) for an, (pn, pt) in zip(call["args"], sig["params"])]
        args = []
        for an, (pn, pt), (code, ty) in zip(call["args"], sig["params"], vals):
            # an argument crosses to another thread: it moves if this was its last use and nothing else shares it,
            # otherwise the task gets its own copy
            code = self.take_node(an, code, ty) if ty != ERR else code
            code = self.coerce(code, ty, pt, an, f"argument '{pn}' of '{shown}' needs {self.type_str(pt)}, found {self.type_str(ty)}")
            args.append(self.types.iso(pt, code) if pt != ERR else code)
        for eff in sig["effects"]:
            self.need_effect(eff, call, shown)
        if any(t == ERR for _, t in sig["params"]) or sig["ret"] == ERR or any(t == ERR for _, t in vals):
            return "0", ERR
        self.types.mt = True
        self.par_n += 1
        k, cret = self.par_n, sig["cret"]
        members = ([f"{self.c(cret)} r;"] if cret != VOID else []) + [f"{self.c(pt)} a{i};" for i, (_, pt) in enumerate(sig["params"])]
        aux = []
        aux.append(f"typedef struct {{ {' '.join(members) or 'char tok_empty;'} }} tok_se{k};")
        call_c = f"{f['c']}({', '.join(f'e->a{i}' for i in range(len(args)))})"
        after = " ".join(self.types.release(pt, f"e->a{i}") for i, ((_, pt), owned) in enumerate(zip(sig["params"], sig["owned"]))
                         if not owned)
        aux.append(f"static void tok_st{k}(void* env) {{ tok_se{k}* e TOK_UNUSED = (tok_se{k}*)env; "
                        f"{'e->r = ' if cret != VOID else ''}{call_c}; {after} }}")
        drop = "NULL"
        if cret != VOID and self.managed(cret):
            drop = f"tok_sd{k}"
            aux.append(f"static void {drop}(void* env) {{ {self.types.release(cret, f'((tok_se{k}*)env)->r')} }}")
        aux.append("")
        self.add_aux(aux)
        e = self.new_tmp()
        self.emit(f"tok_se{k}* {e} = (tok_se{k}*)tok_alloc(sizeof(tok_se{k}));")
        for i, code in enumerate(args):
            self.emit(f"{e}->a{i} = {code};")
        ty = ("task", sig["ret"], sig["fallible"])
        return self.hoist(f"tok_spawn(tok_st{k}, {drop}, {e})", ty, own=True), ty

    def m_task_wait(self, n, bc, bt, want):
        self.margs(n, 0)
        self.emit(f"tok_task_wait({bc}, {self.loc(n)});")
        cret = ("res", bt[1]) if bt[2] else bt[1]
        if cret == VOID:
            return "", VOID
        return self.hoist(f"(*({self.c(cret)}*)({bc})->env)", cret, own=True), cret

    def m_task_done(self, n, bc, bt, want):
        self.margs(n, 0)
        return self.hoist(f"(atomic_load(&({bc})->state) >= 2)", BOOL), BOOL

    # ---------------------------------------------------------- channels

    def x_chan_new(self, n, want):
        tn = n["type"]
        if tn["name"] != "chan":
            self.err("E0605", f"only a channel is made this way: chan[T](capacity); found {tn['name']}", tn)
        ty = self.resolve_type(tn, self.mod)
        if len(n["args"]) != 1:
            self.err("E0104", f"a channel takes its capacity, for example chan[int](8); found {len(n['args'])} argument(s)", n)
        code, ct = self.val(n["args"][0], INT)
        code = self.coerce(code, ct, INT, n["args"][0], f"the capacity of a channel is an int, found {self.type_str(ct)}")
        if ty == ERR or ct == ERR:
            return "0", ERR
        elem = ty[1]
        return self.hoist(f"tok_chan_new({code}, sizeof({self.c(elem)}), {self.types.chan_drop(elem)}, {self.loc(n)})", ty, own=True), ty

    def m_chan_send(self, n, bc, bt, want):
        self.margs(n, 1)
        elem = bt[1]
        code = self.stored(n["args"][0], elem, f"this channel carries {self.type_str(elem)}")
        if n["args"][0].get("ty") == ERR:
            return "", VOID
        # the value leaves this thread: it moves if nothing else shares it, otherwise the channel gets a copy
        t = self.settle(self.types.iso(elem, code), elem)
        self.emit(f"tok_chan_send({bc}, &{t}, {self.loc(n)});")
        return "", VOID

    def m_chan_recv(self, n, bc, bt, want):
        self.margs(n, 0)
        ot = ("opt", bt[1])
        t = self.new_tmp()
        self.emit(f"{self.c(ot)} {t} TOK_UNUSED = {{0}};")
        self.emit(f"{t}.has = tok_chan_recv({bc}, &{t}.v);")
        return self.own(t, ot), ot

    def m_chan_close(self, n, bc, bt, want):
        self.margs(n, 0)
        self.emit(f"tok_chan_close({bc});")
        return "", VOID

    def for_list(self, s):
        ty = self.dry_type(s["iter"])
        if ty is None or ty[0] != "chan":
            return super().for_list(s)
        # for v in ch: receive until the channel is closed and empty
        code, ty = self.val(s["iter"])
        vt = s["var_tok"]
        self.check_new_name(s["var"], {"line": vt.line, "col": vt.col, "end": vt.end}, "loop variable")
        if s.get("var2"):
            self.err("E0103", "'for k, v in' needs a dict, found a channel", s["iter"])
        elem = ty[1]
        t = self.stash(self.take(code, ty), ty)
        self.flush()
        self.scopes.append({"$" + t: {"c": t, "ty": ty, "mutable": False, "decl": None, "own": True}})
        v = f"v_{s['var']}"
        self.emit("for (;;) {")
        self.emit(f"  {self.c(elem)} {v} TOK_UNUSED;")
        self.emit(f"  if (!tok_chan_recv({t}, &{v})) break;")
        self.scopes.append({})
        self.define(s["var"], elem, False, None, own=True)  # what was received belongs to the loop body
        loop = {"scopes": len(self.scopes) - 1, "pools": len(self.pools), "label": None, "used": False}
        self.loops.append(loop)
        try:
            self.loop_stmts(s)
        finally:
            self.loops.pop()
            self.scopes.pop()
        if self.managed(elem):
            self.emit("  " + self.types.release(elem, v))
        self.emit("}")
        self.emit(self.types.release(ty, t))
        self.scopes.pop()
        self.dead = False

    # ---------------------------------------------------------- atomics

    def b_atomic(self, n, want):
        if len(n["args"]) != 1:
            self.err("E0104", f"atomic takes its starting value, an int or a bool; found {len(n['args'])} argument(s)", n)
        code, ty = self.val(n["args"][0])
        if ty == ERR:
            return "0", ERR
        if ty not in (INT, BOOL):
            self.err("E0103", f"an atomic holds an int or a bool, found {self.type_str(ty)}", n["args"][0])
        at = ("atomic", ty)
        return self.hoist(f"tok_atom_new((int64_t){code})", at, own=True), at

    def atomic_arg(self, n, i, bt):
        an = n["args"][i]
        code, ty = self.val(an, bt[1])
        code = self.coerce(code, ty, bt[1], an, f"this atomic holds {self.type_str(bt[1])}, found {self.type_str(ty)}")
        return f"(int64_t){code}"

    def atomic_value(self, code, bt):
        return self.hoist(f"({code} != 0)" if bt[1] == BOOL else code, bt[1]), bt[1]

    def m_atomic_load(self, n, bc, bt, want):
        self.margs(n, 0)
        return self.atomic_value(f"atomic_load(&({bc})->v)", bt)

    def m_atomic_store(self, n, bc, bt, want):
        self.margs(n, 1)
        self.emit(f"atomic_store(&({bc})->v, {self.atomic_arg(n, 0, bt)});")
        return "", VOID

    def m_atomic_add(self, n, bc, bt, want):
        self.margs(n, 1)
        if bt[1] != INT:
            raise Diag("E0109", "'add' is for an atomic int; an atomic bool has load, store, swap and compare_swap",
                       n["line"], n["name_col"], n["end"])
        d = self.hoist(self.atomic_arg(n, 0, bt), INT)
        # the new value; like the sized integers, an atomic add wraps instead of stopping on overflow
        return self.hoist(f"(int64_t)((uint64_t)atomic_fetch_add(&({bc})->v, {d}) + (uint64_t){d})", INT), INT

    def m_atomic_swap(self, n, bc, bt, want):
        self.margs(n, 1)
        return self.atomic_value(f"atomic_exchange(&({bc})->v, {self.atomic_arg(n, 0, bt)})", bt)

    def m_atomic_compare_swap(self, n, bc, bt, want):
        self.margs(n, 2)
        a, b = self.atomic_arg(n, 0, bt), self.atomic_arg(n, 1, bt)
        return self.hoist(f"tok_atom_cas({bc}, {a}, {b})", BOOL), BOOL
