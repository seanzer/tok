"""Concurrency: par for, tasks, channels, atomics, and the checks that keep them free of data races.

Every program here runs with several thread counts. The thread sanitizer runs over the concurrent programs of the
memory corpus in test_memory.py."""
import os
import subprocess
import unittest

from helpers import TokCase, tokc

FILL = """var out: [int] = []
for i in 1..n
  out.push(0)
"""


class ParCase(TokCase):
    def par(self, src, *args, threads=("1", "2", "4", "9"), code=0):
        """Run with each thread count; the output must not depend on it. Returns the output."""
        path = self.write("p.tok", src)
        self.assert_clean_c(path)
        outs = set()
        for t in threads:
            r = tokc("run", path, *args, env=dict(self.env, TOK_THREADS=t))
            self.assertEqual(r.returncode, code, r.stderr)
            outs.add(r.stdout if code == 0 else r.stderr)
        self.assertEqual(len(outs), 1, outs)
        return outs.pop()


class RuntimeTests(ParCase):
    def test_programs_without_concurrency_do_not_use_threads(self):
        c = tokc("emit-c", self.write("plain.tok", "print(1)\n")).stdout
        self.assertNotIn("pthread", c)
        self.assertNotIn("#define TOK_MT", c)
        c = tokc("emit-c", self.write("conc.tok", "par for i in 1..2\n  print(i)\n")).stdout
        self.assertTrue(c.startswith("#define TOK_MT 1"))

    def test_thread_count_comes_from_the_environment(self):
        # each iteration waits for the other, so this only ends if two threads really run the loop
        src = ("a = atomic(0)\npar for i in 1..2\n  a.add(1)\n  while a.load() < 2\n    a.load()\nprint(a.load())\n")
        self.assertEqual(self.par(src, threads=("2", "4")), "2\n")

    def test_bad_thread_counts_fall_back(self):
        for t in ("0", "-3", "junk", "100000"):
            self.assertEqual(self.par("t = 0\nxs = par for i in 1..100 -> i + t\nprint(xs.sum())\n", threads=(t,)), "5050\n")

    def test_a_panic_in_a_worker_stops_the_program(self):
        src = "xs = [1, 2, 3, 0, 5]\nys = par for x in xs -> 10 / x\nprint(ys)\n"
        self.assertIn("tok: panic: division by zero (p.tok:2)", self.par(src, code=70))
        src = "fn boom(n: int) -> int\n  [1, 2][n]\n\nt = spawn boom(5)\nprint(t.wait())\n"
        self.assertIn("index 5 out of bounds", self.par(src, code=70))

    def test_tasks_nobody_waits_for_finish_before_the_program_ends(self):
        src = ("fn say(c: atomic[int], n: int)\n  c.add(n)\n\nfn all(c: atomic[int])\n  for i in 1..50\n    spawn say(c, i)\n\n"
               "c = atomic(0)\nall(c)\nt = spawn all(c)\nt.wait()\nprint(1)\n")
        self.assertEqual(self.par(src), "1\n")


