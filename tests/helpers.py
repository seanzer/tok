"""Shared test helpers: compile real programs, run the binaries, check output and diagnostics."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKC = [sys.executable, os.path.join(ROOT, "compiler", "tokc.py")]


def tokc(*args, env=None, cwd=ROOT, stdin=None):
    """Run the compiler. `stdin` is the text the program reads; without it standard input is empty."""
    if stdin is None:
        return subprocess.run(TOKC + list(args), capture_output=True, text=True, cwd=cwd, env=env, stdin=subprocess.DEVNULL)
    return subprocess.run(TOKC + list(args), capture_output=True, text=True, cwd=cwd, env=env, input=stdin)


class TokCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="toktest")
        cls.env = dict(os.environ, XDG_CACHE_HOME=os.path.join(cls.tmp, "cache"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def write(self, name, src):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as fh:
            fh.write(src)
        return path

    def assert_clean_c(self, path):
        """The generated C must compile without warnings under -Wall -Wextra."""
        r = tokc("emit-c", path)
        self.assertEqual(r.returncode, 0, r.stderr)
        c_path = os.path.join(self.tmp, "strict.c")
        with open(c_path, "w") as fh:
            fh.write(r.stdout)
        cc = subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra", "-Werror", "-fsyntax-only", c_path],
                            capture_output=True, text=True)
        self.assertEqual(cc.returncode, 0, cc.stderr)

    def run_path(self, path, *args, code=0, stdin=None):
        self.assert_clean_c(path)
        r = tokc("run", path, *args, env=self.env, stdin=stdin)
        self.assertEqual(r.returncode, code, r.stderr)
        return r.stdout

    def run_src(self, src, *args, name="t.tok", stdin=None):
        return self.run_path(self.write(name, src), *args, stdin=stdin)

    def run_fail(self, src, code):
        """Run a program that is expected to exit with `code`; returns (stdout, stderr)."""
        r = tokc("run", self.write("f.tok", src), env=self.env)
        self.assertEqual(r.returncode, code, r.stderr)
        return r.stdout, r.stderr

    def errors(self, src, name="e.tok"):
        r = tokc("check", self.write(name, src), "--json")
        self.assertEqual(r.returncode, 1, r.stdout)
        return json.loads(r.stdout)["errors"]

    def check_err(self, src):
        return self.errors(src)[0]

    def codes(self, src):
        return [e["code"] for e in self.errors(src)]

    def fix_and_run(self, src, name="fx.tok"):
        """`tokc fix` must turn the file into one that compiles; returns its output."""
        path = self.write(name, src)
        r = tokc("fix", path)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return self.run_path(path)
