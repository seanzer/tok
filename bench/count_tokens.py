#!/usr/bin/env python3
"""Static token measurements with real tokenizers. Run with the project venv:

  .venv/bin/python bench/count_tokens.py [--write]

Counts the reference solutions per language (the 40 original tasks and the added stdin and string tasks apart),
compares them with two earlier points (bench/baseline.json: the 40 originals before the built-ins;
bench/baseline_builtins.json: all 64 after the built-ins, before script form and the other syntax additions),
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
        elif re.match(r"\s*(var )?\w+: ", line):
            line = re.sub(r"^(\s*(?:var )?\w+): [^=]+ =", r"\1 =", line)
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
    with open(os.path.join(harness.HERE, "baseline_builtins.json")) as fh:
        mid = json.load(fh)
    refs = {t["id"]: {lang: harness.reference(t, lang) for lang in harness.LANGS} for t in tasks}
    ids = {"original": [t["id"] for t in tasks if t["group"] == "original"],
           "added": [t["id"] for t in tasks if t["group"] == "added"], "all": [t["id"] for t in tasks]}
    assert sorted(ids["original"]) == sorted(base["tasks"]), "the original tasks must be the ones in baseline.json"
    assert sorted(ids["all"]) == sorted(mid["tasks"]), "the tasks must be the ones in baseline_builtins.json"
    res = {"tasks": {g: len(ids[g]) for g in GROUPS}, "tokenizers": sorted(toks), "unavailable": missing,
           "baseline_revision": base["revision"], "builtins_revision": mid["revision"], "groups": {}, "before": {},
           "builtins": {}, "per_task": {}, "sources": {}, "indent": {}, "learn": {}, "size": {}}
    for g in GROUPS:
        res["size"][g] = {lang: {"bytes": sum(len(refs[i][lang].encode()) for i in ids[g]),
                                 "lines": sum(refs[i][lang].count("\n") for i in ids[g])} for lang in harness.LANGS}
    res["size"]["before"] = {lang: {"bytes": base["bytes"][lang], "lines": base["lines"][lang]} for lang in harness.LANGS}
    res["size"]["builtins"] = mid["size"]
    res["tok_with_fn_main"] = sum(1 for i in refs if "fn main(" in refs[i]["tok"])
    for name, count in toks.items():
        now = {i: {lang: count(refs[i][lang]) for lang in harness.LANGS} for i in refs}
        res["per_task"][name] = now
        res["groups"][name] = {g: group_stats({lang: [now[i][lang] for i in ids[g]] for lang in harness.LANGS}, len(ids[g]))
                               for g in GROUPS}
        res["before"][name] = group_stats({lang: [base["tasks"][i][lang][name] for i in ids["original"]]
                                           for lang in harness.LANGS}, len(ids["original"]))
        res["builtins"][name] = {g: group_stats({lang: [mid["tasks"][i][lang][name] for i in ids[g]]
                                                 for lang in harness.LANGS}, len(ids[g])) for g in GROUPS}
        src = {}
        for g in GROUPS:
            tok, py = [refs[i]["tok"] for i in ids[g]], [refs[i]["python"] for i in ids[g]]
            total = lambda f, xs: sum(count(f(x)) for x in xs)  # noqa: E731
            t0, p0 = total(str, tok), total(str, py)
            larger = [i for i in ids[g] if now[i]["tok"] > now[i]["python"]]
            smaller = [i for i in ids[g] if now[i]["tok"] < now[i]["python"]]
            src[g] = {"tok": t0, "python": p0, "gap": t0 - p0,
                      "tok_effect_lists": t0 - total(strip_effects, tok),
                      "tok_main_line": t0 - total(strip_main, tok),
                      "tok_defaults": t0 - total(strip_defaults, tok),
                      "tok_type_annotations": t0 - total(strip_types, tok),
                      "python_imports": p0 - total(strip_import, py),
                      "tok_without_defaults": total(strip_defaults, tok),
                      "python_without_imports": total(strip_import, py),
                      "tasks_larger": len(larger), "excess_where_larger": sum(now[i]["tok"] - now[i]["python"] for i in larger),
                      "tasks_smaller": len(smaller), "saving_where_smaller": sum(now[i]["python"] - now[i]["tok"] for i in smaller)}
        res["sources"][name] = src
        tok_all = [refs[i]["tok"] for i in ids["all"]]
        two, four = sum(count(x) for x in tok_all), sum(count(reindent(x, 4)) for x in tok_all)
        res["indent"][name] = {"tok_2_space": two, "tok_4_space": four, "difference": four - two,
                               "percent": 100.0 * (four - two) / two}
        res["learn"][name] = {"full": count(learn.render()), "budget_2000": count(learn.render(2000)),
                              "core_only": count(learn.render(1)), "builtins": mid["learn"].get(name)}
    res["learn_chars"] = {"full": len(learn.render()), "approx_tokens_chars_over_3": learn.approx_tokens(learn.render()),
                          "core_approx_tokens_chars_over_3": learn.approx_tokens(learn.render(1))}
    res["tok_indent_chars"] = sum(indent_share(refs[i]["tok"]) for i in refs)
    print(json.dumps({k: v for k, v in res.items() if k != "per_task"}, indent=1))
    if "--write" in argv:
        with open(os.path.join(harness.HERE, "results.json"), "w") as fh:
            json.dump(res, fh, indent=1)
            fh.write("\n")
        with open(os.path.join(harness.HERE, "RESULTS.md"), "w") as fh:
            fh.write(markdown(res, base, mid))
    return 0 if toks else 1


LEARN_BEFORE = {"tiktoken o200k_base": (2709, 1243), "tiktoken cl100k_base": (2707, 1242),
                "hf Qwen/Qwen2.5-Coder-7B-Instruct": (2754, 1248), "hf deepseek-ai/deepseek-coder-6.7b-instruct": (3189, 1459),
                "hf openai-community/gpt2": (3056, 1351)}  # full guide, core only, as recorded at the baseline revision
MAIN_TOKENIZER = "tiktoken o200k_base"


def bottom_line(r):
    b, m, g = r["before"][MAIN_TOKENIZER], r["builtins"][MAIN_TOKENIZER], r["groups"][MAIN_TOKENIZER]
    o, a, t = g["original"], g["added"], g["all"]
    word = "larger than" if t["tok"] > t["python"] else "smaller than" if t["tok"] < t["python"] else "the same size as"
    return (f"**Bottom line: over the {t['tasks']} tasks the Tok references are {word} Python's, "
            f"{t['tok']} tokens against {t['python']} ({t['tok_over_python_total']:.2f}x) on {MAIN_TOKENIZER}.** "
            f"The 40 original tasks went from {b['tok_over_python_total']:.2f}x Python's tokens (first measurement) to "
            f"{m['original']['tok_over_python_total']:.2f}x (built-ins) to {o['tok_over_python_total']:.2f}x now; the "
            f"{a['tasks']} added tasks from {m['added']['tok_over_python_total']:.2f}x to {a['tok_over_python_total']:.2f}x; "
            f"all {t['tasks']} from {m['all']['tok_over_python_total']:.2f}x to {t['tok_over_python_total']:.2f}x. "
            f"Tok is smaller than Python on {t['tasks_where_tok_is_smaller']} of {t['tasks']} tasks, equal on "
            f"{t['tasks_equal']} and larger on {t['tasks'] - t['tasks_where_tok_is_smaller'] - t['tasks_equal']}. "
            f"The ratio differs by tokenizer; every one is in the tables below.")


def markdown(r, base, mid):
    n = r["tasks"]
    out = ["# Static token measurements", "",
           f"Measured by `bench/count_tokens.py` over the reference solutions in `bench/tasks/`: the {n['original']} original "
           f"tasks and {n['added']} added ones (12 that read standard input, 12 on strings), {n['all']} in all. These are "
           "counts of hand-written reference programs, not of model output, and say nothing yet about tokens-to-green. "
           "No model was called.", "",
           bottom_line(r), "",
           "## Three points in time", "",
           f"1. **Original** = revision `{r['baseline_revision']}`: the first measurement, 40 tasks, before sort, slices, "
           "power, checked chains, string indexing and stdin. The 24 added tasks did not exist yet.",
           f"2. **Built-ins** = revision `{r['builtins_revision']}`: the Tok references rewritten with the built-ins; eight "
           "Python references tightened to the built-in a Python developer would use; 24 tasks added.",
           "3. **Now** = this revision: script form (no `fn main` line), `args` without the program name, the conditional "
           "expression, `while` / `break` / `continue`, multiple assignment and dicts, with the Tok references rewritten "
           f"to use them. The Python references are unchanged since point 2. {r['tok_with_fn_main']} of the "
           f"{n['all']} Tok references still have a `fn main`.", "",
           "| Tokenizer | Group | Tasks | Tok, original | Python, original | Ratio | Tok, built-ins | Python, built-ins | Ratio | Tok, now | Python, now | Ratio |",
           "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name in r["tokenizers"]:
        for g in GROUPS:
            m, a = r["builtins"][name][g], r["groups"][name][g]
            b = r["before"][name] if g == "original" else None
            first = f"{b['tok']} | {b['python']} | {b['tok_over_python_total']:.3f}" if b else "n/a | n/a | n/a"
            out.append(f"| {name} | {g} | {a['tasks']} | {first} | {m['tok']} | {m['python']} | {m['tok_over_python_total']:.3f} | "
                       f"{a['tok']} | {a['python']} | {a['tok_over_python_total']:.3f} |")
    s = r["size"]
    out += ["", f"Source size of the 40 originals: Tok {s['before']['tok']['bytes']} bytes / {s['before']['tok']['lines']} lines "
                f"at the original point, {s['builtins']['original']['tok']['bytes']} / {s['builtins']['original']['tok']['lines']} "
                f"after the built-ins, {s['original']['tok']['bytes']} / {s['original']['tok']['lines']} now; Python "
                f"{s['before']['python']['bytes']} / {s['before']['python']['lines']}, then "
                f"{s['original']['python']['bytes']} / {s['original']['python']['lines']}. All {n['all']}: Tok "
                f"{s['builtins']['all']['tok']['bytes']} bytes / {s['builtins']['all']['tok']['lines']} lines after the built-ins, "
                f"{s['all']['tok']['bytes']} / {s['all']['tok']['lines']} now; Python {s['all']['python']['bytes']} / "
                f"{s['all']['python']['lines']}.", "",
            "## Per-task spread, this revision", "",
            "| Tokenizer | Group | Tasks | Tok / Python | Median per task | Smallest | Largest | Tok smaller | Equal | Tok smaller, built-ins |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for name in r["tokenizers"]:
        for g in GROUPS:
            a, m = r["groups"][name][g], r["builtins"][name][g]
            out.append(f"| {name} | {g} | {a['tasks']} | {a['tok_over_python_total']:.3f} | {a['median_per_task']:.3f} | "
                       f"{a['min_per_task']:.3f} | {a['max_per_task']:.3f} | {a['tasks_where_tok_is_smaller']} | "
                       f"{a['tasks_equal']} | {m['tasks_where_tok_is_smaller']} |")
    per = r["per_task"][MAIN_TOKENIZER]
    group_of = {t["id"]: t["group"] for t in harness.load_tasks()}
    rows = sorted(per, key=lambda i: per[i]["python"] - per[i]["tok"])
    out += ["", f"## Worst remaining tasks ({MAIN_TOKENIZER})", "", "The 15 tasks with the largest Tok minus Python difference.", "",
            "| Task | Group | Tok | Python | Difference | Tok, built-ins | Tok, original |", "|---|---|---|---|---|---|---|"]
    for i in rows[:15]:
        before = base["tasks"].get(i, {}).get("tok", {}).get(MAIN_TOKENIZER, "")
        out.append(f"| {i} | {group_of[i]} | {per[i]['tok']} | {per[i]['python']} | {per[i]['tok'] - per[i]['python']:+d} | "
                   f"{mid['tasks'][i]['tok'][MAIN_TOKENIZER]} | {before} |")
    best = [i for i in reversed(rows) if per[i]["tok"] <= per[i]["python"]]
    out += ["", f"Tasks where Tok is not larger ({len(best)}): " +
            (", ".join(f"{i} ({per[i]['tok']} vs {per[i]['python']})" for i in best) or "none") + ".", "",
            "## Where the remaining difference comes from", "",
            "The gap is a net figure: the tokens Tok spends beyond Python on the tasks where it is larger, minus what it "
            "saves on the tasks where it is smaller. The construct columns are measured by deleting that construct from "
            "every Tok reference and counting again (the transformations are in `count_tokens.py`; the stripped programs "
            "are not valid Tok). They overlap a little and are not a partition of the gap. Python's import lines are the "
            "cost on its side of the same ledger.", "",
            "| Tokenizer | Group | Gap (Tok - Python) | Excess where Tok is larger (tasks) | Saving where Tok is smaller (tasks) | `or` defaults | Type annotations | Effect lists | `fn main` line | Python imports |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for name in r["tokenizers"]:
        for g in GROUPS:
            x = r["sources"][name][g]
            out.append(f"| {name} | {g} | {x['gap']:+d} | {x['excess_where_larger']} ({x['tasks_larger']}) | "
                       f"{x['saving_where_smaller']} ({x['tasks_smaller']}) | {x['tok_defaults']} | {x['tok_type_annotations']} | "
                       f"{x['tok_effect_lists']} | {x['tok_main_line']} | {x['python_imports']} |")
    x = r["sources"][MAIN_TOKENIZER]["all"]
    out += ["", f"Read on {MAIN_TOKENIZER}, all {n['all']} tasks: Tok is {x['tok']} tokens and Python {x['python']}, a gap of "
                f"{x['gap']:+d}. The entry-point line that cost 736 tokens at the built-ins point is gone "
                f"({x['tok_main_line']} now). The largest construct left is the explicit default on a lookup or a parse "
                f"(`args[0].to_int() or 0`): {x['tok_defaults']} tokens, so without them Tok would be "
                f"{x['tok_without_defaults']}. Python pays {x['python_imports']} for its imports and would be "
                f"{x['python_without_imports']} without them. Type annotations cost {x['tok_type_annotations']} and effect "
                f"lists {x['tok_effect_lists']}, because few references still declare a function or a typed empty collection.",
            "",
            "The defaults are not waste: a Python reference that does `int(sys.argv[1])` stops with a traceback on a "
            "missing or malformed argument, and the Tok reference prints a defined answer. The tasks never test that, so "
            "the benchmark gives Tok no credit for it.", "",
            "What the worst tasks above still lack, read from the references themselves: a ready-made counter with "
            "`most_common` (`stdin_word_freq`), ranges as values and comprehensions with a condition (`primes_upto`, "
            "`range_sum`, `perfect_number`), a library `commonprefix` (`common_prefix`), character codes `ord` / `chr` "
            "(`caesar`), `accumulate` and `zip` (`running_sum`, `dot_product`), unpacking a split or a generator into "
            "names (`triangle`, `stdin_passing`), `print(*xs)` against `.join(\" \")`, and negative indexes "
            "(`second_largest`).", "",
            "## Indent width (Tok references, all tasks)", "", "| Tokenizer | 2 spaces | 4 spaces | Difference |", "|---|---|---|---|"]
    for name in r["tokenizers"]:
        i = r["indent"][name]
        out.append(f"| {name} | {i['tok_2_space']} | {i['tok_4_space']} | {i['difference']:+d} ({i['percent']:+.2f}%) |")
    out += ["", "## Learn pack (`tokc learn`)", "",
            "The guide now also teaches script form, the conditional expression, `while`, multiple assignment and dicts.", "",
            "| Tokenizer | Full, original | Full, built-ins | Full, now | Core, original | Core, built-ins | Core, now | --budget 2000, built-ins | --budget 2000, now |",
            "|---|---|---|---|---|---|---|---|---|"]
    for name in r["tokenizers"]:
        g, b = r["learn"][name], LEARN_BEFORE.get(name, ("", ""))
        m = g.get("builtins") or {}
        out.append(f"| {name} | {b[0]} | {m.get('full', '')} | {g['full']} | {b[1]} | {m.get('core_only', '')} | {g['core_only']} | "
                   f"{m.get('budget_2000', '')} | {g['budget_2000']} |")
    lc = r["learn_chars"]
    core = r["learn"][MAIN_TOKENIZER]["core_only"]
    fits = "fits" if lc["core_approx_tokens_chars_over_3"] <= 2000 else "does NOT fit"
    out += ["", f"The guide is {lc['full']} characters; `--budget` counts characters / 3 ({lc['approx_tokens_chars_over_3']} "
                f"for the full guide, {lc['core_approx_tokens_chars_over_3']} for the core), which is an approximation. The "
                f"core (the four prose sections, with no examples) is {core} tokens on {MAIN_TOKENIZER} and {fits} "
                "`--budget 2000` as the compiler counts it. It has not fitted `--budget 1500` since the built-ins.", ""]
    out += ["## Not measured", ""] + [f"- {k}: {v}" for k, v in sorted(r["unavailable"].items())]
    out += ["- Anthropic and Gemini tokenizers: not available offline; their counts come from the API usage fields during a "
            "benchmark run.",
            "- Tokens-to-green with a real model: not run. Nothing here shows whether a model writes the shorter Tok on its "
            "first try.", ""]
    return "\n".join(out)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
