"""Item 4: lambdas and the built-in list and str methods."""
import unittest

from helpers import TokCase


class ListMethodTests(TokCase):
    def test_map_where_first_and_friends(self):
        src = ('fn main() ! io\n'
               '  xs = [1, 2, 3, 4, 5, 6]\n'
               '  sq = xs.map(x -> x * x)\n'
               '  ev = xs.where(x -> x % 2 == 0)\n'
               '  print("{sq} {ev} {xs.first(2)} {xs.first(99)} {xs.first(0 - 1)}")\n'
               '  print("{xs.contains(3)} {xs.contains(9)} {xs.sum()} {[1.5, 2.5].sum()}")\n'
               '  print("{xs.any(x -> x > 5)} {xs.all(x -> x > 5)} {xs.map(x -> "n{x}").join("-")}")\n'
               '  m = 3\n'
               '  print(xs.map((x) -> x / m + [1, 2].len()))\n'
               '  print(xs.where(x -> x > 2).map(x -> x * 10).first(2))\n'
               '  print([["a"], ["b", "c"]].map(l -> l.len()))\n')
        self.assertEqual(self.run_src(src),
                         "[1, 4, 9, 16, 25, 36] [2, 4, 6] [1, 2] [1, 2, 3, 4, 5, 6] []\n"
                         "true false 21 4.0\n"
                         "true false n1-n2-n3-n4-n5-n6\n"
                         "[2, 2, 3, 3, 3, 4]\n"
                         "[30, 40]\n"
                         "[1, 2]\n")

    def test_results_do_not_share_storage_with_a_var(self):
        src = ('type P = { name: str, tags: [str] }\n\n'
               'fn main() ! io\n'
               '  var ps = [P { name: "a", tags: ["x"] }, P { name: "b", tags: [] }]\n'
               '  copy = ps.map(p -> p)\n'
               '  tagged = ps.where(p -> p.tags.len() > 0)\n'
               '  head = ps.first(1)\n'
               '  tags = ps.map(p -> p.tags)\n'
               '  ps[0].tags[0] = "CHANGED"\n'
               '  ps[0].tags.push("more")\n'
               '  print(ps)\n'
               '  print("{copy} {tagged} {head} {tags}")\n')
        self.assertEqual(self.run_src(src),
                         '[{name: "a", tags: ["CHANGED", "more"]}, {name: "b", tags: []}]\n'
                         '[{name: "a", tags: ["x"]}, {name: "b", tags: []}] [{name: "a", tags: ["x"]}] '
                         '[{name: "a", tags: ["x"]}] [["x"], []]\n')

    def test_var_built_from_method_result_is_independent(self):
        src = ('fn main() ! io\n'
               '  a = [[1], [2]]\n'
               '  var b = a.where(x -> true)\n'
               '  var c = a.first(2)\n'
               '  b[0].push(9)\n'
               '  c[1].push(8)\n'
               '  print("{a} {b} {c}")\n')
        self.assertEqual(self.run_src(src), "[[1], [2]] [[1, 9], [2]] [[1], [2, 8]]\n")

    def test_lambda_can_call_fallible_and_effectful_code(self):
        src = ('fn half(n: int) -> int ! err\n'
               '  if n % 2 != 0\n'
               '    fail("{n} is odd")\n'
               '  n / 2\n\n'
               'fn main() ! io\n'
               '  print([2, 4].map(x -> half(x)?))\n'
               '  print([2, 3].map(x -> half(x) or 0))\n'
               '  print([2, 3].map(x -> half(x)?))\n')
        out, err = self.run_fail(src, 1)
        self.assertEqual((out, err), ("[1, 2]\n[1, 0]\n", "error: 3 is odd\n"))

    def test_errors(self):
        cases = {
            'fn main()\n  f = x -> x + 1\n': ["E0113"],
            'fn main()\n  xs = [1]\n  ys = xs.map(3)\n': ["E0113"],
            'fn main()\n  xs = [1]\n  ys = xs.where(x -> x + 1)\n': ["E0103"],
            'fn main()\n  x = 1\n  ys = [1].map(x -> x)\n': ["E0102"],
            'fn main()\n  ys = [1].frob()\n': ["E0109"],
            'fn main()\n  ys = [1].join(",")\n': ["E0109"],
            'fn main()\n  ys = ["a"].sum()\n': ["E0109"],
            'fn main()\n  ys = [1].first("a")\n': ["E0103"],
            'fn main()\n  ys = [1].contains("a")\n': ["E0103"],
            'fn main()\n  ys = [1].map(x -> x, 2)\n': ["E0104"],
            'fn main()\n  ys = [1].map(x -> y)\n': ["E0101"],
        }
        for src, codes in cases.items():
            with self.subTest(src=src):
                self.assertEqual(self.codes(src), codes)


class StrMethodTests(TokCase):
    def test_str_methods(self):
        src = ('fn main() ! io\n'
               '  text = "  one ERROR\\ntwo\\r\\nthree ERROR\\n"\n'
               '  ls = text.lines()\n'
               '  print("{ls.len()} {ls.where(l -> l.contains("ERROR")).len()} {ls}")\n'
               '  print("[{" a b ".trim().upper()}] {"MiXed".lower()} {"a,b,,c".split(",")} {"".lines()} {"x".lines()}")\n'
               '  print("{"12".to_int()} {"-7".to_int() or 0} {"x".to_int()} {"1.5".to_f64() or 0.0} {"1e".to_f64()}")\n'
               '  print("{"hello".find("l")} {"hello".find("z")} {"hello".replace("l", "L")} {"héllo".len()}")\n'
               '  print("{"abc".starts_with("ab")} {"abc".ends_with("bc")} {"abc".starts_with("bc")} {"abc".contains("")}")\n')
        self.assertEqual(self.run_src(src),
                         '3 2 ["  one ERROR", "two", "three ERROR"]\n'
                         '[A B] mixed ["a", "b", "", "c"] [] ["x"]\n'
                         '12 -7 none 1.5 none\n'
                         '2 none heLLo 6\n'
                         'true true false true\n')

    def test_empty_separator_panics(self):
        out, err = self.run_fail('fn main() ! io\n  print("abc".split(""))\n', 70)
        self.assertIn("split needs a non-empty separator (f.tok:2)", err)

    def test_errors(self):
        cases = {
            'fn main()\n  x = "a".nope()\n': ["E0109"],
            'fn main()\n  x = "a".contains(1)\n': ["E0103"],
            'fn main()\n  x = "a".trim(1)\n': ["E0104"],
            'fn main()\n  x = 5\n  y = x.len()\n': ["E0109"],
        }
        for src, codes in cases.items():
            with self.subTest(src=src):
                self.assertEqual(self.codes(src), codes)


if __name__ == "__main__":
    unittest.main()
