"""Design decisions made after Phase 1: int is i64, int overflow panics, optionals narrow,
empty-list mean/min/max are optional."""
import unittest

from helpers import TokCase, tokc


class IntTests(TokCase):
    def test_int_and_i64_are_one_type(self):
        src = ('type P = { a: i64, b: int }\n\n'
               'fn add(a: i64, b: int) -> i64\n'
               '  a + b\n\n'
               'fn main() ! io\n'
               '  x: i64 = 40\n'
               '  y = 2\n'
               '  xs: [i64] = [x, y]\n'
               '  p = P { a: x, b: y }\n'
               '  print("{add(x, y)} {x + y} {xs.sum()} {p.a == p.b} {i64(y) + int(x)}")\n')
        self.assertEqual(self.run_src(src), "42 42 42 false 42\n")

    def test_error_messages_say_int(self):
        e = self.check_err('fn main()\n  a: i64 = 5\n  b: i32 = a\n')
        self.assertEqual(e["code"], "E0106")
        self.assertIn("i32", e["message"])
        self.assertIn("int", e["message"])
        self.assertNotIn("i64", e["message"])

    def test_formatter_writes_int(self):
        path = self.write("a.tok", 'type P = { i64: i64 }\n\nfn f(a: i64) -> [i64]\n  p = P { i64: a }\n  [i64(a), p.i64]\n')
        self.assertEqual(tokc("fmt", path).returncode, 0)
        with open(path) as fh:
            self.assertEqual(fh.read(), 'type P = { i64: int }\n\nfn f(a: int) -> [int]\n  p = P { i64: a }\n  [int(a), p.i64]\n')

    def test_int_overflow_panics_with_the_line(self):
        cases = {
            'a = 9223372036854775807\n  print(a + 1)\n': "integer overflow: 9223372036854775807 + 1 does not fit in int (f.tok:3)",
            'a = -9223372036854775807\n  print(a - 2)\n': "integer overflow: -9223372036854775807 - 2 does not fit in int (f.tok:3)",
            'a = 4611686018427387904\n  print(a * 2)\n': "integer overflow: 4611686018427387904 * 2 does not fit in int (f.tok:3)",
            'a = -9223372036854775807 - 1\n  print(-a)\n': "does not fit in int (f.tok:3)",
            'a = -9223372036854775807 - 1\n  print(abs(a))\n': "does not fit in int (f.tok:3)",
            'a = -9223372036854775807 - 1\n  b = -1\n  print(a / b)\n': "does not fit in int (f.tok:4)",
            'var a = 9223372036854775807\n  a += 1\n': "integer overflow: 9223372036854775807 + 1 does not fit in int (f.tok:3)",
            'xs = [9223372036854775807, 1]\n  print(xs.sum())\n': "does not fit in int (f.tok:3)",
            'a: i64 = 9223372036854775807\n  print(a + 1)\n': "does not fit in int (f.tok:3)",
        }
        for body, msg in cases.items():
            with self.subTest(body=body):
                out, err = self.run_fail('fn main() ! io\n  ' + body, 70)
                self.assertIn("tok: panic: ", err)
                self.assertIn(msg, err)

    def test_int_arithmetic_at_the_edges_is_exact(self):
        src = ('fn main() ! io\n'
               '  hi = 9223372036854775807\n'
               '  lo = -9223372036854775807 - 1\n'
               '  print("{hi - 1 + 1} {lo + 1 - 1} {hi + lo} {(hi / 2) * 2 + 1} {lo % -1} {-hi} {abs(-hi)}")\n')
        self.assertEqual(self.run_src(src), "9223372036854775807 -9223372036854775808 -1 9223372036854775807 0 "
                                            "-9223372036854775807 9223372036854775807\n")

    def test_sized_integers_still_wrap(self):
        src = ('fn main() ! io\n'
               '  var a: u8 = 250\n'
               '  a += 10\n'
               '  b: i32 = 2147483647\n'
               '  c: u64 = 18446744073709551615\n'
               '  d: i8 = -128\n'
               '  print("{a} {b + 1} {c + 1} {c * 2} {-d} {abs(d)} {d - 1}")\n')
        self.assertEqual(self.run_src(src), "4 -2147483648 0 18446744073709551614 -128 -128 127\n")


USER = 'type User = { id: int, name: str, email: str?, boss: Boss? }\n\ntype Boss = { name: str, phone: str? }\n\n'


