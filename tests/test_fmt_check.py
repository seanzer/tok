"""Item 7: the formatter, and check reporting every independent error."""
import glob
import json
import os
import unittest

from helpers import ROOT, TokCase, tokc


class FmtTests(TokCase):
    def fmt(self, src, name="m.tok"):
        path = self.write(name, src)
        r = tokc("fmt", path)
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(path) as fh:
            return fh.read()

    def test_messy_file_matches_the_expected_layout(self):
        with open(os.path.join(ROOT, "tests/fmt/messy.tok")) as fh:
            messy = fh.read()
        with open(os.path.join(ROOT, "tests/fmt/messy.expected.tok")) as fh:
            expected = fh.read()
        self.assertEqual(self.fmt(messy), expected)

    def test_formatting_is_idempotent(self):
        with open(os.path.join(ROOT, "tests/fmt/messy.tok")) as fh:
            once = self.fmt(fh.read())
        self.assertEqual(self.fmt(once), once)
        r = tokc("fmt", self.write("m.tok", once))
        self.assertIn("already formatted", r.stdout)

    def test_formatting_does_not_change_behavior(self):
        with open(os.path.join(ROOT, "tests/fmt/messy.tok")) as fh:
            messy = fh.read()
        before = self.run_src(messy, name="before.tok")
        after = self.run_src(self.fmt(messy), name="after.tok")
        self.assertEqual(before, after)
        self.assertEqual(before, "2\n3\n-2\n[-4, -6] 2 none -2 {}\n-1\n")

    def test_every_example_is_already_formatted(self):
        files = sorted(glob.glob(os.path.join(ROOT, "examples", "**", "*.tok"), recursive=True))
        self.assertGreaterEqual(len(files), 8)
        for path in files:
            with self.subTest(path=path):
                r = tokc("fmt", path, "--check")
                self.assertEqual(r.returncode, 0, r.stdout)

    def test_spacing_rules(self):
        cases = {
            'fn main()!io\n    x=1\n    print(x)\n': 'fn main() ! io\n  x = 1\n  print(x)\n',
            'fn f(a:int,b:[str]?)->int\n  a- -1\n': 'fn f(a: int, b: [str]?) -> int\n  a - -1\n',
            'fn f()->[int]\n  return [-1,(-2),0 - 3]\n': 'fn f() -> [int]\n  return [-1, (-2), 0 - 3]\n',
            'fn f(xs:[int])->int\n  xs[0]+xs [1]\n': 'fn f(xs: [int]) -> int\n  xs[0] + xs[1]\n',
            'fn f(s:str)->[str]\n  s.lines().where(l->l.len()>0)\n':
                'fn f(s: str) -> [str]\n  s.lines().where(l -> l.len() > 0)\n',
            'fn f()->int\n  x:int?=none\n  x or-1\n': 'fn f() -> int\n  x: int? = none\n  x or -1\n',
            'fn f()\n  s="a  //  not a comment {1+1}"//c\n': 'fn f()\n  s = "a  //  not a comment {1+1}"  // c\n',
            '\n\nfn a()\n  x=1\n\n\n\nfn b()\n\n  y=2\n//tail\n': 'fn a()\n  x = 1\n\nfn b()\n  y = 2\n// tail\n',
            'fn a()\n  x=1\n  //last in block\nfn b()\n  y=2\n': 'fn a()\n  x = 1\n  // last in block\n\nfn b()\n  y = 2\n',
            '//doc\n//more\nfn a()\n  x=1\n//about b\nfn b()\n  y=2\n':
                '// doc\n// more\nfn a()\n  x = 1\n\n// about b\nfn b()\n  y = 2\n',
            '': '',
        }
        for src, want in cases.items():
            with self.subTest(src=src):
                got = self.fmt(src)
                self.assertEqual(got, want)
                self.assertEqual(self.fmt(got), got)

    def test_check_flag_reports_without_writing(self):
        src = 'fn main()!io\n  print(1)\n'
        path = self.write("c.tok", src)
        r = tokc("fmt", path, "--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not formatted", r.stdout)
        with open(path) as fh:
            self.assertEqual(fh.read(), src)

    def test_file_that_cannot_be_tokenized_is_left_alone(self):
        src = 'fn main() ! io\n\tprint("x")\n'
        path = self.write("bad.tok", src)
        r = tokc("fmt", path)
        self.assertEqual(r.returncode, 1)
        self.assertIn("E0002", r.stderr)
        with open(path) as fh:
            self.assertEqual(fh.read(), src)


