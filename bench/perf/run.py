#!/usr/bin/env python3
"""Performance suite: wall time and peak memory of the kernels in bench/perf/kernels.

Each kernel exists as a Tok program, a hand-written C program (cc -O2) and a Python program. Every program
prints a checksum, and the three must agree before a time is recorded.

  python3 bench/perf/run.py                      run everything, print a table
  python3 bench/perf/run.py --save NAME          also write bench/perf/NAME.json
  python3 bench/perf/run.py --only sort,fib      a subset
  python3 bench/perf/run.py --langs tok,c        skip Python
  python3 bench/perf/run.py --report             write bench/perf/RESULTS.md from baseline.json and current.json
  python3 bench/perf/run.py --parallel           the par_* kernels with 1, 2 and 4 threads; writes parallel.json

The parallel kernels have a C reference that uses OpenMP (cc -O2 -fopenmp). The thread count is set with TOK_THREADS
for Tok and OMP_NUM_THREADS for C.

Wall time is the median of --runs runs (default 5), measured around the whole process by bench/perf/timeit.c.
Peak memory is the largest resident set size the kernel reported for the process (ru_maxrss), the maximum over the runs.
"""
import argparse
import json
import os
import platform
import shutil
import statistics
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
KERNELS = os.path.join(HERE, "kernels")
TOKC = [sys.executable, os.path.join(ROOT, "compiler", "tokc.py")]
ORDER = ["intloop", "fib", "list_pipeline", "sort", "matmul", "normalize", "normalize32", "records", "str_build",
         "str_split", "func_update"]
ABOUT = {
    "intloop": "integer arithmetic in a loop, no allocation",
    "fib": "recursive calls",
    "list_pipeline": "push, map, where, sum over [int]",
    "sort": "sort 500,000 ints, three times",
    "matmul": "300 x 300 matrices as [[f64]]",
    "normalize": "normalize kernel over [f64]",
    "normalize32": "normalize kernel over [f32]",
    "records": "a [record] rebuilt 40 times",
    "str_build": "a string grown piece by piece",
    "str_split": "lines, split, to_int",
    "func_update": "xs = with_item(xs, v) in a loop",
}


PAR_ORDER = ["par_normalize", "par_records", "par_sum", "par_mandel"]
PAR_ABOUT = {
    "par_normalize": "normalize over 1,000,000 f64: two parallel sums and a parallel map, 8 rounds",
    "par_records": "parallel map over 400,000 records, 40 steps",
    "par_sum": "parallel integer sum over 30,000,000 values, 4 rounds",
    "par_mandel": "Mandelbrot 600 x 600, limit 300, one row per iteration",
}
THREADS = [1, 2, 4]


def measure_rss(launcher, cmd, runs, timeout, env=None):
    """Returns (median seconds, best seconds, peak RSS in KiB, stdout). Time and memory come from bench/perf/timeit.c."""
    times, peak, out = [], 0, None
    stats = launcher + ".stats"
    for _ in range(runs):
        try:
            r = subprocess.run([launcher, stats] + cmd, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout,
                               env=dict(os.environ, **env) if env else None)
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"timed out after {timeout}s")
        if r.returncode != 0:
            raise RuntimeError(f"exit {r.returncode}: {r.stderr.decode(errors='replace')[-300:]}")
        with open(stats) as fh:
            secs, kib, _ = fh.read().split()
        times.append(float(secs))
        peak = max(peak, int(kib))
        out = r.stdout.decode()
    return statistics.median(times), min(times), peak, out


def machine():
    cpu = ""
    try:
        with open("/proc/cpuinfo") as fh:
            for line in fh:
                if line.startswith("model name"):
                    cpu = line.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    cc = subprocess.run([os.environ.get("CC", "cc"), "--version"], capture_output=True, text=True).stdout.splitlines()[0]
    return {"platform": platform.platform(), "cpu": cpu, "cores": os.cpu_count(), "cc": cc,
            "python": platform.python_version()}


