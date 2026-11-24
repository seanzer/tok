"""Lexer and binding diagnostics that the feature tests do not reach."""
import unittest

from helpers import TokCase


class SyntaxErrorTests(TokCase):
    def test_codes(self):
        cases = {
            'fn main() ! io\n  x = 1 $ 2\n': ["E0001"],
            'fn main() ! io\n  print("a\\qb")\n': ["E0004"],
            'fn main() ! io\n  print("{1 + 2")\n': ["E0005"],
            'fn main() ! io\n  x = 12abc\n': ["E0006"],
            'fn main() ! io\n  for = 1\n': ["E0010"],
            'fn if() ! io\n  x = 1\n': ["E0012"],
            'fn main() ! io\n  x = 1\n  print(x.for)\n': ["E0012"],
            'fn main() ! io\n  print("a{}b")\n': ["E0014"],
            'fn main() ! io\n  x = 1\n  var x = 2\n': ["E0302"],
            'fn main() ! io\n  var x = 1\n  x: int = 2\n': ["E0302"],
        }
        for src, codes in cases.items():
            with self.subTest(src=src):
                self.assertEqual(self.codes(src), codes)

    def test_redundant_var_has_a_fix(self):
        src = 'fn main() ! io\n  var x = 1\n  var x = 2\n  print(x)\n'
        e = self.check_err(src)
        self.assertEqual(e["code"], "E0302")
        self.assertEqual(self.fix_and_run(src), "2\n")

    def test_escapes(self):
        src = 'fn main() ! io\n  print("tab\\there \\"quoted\\" \\\\ \\{braces\\} end")\n'
        self.assertEqual(self.run_src(src), 'tab\there "quoted" \\ {braces} end\n')


if __name__ == "__main__":
    unittest.main()