class MultiErrorTests(TokCase):
    SRC = ('type P = { x: int, y: int }\n\n'
           'fn broken(a: int -> int\n'                    # 3: E0010 (parse error, recovers at the next fn)
           '  a\n\n'
           'fn f(n: int) -> int\n'
           '  print(n)\n'                                 # 7: E0201
           '  m = n + "s"\n'                              # 8: E0103
           '  undefined_thing\n\n'                        # 9: E0101
           'fn main() ! io\n'
           '  x = 5\n'
           '  x = 6\n'                                    # 13: E0301
           '  print(broken(1))\n'                         # no error: broken failed to parse, calls are not re-reported
           '  print(f("a", 2))\n'                         # 15: E0104
           '  print(y)\n')                                # 16: E0101

    def test_check_reports_every_independent_error(self):
        errs = self.errors(self.SRC)
        self.assertEqual([(e["line"], e["code"]) for e in errs],
                         [(3, "E0010"), (7, "E0201"), (8, "E0103"), (9, "E0101"),
                          (13, "E0301"), (15, "E0104"), (16, "E0101")])

    def test_json_shape(self):
        r = tokc("check", self.write("e.tok", self.SRC), "--json")
        data = json.loads(r.stdout)
        self.assertEqual(sorted(data), ["errors", "ok"])
        self.assertFalse(data["ok"])
        for e in data["errors"]:
            self.assertTrue({"code", "message", "line", "col", "end_col", "file"} <= set(e), e)
        fixes = [e for e in data["errors"] if e.get("fix")]
        self.assertEqual([e["code"] for e in fixes], ["E0201", "E0301"])
        ok = tokc("check", os.path.join(ROOT, "examples/hello.tok"), "--json")
        self.assertEqual(json.loads(ok.stdout), {"ok": True, "errors": []})

    def test_text_output_lists_all_and_counts(self):
        r = tokc("check", self.write("e.tok", self.SRC))
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stderr.count("error[E"), 7)
        self.assertTrue(r.stderr.rstrip().endswith("7 errors"))

    def test_errors_in_types_signatures_and_bodies_together(self):
        src = ('type P = { x: int, y: Nope }\n\n'
               'fn f(a: Missing) -> int\n  1 + true\n\n'
               'fn main()\n  p = P { x: 1 }\n  q = zzz\n')
        self.assertEqual([(e["line"], e["code"]) for e in self.errors(src)],
                         [(1, "E0110"), (3, "E0110"), (4, "E0103"), (8, "E0101")])

    def test_an_error_does_not_cascade(self):
        # one bad binding: later uses of the name are not reported again
        src = 'fn main() ! io\n  x = nope(1)\n  print(x)\n  y = x + 1\n  print(y)\n'
        self.assertEqual(self.codes(src), ["E0101"])

    def test_fix_applies_every_fixable_error(self):
        src = ('fn half(n: int) -> int ! err\n  n / 2\n\n'
               'fn show(n: int)\n  print(half(n))\n\n'
               'fn main()\n  x = 1\n  x = 2\n  show(x)\n')
        self.assertEqual(self.codes(src), ["E0203", "E0301"])
        self.assertEqual(self.fix_and_run(src), "1\n")


if __name__ == "__main__":
    unittest.main()
