"""Item 3: optionals and the error path."""
import unittest

from helpers import TokCase

HALF = ('fn half(n: int) -> int ! err\n'
        '  if n % 2 != 0\n'
        '    fail("{n} is odd")\n'
        '  n / 2\n\n')


class OptionalTests(TokCase):
    def test_none_default_and_truthiness(self):
        src = ('type User = { id: int, name: str, email: str? }\n\n'
               'fn main() ! io\n'
               '  users = [User { id: 1, name: "Ann", email: "ann@x.io" }, User { id: 2, name: "Bob" }]\n'
               '  for u in users where u.email\n'
               '    print("{u.name}: {u.email}")\n'
               '  for u in users\n'
               '    print(u.email or "(none)")\n'
               '    print(u.email == none)\n'
               '  m: int? = 5\n'
               '  var k: int? = none\n'
               '  if m\n'
               '    print("m is set")\n'
               '  if k\n'
               '    print("not printed")\n'
               '  k = 3\n'
               '  print("{m} {k} {k != none} {m == k}")\n')
        self.assertEqual(self.run_src(src),
                         "Ann: ann@x.io\nann@x.io\nfalse\n(none)\ntrue\nm is set\n5 3 true false\n")

    def test_function_returning_optional(self):
        src = ('fn find(xs: [str], x: str) -> int?\n'
               '  var i = 0\n'
               '  for y in xs\n'
               '    if y == x\n'
               '      return i\n'
               '    i += 1\n'
               '  none\n\n'
               'fn main() ! io\n'
               '  xs = ["a", "b"]\n'
               '  print("{find(xs, "b")} {find(xs, "z")} {find(xs, "z") or 0 - 1}")\n')
        self.assertEqual(self.run_src(src), "1 none -1\n")

    def test_index_or_default(self):
        src = ('fn main() ! io\n'
               '  xs = ["zero", "one"]\n'
               '  print("{xs[1] or "?"} {xs[2] or "?"} {xs[0 - 1] or "?"}")\n'
               '  opts: [int?] = [1, none]\n'
               '  print("{opts[0] or 9} {opts[1] or 9} {opts[5] or 9}")\n')
        self.assertEqual(self.run_src(src), "one ? ?\n1 9 9\n")

    def test_default_is_lazy(self):
        src = ('fn loud() -> int ! io\n'
               '  print("evaluated")\n'
               '  7\n\n'
               'fn main() ! io\n'
               '  a: int? = 1\n'
               '  b: int? = none\n'
               '  print(a or loud())\n'
               '  print(b or loud())\n'
               '  print(true or loud() == 7)\n'
               '  print(false and loud() == 7)\n')
        self.assertEqual(self.run_src(src), "1\nevaluated\n7\ntrue\nfalse\n")


class ErrorPathTests(TokCase):
    def test_propagate_and_default(self):
        src = HALF + ('fn quarter(n: int) -> int ! err\n'
                      '  h = half(n)?\n'
                      '  half(h)?\n\n'
                      'fn main() ! io\n'
                      '  print(quarter(12)?)\n'
                      '  print(quarter(6) or 0)\n'
                      '  print(half(8)? + 1)\n')
        self.assertEqual(self.run_src(src), "3\n0\n5\n")

    def test_error_reaching_main_exits_1(self):
        src = HALF + ('fn show(n: int) ! io, err\n'
                      '  print(half(n)?)\n\n'
                      'fn main() ! io\n'
                      '  show(4)?\n'
                      '  show(3)?\n'
                      '  print("not reached")\n')
        out, err = self.run_fail(src, 1)
        self.assertEqual((out, err), ("2\n", "error: 3 is odd\n"))

    def test_fail_in_main_and_as_a_default(self):
        out, err = self.run_fail(HALF + 'fn main() ! io\n  x = half(5) or fail("no half")\n  print(x)\n', 1)
        self.assertEqual((out, err), ("", "error: no half\n"))

    def test_unhandled_call_is_an_error_with_a_fix(self):
        src = HALF + 'fn main() ! io\n  x = half(4)\n  print(x)\n'
        e = self.check_err(src)
        self.assertEqual((e["code"], e["fix"]["insert"], e["fix"]["line"], e["fix"]["col"]), ("E0203", "?", 7, 14))
        self.assertEqual(self.fix_and_run(src), "2\n")

    def test_fix_chain_adds_question_mark_then_err(self):
        src = HALF + ('fn twice(n: int) -> int\n'
                      '  half(n) * 4\n\n'
                      'fn main() ! io\n'
                      '  print(twice(6)?)\n')
        codes = self.codes(src)
        self.assertIn("E0203", codes)
        path = self.write("chain.tok", src)
        self.assertEqual(self.fix_and_run(src, "chain.tok"), "12\n")
        with open(path) as fh:
            fixed = fh.read()
        self.assertIn("fn twice(n: int) -> int ! err\n  half(n)? * 4\n", fixed)

    def test_errors(self):
        cases = {
            'fn f() -> int\n  fail("x")\n\nfn main()\n  f()\n': ["E0202"],
            'fn main() ! io\n  x = 5\n  print(x?)\n': ["E0204"],
            'fn main() ! io\n  x: int? = 5\n  print(x?)\n': ["E0204"],
            'fn main()\n  x = none\n': ["E0108"],
            'fn main()\n  x: int = none\n': ["E0103"],
            'fn main()\n  x: int?? = none\n': ["E0110"],
            'fn main() ! io\n  x = 5 or 6\n': ["E0103"],
            'fn main() ! io\n  x: int? = 5\n  y = x or "s"\n': ["E0103"],
            'fn main() ! io\n  x: int? = 5\n  y = x + 1\n': ["E0103"],
            'fn main() ! io\n  fail(3)\n': ["E0103"],
            'fn main() ! io\n  x = 1 + fail("a")\n': ["E0103"],
        }
        for src, codes in cases.items():
            with self.subTest(src=src):
                self.assertEqual(self.codes(src), codes)

    def test_remove_question_mark_fix(self):
        self.assertEqual(self.fix_and_run('fn main() ! io\n  x = 5\n  print(x?)\n'), "5\n")


if __name__ == "__main__":
    unittest.main()
