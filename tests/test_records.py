"""Item 1: records. Also the binding annotations, f64 and compound assignment they rely on."""
import unittest

from helpers import TokCase

POINT = "type Point = { x: f64, y: f64 }\n\n"


class RecordTests(TokCase):
    def test_literal_field_access_and_print(self):
        src = POINT + ('fn main() ! io\n'
                       '  p = Point { x: 1.5, y: 2 }\n'
                       '  print("{p.x} {p.y}")\n'
                       '  print(p)\n')
        self.assertEqual(self.run_src(src), "1.5 2.0\n{x: 1.5, y: 2.0}\n")

    def test_records_as_parameters_and_results(self):
        src = POINT + ('fn mid(a: Point, b: Point) -> Point\n'
                       '  Point { x: (a.x + b.x) / 2.0, y: (a.y + b.y) / 2.0 }\n\n'
                       'fn main() ! io\n'
                       '  m = mid(Point { x: 0.0, y: 0.0 }, Point { x: 3.0, y: 5.0 })\n'
                       '  print("{m.x},{m.y}")\n')
        self.assertEqual(self.run_src(src), "1.5,2.5\n")

    def test_value_semantics_copy_then_mutate(self):
        src = POINT + ('type Seg = { a: Point, b: Point, name: str }\n\n'
                       'fn main() ! io\n'
                       '  p = Point { x: 1.0, y: 1.0 }\n'
                       '  var q = p\n'
                       '  q.x = 9.0\n'
                       '  q.y += 1.5\n'
                       '  var s = Seg { a: p, b: q, name: "s" }\n'
                       '  t = s\n'
                       '  s.a.x = 7.0\n'
                       '  s.name = "changed"\n'
                       '  print("{p.x} {q.x} {q.y} {s.a.x} {t.a.x} {t.name}")\n')
        self.assertEqual(self.run_src(src), "1.0 9.0 2.5 7.0 1.0 s\n")

    def test_equality_is_structural(self):
        src = POINT + ('fn main() ! io\n'
                       '  a = Point { x: 1.0, y: 2.0 }\n'
                       '  print(a == Point { x: 1.0, y: 2.0 })\n'
                       '  print(a != Point { x: 1.0, y: 2.5 })\n')
        self.assertEqual(self.run_src(src), "true\ntrue\n")

    def test_annotation_and_declaration_order(self):
        src = ('fn main() ! io\n'
               '  o: Outer = Outer { inner: Inner { n: 3 }, tag: "t" }\n'
               '  print(o.inner.n)\n\n'
               'type Outer = { inner: Inner, tag: str }\n'
               'type Inner = { n: int }\n')
        self.assertEqual(self.run_src(src), "3\n")

    def test_errors(self):
        cases = {
            POINT + 'fn main() ! io\n  p = Point { x: 1.0 }\n': "E0111",              # missing field
            POINT + 'fn main() ! io\n  p = Point { x: 1.0, y: 2.0, z: 3.0 }\n': "E0111",  # unknown field
            POINT + 'fn main() ! io\n  p = Point { x: 1.0, x: 2.0, y: 0.0 }\n': "E0111",  # duplicate
            POINT + 'fn main() ! io\n  p = Point { x: 1.0, y: 2.0 }\n  print(p.z)\n': "E0109",
            POINT + 'fn main() ! io\n  p = Point { x: "a", y: 2.0 }\n': "E0103",
            'fn main() ! io\n  p = Nope { x: 1 }\n': "E0110",
            'fn f(p: Missing)\n  x = 1\n\nfn main()\n  x = 1\n': "E0110",
            'type Node = { next: Node }\n\nfn main()\n  x = 1\n': "E0112",
            'type A = { x: int }\ntype A = { y: int }\n\nfn main()\n  x = 1\n': "E0102",
            'fn main()\n  x: int = "s"\n': "E0103",
            'fn main()\n  1 = 2\n': "E0016",
        }
        for src, code in cases.items():
            with self.subTest(src=src):
                self.assertEqual(self.codes(src), [code])

    def test_field_assign_needs_var_and_fix_applies(self):
        src = POINT + ('fn main() ! io\n'
                       '  p = Point { x: 1.0, y: 2.0 }\n'
                       '  p.x = 5.0\n'
                       '  print(p.x)\n')
        e = self.check_err(src)
        self.assertEqual((e["code"], e["fix"]["insert"], e["fix"]["line"]), ("E0301", "var ", 4))
        self.assertEqual(self.fix_and_run(src), "5.0\n")

    def test_parameters_are_read_only(self):
        e = self.check_err(POINT + 'fn f(p: Point)\n  p.x = 1.0\n\nfn main()\n  f(Point { x: 0.0, y: 0.0 })\n')
        self.assertEqual(e["code"], "E0301")
        self.assertNotIn("fix", e)

    def test_float_formatting(self):
        src = ('fn main() ! io\n'
               '  print(0.1 + 0.2)\n  print(10.0)\n  print(1.0 / 3.0)\n  print(1e21)\n  print(-2.5e-7)\n  print(1.0 / 0.0)\n')
        self.assertEqual(self.run_src(src), "0.30000000000000004\n10.0\n0.3333333333333333\n1e+21\n-2.5e-07\ninf\n")


if __name__ == "__main__":
    unittest.main()
