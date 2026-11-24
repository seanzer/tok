"""Tokens-to-green: how many tokens does a model spend before its program passes the tests?

One task = a prompt, tests, and verified reference solutions (bench/tasks/<id>/). The first test is shown in the
prompt as an example; the others are hidden. The loop for one task:

  generate -> check and run the tests -> if not green, send back structured feedback -> retry, up to a cap

Recorded per task: green or not, attempts, and the input, output and thinking tokens the provider reported, summed
over every attempt (that sum is "tokens to green" when the task ends green).
"""
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "compiler"))

from tok import docs, learn, tools  # noqa: E402

TASK_DIR = os.path.join(HERE, "tasks")
LANGS = {"tok": "ref.tok", "python": "ref.py"}
IO_RULES = ("The program gets its input as command-line arguments. It must print exactly what is asked to standard "
            "output, each line ending in a newline, and nothing else.")


def load_tasks(only=None, task_dir=TASK_DIR):
    out = []
    for name in sorted(os.listdir(task_dir)):
        path = os.path.join(task_dir, name, "task.json")
        if os.path.exists(path) and (not only or name in only):
            with open(path) as fh:
                t = json.load(fh)
            t["dir"] = os.path.join(task_dir, name)
            out.append(t)
    return out


def reference(task, lang):
    with open(os.path.join(task["dir"], LANGS[lang])) as fh:
        return fh.read()


def system_prompt(lang, budget=None):
    if lang == "tok":
        return ("You write programs in Tok, a language you have not seen before. This is the whole language:\n\n"
                + learn.render(budget) + "\n" + IO_RULES + "\nReply with one complete Tok program in a single "
                "fenced code block and nothing else.")
    return ("You write Python 3 programs using only the standard library.\n" + IO_RULES +
            "\nReply with one complete program in a single fenced code block and nothing else.")


def task_prompt(task):
    ex = task["tests"][0]
    shown = " ".join(json.dumps(a) for a in ex["args"]) or "(no arguments)"
    return (f"{task['prompt']}\n\nExample: with the arguments {shown} the program prints:\n"
            f"{ex['stdout'] if ex['stdout'] else '(nothing)'}")


def extract_code(reply):
    """The last fenced block of a reply, or the whole reply when there is none."""
    blocks = re.findall(r"```[^\n]*\n(.*?)```", reply, re.S)
    code = blocks[-1] if blocks else reply
    return code if code.endswith("\n") else code + "\n"


