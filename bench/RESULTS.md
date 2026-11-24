# Static token measurements

Measured by `bench/count_tokens.py` over the 40 reference solutions in `bench/tasks/`. These are counts of hand-written reference programs, not of model output, and say nothing yet about tokens-to-green.

Source size: Tok 7948 bytes / 340 lines, Python 4691 bytes / 234 lines.

## Reference solutions, total tokens

| Tokenizer | Tok | Python | Tok / Python | Median per task | Tasks where Tok is smaller |
|---|---|---|---|---|---|
| hf Qwen/Qwen2.5-Coder-7B-Instruct | 3103 | 1555 | 1.995 | 2.031 | 1 of 40 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | 3566 | 1990 | 1.792 | 1.699 | 2 of 40 |
| hf openai-community/gpt2 | 3642 | 2153 | 1.692 | 1.736 | 4 of 40 |
| tiktoken cl100k_base | 3089 | 1544 | 2.001 | 2.031 | 1 of 40 |
| tiktoken o200k_base | 3092 | 1552 | 1.992 | 2.013 | 1 of 40 |

## Indent width (Tok references)

| Tokenizer | 2 spaces | 4 spaces | Difference |
|---|---|---|---|
| hf Qwen/Qwen2.5-Coder-7B-Instruct | 3103 | 3103 | +0 (+0.00%) |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | 3566 | 3566 | +0 (+0.00%) |
| hf openai-community/gpt2 | 3642 | 4400 | +758 (+20.81%) |
| tiktoken cl100k_base | 3089 | 3089 | +0 (+0.00%) |
| tiktoken o200k_base | 3092 | 3092 | +0 (+0.00%) |

## Learn pack (`tokc learn`)

| Tokenizer | Full | --budget 1500 | Core only |
|---|---|---|---|
| hf Qwen/Qwen2.5-Coder-7B-Instruct | 2754 | 1420 | 1248 |
| hf deepseek-ai/deepseek-coder-6.7b-instruct | 3189 | 1657 | 1459 |
| hf openai-community/gpt2 | 3056 | 1526 | 1351 |
| tiktoken cl100k_base | 2707 | 1400 | 1242 |
| tiktoken o200k_base | 2709 | 1401 | 1243 |

The guide is 8017 characters; `--budget` counts characters / 3 (2673 for the full guide), which is an approximation.
