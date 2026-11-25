# Performance and memory: before and after the memory model

Written by `python3 bench/perf/run.py --report` from `baseline.json` and `current.json`. Every number here was measured on this machine; nothing is estimated.

- Machine: AMD EPYC 9D25 126-Core Processor, 4 cores visible, Linux-7.0.0-44-generic-x86_64-with-glibc2.39
- Compiler: cc (Ubuntu 13.3.0-6ubuntu2~24.04.1) 13.3.0; Python 3.12.3
- Tok: `tokc build (cc -std=c11 -O2)`. C: `cc -std=c11 -O2`. Python: `python3 kernel.py`.
- Wall time is the median of 5 runs of the whole process (Python: up to 3 runs). Peak memory is the largest resident set size over those runs.
- Baseline: the compiler at revision `0da44bfef465`, which copied on every store and never freed. Command: `python3 bench/perf/run.py --save baseline`.
- Current: this revision. Command: `python3 bench/perf/run.py --save current`.
- The machine is a shared VM, so times move by several percent between runs. Read ratios, not third digits.

## Wall time, seconds

| Kernel | What it does | Tok before | Tok now | C | Python | Now vs before | Now vs C | Python vs now |
|---|---|---|---|---|---|---|---|---|
| intloop | integer arithmetic in a loop, no allocation | 0.235 | 0.240 | 0.226 | 12.766 | 1.03x | 1.06x | 53.08x |
| fib | recursive calls | 0.074 | 0.074 | 0.024 | 1.647 | 1.01x | 3.04x | 22.22x |
| list_pipeline | push, map, where, sum over [int] | 0.182 | 0.100 | 0.102 | 1.826 | 0.55x | 0.98x | 18.26x |
| sort | sort 500,000 ints, three times | 0.332 | 0.344 | 0.337 | 1.210 | 1.04x | 1.02x | 3.51x |
| matmul | 300 x 300 matrices as [[f64]] | 0.077 | 0.076 | 0.060 | 3.080 | 0.98x | 1.26x | 40.69x |
| normalize | normalize kernel over [f64] | 0.170 | 0.075 | 0.060 | 1.695 | 0.44x | 1.25x | 22.63x |
| normalize32 | normalize kernel over [f32] | 0.111 | 0.050 | 0.058 | n/a | 0.45x | 0.86x | n/a |
| records | a [record] rebuilt 40 times | 0.141 | 0.018 | 0.008 | 1.556 | 0.13x | 2.16x | 87.93x |
| str_build | a string grown piece by piece | 0.131 | 0.001 | 0.002 | 0.035 | 0.01x | 0.50x | 29.33x |
| str_split | lines, split, to_int | 0.102 | 0.058 | 0.042 | 0.188 | 0.57x | 1.37x | 3.25x |
| func_update | xs = with_item(xs, v) in a loop | 0.163 | 0.001 | 0.001 | 0.045 | 0.01x | 1.00x | 49.67x |

## Peak memory, MiB

| Kernel | Tok before | Tok now | C | Python |
|---|---|---|---|---|
| intloop | 1.6 | 1.3 | 1.6 | 9.9 |
| fib | 1.6 | 1.3 | 1.6 | 10.1 |
| list_pipeline | 240.5 | 19.2 | 19.2 | 130.6 |
| sort | 59.5 | 12.8 | 8.9 | 54.0 |
| matmul | 13.9 | 4.9 | 3.3 | 23.9 |
| normalize | 200.8 | 17.0 | 16.8 | 125.1 |
| normalize32 | 101.4 | 9.3 | 9.1 | n/a |
| records | 240.2 | 7.6 | 5.5 | 38.8 |
| str_build | 160.8 | 1.4 | 1.5 | 10.2 |
| str_split | 82.0 | 4.6 | 2.1 | 13.0 |
| func_update | 260.7 | 1.5 | 1.7 | 10.3 |

## What changed, and where Tok is still slow

`current.json` was measured at revision `479bbfe1d521` on Tue 2026-11-24.

What moved the numbers:

- Values are shared and counted instead of copied on every store, and freed when the last owner lets go. That is the
  whole memory column: the baseline never freed anything.
