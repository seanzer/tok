# Static token measurements

Measured by `bench/count_tokens.py` over the reference solutions in `bench/tasks/`: the 40 original tasks and 24 added ones (12 that read standard input, 12 on strings), 64 in all. These are counts of hand-written reference programs, not of model output, and say nothing yet about tokens-to-green. No model was called.

**Bottom line: over the 64 tasks the Tok references are larger than Python's, 2110 tokens against 2006 (1.05x) on tiktoken o200k_base.** The 40 original tasks went from 1.99x Python's tokens (first measurement) to 1.47x (built-ins) to 1.00x now; the 24 added tasks from 1.70x to 1.18x; all 64 from 1.53x to 1.05x. Tok is smaller than Python on 29 of 64 tasks, equal on 4 and larger on 31. The ratio differs by tokenizer; every one is in the tables below.

## Three points in time

1. **Original** = revision `144c8d94e6c9`: the first measurement, 40 tasks, before sort, slices, power, checked chains, string indexing and stdin. The 24 added tasks did not exist yet.
2. **Built-ins** = revision `0da44bfef465`: the Tok references rewritten with the built-ins; eight Python references tightened to the built-in a Python developer would use; 24 tasks added.
3. **Now** = this revision: script form (no `fn main` line), `args` without the program name, the conditional expression, `while` / `break` / `continue`, multiple assignment and dicts, with the Tok references rewritten to use them. The Python references are unchanged since point 2. 0 of the 64 Tok references still have a `fn main`.

| Tokenizer | Group | Tasks | Tok, original | Python, original | Ratio | Tok, built-ins | Python, built-ins | Ratio | Tok, now | Python, now | Ratio |
|---|---|---|---|---|---|---|---|---|---|---|---|
| hf Qwen/Qwen2.5-Coder-7B-Instruct | original | 40 | 3103 | 1555 | 1.995 | 2137 | 1453 | 1.471 | 1459 | 1453 | 1.004 |
| hf Qwen/Qwen2.5-Coder-7B-Instruct | added | 24 | n/a | n/a | n/a | 954 | 563 | 1.694 | 666 | 563 | 1.183 |
| hf Qwen/Qwen2.5-Coder-7B-Instruct | all | 64 | n/a | n/a | n/a | 3091 | 2016 | 1.533 | 2125 | 2016 | 1.054 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | original | 40 | 3566 | 1990 | 1.792 | 2490 | 1860 | 1.339 | 1728 | 1860 | 0.929 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | added | 24 | n/a | n/a | n/a | 1161 | 780 | 1.488 | 856 | 780 | 1.097 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | all | 64 | n/a | n/a | n/a | 3651 | 2640 | 1.383 | 2584 | 2640 | 0.979 |
| hf openai-community/gpt2 | original | 40 | 3642 | 2153 | 1.692 | 2509 | 1990 | 1.261 | 1632 | 1990 | 0.820 |
| hf openai-community/gpt2 | added | 24 | n/a | n/a | n/a | 1189 | 798 | 1.490 | 828 | 798 | 1.038 |
| hf openai-community/gpt2 | all | 64 | n/a | n/a | n/a | 3698 | 2788 | 1.326 | 2460 | 2788 | 0.882 |
| tiktoken cl100k_base | original | 40 | 3089 | 1544 | 2.001 | 2126 | 1442 | 1.474 | 1448 | 1442 | 1.004 |
| tiktoken cl100k_base | added | 24 | n/a | n/a | n/a | 948 | 557 | 1.702 | 660 | 557 | 1.185 |
| tiktoken cl100k_base | all | 64 | n/a | n/a | n/a | 3074 | 1999 | 1.538 | 2108 | 1999 | 1.055 |
| tiktoken o200k_base | original | 40 | 3092 | 1552 | 1.992 | 2128 | 1448 | 1.470 | 1450 | 1448 | 1.001 |
| tiktoken o200k_base | added | 24 | n/a | n/a | n/a | 948 | 558 | 1.699 | 660 | 558 | 1.183 |
| tiktoken o200k_base | all | 64 | n/a | n/a | n/a | 3076 | 2006 | 1.533 | 2110 | 2006 | 1.052 |

Source size of the 40 originals: Tok 7948 bytes / 340 lines at the original point, 5560 / 203 after the built-ins, 3744 / 119 now; Python 4691 / 234, then 4357 / 216. All 64: Tok 8273 bytes / 295 lines after the built-ins, 5687 / 173 now; Python 6300 / 308.

## Per-task spread, this revision

