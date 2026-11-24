"""`tokc serve`: JSON-RPC over stdio, tested end to end against a real server process."""
import glob
import json
import os
import re
import subprocess
import sys
import unittest

from helpers import ROOT, TOKC

sys.path.insert(0, os.path.join(ROOT, "compiler"))
from tok import docs  # noqa: E402


class Server:
    def __init__(self):
        self.p = subprocess.Popen(TOKC + ["serve"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=ROOT)
        self.n = 0

    def send_raw(self, line):
        self.p.stdin.write(line + "\n")
        self.p.stdin.flush()

    def request(self, method, params=None):
        self.n += 1
        msg = {"jsonrpc": "2.0", "id": self.n, "method": method}
        if params is not None:
            msg["params"] = params
        self.send_raw(json.dumps(msg))
        resp = json.loads(self.p.stdout.readline())
        assert resp["id"] == self.n, resp
        return resp

    def tool(self, name, **arguments):
        resp = self.request("tools/call", {"name": name, "arguments": arguments})
        result = resp["result"]
        assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
        return result["structuredContent"], result["isError"]

    def close(self):
        self.p.stdin.close()
        code = self.p.wait(timeout=10)
        self.p.stdout.close()
        return code


class ServeTests(unittest.TestCase):
    def setUp(self):
        self.s = Server()

    def tearDown(self):
        if self.s.p.poll() is None:
            self.s.close()

    def test_handshake_and_tool_list(self):
        init = self.s.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {}})["result"]
        self.assertEqual(init["serverInfo"]["name"], "tokc")
        self.assertIn("tools", init["capabilities"])
        self.s.send_raw(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))  # no reply expected
        self.assertEqual(self.s.request("ping")["result"], {})
        names = [t["name"] for t in self.s.request("tools/list")["result"]["tools"]]
        self.assertEqual(names, ["check", "fix", "run", "format", "doc"])
        self.assertEqual(self.s.close(), 0)

    def test_check_returns_structured_diagnostics_with_a_fix(self):
        res, is_err = self.s.tool("check", source="fn main()\n  print(1)\n")
        self.assertTrue(is_err)
        self.assertFalse(res["ok"])
        e = res["errors"][0]
        self.assertEqual((e["code"], e["line"], e["col"]), ("E0201", 2, 3))
        self.assertEqual(e["fix"]["edits"], [{"line": 1, "col": 10, "insert": " ! io"}])
        res, is_err = self.s.tool("check", source="fn main() ! io\n  print(1)\n")
        self.assertEqual((res, is_err), ({"ok": True, "errors": []}, False))

    def test_fix_then_run(self):
        res, _ = self.s.tool("fix", source="fn half(n: int) -> int\n  if n % 2 != 0\n    fail(\"odd\")\n  n / 2\n\n"
                                            "fn main()\n  print(half(8))\n")
        self.assertTrue(res["ok"])
        self.assertEqual(sorted(a["code"] for a in res["applied"]), ["E0201", "E0202", "E0203"])
        out, is_err = self.s.tool("run", source=res["source"])
        self.assertEqual((out["stdout"], out["exit_code"], is_err), ("4\n", 0, False))

    def test_fix_reports_what_it_cannot_fix(self):
        res, is_err = self.s.tool("fix", source="fn main() ! io\n  print(nope)\n")
        self.assertTrue(is_err)
        self.assertEqual([e["code"] for e in res["errors"]], ["E0101"])

    def test_run_with_args_files_errors_and_timeout(self):
        out, _ = self.s.tool("run", source="fn main(args: [str]) ! io, fs\n  print(geo.twice(args[1].to_int() or 0))\n"
                                            "  print(fs.read_text(\"data.txt\")?.trim())\n",
                             args=["21"], files={"geo.tok": "fn twice(n: int) -> int\n  n * 2\n", "data.txt": "hi\n"})
        self.assertEqual(out["stdout"], "42\nhi\n")
        out, is_err = self.s.tool("run", source="fn main() ! io\n  print(1 +)\n")
        self.assertEqual((out["stage"], is_err, out["errors"][0]["file"]), ("check", True, "main.tok"))
        out, is_err = self.s.tool("run", source="fn f() ! err\n  fail(\"boom\")\n\nfn main()\n  f()?\n")
        self.assertEqual((out["exit_code"], out["stderr"], is_err), (1, "error: boom\n", True))
        out, _ = self.s.tool("run", source="fn spin(n: int) -> int\n  spin(n)\n\nfn main() ! io\n  print(spin(1))\n",
                             timeout=0.5)
        self.assertTrue(out.get("timed_out") or out.get("exit_code") != 0)

    def test_run_a_path_with_modules(self):
        out, _ = self.s.tool("run", path=os.path.join(ROOT, "examples", "modules", "main.tok"))
        self.assertEqual(out["exit_code"], 0)
        self.assertTrue(out["stdout"])

    def test_format(self):
        res, _ = self.s.tool("format", source="fn main()!io\n    print( 1+2 )\n")
        self.assertEqual((res["source"], res["changed"]), ("fn main() ! io\n  print(1 + 2)\n", True))

    def test_doc(self):
        res, _ = self.s.tool("doc", topic="E0203")
        self.assertIn("can fail", res["text"])
        res, _ = self.s.tool("doc", topic="str")
        self.assertIn("to_int", res["text"])
        res, _ = self.s.tool("doc", topic="effects")
        self.assertIn("! fs, err", res["text"])
        res, is_err = self.s.tool("doc", topic="no such thing")
        self.assertTrue(is_err)
        self.assertIn("E0201", res["topics"])

    def test_protocol_errors_do_not_stop_the_server(self):
        self.s.send_raw("not json")
        self.assertEqual(json.loads(self.s.p.stdout.readline())["error"]["code"], -32700)
        self.assertEqual(self.s.request("nope")["error"]["code"], -32601)
        self.assertEqual(self.s.request("tools/call", {"name": "nope"})["error"]["code"], -32602)
        self.assertEqual(self.s.request("tools/call", {"name": "check", "arguments": {}})["error"]["code"], -32602)
        self.assertEqual(self.s.request("ping")["result"], {})

    def test_every_error_code_has_a_doc(self):
        used = set()
        for f in glob.glob(os.path.join(ROOT, "compiler", "tok", "*.py")):
            if os.path.basename(f) != "docs.py":
                with open(f) as fh:
                    used |= set(re.findall(r'"(E\d{4})"', fh.read()))
        self.assertEqual(sorted(used - set(docs.CODES)), [])


if __name__ == "__main__":
    unittest.main()