class ParForTests(ParCase):
    def test_writes_at_the_loop_index(self):
        src = "fn sq(x: int) -> int\n  x * x\n\nn = 1000\n" + FILL + "par for i in 0..n - 1\n  out[i] = sq(i)\nprint(out[999])\nprint(out.sum())\n"
        self.assertEqual(self.par(src), "998001\n332833500\n")

    def test_writing_a_shared_list_copies_it_first(self):
        src = "n = 100\n" + FILL + "keep = out\npar for i in 0..n - 1\n  out[i] = i\nprint(keep.sum())\nprint(out.sum())\n"
        self.assertEqual(self.par(src), "0\n4950\n")

    def test_records_at_the_loop_index(self):
        src = ("type P = { x: f64, y: f64 }\n\nvar ps: [P] = []\nfor i in 1..200\n  ps.push(P { x: f64(i), y: 0.0 })\n"
               "par for i in 0..199\n  ps[i].y = ps[i].x * 2.0\n  ps[i] = P { x: ps[i].y, y: ps[i].x }\nprint(ps[199])\n")
        self.assertEqual(self.par(src), "{x: 400.0, y: 200.0}\n")

    def test_reductions(self):
        src = ("xs = par for i in 1..1000 -> i\nvar s, c, lo, hi = 0, 0, 5000, -1\nvar p = 1.0\n"
               "par for x in xs where x % 7 == 0\n  s += x\n  c += 1\n  lo = min(lo, x)\n  hi = max(x, hi)\n"
               "par for i in 1..20\n  p *= 1.5\nvar d = 100\npar for i in 1..10\n  d -= i\n"
               "print(\"{s} {c} {lo} {hi} {d}\")\nprint(p.fixed(3))\n")
        self.assertEqual(self.par(src), "71071 142 7 994 45\n3325.257\n")

    def test_a_float_sum_does_not_depend_on_the_thread_count(self):
        src = "var t = 0.0\npar for i in 1..100000\n  t += 1.0 / f64(i)\nprint(t)\n"
        self.par(src)  # par() fails if two thread counts print different text

    def test_int_overflow_in_a_reduction_still_panics(self):
        src = "var t = 9223372036854775000\npar for i in 1..1000\n  t += i\nprint(t)\n"
        self.assertIn("overflow", self.par(src, code=70))

    def test_the_expression_form_keeps_order(self):
        src = ("fn tag(s: str, n: int) -> str\n  \"{s}{n}\"\n\nws = \"a b c d e f g\".words()\npre = ws.join(\"\")\n"
               "ys = par for w in ws -> tag(w + pre, w.len())\nprint(ys.join(\" \"))\n"
               "zs = par for i in 0..6 -> ws[i] + ws[6 - i]\nprint(zs)\nprint((par for i in 1..0 -> i).len())\n")
        self.assertEqual(self.par(src), "aabcdefg1 babcdefg1 cabcdefg1 dabcdefg1 eabcdefg1 fabcdefg1 gabcdefg1\n"
                                        '["ag", "bf", "ce", "dd", "ec", "fb", "ga"]\n0\n')

    def test_results_do_not_share_with_what_the_body_read(self):
        src = ("xs = [[1, 2], [3]]\nvar ys = par for x in xs -> x\nys[0].push(9)\nprint(xs)\nprint(ys)\n"
               "d = {\"a\": [1]}\nvar ds = par for i in 1..3 -> d\nds[0][\"b\"] = [2]\n"
               "print(d)\nprint(ds[0])\nprint(ds[2])\n")
        self.assertEqual(self.par(src), '[[1, 2], [3]]\n[[1, 2, 9], [3]]\n{"a": [1]}\n{"a": [1], "b": [2]}\n{"a": [1]}\n')

    def test_continue_where_and_inner_loops(self):
        src = ("var n = 0\npar for i in 1..100 where i % 2 == 0\n  if i % 3 == 0\n    continue\n  var k = 0\n"
               "  for j in 1..i\n    if j > 3\n      break\n    k += 1\n  n += k\nprint(n)\n")
        self.assertEqual(self.par(src), "101\n")

    def test_par_for_inside_par_for_and_inside_functions(self):
        src = ("fn row(i: int) -> int\n  var t = 0\n  par for j in 1..i\n    t += j\n  t\n\n"
               "rs = par for i in 1..50 -> row(i)\nprint(rs.sum())\n")
        self.assertEqual(self.par(src), "22100\n")

    def test_effects_of_the_body_are_the_effects_of_the_loop(self):
        errs = self.errors("fn f(xs: [int])\n  par for x in xs\n    print(x)\n\nf([1])\n")
        self.assertEqual([e["code"] for e in errs], ["E0201"])
        self.assertEqual(self.fix_and_run("fn f(xs: [int])\n  par for x in xs\n    print(x)\n\nf([1])\n"), "1\n")

    def test_errors_are_handled_inside_the_body(self):
        src = ("fn half(n: int) -> int ! err\n  if n % 2 != 0\n    fail(\"odd\")\n  n / 2\n\n"
               "hs = par for i in 1..6 -> half(i) or -1\nprint(hs)\n")
        self.assertEqual(self.par(src), "[-1, 1, -1, 2, -1, 3]\n")


