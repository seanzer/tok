"""The benchmark: task suite, reference solutions, the generate/check/retry loop, and the model adapters
(against a fake transport: no network, no credentials)."""
import json
import os
import py_compile
import subprocess
import sys
import unittest

from helpers import ROOT, TokCase, tokc

sys.path.insert(0, os.path.join(ROOT, "bench"))
import adapters  # noqa: E402
import harness  # noqa: E402

TASKS = harness.load_tasks()
BY_ID = {t["id"]: t for t in TASKS}


class SuiteTests(TokCase):
    def test_suite_shape(self):
        self.assertEqual(len(TASKS), 64)
        self.assertEqual(len(harness.load_tasks(group="original")), 40)
        added = harness.load_tasks(group="added")
        self.assertEqual(len(added), 24)
        self.assertEqual(sum(1 for t in added if any("stdin" in c for c in t["tests"])), 12)
        with open(os.path.join(ROOT, "bench", "baseline.json")) as fh:
            self.assertEqual(sorted(json.load(fh)["tasks"]), sorted(t["id"] for t in TASKS if t["group"] == "original"))
        for t in TASKS:
            with self.subTest(task=t["id"]):
                self.assertEqual(t["id"], os.path.basename(t["dir"]))
                self.assertGreaterEqual(len(t["tests"]), 3)
                self.assertTrue(t["prompt"].strip())
                self.assertEqual(len({json.dumps([c["args"], c.get("files", {}), c.get("stdin")]) for c in t["tests"]}), len(t["tests"]))

    def test_reference_solutions_pass_in_every_language(self):
        for t in TASKS:
            for lang in harness.LANGS:
                with self.subTest(task=t["id"], lang=lang):
                    r = harness.evaluate(lang, harness.reference(t, lang), t)
                    self.assertTrue(r["green"], r)
                    self.assertEqual(r["passed"], len(t["tests"]))

    def test_tok_references_are_formatted_and_emit_clean_c(self):
        for t in TASKS:
            with self.subTest(task=t["id"]):
                path = os.path.join(t["dir"], "ref.tok")
                self.assertEqual(tokc("fmt", path, "--check").returncode, 0)
                self.assert_clean_c(path)


