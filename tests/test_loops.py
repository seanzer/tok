"""while, break and continue."""
import unittest

from helpers import TokCase


class LoopTests(TokCase):
    def test_while(self):
        src = ('var n = args[0].to_int() or 27\n'
               'var steps = 0\n'
               'while n != 1\n'
               '  n = if n % 2 == 0 then n / 2 else 3 * n + 1\n'
               '  steps += 1\n'
               'print(steps)\n'
               'while false\n'
               '  print("never")\n')
        self.assertEqual(self.run_src(src), "111\n")
        self.assertEqual(self.run_src(src, "--", "6"), "8\n")

    def test_break_and_continue_in_every_kind_of_loop(self):
        src = ('var total = 0\n'
               'for i in 1..10\n'
               '  s = "v{i}"\n'
               '  if i % 2 == 0\n'
               '    continue\n'
               '  if i > 7\n'
               '    break\n'
               '  total += s.len() + i\n'
               'print(total)\n'
               'for i in 1..10 where i % 3 == 0\n'
               '  if i == 6\n'
               '    continue\n'
               '  print(i)\n'
               'for w in ["a", "bb", "ccc", "dddd"]\n'
               '  tag = "<{w}>"\n'
               '  if w.len() == 2\n'
               '    continue\n'
               '  if w.len() == 4\n'
               '    break\n'
               '  print(tag)\n'
               'var i = 0\n'
               'var parts: [str] = []\n'
               'while true\n'
               '  i += 1\n'
               '  piece = "p{i}"\n'
               '  if i == 2\n'
               '    continue\n'
               '  parts.push(piece)\n'
               '  if parts.len() == 3\n'
               '    break\n'
               'print(parts)\n')
        self.assertEqual(self.run_src(src), '24\n3\n9\n<a>\n<ccc>\n["p1", "p3", "p4"]\n')

    def test_a_jump_leaves_only_the_innermost_loop(self):
        src = ('for i in 1..3\n'
               '  for j in 1..3\n'
               '    if j == 2\n'
               '      continue\n'
               '    if i == 2\n'
               '      break\n'
               '    print("{i}{j}")\n')
        self.assertEqual(self.run_src(src), "11\n13\n31\n33\n")

    def test_continue_at_the_largest_int_does_not_overflow(self):
        src = ('m = 9223372036854775806\n'
               'for k in m..9223372036854775807\n'
               '  if k == m\n'
               '    continue\n'
               '  print(k)\n'
               'for k in m..9223372036854775807\n'
               '  continue\n'
               'print("done")\n')
        self.assertEqual(self.run_src(src), "9223372036854775807\ndone\n")

    def test_reading_lines_until_the_end(self):
        src = ('var line = io.read_line()\n'
               'while line\n'
               '  if line == "stop"\n'
               '    break\n'
               '  print(line.upper())\n'
               '  line = io.read_line()\n')
        self.assertEqual(self.run_src(src, stdin="a\nb\nstop\nc\n"), "A\nB\n")
        self.assertEqual(self.run_src(src, stdin="x"), "X\n")

    def test_the_condition_is_not_trusted_after_the_variable_is_assigned(self):
        src = ('var line = io.read_line()\nwhile line\n  line = io.read_line()\n  print(line + "!")\n')
        e = self.check_err(src)
        self.assertEqual(e["code"], "E0103")
        self.assertIn("found str? and str", e["message"])
        self.assertEqual(self.run_src(src.replace('line + "!"', "line.len() or -1"), stdin="ab\ncde\n"), "3\n-1\n")

    def test_continue_after_a_none_test_narrows_the_rest_of_the_body(self):
        src = ('for w in ["4", "x", "5"]\n'
               '  n = w.to_int()\n'
               '  if n == none\n'
               '    continue\n'
               '  print(n + 1)\n')
        self.assertEqual(self.run_src(src), "5\n6\n")

    def test_jumps_in_functions_and_handlers(self):
        src = ('fn half(n: int) -> int ! err\n  if n % 2 != 0\n    fail("odd {n}")\n  n / 2\n\n'
               'fn first_even(xs: [int]) -> str\n'
               '  var out = "none"\n'
               '  for x in xs\n'
               '    label = "got {x}"\n'
               '    h = half(x) or e ->\n'
               '      continue\n'
               '    out = "{label} half {h}"\n'
               '    break\n'
               '  out\n\n'
               'print(first_even([3, 5, 8, 10]))\n'
               'print(first_even([1]))\n')
        self.assertEqual(self.run_src(src), "got 8 half 4\nnone\n")

    def test_errors(self):
        cases = [
            ('break\n', "E0017", "'break' is only allowed inside"),
            ('fn f()\n  continue\n\nf()\n', "E0017", "'continue' is only allowed inside"),
            ('while 1\n  print(1)\n', "E0103", "a condition needs bool"),
            ('while true\nprint(1)\n', "E0010", "an indented block"),
            ('break = 1\n', "E0010", "end of line"),
            ('fn f() -> int\n  while true\n    return 1\n\nprint(f())\n', "E0105", "not every path"),
        ]
        for src, code, text in cases:
            with self.subTest(src=src):
                e = self.check_err(src)
                self.assertEqual(e["code"], code)
                self.assertIn(text, e["message"])


if __name__ == "__main__":
    unittest.main()
