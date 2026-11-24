"""Item 6: modules, fs, program arguments, math, and the design doc examples."""
import os
import unittest

from helpers import ROOT, TokCase, tokc

UTIL = ('type Point = { x: f64, y: f64 }\n\n'
        'fn origin() -> Point\n'
        '  Point { x: 0.0, y: 0.0 }\n\n'
        'fn dist(a: Point, b: Point) -> f64\n'
        '  sqrt((a.x - b.x) * (a.x - b.x) + (a.y - b.y) * (a.y - b.y))\n\n'
        'fn shout(s: str) -> str ! io\n'
        '  print("util says {s}")\n'
        '  other.twice(s)\n')
OTHER = 'fn twice(s: str) -> str\n  s + s\n'


class ModuleTests(TokCase):
    def setUp(self):
        self.write("util.tok", UTIL)
        self.write("other.tok", OTHER)

    def test_qualified_calls_and_types_across_files(self):
        src = ('fn main() ! io\n'
               '  p = util.Point { x: 3.0, y: 4.0 }\n'
               '  q: util.Point = util.origin()\n'
               '  print("{util.dist(p, q)} {util.shout("hi")} {p} {p == q}")\n')
        self.assertEqual(self.run_src(src), "util says hi\n5.0 hihi {x: 3.0, y: 4.0} false\n")

    def test_module_effects_are_checked_at_the_call(self):
        src = 'fn main()\n  s = util.shout("x")\n'
        e = self.check_err(src)
        self.assertEqual(e["code"], "E0201")
        self.assertIn("util.shout", e["message"])

    def test_unknown_module_and_function(self):
        cases = {
            'fn main()\n  x = nope.f(1)\n': ["E0501"],
            'fn main()\n  x = util.nope(1)\n': ["E0101"],
            'fn main()\n  x = fs.nope("a")\n': ["E0101"],
            'fn main()\n  x: nope.T = 1\n': ["E0501"],
            'fn main()\n  x: util.Nope = 1\n': ["E0110"],
            'fn main()\n  x = util.dist(1, 2)\n': ["E0103"],
            'fn main()\n  x = util.origin(1)\n': ["E0104"],
        }
        for src, codes in cases.items():
            with self.subTest(src=src):
                self.assertEqual(self.codes(src), codes)

    def test_errors_inside_a_module_name_its_file(self):
        self.write("broken.tok", 'fn f() -> int\n  "text"\n')
        errs = self.errors('fn main() ! io\n  print(broken.f())\n  print(zzz)\n')
        self.assertEqual([(os.path.basename(e["file"]), e["code"]) for e in errs],
                         [("e.tok", "E0101"), ("broken.tok", "E0103")])


class FsAndArgsTests(TokCase):
    def test_write_then_read(self):
        path = os.path.join(self.tmp, "note.txt")
        src = ('fn main(args: [str]) ! io, fs\n'
               '  fs.write_text(args[1], "one\\ntwo\\n")?\n'
               '  text = fs.read_text(args[1])?\n'
               '  print("{text.lines()} {args.len()} {args[2] or "-"}")\n'
               '  print(fs.read_text(args[1] + ".missing") or "fallback")\n')
        self.assertEqual(self.run_src(src, "--", path), '["one", "two"] 2 -\nfallback\n')
        with open(path) as fh:
            self.assertEqual(fh.read(), "one\ntwo\n")

    def test_missing_file_reaches_main_as_an_error(self):
        out, err = self.run_fail('fn main() ! io, fs\n  print(fs.read_text("/no/such/file")?)\n', 1)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("error: cannot read '/no/such/file'"), err)

    def test_fs_needs_the_effect_and_handling(self):
        e = self.check_err('fn main() ! io\n  print(fs.read_text("x") or "")\n')
        self.assertEqual((e["code"], e["fix"]["insert"]), ("E0201", ", fs"))
        self.assertEqual(self.codes('fn main() ! io, fs\n  t = fs.read_text("x")\n'), ["E0203"])
        self.assertEqual(self.codes('fn main() ! io, fs\n  fs.write_text("x", "y")\n'), ["E0203"])
        self.assertEqual(self.codes('fn main(a: int)\n  x = 1\n'), ["E0402"])


