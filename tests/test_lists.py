"""Item 2: lists."""
import unittest

from helpers import TokCase


class ListTests(TokCase):
    def test_literal_index_len_and_print(self):
        src = ('fn main() ! io\n'
               '  xs = [3, 1, 2]\n'
               '  names = ["a", "b"]\n'
               '  print("{xs[0]} {xs[2]} {xs.len()} {xs} {names}")\n'
               '  print([[1, 2], [3]])\n'
               '  print(xs == [3, 1, 2])\n')
        self.assertEqual(self.run_src(src), '3 2 3 [3, 1, 2] ["a", "b"]\n[[1, 2], [3]]\ntrue\n')

    def test_for_in_and_where(self):
        src = ('fn total(xs: [int]) -> int\n'
               '  var sum = 0\n'
               '  for x in xs\n'
               '    sum += x\n'
               '  sum\n\n'
               'fn main() ! io\n'
               '  xs = [5, 12, 7, 20]\n'
               '  print(total(xs))\n'
               '  for x in xs where x > 6\n'
               '    print(x)\n'
               '  for i in 1..10 where i % 4 == 0\n'
               '    print(i)\n')
        self.assertEqual(self.run_src(src), "44\n12\n7\n20\n4\n8\n")

    def test_push_and_element_assign_on_var(self):
        src = ('fn main() ! io\n'
               '  var xs: [int] = []\n'
               '  for i in 1..5\n'
               '    xs.push(i * i)\n'
               '  xs[0] = 100\n'
               '  xs[4] += 1\n'
               '  print(xs)\n')
        self.assertEqual(self.run_src(src), "[100, 4, 9, 16, 26]\n")

    def test_value_semantics_copy_then_mutate(self):
        src = ('type Bag = { items: [int], name: str }\n\n'
               'fn grow(xs: [int]) -> [int]\n'
               '  var out = xs\n'
               '  out.push(0)\n'
               '  out\n\n'
               'fn same(xs: [int]) -> [int]\n'
               '  xs\n\n'
               'fn main() ! io\n'
               '  a = [1, 2, 3]\n'
               '  var b = a\n'
               '  b.push(4)\n'
               '  b[0] = 9\n'
               '  c = b\n'
               '  b[1] = 8\n'
               '  print("{a} {b} {c}")\n'
               '  var bag = Bag { items: a, name: "x" }\n'
               '  other = bag\n'
               '  bag.items.push(7)\n'
               '  bag.items[0] = 0\n'
               '  print("{a} {bag.items} {other.items}")\n'
               '  g = grow(a)\n'
               '  print("{a} {g}")\n'
               '  var s = same(a)\n'
               '  s[0] = 5\n'
               '  frozen = same(b)\n'
               '  b[2] = 77\n'
               '  print("{a} {s} {frozen}")\n'
               '  var grid = [a, a]\n'
               '  grid[0][0] = 42\n'
               '  row = grid[1]\n'
               '  grid[1].push(6)\n'
               '  print("{a} {grid} {row}")\n')
        self.assertEqual(self.run_src(src),
                         "[1, 2, 3] [9, 8, 3, 4] [9, 2, 3, 4]\n"
                         "[1, 2, 3] [0, 2, 3, 7] [1, 2, 3]\n"
                         "[1, 2, 3] [1, 2, 3, 0]\n"
                         "[1, 2, 3] [5, 2, 3] [9, 8, 3, 4]\n"
                         "[1, 2, 3] [[42, 2, 3], [1, 2, 3, 6]] [1, 2, 3]\n")

    def test_loop_sees_the_list_as_it_was_at_the_start(self):
        src = ('fn main() ! io\n'
               '  var xs = [1, 2, 3]\n'
               '  for x in xs\n'
               '    xs.push(x * 10)\n'
               '    xs[2] = 0\n'
               '  print(xs)\n')
        self.assertEqual(self.run_src(src), "[1, 2, 0, 10, 20, 30]\n")

    def test_out_of_bounds_panics(self):
        for body in ('print(xs[3])', 'print(xs[0 - 1])', 'ys[5] = 1'):
            with self.subTest(body=body):
                _, err = self.run_fail(f'fn main() ! io\n  xs = [1, 2, 3]\n  var ys = xs\n  {body}\n', 70)
                self.assertIn("out of bounds", err)
                self.assertIn("f.tok:4", err)

    def test_errors(self):
        cases = {
            'fn main()\n  xs = []\n': "E0108",
            'fn main()\n  xs = [1, "a"]\n': "E0103",
            'fn main()\n  x = 5\n  y = x[0]\n': "E0103",
            'fn main()\n  xs = [1]\n  y = xs["a"]\n': "E0103",
            'fn main()\n  for c in "abc"\n    x = c\n': "E0103",
            'fn main()\n  xs = [1]\n  y = xs.nope()\n': "E0109",
            'fn main()\n  xs = [1]\n  y = xs.len(1)\n': "E0104",
            'fn main()\n  var xs = [1]\n  xs.push("a")\n': "E0103",
            'fn main()\n  xs = [1]\n  for xs in xs\n    y = 1\n': "E0102",
        }
        for src, code in cases.items():
            with self.subTest(src=src):
                self.assertEqual(self.codes(src), [code])

    def test_push_needs_var_and_fix_applies(self):
        src = 'fn main() ! io\n  xs = [1]\n  xs.push(2)\n  print(xs)\n'
        e = self.check_err(src)
        self.assertEqual((e["code"], e["fix"]["insert"], e["fix"]["line"], e["fix"]["col"]), ("E0301", "var ", 2, 3))
        self.assertEqual(self.fix_and_run(src), "[1, 2]\n")


if __name__ == "__main__":
    unittest.main()
