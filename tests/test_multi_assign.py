"""Multiple assignment: a, b = x, y. The right side is computed in full before anything is stored."""
import unittest

from helpers import TokCase

PROGRAM = '''type P = { x: int, y: int }

fn fib(n: int) -> int
  var a, b = 0, 1
  for i in 1..n
    a, b = b, a + b
  a

fn gcd(a: int, b: int) -> int
  var x, y = a, b
  while y != 0
    x, y = y, x % y
  x

print(fib(10))
print(gcd(48, 18))
let s, t = "one", "two"
var u, v = s + t, [t, s]
u, v = v.join("-"), [u]
print(u)
print(v)
var xs = [1, 2, 3]
xs[0], xs[2] = xs[2], xs[0]
print(xs)
var p = P { x: 1, y: 2 }
p.x, p.y = p.y, p.x
print(p)
q, r = 17 / 5, 17 % 5
print("{q} {r}")
var words = ["a", "b"]
words, fresh = words.reverse(), words.len()
print("{words} {fresh}")
var i, name = 0, "x"
i, name = i + 1, "{name}{i}"
print("{i} {name}")
let one = 1
print(one)
'''


class MultiAssignTests(TokCase):
    def test_swaps_new_names_fields_and_elements(self):
        self.assertEqual(self.run_src(PROGRAM), '55\n6\ntwo-one\n["onetwo"]\n[3, 2, 1]\n{x: 2, y: 1}\n3 2\n'
                                                '["b", "a"] 2\n1 x0\n1\n')

    def test_three_way_rotation_and_literal_typing(self):
        src = ('var a, b, c = 1, 2, 3\na, b, c = b, c, a\nprint("{a}{b}{c}")\n'
               'var x: f64 = 0.5\nvar n = 1\nx, n = 2, n + 1\nprint("{x} {n}")\n')
        self.assertEqual(self.run_src(src), "231\n2.0 2\n")

    def test_errors(self):
        cases = [
            ('a, b = 1\n', "E0018", "2 names on the left but 1 value on the right"),
            ('a, b = 1, 2, 3\n', "E0018", "but 3 values"),
            ('fn f() -> int\n  1\n\na, b = f()\n', "E0018", "there are no tuples"),
            ('x = 1\nlet x, y = 2, 3\n', "E0302", "drop 'let'"),
            ('x = 1\nvar x, y = 2, 3\n', "E0302", "drop 'var'"),
            ('x = 1\nx, y = 2, 3\n', "E0301", "'x' is immutable"),
            ('a, a = 1, 2\n', "E0302", "appears twice"),
            ('var a, b = 1, "s"\na, b = b, a\n', "E0103", "'a' is int, cannot assign str"),
            ('a, b += 1, 2\n', "E0010", "expected '='"),
            ('var a: int, b = 1, 2\n', "E0010", "expected '='"),
            ('let x = 1\nlet x = 2\n', "E0302", "drop 'let'"),
            ('a, 1 = 1, 2\n', "E0016", "left side"),
            ('a, b = print(1), 2\n', "E0103", "returns nothing"),
            ('let = 1\n', "E0010", "a variable name"),
        ]
        for src, code, text in cases:
            with self.subTest(src=src):
                e = self.check_err(src)
                self.assertEqual(e["code"], code)
                self.assertIn(text, e["message"])

    def test_the_let_fix_applies(self):
        src = 'x = 1\nprint(x)\nvar y = 2\nlet y, z = 3, 4\nprint(y + z)\n'
        self.assertEqual(self.fix_and_run(src), "1\n7\n")

    def test_an_error_on_the_right_still_defines_the_names(self):
        errs = self.errors('a, b = 1, nope\nprint(a)\nprint(b)\n')
        self.assertEqual([e["code"] for e in errs], ["E0101"])

    def test_assignment_ends_narrowing(self):
        src = ('var m = "4".to_int()\nvar k = 0\nif m\n  m, k = none, m\n  print(m + 1)\n')
        e = self.check_err(src)
        self.assertEqual(e["code"], "E0103")


if __name__ == "__main__":
    unittest.main()