RACY = [
    ("var total = 0\npar for i in 1..10\n  total = i\nprint(total)\n", "E0601", "is assigned here"),
    ("var total = 0\npar for i in 1..10\n  total = total * 2 + i\nprint(total)\n", "E0601", "is assigned here"),
    ("var xs = [0, 0, 0]\npar for i in 0..1\n  xs[i + 1] = i\nprint(xs)\n", "E0601", "not the loop variable"),
    ("var xs = [0, 0, 0]\npar for i in 0..1\n  xs[0] = i\nprint(xs)\n", "E0601", "not the loop variable"),
    ("var xs: [int] = []\npar for i in 0..9\n  xs.push(i)\nprint(xs)\n", "E0601", "pushed onto"),
    ("var xs = [1, 2, 3]\npar for i in 0..2\n  xs[i] = xs[0] + 1\nprint(xs)\n", "E0601", "only as 'xs[i]'"),
    ("var xs = [1, 2, 3]\npar for i in 0..2\n  xs[i] = xs.len()\nprint(xs)\n", "E0601", "only as 'xs[i]'"),
    ("var xs = [1, 2]\npar for x in xs\n  xs[0] = x\nprint(xs)\n", "E0601", "over a range"),
    ("var t = 0\npar for i in 0..2\n  t += i\n  if t > 1\n    print(t)\n", "E0601", "cannot read it anywhere else"),
    ("var t = 0\npar for i in 0..2\n  t += t\nprint(t)\n", "E0601", "is assigned here"),
    ("var t = 1\npar for i in 0..3\n  t += i\n  t *= 2\nprint(t)\n", "E0601", "two different ways"),
    ("var a, b = 0, 1\npar for i in 0..3\n  a, b = b, a\nprint(a)\n", "E0601", "is assigned here"),
    ("type P = { n: int }\n\nvar p = P { n: 0 }\npar for i in 0..3\n  p.n = i\nprint(p)\n", "E0601", "is assigned here"),
    ("var d = {\"a\": 1}\npar for i in 0..3\n  d[\"a\"] = i\nprint(d)\n", "E0601", "not the loop variable"),
    ("var n = 0\nys = par for i in 0..3 -> n + i\npar for i in 0..3\n  n = n + i\nprint(ys)\n", "E0601", "is assigned here"),
    ("var xs = [1]\nys = par for i in 0..3 -> xs.len()\nzs = par for i in 0..0 -> i\npar for i in 0..0\n  for j in 0..0\n    xs[j] = 1\nprint(ys)\nprint(zs)\n",
     "E0601", "not the loop variable"),
    ("var xs = [\"a\", \"b\"]\npar for i in 0..1\n  xs[i] = \"c\"\nprint(xs)\n", "E0602", "list of str"),
    ("var s = \"\"\npar for i in 0..3\n  s += \"x\"\nprint(s)\n", "E0602", "'s' is str"),
    ("fn f(xs: [int]) -> int\n  par for x in xs\n    if x > 2\n      return x\n  0\n\nprint(f([1, 2, 3]))\n", "E0603", "'return'"),
    ("fn half(n: int) -> int ! err\n  if n % 2 != 0\n    fail(\"odd\")\n  n / 2\n\npar for i in 0..2\n  print(half(i)?)\n", "E0603", "'?'"),
    ("par for i in 0..2\n  if i == 1\n    fail(\"no\")\n", "E0603", "'fail'"),
    ("par for i in 0..2\n  if i == 1\n    break\n  print(i)\n", "E0603", "'break'"),
]


class RaceCheckerTests(ParCase):
    def test_racy_programs_are_rejected(self):
        for src, code, text in RACY:
            with self.subTest(src=src):
                errs = self.errors(src)
                self.assertEqual([e["code"] for e in errs], [code], errs)
                self.assertIn(text, errs[0]["message"])
                self.assertIn("fix", errs[0])

    def test_the_fix_runs_the_loop_on_one_thread(self):
        for src, _, _ in RACY:
            if "fail(\"no\")" in src or "half(i)?" in src:
                continue  # these end with an error, as they should
            with self.subTest(src=src):
                self.fix_and_run(src)
        self.assertEqual(self.fix_and_run(RACY[4][0]), "[0, 1, 2, 3, 4, 5, 6, 7, 8, 9]\n")

    def test_what_is_allowed(self):
        src = ("c = atomic(0)\nvar xs = [0, 0, 0]\nvar t = 0\nlimit = 10\npar for i in 0..2\n  var mine = [limit]\n"
               "  mine.push(i)\n  mine[0] = 5\n  xs[i] = mine.sum()\n  t += xs[i]\n  c.add(1)\nprint(xs)\nprint(t + c.load())\n")
        self.assertEqual(self.par(src), "[5, 6, 7]\n21\n")

    def test_other_errors(self):
        for src, code in [("par for i, j in 0..2\n  print(i)\n", "E0010"),
                          ("par for x in 5\n  print(x)\n", "E0103"),
                          ("ys = par for x in [1, 2] -> print(x)\n", "E0103"),
                          ("ys = par for x in [1, 2] where x > 1 -> x\n", "E0010"),
                          ("x = spawn 1 + 2\n", "E0604"),
                          ("xs = [1]\nt = spawn xs.len()\n", "E0604"),
                          ("t = spawn nothing(1)\n", "E0101"),
                          ("fn f(n: int) -> int\n  n\n\nt = spawn f(1, 2)\n", "E0104"),
                          ("fn f(n: int) -> int\n  n\n\nt = spawn f(\"a\")\n", "E0103"),
                          ("fn f(n: int) -> int ! err\n  fail(\"x\")\n\nt = spawn f(1)\nprint(t.wait() + 1)\n", "E0203"),
                          ("a = atomic(1.5)\n", "E0103"),
                          ("a = atomic(true)\nprint(a.add(1))\n", "E0109"),
                          ("a = atomic(0)\na.store(true)\n", "E0103"),
                          ("fn f(a: atomic[str]) -> int\n  1\n\nx = 1\n", "E0110"),
                          ("c = chan[int?](1)\n", "E0110"),
                          ("c = chan[int](1)\nc.send(\"a\")\n", "E0103"),
                          ("c = chan[int](1, 2)\n", "E0104"),
                          ("fn f() ! io\n  print(1)\n\nfn g()\n  t = spawn f()\n  t.wait()\n\ng()\n", "E0201")]:
            with self.subTest(src=src):
                self.assertEqual([e["code"] for e in self.errors(src)], [code])


