#!/usr/bin/env python3
"""Run the tokens-to-green benchmark.

  python3 bench/run.py --adapter reference --lang tok            dry run: replays the reference solutions (free)
  python3 bench/run.py --adapter anthropic --lang tok --confirm-spend [--model ID] [--tasks a,b] [--limit N] [--cap 3]
  python3 bench/run.py --adapter gemini --lang python --confirm-spend

A paid adapter refuses to start without --confirm-spend, and prints the largest number of model calls it can make
(tasks x cap) before it starts. A rate limit or a missing connection stops the run; nothing is retried in a loop.
Results go to bench/out/<adapter>-<model>-<lang>.json.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import adapters  # noqa: E402
import harness  # noqa: E402


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adapter", required=True, choices=["reference"] + sorted(adapters.ADAPTERS))
    ap.add_argument("--lang", required=True, choices=sorted(harness.LANGS))
    ap.add_argument("--model")
    ap.add_argument("--tasks", help="comma-separated task ids")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--cap", type=int, default=3, help="attempts per task")
    ap.add_argument("--budget", type=int, help="trim the Tok guide (tokc learn --budget)")
    ap.add_argument("--max-tokens", type=int)
    ap.add_argument("--show-expected", action="store_true", help="include the expected output in test feedback")
    ap.add_argument("--confirm-spend", action="store_true")
    ap.add_argument("--out")
    a = ap.parse_args(argv)

    tasks = harness.load_tasks(set(a.tasks.split(",")) if a.tasks else None)
    if a.limit:
        tasks = tasks[:a.limit]
    if not tasks:
        sys.stderr.write("no tasks selected\n")
        return 64
    if a.adapter == "reference":
        by_prompt = {harness.task_prompt(t): harness.reference(t, a.lang) for t in tasks}
        adapter = adapters.ScriptedAdapter(lambda system, messages: "```\n" + by_prompt[messages[0]["content"]] + "```")
    else:
        if not a.confirm_spend:
            sys.stderr.write(f"this would make up to {len(tasks) * a.cap} paid calls to {a.adapter}; "
                             "add --confirm-spend to run it\n")
            return 2
        kw = {"model": a.model}
        if a.max_tokens:
            kw["max_tokens"] = a.max_tokens
        adapter = adapters.ADAPTERS[a.adapter](**kw)
        print(f"{a.adapter} {adapter.model}: up to {len(tasks) * a.cap} calls ({len(tasks)} tasks x {a.cap} attempts)")

    records, log, stopped = [], [], None
    for t in tasks:
        try:
            rec = harness.solve(adapter, a.lang, t, a.cap, a.budget, a.show_expected, log)
        except adapters.AdapterError as e:
            stopped = {"task": t["id"], "error": e.kind, "message": str(e)}
            sys.stderr.write(f"stopped at {t['id']}: {e.kind}: {e}\n")
            break
        records.append(rec)
        print(f"{t['id']:18} {'green' if rec['green'] else 'RED  '} attempts={rec['attempts']} tokens={rec['total_tokens']}")
    summary = harness.summarize(records)
    token_unit = "words (scripted adapter, not tokens)" if a.adapter == "reference" else "tokens as reported by the provider"
    out = {"adapter": a.adapter, "model": adapter.model, "lang": a.lang, "cap": a.cap, "budget": a.budget,
           "show_expected": a.show_expected, "token_unit": token_unit, "stopped": stopped, "summary": summary,
           "records": records, "log": log}
    path = a.out or os.path.join(harness.HERE, "out", f"{a.adapter}-{adapter.model}-{a.lang}.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1)
    print(json.dumps(summary))
    print(f"unit: {token_unit}; wrote {path}")
    return 1 if stopped else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