class LoopTests(unittest.TestCase):
    def script(self, replies):
        it = iter(replies)
        return adapters.ScriptedAdapter(lambda system, messages: next(it))

    def test_compile_error_then_wrong_output_then_green(self):
        t = BY_ID["sum_args"]
        good = harness.reference(t, "tok")
        no_effect = "fn main(args: [str])\n" + "".join("  " + line + "\n" for line in good.splitlines())  # the older form, effect missing
        wrong = good.replace(".sum())", ".sum() + 1)")
        self.assertNotIn(good, (no_effect, wrong))
        a = self.script([f"```tok\n{no_effect}```", f"Here it is:\n```\n{wrong}```\nDone.", f"```\n{good}```"])
        log = []
        rec = harness.solve(a, "tok", t, cap=3, log=log)
        self.assertEqual((rec["green"], rec["attempts"], rec["first_try"], rec["stages"]), (True, 3, False, ["check", "tests", "green"]))
        self.assertEqual(len(a.calls), 3)
        system, first = a.calls[0]
        self.assertIn("## Effects and errors", system)
        self.assertEqual(len(first), 1)
        self.assertIn('"1" "2" "3"', first[0]["content"])
        fb1 = a.calls[1][1][-1]["content"]
        diag = json.loads(fb1[fb1.index("["):fb1.rindex("]") + 1])
        self.assertEqual(diag[0]["code"], "E0201")
        self.assertEqual(diag[0]["fix"]["edits"][0]["insert"], " ! io")
        self.assertTrue(diag[0]["doc"])
        fb2 = a.calls[2][1][-1]["content"]
        self.assertIn("tests failed", fb2)
        self.assertNotIn("expected_stdout", fb2)
        self.assertEqual([m["role"] for m in a.calls[2][1]], ["user", "assistant", "user", "assistant", "user"])
        self.assertEqual(rec["total_tokens"], rec["input_tokens"] + rec["output_tokens"] + rec["thinking_tokens"])
        self.assertEqual(rec["input_tokens"], sum(len(s.split()) + sum(len(m["content"].split()) for m in ms) for s, ms in a.calls))
        self.assertEqual(len(log), 3)

    def test_cap_and_summary(self):
        t = BY_ID["gcd"]
        bad = "```\nprint('nope')\n```"
        red = harness.solve(self.script([bad, bad]), "python", t, cap=2, show_expected=True)
        self.assertEqual((red["green"], red["attempts"]), (False, 2))
        green = harness.solve(self.script(["```python\n" + harness.reference(t, "python") + "```"]), "python", t, cap=2)
        self.assertEqual((green["green"], green["first_try"]), (True, True))
        s = harness.summarize([red, green])
        self.assertEqual((s["tasks"], s["green"], s["first_try"], s["retries"]), (2, 1, 1, 1))
        self.assertEqual(s["first_try_rate"], 0.5)
        self.assertEqual(s["tokens_to_green"], green["total_tokens"])
        self.assertEqual(s["total_tokens"], red["total_tokens"] + green["total_tokens"])

    def test_feedback_shows_expected_only_on_request(self):
        t = BY_ID["safe_divide"]
        r = harness.evaluate("python", "print(3)\n", t)
        self.assertFalse(r["green"])
        self.assertEqual(r["failure"]["test"], 1)
        self.assertIn("expected_exit_code", harness.feedback("python", r))
        self.assertNotIn("expected_stdout", harness.feedback("python", r))
        self.assertIn("expected_stdout", harness.feedback("python", r, show_expected=True))

    def test_standard_input_reaches_both_languages_and_the_prompt(self):
        t = BY_ID["stdin_grep"]
        prompt = harness.task_prompt(t)
        self.assertIn('with the standard input "an error here\\nall fine\\nerror again\\n" and the arguments "error"', prompt)
        self.assertIn("with the standard input", harness.task_prompt(BY_ID["stdin_sum"]))
        self.assertNotIn("and the arguments", harness.task_prompt(BY_ID["stdin_sum"]))
        self.assertIn("with the arguments", harness.task_prompt(BY_ID["gcd"]))
        for lang, src in (("python", "print(1)\n"), ("tok", "fn main() ! io\n  print(1)\n")):
            r = harness.evaluate(lang, src, t)
            self.assertFalse(r["green"])
            self.assertEqual(r["failure"]["stdin"], t["tests"][0]["stdin"])
            self.assertIn('"stdin": "an error here', harness.feedback(lang, r))
        r = harness.evaluate("python", "print(3)\n", BY_ID["gcd"])
        self.assertNotIn('"stdin"', harness.feedback("python", r))

    def test_extract_code(self):
        self.assertEqual(harness.extract_code("text\n```tok\nA\n```\nmore\n```\nB\n```\n"), "B\n")
        self.assertEqual(harness.extract_code("fn main()"), "fn main()\n")

    def test_thinking_tokens_are_counted(self):
        class Thinker:
            name = model = "t"

            def complete(self, system, messages):
                return adapters.Reply("```\n" + harness.reference(BY_ID["gcd"], "python") + "```", 100, 20, 300)
        rec = harness.solve(Thinker(), "python", BY_ID["gcd"])
        self.assertEqual((rec["input_tokens"], rec["output_tokens"], rec["thinking_tokens"], rec["total_tokens"]), (100, 20, 300, 420))

    def test_paid_run_needs_confirmation(self):
        r = subprocess.run([sys.executable, os.path.join(ROOT, "bench", "run.py"), "--adapter", "anthropic", "--lang", "tok",
                            "--limit", "2"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("up to 6 paid calls", r.stderr)


class CountTests(unittest.TestCase):
    def test_gap_source_transformations(self):
        import count_tokens as ct
        src = ('fn gcd(a: int, b: int) -> int ! err\n  a\n\nfn main(args: [str]) ! io, fs\n  var xs: [int] = []\n'
               '  n = args[0].to_int() or 0\n  s = (args[1] or "").len()\n  if n > 0 or s > 0\n    print(gcd(n, s) or 1)\n')
        self.assertEqual(ct.strip_effects(src).split("\n")[0], "fn gcd(a: int, b: int) -> int")
        self.assertIn("fn main(args: [str])\n", ct.strip_effects(src))
        self.assertEqual(ct.strip_main(src), 'fn gcd(a: int, b: int) -> int ! err\n  a\n\nvar xs: [int] = []\n'
                         'n = args[0].to_int() or 0\ns = (args[1] or "").len()\nif n > 0 or s > 0\n  print(gcd(n, s) or 1)\n')
        d = ct.strip_defaults(src)
        self.assertIn("n = args[0].to_int()\n", d)
        self.assertIn("s = args[1].len()\n", d)
        self.assertIn("if n > 0 or s > 0\n", d)  # a logical `or` is kept
        self.assertIn("print(gcd(n, s))\n", d)
        t = ct.strip_types(src)
        self.assertIn("fn gcd(a, b) ! err\n", t)
        self.assertEqual(ct.strip_types("var xs: [int] = []\nd: {str: int} = {}\n"), "var xs = []\nd = {}\n")
        self.assertIn("  var xs = []\n", t)
        self.assertIn("fn main(args: [str]) ! io, fs\n", t)
        self.assertEqual(ct.strip_import("import sys\nfrom x import y\n\nprint(1)\n"), "print(1)\n")

    def test_results_file_matches_the_suite(self):
        with open(os.path.join(ROOT, "bench", "results.json")) as fh:
            r = json.load(fh)
        self.assertEqual(r["tasks"], {"original": 40, "added": 24, "all": 64})
        self.assertEqual(sorted(r["per_task"]["tiktoken o200k_base"]), sorted(BY_ID))
        with open(os.path.join(ROOT, "bench", "baseline_builtins.json")) as fh:
            mid = json.load(fh)
        self.assertEqual(sorted(mid["tasks"]), sorted(BY_ID))
        self.assertEqual(r["builtins_revision"], mid["revision"])
        self.assertEqual(r["tok_with_fn_main"], 0)
        for name in r["tokenizers"]:  # the stored totals are the sums of the stored per-task counts
            for lang in ("tok", "python"):
                self.assertEqual(r["groups"][name]["all"][lang], sum(v[lang] for v in r["per_task"][name].values()))
                self.assertEqual(r["builtins"][name]["all"][lang], sum(v[lang][name] for v in mid["tasks"].values()))

    def test_results_are_current(self):
        try:
            import tiktoken
        except ImportError:
            self.skipTest("tiktoken is not installed for this interpreter")
        enc = tiktoken.get_encoding("o200k_base")
        with open(os.path.join(ROOT, "bench", "results.json")) as fh:
            per = json.load(fh)["per_task"]["tiktoken o200k_base"]
        for t in harness.load_tasks():
            for lang in harness.LANGS:
                self.assertEqual(per[t["id"]][lang], len(enc.encode(harness.reference(t, lang))), (t["id"], lang))


class FakeTransport:
    def __init__(self, status, payload):
        self.status, self.payload, self.seen = status, payload, []

    def __call__(self, method, url, headers, body):
        self.seen.append((method, url, dict(headers), body))
        return self.status, self.payload


MSGS = [{"role": "user", "content": "task"}, {"role": "assistant", "content": "try"}, {"role": "user", "content": "fix"}]


class AdapterTests(unittest.TestCase):
    def test_adapters_compile(self):
        for f in ("adapters.py", "harness.py", "run.py", "count_tokens.py"):
            py_compile.compile(os.path.join(ROOT, "bench", f), doraise=True)

    def test_anthropic_request_and_usage(self):
        ft = FakeTransport(200, {"content": [{"type": "thinking", "thinking": "hm"}, {"type": "text", "text": "hello"}],
                                 "stop_reason": "end_turn",
                                 "usage": {"input_tokens": 10, "output_tokens": 7, "cache_read_input_tokens": 5}})
        a = adapters.AnthropicAdapter(transport=ft)
        self.assertEqual(a.model, "claude-opus-5-5")
        r = a.complete("sys", MSGS)
        method, url, headers, body = ft.seen[0]
        self.assertEqual((method, url), ("POST", "https://api.anthropic.com/v1/messages"))
        self.assertEqual(headers, {"anthropic-version": "2023-06-01"})  # the key is added by the transport, never here
        self.assertEqual((body["model"], body["system"], body["messages"]), ("claude-opus-5-5", "sys", MSGS))
        self.assertEqual((r.text, r.input_tokens, r.output_tokens, r.thinking_tokens, r.billed_tokens()), ("hello", 15, 7, None, 22))
        self.assertEqual(adapters.AnthropicAdapter(model="other", transport=ft).model, "other")

    def test_gemini_request_and_thinking_tokens(self):
        ft = FakeTransport(200, {"candidates": [{"content": {"parts": [{"text": "plan", "thought": True}, {"text": "hi"}]},
                                                 "finishReason": "STOP"}],
                                 "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 3, "thoughtsTokenCount": 40,
                                                   "totalTokenCount": 55}})
        g = adapters.GeminiAdapter(transport=ft)
        self.assertEqual(g.model, "gemini-4-neon")
        r = g.complete("sys", MSGS)
        method, url, headers, body = ft.seen[0]
        self.assertEqual((method, url, headers),
                         ("POST", "https://generativelanguage.googleapis.com/v1beta/models/gemini-4-neon:generateContent", {}))
        self.assertEqual([c["role"] for c in body["contents"]], ["user", "model", "user"])
        self.assertEqual(body["systemInstruction"], {"parts": [{"text": "sys"}]})
        self.assertEqual((r.text, r.input_tokens, r.output_tokens, r.thinking_tokens, r.billed_tokens()), ("hi", 12, 3, 40, 55))
        g2 = adapters.GeminiAdapter(model="gemini-4-hydrogen", transport=ft)
        g2.complete("", MSGS[:1])
        self.assertIn("/models/gemini-4-hydrogen:generateContent", ft.seen[-1][1])
        self.assertNotIn("systemInstruction", ft.seen[-1][3])

    def test_model_listing(self):
        a = adapters.AnthropicAdapter(transport=FakeTransport(200, {"data": [{"id": "m1"}, {"id": "m2"}]}))
        self.assertEqual(a.models(), ["m1", "m2"])
        g = adapters.GeminiAdapter(transport=FakeTransport(200, {"models": [
            {"name": "models/g1", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]}]}))
        self.assertEqual(g.models(), ["g1"])

    def test_errors_are_classified_and_stop_a_run(self):
        for status, kind in ((429, "rate_limited"), (401, "not_connected"), (403, "not_connected"), (500, "http")):
            with self.subTest(status=status):
                a = adapters.AnthropicAdapter(transport=FakeTransport(status, {"error": "x"}))
                with self.assertRaises(adapters.AdapterError) as cm:
                    a.complete("", MSGS[:1])
                self.assertEqual(cm.exception.kind, kind)
                self.assertEqual(len(a.send.seen), 1)  # no retry

    def test_missing_usage_is_flagged_not_guessed(self):
        a = adapters.AnthropicAdapter(transport=FakeTransport(200, {"content": [{"type": "text", "text": "```\nprint(1)\n```"}]}))
        r = a.complete("", MSGS[:1])
        self.assertIsNone(r.billed_tokens())
        rec = harness.solve(a, "python", BY_ID["gcd"], cap=1)
        self.assertTrue(rec["tokens_missing"])


if __name__ == "__main__":
    unittest.main()