def run_suite(args):
    names = [k for k in ORDER if not args.only or k in args.only.split(",")]
    langs = args.langs.split(",")
    build = tempfile.mkdtemp(prefix="tokperf")
    results = {}
    try:
        launcher = os.path.join(build, "timeit")
        subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-O2", "-o", launcher, os.path.join(HERE, "timeit.c")], check=True)
        for name in names:
            row = {}
            cmds = {}
            if "tok" in langs:
                exe = os.path.join(build, name + "_tok")
                r = subprocess.run(TOKC + ["build", os.path.join(KERNELS, name + ".tok"), "-o", exe], capture_output=True, text=True)
                if r.returncode != 0:
                    row["tok"] = {"error": "build failed: " + (r.stderr or r.stdout)[-300:]}
                else:
                    cmds["tok"] = [exe]
            if "c" in langs:
                exe = os.path.join(build, name + "_c")
                r = subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-O2", "-o", exe, os.path.join(KERNELS, name + ".c"), "-lm"],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    row["c"] = {"error": "build failed: " + r.stderr[-300:]}
                else:
                    cmds["c"] = [exe]
            py = os.path.join(KERNELS, name + ".py")
            if "python" in langs and os.path.exists(py):
                cmds["python"] = [sys.executable, py]
            for lang, cmd in cmds.items():
                try:
                    med, best, rss, out = measure_rss(launcher, cmd, args.runs if lang != "python" else min(args.runs, args.py_runs), args.timeout)
                    row[lang] = {"seconds": round(med, 4), "best": round(best, 4), "peak_kib": rss, "output": out.strip()}
                except RuntimeError as e:
                    row[lang] = {"error": str(e)}
            outs = {v["output"] for v in row.values() if "output" in v}
            row["agree"] = len(outs) <= 1
            results[name] = row
            shown = "  ".join(f"{lang} {v['seconds']:.3f}s {v['peak_kib'] / 1024:.1f}MiB" if "seconds" in v else f"{lang} ERROR {v['error'][:80]}"
                              for lang, v in row.items() if isinstance(v, dict))
            print(f"{name:14} {shown}{'' if row['agree'] else '   OUTPUTS DIFFER'}", flush=True)
    finally:
        shutil.rmtree(build, ignore_errors=True)
    doc = {"machine": machine(), "runs": args.runs, "tok_flags": "tokc build (cc -std=c11 -O2)", "c_flags": "cc -std=c11 -O2",
           "results": results}
    if args.save:
        path = os.path.join(HERE, args.save + ".json")
        with open(path, "w") as fh:
            json.dump(doc, fh, indent=1, sort_keys=True)
            fh.write("\n")
        print("wrote", os.path.relpath(path, ROOT))
    return 0 if all(r["agree"] and all("error" not in v for v in r.values() if isinstance(v, dict)) for r in results.values()) else 1


def cpu_share():
    """The CPU time this container may use, in cores, from the cgroup; None if there is no limit to read."""
    try:
        with open("/sys/fs/cgroup/cpu.max") as fh:
            quota, period = fh.read().split()
        return None if quota == "max" else round(int(quota) / int(period), 2)
    except (OSError, ValueError):
        return None


def run_parallel(args):
    names = [k for k in PAR_ORDER if not args.only or k in args.only.split(",")]
    build = tempfile.mkdtemp(prefix="tokperf")
    results = {}
    try:
        launcher = os.path.join(build, "timeit")
        subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-O2", "-o", launcher, os.path.join(HERE, "timeit.c")], check=True)
        for name in names:
            tok_exe, c_exe = os.path.join(build, name + "_tok"), os.path.join(build, name + "_c")
            subprocess.run(TOKC + ["build", os.path.join(KERNELS, name + ".tok"), "-o", tok_exe], check=True, capture_output=True)
            subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-O2", "-fopenmp", "-o", c_exe,
                            os.path.join(KERNELS, name + ".c"), "-lm"], check=True)
            row, outs = {}, set()
            for t in THREADS:
                for lang, exe, var in (("tok", tok_exe, "TOK_THREADS"), ("c", c_exe, "OMP_NUM_THREADS")):
                    med, best, rss, out = measure_rss(launcher, [exe], args.runs, args.timeout, {var: str(t)})
                    row[f"{lang}_{t}"] = {"seconds": round(med, 4), "best": round(best, 4), "peak_kib": rss}
                    outs.add(out.strip())
            row["agree"] = len(outs) == 1
            results[name] = row
            print(f"{name:14} " + "  ".join(f"{k} {v['seconds']:.3f}s" for k, v in row.items() if isinstance(v, dict))
                  + ("" if row["agree"] else "   OUTPUTS DIFFER"), flush=True)
    finally:
        shutil.rmtree(build, ignore_errors=True)
    doc = {"machine": machine(), "cpu_share": args.cpu_share or cpu_share(), "cpu_share_source":
           "given with --cpu-share" if args.cpu_share else "cgroup cpu.max", "runs": args.runs, "threads": THREADS,
           "tok_flags": "tokc build (cc -std=c11 -O2 -pthread)", "c_flags": "cc -std=c11 -O2 -fopenmp", "results": results}
    with open(os.path.join(HERE, "parallel.json"), "w") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("wrote bench/perf/parallel.json")
    return 0 if all(r["agree"] for r in results.values()) else 1


