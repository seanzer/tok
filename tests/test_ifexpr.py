"""The conditional expression: if c then a else b."""
import unittest

from helpers import TokCase


class IfExprTests(TokCase):
    def test_values_chains_and_the_tail_of_a_function(self):
        src = ('fn sign(n: int) -> str\n'
               '  if n < 0 then "neg" else if n == 0 then "zero" else "pos"\n'
               '\n'
               'y = args[0].to_int() or 2024\n'
               'print(if y % 400 == 0 or (y % 4 == 0 and y % 100 != 0) then "leap" else "common")\n'
               'print(sign(-3) + sign(0) + sign(4))\n'
               'x = if y > 5 then "big{y}" else "small"\n'
               'print(x)\n'
               'xs = if y > 1 then [1, 2] else []\n'
               'print(xs.len() + (if xs.len() > 1 then 10 else 20))\n'
               'print(2.5 * (if y > 1 then 2.0 else 3.0))\n'
               'print("{if y > 1 then "a" else "b"}!")\n')
        self.assertEqual(self.run_src(src), "leap\nnegzeropos\nbig2024\n12\n5.0\na!\n")
        self.assertEqual(self.run_src(src, "--", "1"), "common\nnegzeropos\nsmall\n20\n7.5\nb!\n")

    def test_only_the_chosen_branch_runs(self):
        src = ('fn loud(n: int) -> int ! io\n  print("called {n}")\n  n\n\n'
               'xs = [1]\n'
               'print(if xs.len() > 5 then xs[5] else -1)\n'
               'print(if true then loud(1) else loud(2))\n')
        self.assertEqual(self.run_src(src), "-1\ncalled 1\n1\n")

    def test_none_branches_and_narrowing(self):
        src = ('y = args.len()\n'
               'o = if y > 5 then none else 3\n'
               'print(o)\n'
               'q: int? = if y > 5 then 1 else none\n'
               'print(q)\n'
               'm = "12".to_int()\n'
               'print(if m then m + 1 else 0)\n'
               'print(if m == none then 0 else m * 2)\n')
        self.assertEqual(self.run_src(src), "3\nnone\n13\n24\n")

    def test_a_branch_may_fail(self):
        src = ('fn pick(n: int) -> int ! err\n  if n > 0 then n else fail("not positive: {n}")\n\n'
               'print(pick(3)?)\nprint(pick(-3) or -1)\nprint(pick(-4)?)\n')
        out, err = self.run_fail(src, 1)
        self.assertEqual((out, err), ("3\n-1\n", "error: not positive: -4\n"))

    def test_errors(self):
        cases = [
            ('x = if true then 2\n', "E0010", "needs an 'else' value"),
            ('y = if true 3 else 4\n', "E0010", "expected 'then'"),
            ('x = 1 if true else 2\n', "E0010", "write 'if c then a else b'"),
            ('z = if true then 1 else "a"\n', "E0103", "the other is int, this one is str"),
            ('w = if 1 then 1 else 2\n', "E0103", "a condition needs bool"),
            ('print(if true then print(1) else print(2))\n', "E0103", "must give a value"),
            ('x = if true then none else none\n', "E0108", "both branches are none"),
            ('n = 1\nx = if true then n else 2.5\n', "E0106", "found a float literal"),
            ('then = 1\n', "E0010", "keyword 'then'"),
        ]
        for src, code, text in cases:
            with self.subTest(src=src):
                e = self.check_err(src)
                self.assertEqual(e["code"], code)
                self.assertIn(text, e["message"])

    def test_the_statement_if_is_unchanged(self):
        src = 'n = 3\nif n > 2\n  print("big")\nelse if n > 1\n  print("mid")\nelse\n  print("small")\n'
        self.assertEqual(self.run_src(src), "big\n")


if __name__ == "__main__":
    unittest.main()