| Tokenizer | Group | Tasks | Tok / Python | Median per task | Smallest | Largest | Tok smaller | Equal | Tok smaller, built-ins |
|---|---|---|---|---|---|---|---|---|---|
| hf Qwen/Qwen2.5-Coder-7B-Instruct | original | 40 | 1.004 | 1.000 | 0.438 | 1.739 | 19 | 3 | 3 |
| hf Qwen/Qwen2.5-Coder-7B-Instruct | added | 24 | 1.183 | 1.124 | 0.636 | 2.125 | 10 | 1 | 0 |
| hf Qwen/Qwen2.5-Coder-7B-Instruct | all | 64 | 1.054 | 1.000 | 0.438 | 2.125 | 29 | 4 | 3 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | original | 40 | 0.929 | 0.933 | 0.478 | 1.690 | 24 | 2 | 4 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | added | 24 | 1.097 | 1.026 | 0.684 | 2.000 | 11 | 0 | 0 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | all | 64 | 0.979 | 0.948 | 0.478 | 2.000 | 35 | 2 | 4 |
| hf openai-community/gpt2 | original | 40 | 0.820 | 0.858 | 0.386 | 1.714 | 28 | 2 | 6 |
| hf openai-community/gpt2 | added | 24 | 1.038 | 0.929 | 0.622 | 1.920 | 14 | 0 | 1 |
| hf openai-community/gpt2 | all | 64 | 0.882 | 0.892 | 0.386 | 1.920 | 42 | 2 | 7 |
| tiktoken cl100k_base | original | 40 | 1.004 | 1.000 | 0.438 | 1.739 | 19 | 3 | 3 |
| tiktoken cl100k_base | added | 24 | 1.185 | 1.124 | 0.636 | 2.125 | 10 | 1 | 0 |
| tiktoken cl100k_base | all | 64 | 1.055 | 1.000 | 0.438 | 2.125 | 29 | 4 | 3 |
| tiktoken o200k_base | original | 40 | 1.001 | 1.000 | 0.438 | 1.680 | 19 | 3 | 3 |
| tiktoken o200k_base | added | 24 | 1.183 | 1.124 | 0.636 | 2.125 | 10 | 1 | 0 |
| tiktoken o200k_base | all | 64 | 1.052 | 1.000 | 0.438 | 2.125 | 29 | 4 | 3 |

## Worst remaining tasks (tiktoken o200k_base)

The 15 tasks with the largest Tok minus Python difference.

| Task | Group | Tok | Python | Difference | Tok, built-ins | Tok, original |
|---|---|---|---|---|---|---|
| stdin_word_freq | added | 68 | 32 | +36 | 78 |  |
| primes_upto | original | 84 | 50 | +34 | 102 | 113 |
| triangle | original | 109 | 89 | +20 | 131 | 161 |
| common_prefix | added | 35 | 17 | +18 | 70 |  |
| caesar | added | 65 | 48 | +17 | 86 |  |
| range_sum | original | 53 | 36 | +17 | 68 | 78 |
| running_sum | original | 40 | 24 | +16 | 57 | 65 |
| gcd | original | 47 | 36 | +11 | 68 | 88 |
| perfect_number | original | 61 | 50 | +11 | 80 | 85 |
| make_change | original | 55 | 45 | +10 | 69 | 75 |
| stdin_passing | added | 52 | 42 | +10 | 64 |  |
| dot_product | original | 56 | 47 | +9 | 75 | 112 |
| power | original | 26 | 17 | +9 | 37 | 73 |
| anagram | added | 33 | 25 | +8 | 48 |  |
| pad_number | added | 22 | 14 | +8 | 34 |  |

Tasks where Tok is not larger (33): sum_file (39 vs 79), file_stats (37 vs 54), is_prime (60 vs 74), csv_sum (26 vs 40), count_errors (27 vs 41), max_arg (15 vs 28), replace_word (27 vs 37), palindrome_list (15 vs 25), dedupe (7 vs 16), upper_join (11 vs 19), acronym (14 vs 22), stdin_grep (20 vs 27), reverse_args (7 vs 13), safe_divide (44 vs 49), stdin_average (34 vs 38), adult_average (49 vs 53), stdin_sort_lines (15 vs 18), stdin_reverse_lines (15 vs 18), stdin_longest_line (15 vs 18), stdin_line_count (9 vs 12), longest_word (10 vs 13), sum_args (15 vs 17), reverse_string (10 vs 12), factorial (31 vs 33), times_table (36 vs 37), stdin_unique_words (11 vs 12), oldest (25 vs 26), is_palindrome (26 vs 27), fizzbuzz (72 vs 73), word_count (12 vs 12), stdin_head (25 vs 25), sort_ints (18 vs 18), count_evens (26 vs 26).

## Where the remaining difference comes from

The gap is a net figure: the tokens Tok spends beyond Python on the tasks where it is larger, minus what it saves on the tasks where it is smaller. The construct columns are measured by deleting that construct from every Tok reference and counting again (the transformations are in `count_tokens.py`; the stripped programs are not valid Tok). They overlap a little and are not a partition of the gap. Python's import lines are the cost on its side of the same ledger.

