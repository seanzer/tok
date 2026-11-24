"""The learn pack: every code sample in the guide is compiled and run, so the guide cannot drift."""
import os
import re
import sys
import unittest

from helpers import ROOT, TokCase, tokc

sys.path.insert(0, os.path.join(ROOT, "compiler"))
from tok import learn  # noqa: E402


class LearnTests(TokCase):
    def test_examples_run_and_print_what_the_guide_says(self):
        for i, (title, src, out, args) in enumerate(learn.EXAMPLES):
            with self.subTest(example=title):
                path = self.write(f"learn_ex{i}.tok", src)
                self.assertEqual(self.run_path(path, "--", *args), out)
                self.assertEqual(tokc("fmt", path, "--check").returncode, 0, "the sample is not in canonical format")

    def test_mistakes_fail_with_the_stated_code_and_fixes_run(self):
        for i, (what, bad, code, good, out) in enumerate(learn.MISTAKES):
            with self.subTest(mistake=what):
                self.assertIn(code, self.codes(bad))
                self.assertEqual(self.run_src(good, name=f"learn_fix{i}.tok"), out)

    def test_every_fenced_block_in_the_guide_is_a_tested_sample(self):
        known = {s.rstrip("\n") for _, s, _, _ in learn.EXAMPLES}
        known |= {m[1].rstrip("\n") for m in learn.MISTAKES} | {m[3].rstrip("\n") for m in learn.MISTAKES}
        blocks = re.findall(r"```\n(.*?)\n```", learn.render(), re.S)
        self.assertEqual(len(blocks), len(learn.EXAMPLES) + 2 * len(learn.MISTAKES))
        for b in blocks:
            self.assertIn(b, known)

    def test_command_and_budget(self):
        full = tokc("learn")
        self.assertEqual((full.returncode, full.stdout), (0, learn.render()))
        small = tokc("learn", "--budget", "1500")
        self.assertEqual(small.returncode, 0)
        self.assertLessEqual(learn.approx_tokens(small.stdout), 1500)
        self.assertLess(len(small.stdout), len(full.stdout))
        for must in ("## Effects and errors", "## Different from both"):
            self.assertIn(must, small.stdout)
        tiny = tokc("learn", "--budget", "10")
        self.assertEqual(tiny.returncode, 0)
        self.assertIn("cannot be trimmed", tiny.stderr)
        self.assertEqual(tokc("learn", "--budget", "x").returncode, 64)


if __name__ == "__main__":
    unittest.main()
