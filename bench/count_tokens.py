#!/usr/bin/env python3
"""Static token measurements with real tokenizers. Run with the project venv:

  .venv/bin/python bench/count_tokens.py [--write]

Counts the reference solutions per language (the 40 original tasks and the added stdin and string tasks apart),
compares the originals with bench/baseline.json (the same 40 tasks before the built-ins of revision cbd887844af6),
measures where the remaining difference comes from, the Tok references at 2-space and 4-space indent, and the learn
pack. Tokenizers: tiktoken's o200k_base and cl100k_base (OpenAI), plus any Hugging Face tokenizer in HF_MODELS
that can be downloaded. Anthropic and Gemini do not publish an offline tokenizer, so they are not measured here.
--write saves bench/RESULTS.md and bench/results.json. Nothing is estimated: a tokenizer that cannot be loaded
is listed as unavailable.
"""
import json
import os
import re
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


GROUPS = ("original", "added", "all")


def strip_effects(src):
    """Tok without effect lists: `fn main() ! io` becomes `fn main()`."""
    return re.sub(r"^(fn .*?) ! [a-z, ]+$", r"\1", src, flags=re.M)


def strip_main(src):
    """Tok with the body of main at the top level, as in a script: the `fn main` line removed, its body dedented."""
    out, inside = [], False
    for line in src.split("\n"):
        if line.startswith("fn main("):
            inside = True
            continue
        if inside and line and not line.startswith("  "):
            inside = False
        out.append(line[2:] if inside else line)
    return "\n".join(out)


def strip_defaults(src):
    """Tok without the explicit defaults of a lookup or a parse: `args[1].to_int() or 0` becomes `args[1].to_int()`.
    The Python references have no counterpart: they stop with a traceback on a missing or malformed argument."""
    src = re.sub(r"\((args\[\d\]) or \"[^\"]*\"\)", r"\1", src)
    return re.sub(r" or (0\.0|0|1|\"\")(?=[\s),.]|$)", "", src, flags=re.M)


def strip_types(src):
    """Tok without type annotations on parameters, results and bindings (not on `main`, see strip_main)."""
    out = []
    for line in src.split("\n"):
        if line.startswith("fn ") and not line.startswith("fn main("):
            line = re.sub(r": \[?[a-z0-9]+\]?\??", "", line)
            line = re.sub(r" -> \[?[a-z0-9]+\]?\??", "", line)
        elif re.match(r"\s+(var )?\w+: ", line):
            line = re.sub(r"^(\s+(?:var )?\w+): [^=]+ =", r"\1 =", line)
        out.append(line)
    return "\n".join(out)


def strip_import(src):
    """Python without its import lines and the blank line after them."""
    lines = [l for l in src.split("\n") if not re.match(r"(import|from) ", l)]
    while lines and not lines[0]:
        lines.pop(0)
    return "\n".join(lines)


def group_stats(per, n):
    ratios = [a / b for a, b in zip(per["tok"], per["python"])]
    return {"tasks": n, "tok": sum(per["tok"]), "python": sum(per["python"]),
            "tok_over_python_total": sum(per["tok"]) / sum(per["python"]), "median_per_task": statistics.median(ratios),
            "min_per_task": min(ratios), "max_per_task": max(ratios),
            "tasks_where_tok_is_smaller": sum(1 for r in ratios if r < 1), "tasks_equal": sum(1 for r in ratios if r == 1)}