class TaskTests(ParCase):
    def test_spawn_and_wait(self):
        src = ("fn fib(n: int) -> int\n  if n < 2\n    return n\n  fib(n - 1) + fib(n - 2)\n\nfn hi(s: str) -> str\n  s + \"!\"\n\n"
               "fn quiet(n: int)\n  n + 1\n\n"
               "t = spawn fib(20)\nu = spawn hi(\"a\" + \"b\")\nq = spawn quiet(1)\nq.wait()\nprint(t.wait())\nprint(u.wait())\nprint(t.done())\n"
               "ts = par for i in 1..10 -> spawn fib(i)\nvar s = 0\nfor x in ts\n  s += x.wait()\nprint(s)\n")
        self.assertEqual(self.par(src), "6765\nab!\ntrue\n143\n")

    def test_errors_reach_the_waiter(self):
        src = ("error Odd\n\nfn half(n: int) -> int ! err\n  if n % 2 != 0\n    fail(Odd, \"{n} is odd\")\n  n / 2\n\n"
               "fn both(a: int, b: int) -> int ! err\n  x = spawn half(a)\n  y = spawn half(b)\n  x.wait()? + y.wait()?\n\n"
               "print(both(4, 6)?)\nprint(both(4, 7) or e -> if e.kind == Odd then -1 else -2)\n"
               "t = spawn half(9)\nprint(t.wait() or 0)\nu = spawn half(3)\nprint(u.wait()?)\nprint(\"not reached\")\n")
        path = self.write("p.tok", src)
        for t in ("1", "4"):
            r = tokc("run", path, env=dict(self.env, TOK_THREADS=t))
            self.assertEqual((r.returncode, r.stdout, r.stderr), (1, "5\n-1\n0\n", "error: 3 is odd\n"))

    def test_arguments_move_or_copy(self):
        src = ("fn grow(xs: [str]) -> [str]\n  var ys = xs\n  ys.push(\"task\")\n  ys\n\n"
               "var mine = [\"a\", \"b\"]\nt = spawn grow(mine)\nmine.push(\"main\")\nprint(t.wait())\nprint(mine)\n"
               "gone = [\"x\"]\nu = spawn grow(gone)\nprint(u.wait())\n")
        self.assertEqual(self.par(src), '["a", "b", "task"]\n["a", "b", "main"]\n["x", "task"]\n')

    def test_waiting_twice_is_a_panic(self):
        src = "fn f() -> [int]\n  [1]\n\nt = spawn f()\nprint(t.wait())\nprint(t.wait())\n"
        self.assertIn("wait() was already called", self.par(src, code=70))

    def test_task_types_in_signatures(self):
        src = ("fn one() -> int\n  1\n\nfn bad() -> int ! err\n  fail(\"bad\")\n\nfn start() -> task[int]\n  spawn one()\n\n"
               "fn get(t: task[int], u: task[int, err]) -> int\n  t.wait() + (u.wait() or 10)\n\nprint(get(start(), spawn bad()))\n")
        self.assertEqual(self.par(src), "11\n")