| Tokenizer | Group | Gap (Tok - Python) | Excess where Tok is larger (tasks) | Saving where Tok is smaller (tasks) | `or` defaults | Type annotations | Effect lists | `fn main` line | Python imports |
|---|---|---|---|---|---|---|---|---|---|
| hf Qwen/Qwen2.5-Coder-7B-Instruct | original | +6 | 177 (18) | 171 (19) | 166 | 17 | 0 | 0 | 125 |
| hf Qwen/Qwen2.5-Coder-7B-Instruct | added | +103 | 137 (13) | 34 (10) | 73 | 6 | 0 | 0 | 85 |
| hf Qwen/Qwen2.5-Coder-7B-Instruct | all | +109 | 314 (31) | 205 (29) | 239 | 23 | 0 | 0 | 210 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | original | -132 | 128 (14) | 260 (24) | 159 | 17 | 0 | 0 | 168 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | added | +76 | 138 (13) | 62 (11) | 78 | 6 | 0 | 0 | 111 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | all | -56 | 266 (27) | 322 (35) | 237 | 23 | 0 | 0 | 279 |
| hf openai-community/gpt2 | original | -358 | 80 (10) | 438 (28) | 111 | 16 | 0 | 0 | 167 |
| hf openai-community/gpt2 | added | +30 | 123 (10) | 93 (14) | 67 | 6 | 0 | 0 | 109 |
| hf openai-community/gpt2 | all | -328 | 203 (20) | 531 (42) | 178 | 22 | 0 | 0 | 276 |
| tiktoken cl100k_base | original | +6 | 177 (18) | 171 (19) | 166 | 17 | 0 | 0 | 125 |
| tiktoken cl100k_base | added | +103 | 138 (13) | 35 (10) | 73 | 6 | 0 | 0 | 85 |
| tiktoken cl100k_base | all | +109 | 315 (31) | 206 (29) | 239 | 23 | 0 | 0 | 210 |
| tiktoken o200k_base | original | +2 | 176 (18) | 174 (19) | 166 | 17 | 0 | 0 | 125 |
| tiktoken o200k_base | added | +102 | 137 (13) | 35 (10) | 73 | 6 | 0 | 0 | 85 |
| tiktoken o200k_base | all | +104 | 313 (31) | 209 (29) | 239 | 23 | 0 | 0 | 210 |

Read on tiktoken o200k_base, all 64 tasks: Tok is 2110 tokens and Python 2006, a gap of +104. The entry-point line that cost 736 tokens at the built-ins point is gone (0 now). The largest construct left is the explicit default on a lookup or a parse (`args[0].to_int() or 0`): 239 tokens, so without them Tok would be 1871. Python pays 210 for its imports and would be 1796 without them. Type annotations cost 23 and effect lists 0, because few references still declare a function or a typed empty collection.

The defaults are not waste: a Python reference that does `int(sys.argv[1])` stops with a traceback on a missing or malformed argument, and the Tok reference prints a defined answer. The tasks never test that, so the benchmark gives Tok no credit for it.

What the worst tasks above still lack, read from the references themselves: a ready-made counter with `most_common` (`stdin_word_freq`), ranges as values and comprehensions with a condition (`primes_upto`, `range_sum`, `perfect_number`), a library `commonprefix` (`common_prefix`), character codes `ord` / `chr` (`caesar`), `accumulate` and `zip` (`running_sum`, `dot_product`), unpacking a split or a generator into names (`triangle`, `stdin_passing`), `print(*xs)` against `.join(" ")`, and negative indexes (`second_largest`).

## Indent width (Tok references, all tasks)

| Tokenizer | 2 spaces | 4 spaces | Difference |
|---|---|---|---|
| hf Qwen/Qwen2.5-Coder-7B-Instruct | 2125 | 2125 | +0 (+0.00%) |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | 2584 | 2584 | +0 (+0.00%) |
| hf openai-community/gpt2 | 2460 | 2564 | +104 (+4.23%) |
| tiktoken cl100k_base | 2108 | 2108 | +0 (+0.00%) |
| tiktoken o200k_base | 2110 | 2110 | +0 (+0.00%) |

## Learn pack (`tokc learn`)

The guide now also teaches script form, the conditional expression, `while`, multiple assignment and dicts.

| Tokenizer | Full, original | Full, built-ins | Full, now | Core, original | Core, built-ins | Core, now | --budget 2000, built-ins | --budget 2000, now |
|---|---|---|---|---|---|---|---|---|
| hf Qwen/Qwen2.5-Coder-7B-Instruct | 2754 | 3199 | 3206 | 1248 | 1550 | 1720 | 1892 | 1881 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | 3189 | 3725 | 3753 | 1459 | 1823 | 2010 | 2225 | 2197 |
| hf openai-community/gpt2 | 3056 | 3556 | 3551 | 1351 | 1693 | 1869 | 2061 | 2031 |
| tiktoken cl100k_base | 2707 | 3147 | 3158 | 1242 | 1541 | 1711 | 1867 | 1858 |
| tiktoken o200k_base | 2709 | 3150 | 3159 | 1243 | 1543 | 1714 | 1870 | 1861 |

The guide is 9463 characters; `--budget` counts characters / 3 (3155 for the full guide, 1794 for the core), which is an approximation. The core (the four prose sections, with no examples) is 1714 tokens on tiktoken o200k_base and fits `--budget 2000` as the compiler counts it. It has not fitted `--budget 1500` since the built-ins.

## Not measured

- Anthropic and Gemini tokenizers: not available offline; their counts come from the API usage fields during a benchmark run.
- Tokens-to-green with a real model: not run. Nothing here shows whether a model writes the shorter Tok on its first try.
