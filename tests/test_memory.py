"""Memory: every program frees what it allocates and never touches freed memory.

All the other tests already run their programs with TOK_LEAK_CHECK (the program counts its own allocations and exits
with 71 if any are left). The tests here run a corpus under AddressSanitizer, LeakSanitizer and
UndefinedBehaviorSanitizer (`tokc build --sanitize`): the examples, every benchmark reference solution on every one of
its test cases, the performance kernels at small sizes, and the programs in tests/mem, which go after sharing,
copy-on-write and early exits. To put the whole test suite under the sanitizers: TOK_SANITIZE=1 python3 tests/run_tests.py
"""
import glob
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor

from helpers import ROOT, TIMEOUT, TokCase, tokc

sys.path.insert(0, os.path.join(ROOT, "bench"))
import harness  # noqa: E402

KERNELS = os.path.join(ROOT, "bench", "perf", "kernels")
SMALL = {"intloop": ["20000"], "fib": ["15"], "list_pipeline": ["2000", "3"], "sort": ["3000", "2"], "matmul": ["12", "2"],
         "normalize": ["2000", "3"], "normalize32": ["2000", "3"], "records": ["300", "5"], "str_build": ["200", "3"],
         "str_split": ["300", "3"], "func_update": ["200"], "par_normalize": ["2000", "3"], "par_records": ["300", "5"],
         "par_sum": ["5000", "2"], "par_mandel": ["24", "30"]}


def has_sanitizers():
    with tempfile.TemporaryDirectory() as d:
        src = os.path.join(d, "t.c")
        with open(src, "w") as fh:
            fh.write("int main(void) { return 0; }\n")
        exe = os.path.join(d, "t")
        r = subprocess.run([os.environ.get("CC", "cc"), "-fsanitize=address,undefined", "-o", exe, src], capture_output=True)
        return r.returncode == 0 and subprocess.run([exe]).returncode == 0


