"""Typed errors: error kinds, fail(Kind, msg), and handlers that bind the error (call() or e -> ...)."""
import os
import unittest

from helpers import TokCase, tokc

LOAD = ('error NotFound, Timeout\n\n'
        'fn load(id: int) -> str ! err\n'
        '  if id < 0\n'
        '    fail(NotFound, "no user {id}")\n'
        '  if id > 100\n'
        '    fail(Timeout)\n'
        '  if id == 50\n'
        '    fail("plain")\n'
        '  "user{id}"\n\n')


class ErrorKindTests(TokCase):
    def test_kinds_messages_and_one_line_handler(self):
        src = LOAD + ('fn main() ! io\n'
                      '  print(load(7) or e -> "never")\n'
                      '  print(load(-1) or e -> "{e.kind}: {e.msg}")\n'
                      '  print(load(500) or e -> "{e.kind == Timeout} {e.msg}")\n'
                      '  print(load(50) or e -> "{e.kind} {e.kind == Failed} {e}")\n'
                      '  print(load(-2) or "default still works")\n')
        self.assertEqual(self.run_src(src),
                         "user7\nNotFound: no user -1\ntrue Timeout\nFailed true Failed: plain\ndefault still works\n")

    def test_handler_block_branches_on_kind(self):
        src = LOAD + ('fn name(id: int) -> str ! err\n'
                      '  n = load(id) or e ->\n'
                      '    if e.kind == NotFound\n'
                      '      "anonymous"\n'
                      '    else if e.kind == Timeout\n'
                      '      return "slow"\n'
                      '    else\n'
                      '      fail(e)\n'
                      '  "<{n}>"\n\n'
                      'fn main() ! io\n'
                      '  print(name(1)?)\n'
                      '  print(name(-1)?)\n'
                      '  print(name(500)?)\n'
                      '  print(name(50) or e -> "passed on: {e.kind} {e.msg}")\n')
        self.assertEqual(self.run_src(src), "<user1>\n<anonymous>\nslow\npassed on: Failed plain\n")

    def test_handler_for_a_call_that_returns_nothing(self):
        src = ('error Empty\n\n'
               'fn log(msg: str) ! io, err\n'
               '  if msg == ""\n'
               '    fail(Empty, "nothing to log")\n'
               '  print(msg)\n\n'
               'fn main() ! io\n'
               '  log("a") or e -> print("not printed")\n'
               '  log("") or e -> print("one line: {e.msg}")\n'
               '  log("") or e ->\n'
               '    print("block: {e.kind}")\n'
               '    print("second statement")\n'
               '  print("done")\n')
        self.assertEqual(self.run_src(src), "a\none line: nothing to log\nblock: Empty\nsecond statement\ndone\n")

    def test_question_mark_keeps_the_kind(self):
        src = LOAD + ('fn outer(id: int) -> str ! err\n'
                      '  load(id)?\n\n'
                      'fn main() ! io\n'
                      '  print(outer(-5) or e -> "{e.kind}")\n'
                      '  print(outer(-5)?)\n')
        r = tokc("run", self.write("q.tok", src), env=self.env)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (1, "NotFound\n", "error: no user -5\n"))

    def test_fs_errors_have_a_kind(self):
        src = ('fn main() ! io, fs\n'
               '  text = fs.read_text("/no/such/file") or e ->\n'
               '    if e.kind == FsError\n'
               '      "missing"\n'
               '    else\n'
               '      "other"\n'
               '  print(text)\n')
        self.assertEqual(self.run_src(src), "missing\n")

    def test_error_values_are_values(self):
        src = LOAD + ('fn describe(e: Error) -> str\n'
                      '  "[{e.kind}] {e.msg}"\n\n'
                      'fn main() ! io\n'
                      '  var seen: [Error] = []\n'
                      '  for id in [-1, 500, 3]\n'
                      '    v = load(id) or e ->\n'
                      '      seen.push(e)\n'
                      '      "-"\n'
                      '    print(v)\n'
                      '  print(seen.map(e -> describe(e)).join(", "))\n'
                      '  k: ErrorKind = NotFound\n'
                      '  print("{k} {k == seen[0].kind} {seen[0] == seen[1]}")\n')
        self.assertEqual(self.run_src(src), "-\n-\nuser3\n[NotFound] no user -1, [Timeout] Timeout\nNotFound true false\n")

    def test_kind_from_another_module(self):
        d = os.path.join(self.tmp, "kindmod")
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "store.tok"), "w") as fh:
            fh.write('error Missing\n\nfn get(k: str) -> str ! err\n  if k == ""\n    fail(Missing, "empty key")\n  k\n')
        main = os.path.join(d, "main.tok")
        with open(main, "w") as fh:
            fh.write('error Missing\n\n'
                     'fn main() ! io\n'
                     '  v = store.get("") or e ->\n'
                     '    "{e.kind == store.Missing} {e.kind == Missing}"\n'
                     '  print(v)\n')
        self.assertEqual(self.run_path(main), "true false\n")

    def test_formatter_keeps_handlers(self):
        src = LOAD + ('fn main() ! io\n'
                      '  n = load(-1) or e ->\n'
                      '    if e.kind == NotFound\n'
                      '      "a"\n'
                      '    else\n'
                      '      "b"\n'
                      '  print(n)\n')
        path = self.write("fmt_h.tok", src)
        self.assertEqual(tokc("fmt", path, "--check").returncode, 0)
        messy = self.write("fmt_m.tok", src.replace("error NotFound, Timeout", "error   NotFound ,Timeout").replace("or e ->", "or  e->"))
        self.assertEqual(tokc("fmt", messy).returncode, 0)
        with open(messy) as fh:
            self.assertEqual(fh.read(), src)

    def test_diagnostics(self):
        cases = {
            'error A, A\n\nfn main()\n  x = 1\n': ["E0102"],
            'error A\n\nfn A()\n  x = 1\n\nfn main()\n  x = 1\n': ["E0102"],
            'error Failed\n\nfn main()\n  x = 1\n': ["E0102"],
            'error A\n\nfn main()\n  A = 1\n': ["E0102"],
            'fn f() ! err\n  fail(1, 2, 3)\n\nfn main()\n  f() or e -> 0\n': ["E0104"],
            'fn f() ! err\n  fail(3)\n\nfn main()\n  x = 1\n': ["E0103"],
            'fn f() ! err\n  fail("a", "b")\n\nfn main()\n  x = 1\n': ["E0103"],
            'fn f() ! err\n  fail(Nope, "b")\n\nfn main()\n  x = 1\n': ["E0101"],
            'error A\n\nfn f()\n  fail(A, "b")\n\nfn main()\n  f()\n': ["E0202"],
            LOAD + 'fn main() ! io\n  n = load(1) or e ->\n    if e.kind == NotFound\n      "a"\n  print(n)\n': ["E0105"],
            LOAD + 'fn main() ! io\n  n = load(1) or e -> 5\n  print(n)\n': ["E0103"],
            LOAD + 'fn main() ! io\n  n = load(1) or e ->\n    5\n  print(n)\n': ["E0103"],
            LOAD + 'fn main() ! io\n  n = load(1) or e -> e.code\n  print(n)\n': ["E0109"],
            'fn main() ! io\n  m: int? = none\n  print(m or e -> 1)\n': ["E0113"],
            'fn main() ! io\n  ys = [1].map(x ->\n    x + 1)\n': ["E0010"],
            LOAD + 'fn main() ! io\n  e = 1\n  print(load(1) or e -> "x")\n': ["E0102"],
            LOAD + 'fn main() ! io\n  print(load(1))\n': ["E0203"],
        }
        for src, codes in cases.items():
            with self.subTest(src=src[-60:]):
                self.assertEqual(self.codes(src), codes)

    def test_example(self):
        out = self.run_path(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples", "errors.tok"))
        self.assertEqual(out, "user7\nanonymous\ngave up: Timeout, user 500 took too long\n")

    def test_missing_err_fix_still_applies(self):
        src = 'error A\n\nfn f()\n  fail(A, "b")\n\nfn main() ! io\n  f() or e -> print(e.kind)\n'
        self.assertEqual(self.fix_and_run(src), "A\n")


if __name__ == "__main__":
    unittest.main()