class MathTests(TokCase):
    def test_math(self):
        src = ('fn main() ! io\n'
               '  xs = [2.0, 4.0, 9.0]\n'
               '  print("{sqrt(16.0)} {sqrt(2)} {mean(xs)} {mean([1, 2])} {max(xs)} {min(xs)}")\n'
               '  print("{max([3, 9, 2])} {min(2.5, 1)} {max(1, 2)} {abs(0 - 5)} {abs(-2.5)} {min([4, 1])}")\n')
        self.assertEqual(self.run_src(src), "4.0 1.4142135623730951 5.0 1.5 9.0 2.0\n9 1.0 2 5 2.5 1\n")

    def test_empty_list_gives_none(self):
        src = ('fn spread(xs: [int]) -> int\n'
               '  (max(xs) or 0) - (min(xs) or 0)\n\n'
               'fn main() ! io\n'
               '  xs: [f64] = []\n'
               '  ns: [int] = []\n'
               '  bs: [u8] = [7, 3]\n'
               '  print("{mean(xs)} {min(xs)} {max(ns)} {mean(xs) or 0.5} {mean(xs) == none} {max(ns) or -1}")\n'
               '  print("{mean([1.0, 2.0])} {max(bs)} {min(bs) or 0} {spread(ns)} {spread([4, 9, 1])}")\n'
               '  m: f64? = mean([2.0, 4.0])\n'
               '  if m\n'
               '    print("has a mean")\n')
        self.assertEqual(self.run_src(src), "none none none 0.5 true -1\n1.5 7 3 0 8\nhas a mean\n")

    def test_mean_of_a_list_is_not_a_plain_number(self):
        e = self.check_err('fn main() ! io\n  xs = [1.0, 2.0]\n  print(mean(xs) + 1.0)\n')
        self.assertEqual(e["code"], "E0103")
        self.assertIn("f64?", e["message"])

    def test_errors(self):
        cases = {
            'fn main()\n  n = 4\n  x = sqrt(n)\n': ["E0106"],
            'fn main()\n  x = sqrt("a")\n': ["E0103"],
            'fn main()\n  x = mean([1, 2].map(v -> v))\n': ["E0103"],
            'fn main()\n  a = 1\n  b = 2.0\n  x = max(a, b)\n': ["E0106"],
            'fn main()\n  x = max(1, 2, 3)\n': ["E0104"],
            'fn main()\n  x = abs("a")\n': ["E0103"],
            'fn sqrt(x: f64) -> f64\n  x\n\nfn main()\n  x = 1\n': ["E0102"],
        }
        for src, codes in cases.items():
            with self.subTest(src=src):
                self.assertEqual(self.codes(src), codes)


class DocExampleTests(TokCase):
    def example(self, *args):
        r = tokc("run", *args, env=self.env)
        return r.returncode, r.stdout, r.stderr

    def test_example_c_logscan(self):
        self.assert_clean_c(os.path.join(ROOT, "examples/logscan.tok"))
        code, out, _ = self.example("examples/logscan.tok", "--", "examples/data/server.log")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[0], "3 errors in 7 lines")
        self.assertEqual(len(out.splitlines()), 4)
        self.assertTrue(all("ERROR" in l for l in out.splitlines()[1:]))

    def test_example_c_default_path_missing(self):
        r = tokc("run", os.path.join(ROOT, "examples/logscan.tok"), env=self.env, cwd=self.tmp)
        self.assertEqual((r.returncode, r.stdout), (1, ""))
        self.assertTrue(r.stderr.startswith("error: cannot read 'app.log'"), r.stderr)

    def test_example_c_only_first_ten(self):
        log = self.write("big.log", "".join(f"line {i} ERROR\nline {i} ok\n" for i in range(25)))
        code, out, _ = self.example("examples/logscan.tok", "--", log)
        self.assertEqual(out.splitlines()[0], "25 errors in 50 lines")
        self.assertEqual(len(out.splitlines()), 11)

    def test_example_b_normalize(self):
        self.assert_clean_c(os.path.join(ROOT, "examples/normalize.tok"))
        code, out, _ = self.example("examples/normalize.tok")
        self.assertEqual(out, "mean 5.0, max 9.0\n[-1.5, -0.5, -0.5, -0.5, 0.0, 0.0, 1.0, 2.0]\n")

    def test_example_a_users(self):
        self.assert_clean_c(os.path.join(ROOT, "examples/users.tok"))
        code, out, _ = self.example("examples/users.tok")
        self.assertEqual(out, "Ann: ann@example.com\nCy: cy@example.com\n")

    def test_modules_example(self):
        self.assert_clean_c(os.path.join(ROOT, "examples/modules/main.tok"))
        code, out, _ = self.example("examples/modules/main.tok")
        self.assertEqual(out, "distance 5.0\npoint at (3.0, 4.0)\n")


if __name__ == "__main__":
    unittest.main()