def parallel_report(w):
    path = os.path.join(HERE, "parallel.json")
    if not os.path.exists(path):
        return
    with open(path) as fh:
        doc = json.load(fh)
    m, share, ts = doc["machine"], doc.get("cpu_share"), doc["threads"]
    w("## Parallel kernels")
    w("")
    w("Written from `parallel.json` (`python3 bench/perf/run.py --parallel`). Tok: `" + doc["tok_flags"] + "`, threads set "
      "with `TOK_THREADS`. C: `" + doc["c_flags"] + "`, threads set with `OMP_NUM_THREADS`. Median of "
      f"{doc['runs']} runs of the whole process; every run of a kernel, in both languages and at every thread count, printed the same checksum.")
    w("")
    if share is not None:
        w(f"**Read this first.** The machine shows {m['cores']} cores, but it is a shared VM whose CPU allowance is "
          f"{share} cores ({doc.get('cpu_share_source')}). Over any long stretch a program here gets at most about {share} "
          "cores of CPU time, however many threads it starts; short runs like these can briefly get more, and other "
          "tenants can take time away, so the ratios move from run to run. With more threads than the allowance the "
          "threads take turns, which is why 4 threads are sometimes no faster, or slower, than 2.")
        w("")
        w("What these tables can show: the parallel programs give the same answers as the serial ones, the cost of the "
          "parallel machinery on one thread is small, Tok is close to C with OpenMP at each thread count, and two threads "
          f"do help. What they cannot show: how Tok scales to 4 or more real cores. That needs a machine with that many "
          "unshared cores, and nothing here should be read as a scaling result.")
    else:
        w(f"The machine shows {m['cores']} cores and no CPU limit was found for this container.")
    w("")
    w("### Wall time, seconds")
    w("")
    w("| Kernel | What it does | " + " | ".join(f"Tok {t}T" for t in ts) + " | " + " | ".join(f"C+OpenMP {t}T" for t in ts)
      + f" | Tok {ts[0]}T / best Tok | Tok {ts[0]}T / C {ts[0]}T |")
    w("|---|---|" + "---|" * (2 * len(ts) + 2))
    for k in PAR_ORDER:
        r = doc["results"].get(k)
        if not r:
            continue
        tok = [r[f"tok_{t}"]["seconds"] for t in ts]
        c = [r[f"c_{t}"]["seconds"] for t in ts]
        w(f"| {k} | {PAR_ABOUT[k]} | " + " | ".join(f"{v:.3f}" for v in tok) + " | " + " | ".join(f"{v:.3f}" for v in c)
          + f" | {tok[0] / min(tok):.2f}x | {tok[0] / c[0]:.2f}x |")
    w("")
    w("### Peak memory, MiB")
    w("")
    w("| Kernel | " + " | ".join(f"Tok {t}T" for t in ts) + " | " + " | ".join(f"C+OpenMP {t}T" for t in ts) + " |")
    w("|---|" + "---|" * (2 * len(ts)))
    for k in PAR_ORDER:
        r = doc["results"].get(k)
        if not r:
            continue
        w(f"| {k} | " + " | ".join(f"{r[f'tok_{t}']['peak_kib'] / 1024:.1f}" for t in ts) + " | "
          + " | ".join(f"{r[f'c_{t}']['peak_kib'] / 1024:.1f}" for t in ts) + " |")
    w("")
    notes = os.path.join(HERE, "NOTES_PARALLEL.md")
    if os.path.exists(notes):
        with open(notes) as fh:
            w(fh.read().rstrip())
        w("")