class ChannelTests(ParCase):
    def test_producer_and_consumer(self):
        src = ("fn produce(ch: chan[str], n: int)\n  for i in 1..n\n    ch.send(\"m{i}\")\n  ch.close()\n\n"
               "ch = chan[str](2)\nt = spawn produce(ch, 20)\nvar got: [str] = []\nfor m in ch\n  got.push(m)\nt.wait()\n"
               "print(got.len())\nprint(got[19])\nprint(ch.recv() == none)\n")
        self.assertEqual(self.par(src), "20\nm20\ntrue\n")

    def test_many_senders_and_receivers(self):
        src = ("fn work(jobs: chan[int], out: chan[int])\n  for j in jobs\n    out.send(j * j)\n\n"
               "fn feed(jobs: chan[int], lo: int, hi: int)\n  for i in lo..hi\n    jobs.send(i)\n\n"
               "jobs = chan[int](3)\nout = chan[int](1)\nws = par for i in 1..3 -> spawn work(jobs, out)\n"
               "a = spawn feed(jobs, 1, 100)\nb = spawn feed(jobs, 101, 200)\nvar t = 0\nfor i in 1..200\n  t += out.recv() or 0\n"
               "a.wait()\nb.wait()\njobs.close()\nfor w in ws\n  w.wait()\nprint(t)\n")
        self.assertEqual(self.par(src), "2686700\n")

    def test_break_out_of_a_channel_loop_and_messages_left_behind(self):
        src = ("ch = chan[[str]](8)\nfor i in 1..5\n  ch.send([\"v{i}\"])\nfor m in ch\n  if m[0] == \"v2\"\n    break\n"
               "  if m[0] == \"v1\"\n    continue\nprint(ch.recv() or [])\n")
        self.assertEqual(self.par(src), '["v3"]\n')

    def test_a_sent_value_is_independent(self):
        src = ("ch = chan[[int]](2)\nvar xs = [1, 2]\nch.send(xs)\nxs.push(3)\nch.send(xs)\nprint(ch.recv() or [])\nprint(ch.recv() or [])\n")
        self.assertEqual(self.par(src), "[1, 2]\n[1, 2, 3]\n")

    def test_misuse_is_a_panic(self):
        self.assertIn("closed channel", self.par("c = chan[int](1)\nc.close()\nc.send(1)\n", code=70))
        self.assertIn("room for at least 1 value", self.par("c = chan[int](0)\n", code=70))


class AtomicTests(ParCase):
    def test_a_parallel_counter_totals_exactly(self):
        src = "c = atomic(0)\npar for i in 1..20000\n  c.add(1)\nprint(c.load())\nprint(c)\n"
        self.assertEqual(self.par(src), "20000\n20000\n")

    def test_swap_and_compare_swap(self):
        src = ("a = atomic(5)\nprint(a.swap(7))\nprint(a.load())\nprint(a.compare_swap(5, 9))\nprint(a.compare_swap(7, 9))\n"
               "print(a.load())\nprint(a.add(-10))\na.store(3)\nprint(a.add(0))\n"
               "f = atomic(false)\nprint(f.swap(true))\nprint(f.compare_swap(false, true))\nprint(f.compare_swap(true, false))\n"
               "print(f.load())\nf.store(true)\nprint(f)\n")
        self.assertEqual(self.par(src), "5\n7\nfalse\ntrue\n9\n-1\n3\nfalse\nfalse\ntrue\nfalse\ntrue\n")

    def test_a_compare_swap_retry_loop(self):
        src = ("fn bump(c: atomic[int], n: int)\n  par for i in 1..n\n    var old = c.load()\n"
               "    while not c.compare_swap(old, old + 2)\n      old = c.load()\n\n"
               "c = atomic(0)\nts = par for i in 1..6 -> spawn bump(c, 500)\nfor t in ts\n  t.wait()\nprint(c.load())\n")
        self.assertEqual(self.par(src), "6000\n")

    def test_one_iteration_claims_a_flag(self):
        src = ("won = atomic(false)\nvar winners = 0\npar for i in 1..1000\n  if won.compare_swap(false, true)\n    winners += 1\n"
               "print(winners)\n")
        self.assertEqual(self.par(src), "1\n")

    def test_an_atomic_add_wraps(self):
        src = "a = atomic(9223372036854775807)\nprint(a.add(1))\n"
        self.assertEqual(self.par(src), "-9223372036854775808\n")

    def test_atomics_in_records_and_lists(self):
        src = ("type Stats = { hits: atomic[int], name: str }\n\nfn hit(s: Stats)\n  s.hits.add(1)\n\n"
               "s = Stats { hits: atomic(0), name: \"a\" }\nts = par for i in 1..20 -> spawn hit(s)\nfor t in ts\n  t.wait()\n"
               "print(s.hits.load())\ncs = [atomic(0), atomic(0)]\npar for i in 1..100\n  cs[i % 2].add(i)\nprint(cs)\n")
        self.assertEqual(self.par(src), "20\n[2550, 2500]\n")


if __name__ == "__main__":
    unittest.main()