class NarrowingTests(TokCase):
    def test_if_and_where_narrow_an_optional(self):
        src = USER + (
            'fn shout(s: str) -> str\n'
            '  s.upper()\n\n'
            'fn main() ! io\n'
            '  users = [User { id: 1, name: "Ann", email: "ann@x.io", boss: Boss { name: "Zed", phone: "555" } }, User { id: 2, name: "Bob" }]\n'
            '  for u in users where u.email\n'
            '    print(shout(u.email) + " " + "{u.email.len()}")\n'
            '  for u in users\n'
            '    if u.email\n'
            '      print(u.email.len())\n'
            '    else\n'
            '      print(u.email or "no email")\n'
            '    if u.boss\n'
            '      b = u.boss\n'
            '      print(b.name)\n'
            '      if u.boss.phone\n'
            '        print(shout("tel " + u.boss.phone))\n'
            '  n: int? = 5\n'
            '  if n\n'
            '    print(n + 1)\n'
            '    print([1, 2, 3].map(x -> x * n))\n')
        self.assertEqual(self.run_src(src), "ANN@X.IO 8\n8\nZed\nTEL 555\nno email\n6\n[5, 10, 15]\n")

    def test_comparisons_with_none_and_boolean_operators(self):
        src = ('fn pick(a: int?, b: int?) -> int\n'
               '  if a != none and a > 3\n'
               '    return a * 2\n'
               '  if a == none or a < 0\n'
               '    return -1\n'
               '  if not (b == none)\n'
               '    return a + b\n'
               '  a\n\n'
               'fn main() ! io\n'
               '  print("{pick(5, none)} {pick(none, 1)} {pick(-4, 1)} {pick(2, 10)} {pick(2, none)}")\n')
        self.assertEqual(self.run_src(src), "10 -1 -1 12 2\n")

    def test_guard_clause_narrows_the_rest_of_the_block(self):
        src = ('fn avg(xs: [f64]) -> f64 ! err\n'
               '  m = mean(xs)\n'
               '  if m == none\n'
               '    fail("no data")\n'
               '  m * 1.0\n\n'
               'fn first_word(s: str?) -> str\n'
               '  if s == none\n'
               '    return "(nothing)"\n'
               '  else if s.len() == 0\n'
               '    return "(empty)"\n'
               '  s.split(" ")[0]\n\n'
               'fn size(s: str?) -> int\n'
               '  if s\n'
               '    print_nothing()\n'
               '  else\n'
               '    return 0\n'
               '  s.len()\n\n'
               'fn print_nothing()\n'
               '  x = 1\n\n'
               'fn main() ! io\n'
               '  print("{avg([1.0, 2.0]) or 0.0} {avg([]) or -1.0} {first_word(none)} {first_word("")} {first_word("hi there")} {size("abc")} {size(none)}")\n')
        self.assertEqual(self.run_src(src), "1.5 -1.0 (nothing) (empty) hi 3 0\n")

    def test_a_var_is_narrowed_only_when_nothing_in_the_block_assigns_it(self):
        ok = ('fn main() ! io\n'
              '  var k: int? = 5\n'
              '  if k\n'
              '    print(k + 1)\n'
              '  k = none\n'
              '  if k == none\n'
              '    print("gone")\n')
        self.assertEqual(self.run_src(ok), "6\ngone\n")
        cases = [
            # assigned later in the same block
            'var k: int? = 5\n  if k\n    print(k + 1)\n    k = none\n',
            # assigned in a loop inside the block: an earlier line of the loop runs again afterwards
            'var k: int? = 5\n  if k\n    for i in 1..3\n      print(k + i)\n      k = none\n',
            # a field of a var record
            'var u = User { id: 1, name: "a", email: "e" }\n  if u.email\n    print(u.email.len() + 1)\n    u.email = none\n',
            # the whole record is replaced
            'var u = User { id: 1, name: "a", email: "e" }\n  if u.email\n    u = User { id: 2, name: "b" }\n    print(u.email.len() + 1)\n',
            # guard clause, then reassigned before the use
            'var k: int? = 5\n  if k == none\n    return\n  k = none\n  print(k + 1)\n',
            # a list element is not a narrowable path
            'xs: [int?] = [1]\n  if xs[0]\n    print(xs[0] + 1)\n',
        ]
        for body in cases:
            with self.subTest(body=body):
                codes = self.codes(USER + 'fn main() ! io\n  ' + body)
                self.assertTrue(codes and set(codes) <= {"E0103", "E0109"}, codes)

    def test_var_guard_clause_holds_until_the_end_of_the_block(self):
        src = ('fn main() ! io\n'
               '  var total = 0\n'
               '  var k: int? = none\n'
               '  for i in 1..3\n'
               '    k = i\n'
               '    if k == none\n'
               '      return\n'
               '    total += k\n'
               '  print(total)\n')
        self.assertEqual(self.run_src(src), "6\n")

    def test_narrowed_copy_does_not_alias(self):
        src = ('type Bag = { items: [int]? }\n\n'
               'fn main() ! io\n'
               '  b = Bag { items: [1, 2] }\n'
               '  if b.items\n'
               '    var mine = b.items\n'
               '    mine.push(3)\n'
               '    print("{mine} {b.items}")\n')
        self.assertEqual(self.run_src(src), "[1, 2, 3] [1, 2]\n")

    def test_default_on_a_known_value_is_an_error_with_a_fix(self):
        src = USER + ('fn main() ! io\n'
                      '  u = User { id: 1, name: "Ann", email: "ann@x.io" }\n'
                      '  if u.email\n'
                      '    print(u.email or "none")\n')
        e = self.check_err(src)
        self.assertEqual(e["code"], "E0205")
        self.assertIn("'u.email'", e["message"])
        self.assertEqual(self.fix_and_run(src), "ann@x.io\n")

    def test_testing_a_known_value_again_is_an_error(self):
        e = self.check_err('fn main() ! io\n  n: int? = 1\n  if n\n    if n\n      print(n)\n')
        self.assertEqual((e["code"], e["line"]), ("E0205", 4))
        e = self.check_err('fn f(n: int?) -> int\n  if n == none\n    return 0\n  if n != none\n    return 1\n  n\n\nfn main()\n  x = f(1)\n')
        self.assertEqual((e["code"], e["line"]), ("E0205", 4))
        self.assertIn("always true", e["message"])


if __name__ == "__main__":
    unittest.main()