- `s = s + x` and `s = "{s}..."` append in place when the string has one owner (str_build: quadratic before, linear now).
- `xs = with_item(xs, v)` hands the list over and gets it back, so the function appends in place (func_update:
  quadratic before, linear now).
- `s[i]` and `chars()` return shared one-byte strings, string literals are static, `print` of an interpolated string
  writes straight to the output.

Where Tok is still behind C, bluntly:

- **fib is 3x slower than C, and it is entirely the overflow checks.** `int` arithmetic panics on overflow. With the
  three checked operations in `fib` replaced by plain `+` and `-` in the generated C, the same binary runs in
  0.023-0.026 s instead of 0.077-0.084 s (3 runs each, `cc -std=c11 -O2`, `fib 35`), which is the C time. The checks
  stop gcc from turning the second recursive call into a loop. Nothing in this round removes checks that can be
  proven unnecessary.
- **records is 2x slower than C and uses 1.4x the memory.** `bs = step(bs, dt)` with `bs.map(...)` inside builds a new
  list every step and bumps the count of every `name` string, then frees the old list element by element. The C
  version updates the array in place. Reusing the input list of a `map` when it has one owner was designed and not built.
- **str_split is 1.4x slower than C and uses 2.2x the memory.** `lines()` and `split()` allocate one heap string per
  piece (a 16-byte header plus the bytes). The C version walks the buffer with pointers and allocates nothing.
  String slices that share the parent's buffer would close this; they are not built.
- **sort uses 1.4x the memory of C** (12.8 vs 8.9 MiB): `sort()` returns a new list, so the unsorted and sorted lists
  are alive together. Time is the same as C's `qsort`.
- **matmul is about 1.2x slower than C and uses 1.5x the memory.** Every `a[i][k]` is two bounds checks and the row is
  looked up again on each iteration; gcc removes some of that, not all. Bounds checks are not hoisted out of loops
  yet. The memory difference is one separately allocated row per matrix row plus list growth by doubling, against
  one flat array in C.
- **normalize** shows 0.075 s against C's 0.060 s in the median, but the best runs are identical (0.0596 vs 0.0593 s
  in `current.json`); that kernel is at parity and the median caught noise from this shared VM.
- str_build and func_update now finish in about a millisecond, which is too short for this harness to compare with
  C. At larger sizes, measured by hand on the same machine (3 runs each): `func_update 3000000` Tok 0.017-0.020 s,
  C 0.015-0.016 s; `str_build 200000 10` Tok 0.035-0.037 s, C (which uses `snprintf`) 0.118-0.124 s.

Not measured: anything with more than one thread (there is no concurrency yet), compile time, and programs larger
than these kernels. Counts are not atomic, so none of this carries over to threads as it stands.

## After the syntax round (script form, conditional expression, while, multiple assignment, dicts)

`tokens_round.json` (`python3 bench/perf/run.py --langs tok,c --save tokens_round`, Tue 2026-11-24) repeats the
Tok and C runs with the kernels unchanged apart from `args` no longer holding the program name. Against
`current.json`, every Tok median is within 7% (largest moves: normalize32 0.050 to 0.053 s, matmul 0.076 to 0.072 s,
sort 0.344 to 0.328 s) and every peak memory within 2%, which is inside the run-to-run spread of this shared VM. No
regression was found. No kernel uses the new syntax, and dicts have no kernel yet, so their speed is not measured.

## Parallel kernels

Written from `parallel.json` (`python3 bench/perf/run.py --parallel`). Tok: `tokc build (cc -std=c11 -O2 -pthread)`, threads set with `TOK_THREADS`. C: `cc -std=c11 -O2 -fopenmp`, threads set with `OMP_NUM_THREADS`. Median of 7 runs of the whole process; every run of a kernel, in both languages and at every thread count, printed the same checksum.

**Read this first.** The machine shows 4 cores, but it is a shared VM whose CPU allowance is 1.5 cores (given with --cpu-share). Over any long stretch a program here gets at most about 1.5 cores of CPU time, however many threads it starts; short runs like these can briefly get more, and other tenants can take time away, so the ratios move from run to run. With more threads than the allowance the threads take turns, which is why 4 threads are sometimes no faster, or slower, than 2.

