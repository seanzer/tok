"""The script form: a program written as top-level statements, with no `fn main`."""
import os
import unittest

from helpers import TokCase, tokc


class ScriptTests(TokCase):
    def test_statements_run_in_order_with_functions_types_and_errors_around_them(self):
        src = ('error Odd\n'
               'total = double(20) + 2\n'
               '\n'
               'fn double(n: int) -> int\n'
               '  n * 2\n'
               '\n'
               'type P = { x: int, name: str }\n'
               'p = P { x: total, name: "p{total}" }\n'
               'print(p)\n'
               'var xs: [int] = []\n'
               'for i in 1..3 where i != 2\n'
               '  xs.push(half(i * 2)?)\n'
               '\n'
               'fn half(n: int) -> int ! err\n'
               '  if n % 2 != 0\n'
               '    fail(Odd, "{n} is odd")\n'
               '  n / 2\n'
               '\n'
               'print(xs)\n')
        self.assertEqual(self.run_src(src), '{x: 42, name: "p42"}\n[1, 3]\n')

    def test_args_are_there_without_being_declared(self):
        src = 'print(args)\nprint(args[0].to_int() or -1)\nprint(args.len())\n'
        self.assertEqual(self.run_src(src, "--", "7", "b"), '["7", "b"]\n7\n2\n')
        self.assertEqual(self.run_src(src), '[]\n-1\n0\n')

    def test_effects_are_inferred_for_the_script_but_not_for_its_functions(self):
        src = 'text = fs.read_text("nope.txt") or "none"\nprint(text)\nprint(io.read_all().len())\n'
        self.assertEqual(self.run_src(src, stdin="abc"), "none\n3\n")
        e = self.check_err('fn show(n: int)\n  print(n)\n\nshow(1)\n')
        self.assertEqual((e["code"], e["line"], e["fix"]["insert"]), ("E0201", 2, " ! io"))
        self.assertEqual(self.fix_and_run('fn show(n: int)\n  print(n)\n\nshow(1)\n'), "1\n")

    def test_a_failure_at_the_top_level_prints_the_message_and_exits_with_1(self):
        src = ('fn half(n: int) -> int ! err\n  if n % 2 != 0\n    fail("{n} is odd")\n  n / 2\n\n'
               'print(half(8)?)\nprint(half(7)?)\nprint("not reached")\n')
        out, err = self.run_fail(src, 1)
        self.assertEqual((out, err), ("4\n", "error: 7 is odd\n"))
        out, err = self.run_fail('xs = ["a{args.len()}"]\nif xs.len() == 1\n  fail("stop")\nprint(xs)\n', 1)
        self.assertEqual((out, err), ("", "error: stop\n"))

    def test_return_ends_the_script(self):
        self.assertEqual(self.run_src('print(1)\nif args.len() == 0\n  return\nprint(2)\n'), "1\n")

    def test_fn_main_still_works(self):
        self.assertEqual(self.run_src('fn main(args: [str]) ! io\n  print(args.len())\n', "--", "a"), "1\n")
        self.assertEqual(self.run_src('fn main() ! io\n  print("m")\n'), "m\n")

    def test_mixing_statements_and_fn_main_is_an_error(self):
        e = self.check_err('fn main() ! io\n  print(1)\n\nprint(2)\n')
        self.assertEqual((e["code"], e["line"], e["col"]), ("E0403", 4, 1))
        self.assertIn("both top-level statements and 'fn main'", e["message"])
        self.assertEqual(self.codes('print(2)\n\nfn main() ! io\n  print(1)\n'), ["E0403"])

    def test_a_file_with_nothing_to_run(self):
        e = self.check_err('fn helper()\n  x = 1\n')
        self.assertEqual(e["code"], "E0401")
        self.assertIn("nothing to run", e["message"])

    def test_a_top_level_variable_is_not_visible_in_a_function(self):
        e = self.check_err('limit = 3\n\nfn over(n: int) -> bool\n  n > limit\n\nprint(over(4))\n')
        self.assertEqual((e["code"], e["line"]), ("E0101", 4))
        self.assertIn("not visible inside a function", e["message"])

    def test_a_module_cannot_have_statements(self):
        with open(os.path.join(self.tmp, "lib.tok"), "w") as fh:
            fh.write('fn one() -> int\n  1\n\nprint(1)\n')
        errs = self.errors('print(lib.one())\n', name="usesmod.tok")
        self.assertEqual([(e["code"], os.path.basename(e["file"]), e["line"]) for e in errs], [("E0404", "lib.tok", 4)])

    def test_every_independent_error_is_reported(self):
        src = 'x = 1 +\nprint(x)\ny: int = "s"\nimport sys\nprint(nope)\n'
        errs = self.errors(src)
        self.assertEqual([(e["line"], e["code"]) for e in errs], [(1, "E0010"), (3, "E0103"), (4, "E0010"), (5, "E0101")])
        self.assertIn("Tok has no 'import'", errs[2]["message"])

    def test_formatter_puts_a_blank_line_around_functions(self):
        src = 'x=1\nfn f()->int\n  2\nprint( x+f() )\n'
        path = self.write("fm.tok", src)
        self.assertEqual(tokc("fmt", path).returncode, 0)
        with open(path) as fh:
            self.assertEqual(fh.read(), 'x = 1\n\nfn f() -> int\n  2\n\nprint(x + f())\n')
        self.assertEqual(self.run_path(path), "3\n")


if __name__ == "__main__":
    unittest.main()