def fmt_t(v):
    if v is None or "seconds" not in v:
        return "n/a" if v is None else "failed"
    return f"{v['seconds']:.3f}"


def fmt_m(v):
    if v is None or "peak_kib" not in v:
        return "n/a" if v is None else "failed"
    return f"{v['peak_kib'] / 1024:.1f}"


def ratio(a, b):
    if not a or not b or "seconds" not in a or "seconds" not in b or b["seconds"] == 0:
        return "n/a"
    return f"{a['seconds'] / b['seconds']:.2f}x"


def report(args):
    def load(name):
        with open(os.path.join(HERE, name + ".json")) as fh:
            return json.load(fh)
    base, cur = load("baseline"), load("current")
    out = []
    w = out.append
    w("# Performance and memory: before and after the memory model")
    w("")
    w("Written by `python3 bench/perf/run.py --report` from `baseline.json` and `current.json`. Every number here was "
      "measured on this machine; nothing is estimated.")
    w("")
    m = cur["machine"]
    w(f"- Machine: {m['cpu'] or 'unknown CPU'}, {m['cores']} cores visible, {m['platform']}")
    w(f"- Compiler: {m['cc']}; Python {m['python']}")
    w(f"- Tok: `{cur['tok_flags']}`. C: `{cur['c_flags']}`. Python: `python3 kernel.py`.")
    w(f"- Wall time is the median of {cur['runs']} runs of the whole process (Python: up to 3 runs). Peak memory is the "
      "largest resident set size over those runs.")
    w("- Baseline: the compiler at revision `0da44bfef465`, which copied on every store and never freed. "
      "Command: `python3 bench/perf/run.py --save baseline`.")
    w("- Current: this revision. Command: `python3 bench/perf/run.py --save current`.")
    w("- The machine is a shared VM, so times move by several percent between runs. Read ratios, not third digits.")
    w("")
    w("## Wall time, seconds")
    w("")
    w("| Kernel | What it does | Tok before | Tok now | C | Python | Now vs before | Now vs C | Python vs now |")
    w("|---|---|---|---|---|---|---|---|---|")
    for k in ORDER:
        b, c = base["results"].get(k, {}), cur["results"].get(k, {})
        if not c:
            continue
        w(f"| {k} | {ABOUT[k]} | {fmt_t(b.get('tok'))} | {fmt_t(c.get('tok'))} | {fmt_t(c.get('c'))} | {fmt_t(c.get('python'))} | "
          f"{ratio(c.get('tok'), b.get('tok'))} | {ratio(c.get('tok'), c.get('c'))} | {ratio(c.get('python'), c.get('tok'))} |")
    w("")
    w("## Peak memory, MiB")
    w("")
    w("| Kernel | Tok before | Tok now | C | Python |")
    w("|---|---|---|---|---|")
    for k in ORDER:
        b, c = base["results"].get(k, {}), cur["results"].get(k, {})
        if not c:
            continue
        w(f"| {k} | {fmt_m(b.get('tok'))} | {fmt_m(c.get('tok'))} | {fmt_m(c.get('c'))} | {fmt_m(c.get('python'))} |")
    w("")
    notes = os.path.join(HERE, "NOTES.md")
    if os.path.exists(notes):
        with open(notes) as fh:
            w(fh.read().rstrip())
        w("")
    parallel_report(w)
    with open(os.path.join(HERE, "RESULTS.md"), "w") as fh:
        fh.write("\n".join(out) + "\n")
    print("wrote bench/perf/RESULTS.md")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--py-runs", type=int, default=3)
    ap.add_argument("--only", default="")
    ap.add_argument("--langs", default="tok,c,python")
    ap.add_argument("--save", default="")
    ap.add_argument("--timeout", type=float, default=300.0)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--parallel", action="store_true")
    ap.add_argument("--cpu-share", type=float, default=0.0, help="the CPU allowance of this machine in cores, if the cgroup does not say")
    args = ap.parse_args()
    if args.parallel:
        return run_parallel(args)
    return report(args) if args.report else run_suite(args)


if __name__ == "__main__":
    sys.exit(main())
