# Static token measurements

Measured by `bench/count_tokens.py` over the reference solutions in `bench/tasks/`: the 40 original tasks and 24 added ones (12 that read standard input, 12 on strings), 64 in all. These are counts of hand-written reference programs, not of model output, and say nothing yet about tokens-to-green. No model was called.

**Bottom line: Tok is still larger than Python.** On tiktoken o200k_base the built-ins took the 40 original tasks from 1.99x Python's tokens to 1.47x. On the 24 added tasks, where Python's own built-ins do most of the work, Tok is 1.70x, and over all 64 it is 1.53x. Tok is smaller than Python on 3 of 64 tasks and equal on 1.

## Before and after, the 40 original tasks

Before = revision `144c8d94e6c9` (no sort, slices, power, checked chains, string indexing, stdin). After = this revision, with the Tok references rewritten to use the built-ins. Eight Python references were also tightened to the built-in a Python developer would use (`print(*xs)`, `dict.fromkeys`, `accumulate`, `max(key=)`), which is why Python's total moved too.

| Tokenizer | Tok before | Python before | Ratio before | Tok after | Python after | Ratio after | Tok smaller, before | Tok smaller, after |
|---|---|---|---|---|---|---|---|---|
| hf Qwen/Qwen2.5-Coder-7B-Instruct | 3103 | 1555 | 1.995 | 2137 | 1453 | 1.471 | 1 of 40 | 3 of 40 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | 3566 | 1990 | 1.792 | 2490 | 1860 | 1.339 | 2 of 40 | 4 of 40 |
| hf openai-community/gpt2 | 3642 | 2153 | 1.692 | 2509 | 1990 | 1.261 | 4 of 40 | 6 of 40 |
| tiktoken cl100k_base | 3089 | 1544 | 2.001 | 2126 | 1442 | 1.474 | 1 of 40 | 3 of 40 |
| tiktoken o200k_base | 3092 | 1552 | 1.992 | 2128 | 1448 | 1.470 | 1 of 40 | 3 of 40 |

Source size of the 40 originals: Tok 7948 bytes / 340 lines before, 5560 bytes / 203 lines after; Python 4691 bytes / 234 lines before, 4357 bytes / 216 lines after.

## Originals and added tasks, this revision

| Tokenizer | Group | Tasks | Tok | Python | Tok / Python | Median per task | Tok smaller | Equal |
|---|---|---|---|---|---|---|---|---|
| hf Qwen/Qwen2.5-Coder-7B-Instruct | original | 40 | 2137 | 1453 | 1.471 | 1.559 | 3 | 0 |
| hf Qwen/Qwen2.5-Coder-7B-Instruct | added | 24 | 954 | 563 | 1.694 | 1.611 | 0 | 0 |
| hf Qwen/Qwen2.5-Coder-7B-Instruct | all | 64 | 3091 | 2016 | 1.533 | 1.566 | 3 | 0 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | original | 40 | 2490 | 1860 | 1.339 | 1.444 | 4 | 0 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | added | 24 | 1161 | 780 | 1.488 | 1.380 | 0 | 2 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | all | 64 | 3651 | 2640 | 1.383 | 1.441 | 4 | 2 |
| hf openai-community/gpt2 | original | 40 | 2509 | 1990 | 1.261 | 1.406 | 6 | 0 |
| hf openai-community/gpt2 | added | 24 | 1189 | 798 | 1.490 | 1.352 | 1 | 4 |
| hf openai-community/gpt2 | all | 64 | 3698 | 2788 | 1.326 | 1.381 | 7 | 4 |
| tiktoken cl100k_base | original | 40 | 2126 | 1442 | 1.474 | 1.568 | 3 | 0 |
| tiktoken cl100k_base | added | 24 | 948 | 557 | 1.702 | 1.611 | 0 | 0 |
| tiktoken cl100k_base | all | 64 | 3074 | 1999 | 1.538 | 1.568 | 3 | 0 |
| tiktoken o200k_base | original | 40 | 2128 | 1448 | 1.470 | 1.568 | 3 | 1 |
| tiktoken o200k_base | added | 24 | 948 | 558 | 1.699 | 1.611 | 0 | 0 |
| tiktoken o200k_base | all | 64 | 3076 | 2006 | 1.533 | 1.568 | 3 | 1 |

## Worst remaining tasks (tiktoken o200k_base)

The 15 tasks with the largest Tok minus Python difference.

| Task | Group | Tok | Python | Difference | Tok before |
|---|---|---|---|---|---|
| common_prefix | added | 70 | 17 | +53 |  |
| primes_upto | original | 102 | 50 | +52 | 113 |
| stdin_word_freq | added | 78 | 32 | +46 |  |
| triangle | original | 131 | 89 | +42 | 161 |
| caesar | added | 86 | 48 | +38 |  |
| collatz | original | 90 | 56 | +34 | 95 |
| running_sum | original | 57 | 24 | +33 | 65 |
| gcd | original | 68 | 36 | +32 | 88 |
| range_sum | original | 68 | 36 | +32 | 78 |
| perfect_number | original | 80 | 50 | +30 | 85 |
| dot_product | original | 75 | 47 | +28 | 112 |
| fibonacci | original | 63 | 37 | +26 | 73 |
| is_prime | original | 100 | 74 | +26 | 110 |
| second_largest | original | 62 | 37 | +25 | 114 |
| make_change | original | 69 | 45 | +24 | 75 |