def main(argv):
    toks, missing = tokenizers()
    tasks = harness.load_tasks()
    with open(os.path.join(harness.HERE, "baseline.json")) as fh:
        base = json.load(fh)
    refs = {t["id"]: {lang: harness.reference(t, lang) for lang in harness.LANGS} for t in tasks}
    ids = {"original": [t["id"] for t in tasks if t["group"] == "original"],
           "added": [t["id"] for t in tasks if t["group"] == "added"], "all": [t["id"] for t in tasks]}
    assert sorted(ids["original"]) == sorted(base["tasks"]), "the original tasks must be the ones in baseline.json"
    res = {"tasks": {g: len(ids[g]) for g in GROUPS}, "tokenizers": sorted(toks), "unavailable": missing,
           "baseline_revision": base["revision"], "groups": {}, "before": {}, "per_task": {}, "sources": {}, "indent": {},
           "learn": {}, "size": {}}
    for g in GROUPS:
        res["size"][g] = {lang: {"bytes": sum(len(refs[i][lang].encode()) for i in ids[g]),
                                 "lines": sum(refs[i][lang].count("\n") for i in ids[g])} for lang in harness.LANGS}
    res["size"]["before"] = {lang: {"bytes": base["bytes"][lang], "lines": base["lines"][lang]} for lang in harness.LANGS}
    for name, count in toks.items():
        now = {i: {lang: count(refs[i][lang]) for lang in harness.LANGS} for i in refs}
        res["per_task"][name] = now
        res["groups"][name] = {g: group_stats({lang: [now[i][lang] for i in ids[g]] for lang in harness.LANGS}, len(ids[g]))
                               for g in GROUPS}
        res["before"][name] = group_stats({lang: [base["tasks"][i][lang][name] for i in ids["original"]]
                                           for lang in harness.LANGS}, len(ids["original"]))
        src = {}
        for g in GROUPS:
            tok, py = [refs[i]["tok"] for i in ids[g]], [refs[i]["python"] for i in ids[g]]
            total = lambda f, xs: sum(count(f(x)) for x in xs)  # noqa: E731
            t0, p0 = total(str, tok), total(str, py)
            src[g] = {"tok": t0, "python": p0, "gap": t0 - p0,
                      "tok_effect_lists": t0 - total(strip_effects, tok),
                      "tok_main_line": t0 - total(strip_main, tok),
                      "tok_defaults": t0 - total(strip_defaults, tok),
                      "tok_type_annotations": t0 - total(strip_types, tok),
                      "python_imports": p0 - total(strip_import, py),
                      "tok_without_main_and_defaults": total(lambda x: strip_defaults(strip_main(x)), tok),
                      "python_without_imports": total(strip_import, py)}
        res["sources"][name] = src
        tok_all = [refs[i]["tok"] for i in ids["all"]]
        two, four = sum(count(x) for x in tok_all), sum(count(reindent(x, 4)) for x in tok_all)
        res["indent"][name] = {"tok_2_space": two, "tok_4_space": four, "difference": four - two,
                               "percent": 100.0 * (four - two) / two}
        res["learn"][name] = {"full": count(learn.render()), "budget_2000": count(learn.render(2000)),
                              "core_only": count(learn.render(1))}
    res["learn_chars"] = {"full": len(learn.render()), "approx_tokens_chars_over_3": learn.approx_tokens(learn.render())}
    res["tok_indent_chars"] = sum(indent_share(refs[i]["tok"]) for i in refs)
    print(json.dumps({k: v for k, v in res.items() if k != "per_task"}, indent=1))
    if "--write" in argv:
        with open(os.path.join(harness.HERE, "results.json"), "w") as fh:
            json.dump(res, fh, indent=1)
            fh.write("\n")
        with open(os.path.join(harness.HERE, "RESULTS.md"), "w") as fh:
            fh.write(markdown(res, base))
    return 0 if toks else 1


LEARN_BEFORE = {"tiktoken o200k_base": (2709, 1243), "tiktoken cl100k_base": (2707, 1242),
                "hf Qwen/Qwen2.5-Coder-7B-Instruct": (2754, 1248), "hf deepseek-ai/deepseek-coder-6.7b-instruct": (3189, 1459),
                "hf openai-community/gpt2": (3056, 1351)}  # full guide, core only, as recorded at the baseline revision
MAIN_TOKENIZER = "tiktoken o200k_base"


