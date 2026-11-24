#!/usr/bin/env python3
"""Static token measurements with real tokenizers. Run with the project venv:

  .venv/bin/python bench/count_tokens.py [--write]

Counts the reference solutions per language, the Tok references at 2-space and 4-space indent, and the learn
pack. Tokenizers: tiktoken's o200k_base and cl100k_base (OpenAI), plus any Hugging Face tokenizer in HF_MODELS
that can be downloaded. Anthropic and Gemini do not publish an offline tokenizer, so they are not measured here.
--write saves bench/RESULTS.md and bench/results.json. Nothing is estimated: a tokenizer that cannot be loaded
is listed as unavailable.
"""
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness  # noqa: E402
from tok import learn  # noqa: E402

HF_MODELS = ["Qwen/Qwen2.5-Coder-7B-Instruct", "deepseek-ai/deepseek-coder-6.7b-instruct", "openai-community/gpt2"]


def tokenizers():
    out, missing = {}, {}
    try:
        import tiktoken
        for name in ("o200k_base", "cl100k_base"):
            try:
                enc = tiktoken.get_encoding(name)
                out[f"tiktoken {name}"] = lambda s, enc=enc: len(enc.encode(s))
            except Exception as e:
                missing[f"tiktoken {name}"] = f"{type(e).__name__}: {e}"[:200]
    except ImportError as e:
        missing["tiktoken"] = str(e)
    try:
        from tokenizers import Tokenizer
        for model in HF_MODELS:
            try:
                tk = Tokenizer.from_pretrained(model)
                out[f"hf {model}"] = lambda s, tk=tk: len(tk.encode(s, add_special_tokens=False).ids)
            except Exception as e:
                missing[f"hf {model}"] = f"{type(e).__name__}: {e}"[:200]
    except ImportError as e:
        missing["tokenizers"] = str(e)
    return out, missing


def reindent(src, width):
    """Tok source with each 2-space indent level rewritten to `width` spaces."""
    out = []
    for line in src.split("\n"):
        body = line.lstrip(" ")
        out.append(" " * ((len(line) - len(body)) // 2 * width) + body)
    return "\n".join(out)


def indent_share(src):
    lines = src.split("\n")
    return sum(len(l) - len(l.lstrip(" ")) for l in lines)


def main(argv):
    toks, missing = tokenizers()
    tasks = harness.load_tasks()
    refs = {lang: [harness.reference(t, lang) for t in tasks] for lang in harness.LANGS}
    res = {"tasks": len(tasks), "tokenizers": sorted(toks), "unavailable": missing, "languages": {}, "ratio": {}, "indent": {},
           "learn": {}, "bytes": {lang: sum(len(s.encode()) for s in refs[lang]) for lang in refs},
           "lines": {lang: sum(s.count("\n") for s in refs[lang]) for lang in refs}}
    for name, count in toks.items():
        per = {lang: [count(s) for s in refs[lang]] for lang in refs}
        res["languages"][name] = {lang: {"total": sum(v), "mean": statistics.mean(v), "median": statistics.median(v)}
                                  for lang, v in per.items()}
        ratios = [a / b for a, b in zip(per["tok"], per["python"])]
        res["ratio"][name] = {"tok_over_python_total": sum(per["tok"]) / sum(per["python"]),
                              "median_per_task": statistics.median(ratios), "min_per_task": min(ratios),
                              "max_per_task": max(ratios), "tasks_where_tok_is_smaller": sum(1 for r in ratios if r < 1),
                              "tasks_equal": sum(1 for r in ratios if r == 1)}
        two = sum(per["tok"])
        four = sum(count(reindent(s, 4)) for s in refs["tok"])
        res["indent"][name] = {"tok_2_space": two, "tok_4_space": four, "difference": four - two,
                               "percent": 100.0 * (four - two) / two}
        guide = learn.render()
        res["learn"][name] = {"full": count(guide), "budget_1500": count(learn.render(1500)),
                              "core_only": count(learn.render(1))}
    res["learn_chars"] = {"full": len(learn.render()), "approx_tokens_chars_over_3": learn.approx_tokens(learn.render())}
    res["tok_indent_chars"] = sum(indent_share(s) for s in refs["tok"])
    print(json.dumps(res, indent=1))
    if "--write" in argv:
        with open(os.path.join(harness.HERE, "results.json"), "w") as fh:
            json.dump(res, fh, indent=1)
            fh.write("\n")
        with open(os.path.join(harness.HERE, "RESULTS.md"), "w") as fh:
            fh.write(markdown(res))
    return 0 if toks else 1


def markdown(r):
    out = ["# Static token measurements", "",
           f"Measured by `bench/count_tokens.py` over the {r['tasks']} reference solutions in `bench/tasks/`. "
           "These are counts of hand-written reference programs, not of model output, and say nothing yet about "
           "tokens-to-green.", "",
           f"Source size: Tok {r['bytes']['tok']} bytes / {r['lines']['tok']} lines, "
           f"Python {r['bytes']['python']} bytes / {r['lines']['python']} lines.", "",
           "## Reference solutions, total tokens", "",
           "| Tokenizer | Tok | Python | Tok / Python | Median per task | Tasks where Tok is smaller |", "|---|---|---|---|---|---|"]
    for name in r["tokenizers"]:
        l, q = r["languages"][name], r["ratio"][name]
        out.append(f"| {name} | {l['tok']['total']} | {l['python']['total']} | {q['tok_over_python_total']:.3f} | "
                   f"{q['median_per_task']:.3f} | {q['tasks_where_tok_is_smaller']} of {r['tasks']} |")
    out += ["", "## Indent width (Tok references)", "", "| Tokenizer | 2 spaces | 4 spaces | Difference |", "|---|---|---|---|"]
    for name in r["tokenizers"]:
        i = r["indent"][name]
        out.append(f"| {name} | {i['tok_2_space']} | {i['tok_4_space']} | {i['difference']:+d} ({i['percent']:+.2f}%) |")
    out += ["", "## Learn pack (`tokc learn`)", "", "| Tokenizer | Full | --budget 1500 | Core only |", "|---|---|---|---|"]
    for name in r["tokenizers"]:
        g = r["learn"][name]
        out.append(f"| {name} | {g['full']} | {g['budget_1500']} | {g['core_only']} |")
    out += ["", f"The guide is {r['learn_chars']['full']} characters; `--budget` counts characters / 3 "
                f"({r['learn_chars']['approx_tokens_chars_over_3']} for the full guide), which is an approximation.", ""]
    if r["unavailable"]:
        out += ["## Not measured", ""] + [f"- {k}: {v}" for k, v in sorted(r["unavailable"].items())]
        out.append("- Anthropic and Gemini tokenizers: not available offline; their counts come from the API usage "
                   "fields during a benchmark run.")
        out.append("")
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