def run_python(src, args, files, timeout):
    with tempfile.TemporaryDirectory(prefix="pyrun") as d:
        for name, text in (files or {}).items():
            with open(os.path.join(d, name), "w") as fh:
                fh.write(text)
        with open(os.path.join(d, "main.py"), "w") as fh:
            fh.write(src)
        try:
            r = subprocess.run([sys.executable, "main.py"] + list(args), cwd=d, capture_output=True, text=True,
                               errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            return {"ok": False, "stage": "run", "timed_out": True}
        return {"ok": r.returncode == 0, "stage": "run", "exit_code": r.returncode, "stdout": r.stdout, "stderr": r.stderr}


def evaluate(lang, src, task, timeout=10.0):
    """Check and test one program. Returns {green, stage, errors | failure, passed, total}."""
    if lang == "tok":
        res = tools.check(src)
        if not res["ok"]:
            return {"green": False, "stage": "check", "errors": res["errors"], "passed": 0, "total": len(task["tests"])}
    passed = 0
    failure = None
    for i, t in enumerate(task["tests"]):
        if lang == "tok":
            r = tools.run(src, t["args"], t.get("files"), timeout)
        else:
            r = run_python(src, t["args"], t.get("files"), timeout)
        want_code = t.get("exit_code", 0)
        if r.get("stdout") == t["stdout"] and r.get("exit_code") == want_code:
            passed += 1
        elif failure is None:
            failure = {"test": i, "args": t["args"], "files": sorted(t.get("files", {})), "stdout": r.get("stdout", ""),
                       "stderr": (r.get("stderr") or "")[-1500:], "exit_code": r.get("exit_code"),
                       "timed_out": bool(r.get("timed_out")), "expected_exit_code": want_code,
                       "expected_stdout": t["stdout"], "stage": r.get("stage")}
            if r.get("stage") in ("check", "cc", "setup"):
                failure["message"] = r.get("message", "")
    return {"green": failure is None, "stage": "tests", "failure": failure, "passed": passed, "total": len(task["tests"])}


def feedback(lang, result, show_expected=False):
    """The message sent back to the model after a failed attempt."""
    if result["stage"] == "check":
        errs = []
        for e in result["errors"]:
            item = {k: e[k] for k in ("code", "line", "col", "end_col", "message")}
            if e.get("fix"):
                item["fix"] = e["fix"]
            item["doc"] = docs.CODES.get(e["code"], "")
            errs.append(item)
        return ("The program does not compile. Compiler diagnostics (JSON):\n" + json.dumps(errs, indent=1) +
                "\nFix every error and reply with the complete corrected program in a single fenced code block.")
    f = result["failure"]
    shown = {"args": f["args"], "stdout": f["stdout"], "stderr": f["stderr"], "exit_code": f["exit_code"]}
    if f["files"]:
        shown["files_present"] = f["files"]
    if f["timed_out"]:
        shown["timed_out"] = True
    if f["expected_exit_code"] != 0 or f["exit_code"] not in (0, None):
        shown["expected_exit_code"] = f["expected_exit_code"]
    if show_expected:
        shown["expected_stdout"] = f["expected_stdout"]
    return (f"The program ran but {result['total'] - result['passed']} of {result['total']} tests failed. "
            "The first failing run (JSON):\n" + json.dumps(shown, indent=1) +
            "\nRe-read the task, fix the program and reply with the complete corrected program in a single fenced code block.")


def solve(adapter, lang, task, cap=3, budget=None, show_expected=False, log=None):
    """Run the generate / check / retry loop for one task."""
    system = system_prompt(lang, budget)
    messages = [{"role": "user", "content": task_prompt(task)}]
    rec = {"task": task["id"], "lang": lang, "green": False, "attempts": 0, "first_try": False, "input_tokens": 0,
           "output_tokens": 0, "thinking_tokens": 0, "tokens_missing": False, "stages": []}
    for attempt in range(1, cap + 1):
        reply = adapter.complete(system, messages)
        rec["attempts"] = attempt
        if reply.input_tokens is None or reply.output_tokens is None:
            rec["tokens_missing"] = True
        rec["input_tokens"] += reply.input_tokens or 0
        rec["output_tokens"] += reply.output_tokens or 0
        rec["thinking_tokens"] += reply.thinking_tokens or 0
        code = extract_code(reply.text)
        result = evaluate(lang, code, task)
        rec["stages"].append("green" if result["green"] else result["stage"])
        if log is not None:
            log.append({"task": task["id"], "attempt": attempt, "reply": reply.text, "usage": reply.usage,
                        "result": {k: v for k, v in result.items() if k != "errors"} | {"errors": result.get("errors", [])}})
        if result["green"]:
            rec["green"], rec["first_try"] = True, attempt == 1
            break
        messages.append({"role": "assistant", "content": reply.text})
        messages.append({"role": "user", "content": feedback(lang, result, show_expected)})
    rec["total_tokens"] = rec["input_tokens"] + rec["output_tokens"] + rec["thinking_tokens"]
    return rec


def summarize(records):
    n = len(records)
    green = [r for r in records if r["green"]]
    s = {"tasks": n, "green": len(green), "first_try": sum(1 for r in records if r["first_try"]),
         "retries": sum(r["attempts"] - 1 for r in records),
         "input_tokens": sum(r["input_tokens"] for r in records), "output_tokens": sum(r["output_tokens"] for r in records),
         "thinking_tokens": sum(r["thinking_tokens"] for r in records),
         "total_tokens": sum(r["total_tokens"] for r in records),
         "tokens_to_green": sum(r["total_tokens"] for r in green),
         "tokens_missing": any(r["tokens_missing"] for r in records)}
    s["first_try_rate"] = s["first_try"] / n if n else None
    s["green_rate"] = len(green) / n if n else None
    s["mean_tokens_to_green"] = s["tokens_to_green"] / len(green) if green else None
    return s