def bottom_line(r):
    b, g = r["before"][MAIN_TOKENIZER], r["groups"][MAIN_TOKENIZER]
    o, a, t = g["original"], g["added"], g["all"]
    return (f"On {MAIN_TOKENIZER} the built-ins took the 40 original tasks from {b['tok_over_python_total']:.2f}x Python's "
            f"tokens to {o['tok_over_python_total']:.2f}x. On the {a['tasks']} added tasks, where Python's own built-ins do "
            f"most of the work, Tok is {a['tok_over_python_total']:.2f}x, and over all {t['tasks']} it is "
            f"{t['tok_over_python_total']:.2f}x. Tok is smaller than Python on {t['tasks_where_tok_is_smaller']} of "
            f"{t['tasks']} tasks and equal on {t['tasks_equal']}.")


def markdown(r, base):
    n = r["tasks"]
    out = ["# Static token measurements", "",
           f"Measured by `bench/count_tokens.py` over the reference solutions in `bench/tasks/`: the {n['original']} original "
           f"tasks and {n['added']} added ones (12 that read standard input, 12 on strings), {n['all']} in all. These are "
           "counts of hand-written reference programs, not of model output, and say nothing yet about tokens-to-green. "
           "No model was called.", "",
           "**Bottom line: Tok is still larger than Python.** " + bottom_line(r), "",
           "## Before and after, the 40 original tasks", "",
           f"Before = revision `{r['baseline_revision']}` (no sort, slices, power, checked chains, string indexing, stdin). "
           "After = this revision, with the Tok references rewritten to use the built-ins. Eight Python references were "
           "also tightened to the built-in a Python developer would use (`print(*xs)`, `dict.fromkeys`, `accumulate`, "
           "`max(key=)`), which is why Python's total moved too.", "",
           "| Tokenizer | Tok before | Python before | Ratio before | Tok after | Python after | Ratio after | Tok smaller, before | Tok smaller, after |",
           "|---|---|---|---|---|---|---|---|---|"]
    for name in r["tokenizers"]:
        b, a = r["before"][name], r["groups"][name]["original"]
        out.append(f"| {name} | {b['tok']} | {b['python']} | {b['tok_over_python_total']:.3f} | {a['tok']} | {a['python']} | "
                   f"{a['tok_over_python_total']:.3f} | {b['tasks_where_tok_is_smaller']} of {b['tasks']} | "
                   f"{a['tasks_where_tok_is_smaller']} of {a['tasks']} |")
    s = r["size"]
    out += ["", f"Source size of the 40 originals: Tok {s['before']['tok']['bytes']} bytes / {s['before']['tok']['lines']} lines "
                f"before, {s['original']['tok']['bytes']} bytes / {s['original']['tok']['lines']} lines after; Python "
                f"{s['before']['python']['bytes']} bytes / {s['before']['python']['lines']} lines before, "
                f"{s['original']['python']['bytes']} bytes / {s['original']['python']['lines']} lines after.", "",
            "## Originals and added tasks, this revision", "",
            "| Tokenizer | Group | Tasks | Tok | Python | Tok / Python | Median per task | Tok smaller | Equal |", "|---|---|---|---|---|---|---|---|---|"]
    for name in r["tokenizers"]:
        for g in GROUPS:
            a = r["groups"][name][g]
            out.append(f"| {name} | {g} | {a['tasks']} | {a['tok']} | {a['python']} | {a['tok_over_python_total']:.3f} | "
                       f"{a['median_per_task']:.3f} | {a['tasks_where_tok_is_smaller']} | {a['tasks_equal']} |")
    per = r["per_task"][MAIN_TOKENIZER]
    group_of = {t["id"]: t["group"] for t in harness.load_tasks()}
    rows = sorted(per, key=lambda i: per[i]["python"] - per[i]["tok"])
    out += ["", f"## Worst remaining tasks ({MAIN_TOKENIZER})", "", "The 15 tasks with the largest Tok minus Python difference.", "",
            "| Task | Group | Tok | Python | Difference | Tok before |", "|---|---|---|---|---|---|"]
    for i in rows[:15]:
        before = base["tasks"].get(i, {}).get("tok", {}).get(MAIN_TOKENIZER, "")
        out.append(f"| {i} | {group_of[i]} | {per[i]['tok']} | {per[i]['python']} | {per[i]['tok'] - per[i]['python']:+d} | {before} |")
    best = [i for i in reversed(rows) if per[i]["tok"] <= per[i]["python"]]
    out += ["", "Tasks where Tok is not larger: " +
            (", ".join(f"{i} ({per[i]['tok']} vs {per[i]['python']})" for i in best) or "none") + ".", "",
            "## Where the remaining difference comes from", "",
            "Each column is measured by deleting that construct from every Tok reference and counting again (the "
            "transformations are in `count_tokens.py`; the stripped programs are not valid Tok). The columns overlap a "
            "little, so they do not add up exactly. Python's import lines are the cost on its side of the same ledger.", "",
            "| Tokenizer | Group | Gap (Tok - Python) | `fn main(...) ! io` line | of which effect lists | `or` defaults | Type annotations | Python imports |",
            "|---|---|---|---|---|---|---|---|"]
    for name in r["tokenizers"]:
        for g in GROUPS:
            x = r["sources"][name][g]
            out.append(f"| {name} | {g} | {x['gap']:+d} | {x['tok_main_line']} | {x['tok_effect_lists']} | {x['tok_defaults']} | "
                       f"{x['tok_type_annotations']} | {x['python_imports']} |")
    x = r["sources"][MAIN_TOKENIZER]["all"]
    out += ["", f"Read on {MAIN_TOKENIZER}, all {n['all']} tasks: Tok is {x['tok']} tokens and Python {x['python']}. Without the "
                f"`fn main` line and the explicit defaults Tok would be {x['tok_without_main_and_defaults']}; Python without "
                f"its imports would be {x['python_without_imports']}. The `fn main(args: [str]) ! io` line alone costs "
                f"{x['tok_main_line']} tokens over {n['all']} programs, against {x['python_imports']} for Python's imports: it "
                "is the largest single source, larger than everything more built-ins could still remove. What is left after "
                "the entry-point line and the defaults is the absence of a conditional expression (`\"yes\" if c else "
                "\"no\"` is four lines in Tok), of multiple assignment, of `while`, of dicts (`Counter`), of character "
                "codes (`ord` / `chr`), of ranges as values (`sum(range(a, b))`), and typed helper functions.", "",
            "The defaults are not waste: a Python reference that does `int(sys.argv[1])` stops with a traceback on a "
            "missing or malformed argument, and the Tok reference prints a defined answer. The tasks never test that, so "
            "the benchmark gives Tok no credit for it.", "",
            "## Indent width (Tok references, all tasks)", "", "| Tokenizer | 2 spaces | 4 spaces | Difference |", "|---|---|---|---|"]
    for name in r["tokenizers"]:
        i = r["indent"][name]
        out.append(f"| {name} | {i['tok_2_space']} | {i['tok_4_space']} | {i['difference']:+d} ({i['percent']:+.2f}%) |")
    out += ["", "## Learn pack (`tokc learn`)", "",
            "The guide grew with the built-ins. Before = the baseline revision.", "",
            "| Tokenizer | Full, before | Full, after | Core only, before | Core only, after | --budget 2000 |", "|---|---|---|---|---|---|"]
    for name in r["tokenizers"]:
        g, b = r["learn"][name], LEARN_BEFORE.get(name, ("", ""))
        out.append(f"| {name} | {b[0]} | {g['full']} | {b[1]} | {g['core_only']} | {g['budget_2000']} |")
    out += ["", f"The guide is {r['learn_chars']['full']} characters (8017 before); `--budget` counts characters / 3 "
                f"({r['learn_chars']['approx_tokens_chars_over_3']} for the full guide), which is an approximation. The core "
                "no longer fits `--budget 1500`, so the smallest useful budget is now 2000.", ""]
    out += ["## Not measured", ""] + [f"- {k}: {v}" for k, v in sorted(r["unavailable"].items())]
    out += ["- Anthropic and Gemini tokenizers: not available offline; their counts come from the API usage fields during a "
            "benchmark run.",
            "- Tokens-to-green with a real model: not run. Nothing here shows whether a model writes the shorter Tok on its "
            "first try.", ""]
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