@unittest.skipUnless(has_sanitizers(), "the C compiler has no address sanitizer")
class SanitizerTests(TokCase):
    def build(self, path, name):
        exe = os.path.join(self.tmp, name)
        r = tokc("build", path, "--sanitize", "-o", exe)
        self.assertEqual(r.returncode, 0, r.stderr)
        return exe

    def clean(self, exe, args=(), cwd=None, stdin="", code=0):
        """Run a sanitized binary: any sanitizer report, or a block left allocated, fails the test. Returns stdout."""
        r = subprocess.run([exe] + list(args), capture_output=True, text=True, errors="replace", cwd=cwd or self.tmp, input=stdin,
                           timeout=TIMEOUT)
        for word in ("Sanitizer", "runtime error", "leak check"):
            self.assertNotIn(word, r.stderr, f"{exe} {args}")
        self.assertEqual(r.returncode, code, r.stderr)
        return r.stdout

    def test_the_sanitizers_do_catch_a_leak(self):
        """The check itself: C that leaks must fail, or a clean run of the corpus means nothing."""
        src = os.path.join(self.tmp, "leak.c")
        with open(src, "w") as fh:
            fh.write('#include <stdlib.h>\nint main(void) { char* volatile p = malloc(10); p = 0; return p != 0; }\n')
        exe = os.path.join(self.tmp, "leak")
        subprocess.run([os.environ.get("CC", "cc"), "-fsanitize=address,undefined", "-o", exe, src], check=True)
        r = subprocess.run([exe], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("LeakSanitizer", r.stderr)

    def test_memory_programs(self):
        """tests/mem: the expected output was produced by the last compiler that copied every value."""
        files = sorted(glob.glob(os.path.join(ROOT, "tests", "mem", "*.tok")))
        self.assertGreaterEqual(len(files), 4)
        for path in files:
            name = os.path.splitext(os.path.basename(path))[0]
            with self.subTest(program=name):
                self.assertEqual(tokc("fmt", path, "--check").returncode, 0)
                self.assert_clean_c(path)
                with open(path) as fh:
                    code = int(fh.read().split("\n")[1].split("// exit ")[1])
                with open(path[:-4] + ".out") as fh:
                    want = fh.read()
                self.assertEqual(self.clean(self.build(path, "mem_" + name), ["x", "y", "z"], code=code), want)

    def test_examples(self):
        runs = [("hello.tok", []), ("fizzbuzz.tok", []), ("errors.tok", []), ("normalize.tok", []), ("pixels.tok", []),
                ("users.tok", []), ("modules/main.tok", []), ("logscan.tok", [os.path.join(ROOT, "examples", "data", "server.log")])]
        found = {os.path.relpath(p, os.path.join(ROOT, "examples")) for p in glob.glob(os.path.join(ROOT, "examples", "*.tok"))}
        self.assertEqual(found | {"modules/main.tok"}, {name for name, _ in runs})
        for name, args in runs:
            with self.subTest(example=name):
                exe = self.build(os.path.join(ROOT, "examples", name), "ex_" + name.replace("/", "_"))
                self.assertTrue(self.clean(exe, args))
        # the error path too: logscan without a readable file
        exe = os.path.join(self.tmp, "ex_logscan.tok")
        self.clean(exe, ["no-such-file.log"], code=1)

    def test_perf_kernels(self):
        names = sorted(os.path.splitext(os.path.basename(p))[0] for p in glob.glob(os.path.join(KERNELS, "*.tok")))
        self.assertEqual(names, sorted(SMALL))
        for name in names:
            with self.subTest(kernel=name):
                out = self.clean(self.build(os.path.join(KERNELS, name + ".tok"), "k_" + name), SMALL[name])
                py = os.path.join(KERNELS, name + ".py")
                if os.path.exists(py):
                    want = subprocess.run([sys.executable, py] + SMALL[name], capture_output=True, text=True, check=True).stdout
                    self.assertEqual(out, want)

    def one_task(self, t):
        """Returns a list of problems for one benchmark task (empty when clean)."""
        exe = os.path.join(self.tmp, "b_" + t["id"])
        r = tokc("build", os.path.join(t["dir"], "ref.tok"), "--sanitize", "-o", exe)
        if r.returncode != 0:
            return [f"{t['id']}: build failed: {r.stderr[-500:]}"]
        bad = []
        for i, case in enumerate(t["tests"]):
            with tempfile.TemporaryDirectory(dir=self.tmp) as d:
                for name, text in case.get("files", {}).items():
                    with open(os.path.join(d, name), "w") as fh:
                        fh.write(text)
                r = subprocess.run(["main.tok"] + [str(a) for a in case["args"]], executable=exe, cwd=d, capture_output=True,
                                   text=True, errors="replace", input=case.get("stdin", ""), timeout=TIMEOUT)
            if (r.stdout, r.returncode) != (case["stdout"], case.get("exit_code", 0)) or "Sanitizer" in r.stderr \
                    or "runtime error" in r.stderr or "leak check" in r.stderr:
                bad.append(f"{t['id']} case {i}: exit {r.returncode}, stderr: {r.stderr[-800:]}")
        return bad

    def test_benchmark_references(self):
        tasks = harness.load_tasks()
        self.assertEqual(len(tasks), 64)
        with ThreadPoolExecutor(max_workers=3) as pool:
            bad = [line for lines in pool.map(self.one_task, tasks) for line in lines]
        self.assertEqual(bad, [])


CONCURRENT = ["par_for", "tasks_channels", "atomics"]  # the programs of tests/mem that use threads


def tsan_prefix():
    sys.path.insert(0, os.path.join(ROOT, "compiler"))
    from tok.tools import tsan_prefix as prefix
    return prefix("thread")


def has_tsan():
    """ThreadSanitizer must build and its binaries must start (through `setarch -R` where the kernel needs that)."""
    with tempfile.TemporaryDirectory() as d:
        src, exe = os.path.join(d, "t.c"), os.path.join(d, "t")
        with open(src, "w") as fh:
            fh.write("int main(void) { return 0; }\n")
        r = subprocess.run([os.environ.get("CC", "cc"), "-fsanitize=thread", "-pthread", "-o", exe, src], capture_output=True)
        return r.returncode == 0 and subprocess.run(tsan_prefix() + [exe], capture_output=True).returncode == 0


@unittest.skipUnless(has_tsan(), "the C compiler has no usable thread sanitizer")
class ThreadSanitizerTests(TokCase):
    def tsan(self, path, name, args=(), threads="4"):
        exe = os.path.join(self.tmp, name)
        r = tokc("build", path, "--tsan", "-o", exe)
        self.assertEqual(r.returncode, 0, r.stderr)
        return subprocess.run(tsan_prefix() + [exe] + list(args), capture_output=True, text=True,
                              env=dict(os.environ, TOK_THREADS=threads), timeout=TIMEOUT)

    def test_the_thread_sanitizer_does_catch_a_race(self):
        """The check itself: C with a data race must be reported, or a clean run of the corpus means nothing."""
        src, exe = os.path.join(self.tmp, "race.c"), os.path.join(self.tmp, "race")
        with open(src, "w") as fh:
            fh.write("#include <pthread.h>\nlong n;\nvoid* f(void* a) { for (int i = 0; i < 100000; i++) n++; return a; }\n"
                     "int main(void) { pthread_t t; pthread_create(&t, 0, f, 0); f(0); pthread_join(t, 0); return 0; }\n")
        subprocess.run([os.environ.get("CC", "cc"), "-fsanitize=thread", "-pthread", "-o", exe, src], check=True)
        r = subprocess.run(tsan_prefix() + [exe], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("ThreadSanitizer: data race", r.stderr)

    def test_concurrent_memory_programs(self):
        for name in CONCURRENT:
            path = os.path.join(ROOT, "tests", "mem", name + ".tok")
            with open(path[:-4] + ".out") as fh:
                want = fh.read()
            for threads in ("2", "4", "8"):
                with self.subTest(program=name, threads=threads):
                    r = self.tsan(path, "tsan_" + name, ["x", "y", "z"], threads)
                    self.assertNotIn("Sanitizer", r.stderr)
                    self.assertNotIn("leak check", r.stderr)
                    self.assertEqual((r.returncode, r.stdout), (0, want), r.stderr)

    def test_parallel_kernels(self):
        for name in sorted(SMALL):
            if not name.startswith("par_"):
                continue
            with self.subTest(kernel=name):
                r = self.tsan(os.path.join(KERNELS, name + ".tok"), "tsank_" + name, SMALL[name])
                self.assertNotIn("Sanitizer", r.stderr)
                self.assertEqual(r.returncode, 0, r.stderr)


class LeakCounterTests(TokCase):
    def test_the_counter_reports_a_block_that_is_never_freed(self):
        """TOK_LEAK_CHECK is what every other test relies on: prove that it fires."""
        path = self.write("leaky.tok", 'fn main() ! io\n  print("{1}")\n')
        c = tokc("emit-c", path).stdout
        self.assertIn("tok_live", c)
        broken = c.replace("int rc = tok_f0_main();", "int rc = tok_f0_main(); (void)tok_alloc(8);")
        self.assertNotEqual(broken, c)
        src, exe = os.path.join(self.tmp, "leaky.c"), os.path.join(self.tmp, "leaky")
        with open(src, "w") as fh:
            fh.write(broken)
        subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-DTOK_LEAK_CHECK", "-o", exe, src, "-lm"], check=True)
        r = subprocess.run([exe], capture_output=True, text=True)
        self.assertEqual((r.returncode, r.stdout), (71, "1\n"))
        self.assertIn("leak check: 1 block(s) still allocated", r.stderr)

    def test_a_panic_exits_with_70_and_is_not_a_leak_report(self):
        out, err = self.run_fail('fn main() ! io\n  xs = ["a{1}"]\n  print(xs[3])\n', 70)
        self.assertNotIn("leak check", err)
        self.assertIn("out of bounds", err)