What these tables can show: the parallel programs give the same answers as the serial ones, the cost of the parallel machinery on one thread is small, Tok is close to C with OpenMP at each thread count, and two threads do help. What they cannot show: how Tok scales to 4 or more real cores. That needs a machine with that many unshared cores, and nothing here should be read as a scaling result.

### Wall time, seconds

| Kernel | What it does | Tok 1T | Tok 2T | Tok 4T | C+OpenMP 1T | C+OpenMP 2T | C+OpenMP 4T | Tok 1T / best Tok | Tok 1T / C 1T |
|---|---|---|---|---|---|---|---|---|---|
| par_normalize | normalize over 1,000,000 f64: two parallel sums and a parallel map, 8 rounds | 0.061 | 0.045 | 0.037 | 0.063 | 0.055 | 0.090 | 1.62x | 0.96x |
| par_records | parallel map over 400,000 records, 40 steps | 0.073 | 0.051 | 0.068 | 0.063 | 0.054 | 0.104 | 1.42x | 1.16x |
| par_sum | parallel integer sum over 30,000,000 values, 4 rounds | 0.347 | 0.224 | 0.279 | 0.292 | 0.204 | 0.329 | 1.55x | 1.19x |
| par_mandel | Mandelbrot 600 x 600, limit 300, one row per iteration | 0.160 | 0.125 | 0.101 | 0.176 | 0.122 | 0.104 | 1.58x | 0.91x |

### Peak memory, MiB

| Kernel | Tok 1T | Tok 2T | Tok 4T | C+OpenMP 1T | C+OpenMP 2T | C+OpenMP 4T |
|---|---|---|---|---|---|---|
| par_normalize | 17.3 | 17.3 | 17.2 | 17.5 | 17.5 | 17.5 |
| par_records | 32.2 | 32.3 | 32.3 | 32.4 | 32.6 | 32.6 |
| par_sum | 1.6 | 2.0 | 1.9 | 2.0 | 2.1 | 2.1 |
| par_mandel | 1.8 | 2.0 | 2.0 | 2.0 | 1.9 | 2.1 |

### Notes on the parallel kernels

- `par_normalize`: the two sums are reductions and the last pass is `par for x in xs -> ...`. The C reference cuts its
  sums into the same 64 chunks so that both print the same checksum; a plain OpenMP `reduction(+:s)` would add in a
  different order and differ in the last bits.
- `par_records`: the record has no string field (unlike the serial `records` kernel), so a result is a flat copy.
  With managed fields each result is isolated first, which costs a copy per shared field.
- `par_sum`: Tok checks every `int` add and multiply for overflow, C does not; that is the one-thread gap.
- `par_mandel`: rows take very different times, so this is the kernel where taking chunks on demand matters. The C
  reference uses `schedule(dynamic, 1)`.
- Peak memory barely moves with the thread count at these sizes: the data dominates, and a thread adds its stack.

### Single-threaded kernels after this change

Measured with `python3 bench/perf/run.py --save phase4 --langs tok,c` (`phase4.json`), against `current.json` from
the memory-model round. Programs that do not use concurrency get exactly the C they got before, apart from the
form of the count macros, so no change is expected, and none beyond run-to-run noise was measured.

| Kernel | Tok before, s | Tok now, s | C now, s | Tok peak before, MiB | Tok peak now, MiB |
|---|---|---|---|---|---|
| intloop | 0.240 | 0.243 | 0.221 | 1.3 | 1.4 |
| fib | 0.074 | 0.073 | 0.024 | 1.3 | 1.3 |
| list_pipeline | 0.100 | 0.103 | 0.110 | 19.2 | 19.2 |
| sort | 0.344 | 0.326 | 0.332 | 12.8 | 12.8 |
| matmul | 0.076 | 0.070 | 0.061 | 4.9 | 4.9 |
| normalize | 0.075 | 0.067 | 0.062 | 17.0 | 17.0 |
| normalize32 | 0.050 | 0.055 | 0.056 | 9.3 | 9.3 |
| records | 0.018 | 0.018 | 0.008 | 7.6 | 7.5 |
| str_build | 0.001 | 0.001 | 0.002 | 1.4 | 1.3 |
| str_split | 0.058 | 0.058 | 0.041 | 4.6 | 4.6 |
| func_update | 0.001 | 0.001 | 0.001 | 1.5 | 1.5 |

