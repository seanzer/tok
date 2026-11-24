#!/usr/bin/env python3
"""End-to-end tests: compile real programs, run the binaries, check output and diagnostics."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKC = [sys.executable, os.path.join(ROOT, "compiler", "tokc.py")]


def tokc(*args, env=None):
    return subprocess.run(TOKC + list(args), capture_output=True, text=True, cwd=ROOT, env=env)


class TokTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="toktest")
        cls.env = dict(os.environ, XDG_CACHE_HOME=os.path.join(cls.tmp, "cache"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def write(self, name, src):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fh:
            fh.write(src)
        return path

    def run_src(self, src):
        r = tokc("run", self.write("t.tok", src), env=self.env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout

    def check_err(self, src):
        r = tokc("check", self.write("e.tok", src), "--json")
        self.assertEqual(r.returncode, 1)
        return json.loads(r.stdout)["errors"][0]

    def test_hello_run(self):
        r = tokc("run", "examples/hello.tok", env=self.env)
        self.assertEqual((r.returncode, r.stdout), (0, "hello, world\n"))

    def test_hello_build_native(self):
        out = os.path.join(self.tmp, "hello")
        r = tokc("build", "examples/hello.tok", "-o", out)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(subprocess.run([out], capture_output=True, text=True).stdout, "hello, world\n")

    def test_fizzbuzz(self):
        r = tokc("run", "examples/fizzbuzz.tok", env=self.env)
        lines = r.stdout.splitlines()
        self.assertEqual(lines[:5], ["1", "2", "Fizz", "4", "Buzz"])
        self.assertEqual(lines[14], "FizzBuzz")
        self.assertEqual(lines[15], "sum of 1..15 = 120")

    def test_arithmetic_and_precedence(self):
        self.assertEqual(self.run_src('fn main() ! io\n  print(2 + 3 * 4 - 10 / 3 % 2)\n  print(-(1 + 2))\n'), "13\n-3\n")

    def test_strings(self):
        src = 'fn main() ! io\n  a = "to" + "k"\n  print("{a}: {a == "tok"} 100% \\{ok\\} \\"q\\"")\n'
        self.assertEqual(self.run_src(src), 'tok: true 100% {ok} "q"\n')

    def test_recursion_and_forward_reference(self):
        src = ('fn main() ! io\n  print(fib(20))\n\n'
               'fn fib(n: int) -> int\n  if n < 2\n    return n\n  fib(n - 1) + fib(n - 2)\n')
        self.assertEqual(self.run_src(src), "6765\n")

    def test_bool_logic(self):
        src = 'fn main() ! io\n  x = 5\n  if x > 3 and not (x == 9) or false\n    print("yes")\n  else\n    print("no")\n'
        self.assertEqual(self.run_src(src), "yes\n")

    def test_division_by_zero_panics(self):
        r = tokc("run", self.write("z.tok", 'fn main() ! io\n  z = 0\n  print(1 / z)\n'), env=self.env)
        self.assertEqual(r.returncode, 70)
        self.assertIn("division by zero", r.stderr)

    def test_missing_effect_has_fix(self):
        with open(os.path.join(ROOT, "tests/bad/missing_effect.tok")) as fh:
            e = self.check_err(fh.read())
        self.assertEqual(e["code"], "E0201")
        self.assertEqual((e["fix"]["line"], e["fix"]["insert"]), (1, " ! io"))

    def test_effects_propagate_to_callers(self):
        e = self.check_err('fn a() ! io\n  print("x")\n\nfn b() ! net\n  a()\n\nfn main() ! io, net\n  b()\n')
        self.assertEqual((e["code"], e["fix"]["insert"]), ("E0201", ", io"))

    def test_fix_makes_file_compile(self):
        for name in ("missing_effect.tok", "immutable.tok"):
            path = os.path.join(self.tmp, name)
            shutil.copy(os.path.join(ROOT, "tests/bad", name), path)
            self.assertEqual(tokc("fix", path).returncode, 0)
            self.assertEqual(tokc("run", path, env=self.env).returncode, 0)

    def test_error_codes(self):
        cases = {
            'fn main() ! io\n  print(1 + "two")\n': "E0103",
            'fn main() ! io\n  print(nope)\n': "E0101",
            'fn main() ! io\n  x = 1\n  x = 2\n': "E0301",
            'fn f() -> int\n  if true\n    return 1\n\nfn main()\n  f()\n': "E0105",
            'fn helper()\n  x = 1\n': "E0401",
            'fn main() ! io\n\tprint("tab")\n': "E0002",
            'fn main() ! io\n  print("open)\n': "E0003",
            'fn main() ! magic\n  x = 1\n': "E0013",
            'fn main() ! io\n  print(1, 2)\n': "E0104",
            'fn main() ! io\n  print(1 +)\n': "E0010",
        }
        for src, code in cases.items():
            with self.subTest(code=code):
                self.assertEqual(self.check_err(src)["code"], code)

    def test_error_span_points_at_problem(self):
        e = self.check_err('fn main() ! io\n  print(nope)\n')
        self.assertEqual((e["line"], e["col"], e["end_col"]), (2, 9, 13))


if __name__ == "__main__":
    unittest.main(verbosity=1)