Tasks where Tok is not larger: sum_file (53 vs 79), file_stats (51 vs 54), csv_sum (37 vs 40), count_errors (41 vs 41).

## Where the remaining difference comes from

Each column is measured by deleting that construct from every Tok reference and counting again (the transformations are in `count_tokens.py`; the stripped programs are not valid Tok). The columns overlap a little, so they do not add up exactly. Python's import lines are the cost on its side of the same ledger.

| Tokenizer | Group | Gap (Tok - Python) | `fn main(...) ! io` line | of which effect lists | `or` defaults | Type annotations | Python imports |
|---|---|---|---|---|---|---|---|
| hf Qwen/Qwen2.5-Coder-7B-Instruct | original | +684 | 488 | 128 | 166 | 55 | 125 |
| hf Qwen/Qwen2.5-Coder-7B-Instruct | added | +391 | 248 | 72 | 65 | 0 | 85 |
| hf Qwen/Qwen2.5-Coder-7B-Instruct | all | +1075 | 736 | 200 | 231 | 55 | 210 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | original | +630 | 529 | 88 | 163 | 56 | 168 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | added | +381 | 266 | 48 | 70 | 0 | 111 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | all | +1011 | 795 | 136 | 233 | 56 | 279 |
| hf openai-community/gpt2 | original | +519 | 603 | 88 | 113 | 50 | 167 |
| hf openai-community/gpt2 | added | +391 | 312 | 48 | 61 | 0 | 109 |
| hf openai-community/gpt2 | all | +910 | 915 | 136 | 174 | 50 | 276 |
| tiktoken cl100k_base | original | +684 | 488 | 128 | 166 | 55 | 125 |
| tiktoken cl100k_base | added | +391 | 248 | 72 | 65 | 0 | 85 |
| tiktoken cl100k_base | all | +1075 | 736 | 200 | 231 | 55 | 210 |
| tiktoken o200k_base | original | +680 | 488 | 128 | 166 | 55 | 125 |
| tiktoken o200k_base | added | +390 | 248 | 72 | 65 | 0 | 85 |
| tiktoken o200k_base | all | +1070 | 736 | 200 | 231 | 55 | 210 |

Read on tiktoken o200k_base, all 64 tasks: Tok is 3076 tokens and Python 2006. Without the `fn main` line and the explicit defaults Tok would be 2109; Python without its imports would be 1796. The `fn main(args: [str]) ! io` line alone costs 736 tokens over 64 programs, against 210 for Python's imports: it is the largest single source, larger than everything more built-ins could still remove. What is left after the entry-point line and the defaults is the absence of a conditional expression (`"yes" if c else "no"` is four lines in Tok), of multiple assignment, of `while`, of dicts (`Counter`), of character codes (`ord` / `chr`), of ranges as values (`sum(range(a, b))`), and typed helper functions.

The defaults are not waste: a Python reference that does `int(sys.argv[1])` stops with a traceback on a missing or malformed argument, and the Tok reference prints a defined answer. The tasks never test that, so the benchmark gives Tok no credit for it.

## Indent width (Tok references, all tasks)

| Tokenizer | 2 spaces | 4 spaces | Difference |
|---|---|---|---|
| hf Qwen/Qwen2.5-Coder-7B-Instruct | 3091 | 3091 | +0 (+0.00%) |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | 3651 | 3651 | +0 (+0.00%) |
| hf openai-community/gpt2 | 3698 | 4320 | +622 (+16.82%) |
| tiktoken cl100k_base | 3074 | 3074 | +0 (+0.00%) |
| tiktoken o200k_base | 3076 | 3076 | +0 (+0.00%) |

## Learn pack (`tokc learn`)

The guide grew with the built-ins. Before = the baseline revision.

| Tokenizer | Full, before | Full, after | Core only, before | Core only, after | --budget 2000 |
|---|---|---|---|---|---|
| hf Qwen/Qwen2.5-Coder-7B-Instruct | 2754 | 3199 | 1248 | 1550 | 1892 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | 3189 | 3725 | 1459 | 1823 | 2225 |
| hf openai-community/gpt2 | 3056 | 3556 | 1351 | 1693 | 2061 |
| tiktoken cl100k_base | 2707 | 3147 | 1242 | 1541 | 1867 |
| tiktoken o200k_base | 2709 | 3150 | 1243 | 1543 | 1870 |

The guide is 9331 characters (8017 before); `--budget` counts characters / 3 (3111 for the full guide), which is an approximation. The core no longer fits `--budget 1500`, so the smallest useful budget is now 2000.

## Not measured

- Anthropic and Gemini tokenizers: not available offline; their counts come from the API usage fields during a benchmark run.
- Tokens-to-green with a real model: not run. Nothing here shows whether a model writes the shorter Tok on its first try.
