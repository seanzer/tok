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
