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
