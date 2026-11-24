"""`tokc serve`: the compiler as a tool server.

It speaks JSON-RPC 2.0 over stdio, one JSON message per line, with the method names of the Model Context Protocol
(initialize, tools/list, tools/call, ping), so an MCP client or a plain script can drive it.

Tools: check, fix, run, format, doc. Each takes the program as `source` text (or `path` to a file) and answers
with structured JSON: the same diagnostics as `tokc check --json`, with spans and apply-able fixes.
"""
import json
import sys

from . import docs, tools

PROTOCOL = "2024-11-05"
SRC = {"source": {"type": "string", "description": "Program text"},
       "path": {"type": "string", "description": "Path of a .tok file, instead of source"}}


def schema(extra=None, required=()):
    props = dict(SRC)
    props.update(extra or {})
    return {"type": "object", "properties": props, "required": list(required)}


TOOLS = [
    {"name": "check", "description": "Type and effect check a program. Returns {ok, errors}; each error has code, "
                                     "message, file, line, col, end_col and, when mechanical, a fix with edits.",
     "inputSchema": schema()},
    {"name": "fix", "description": "Apply the compiler's suggested fixes until the program checks or none is left. "
                                   "Returns {ok, source, applied, errors}.", "inputSchema": schema()},
    {"name": "run", "description": "Compile and run a program. Returns {ok, stage, exit_code, stdout, stderr} or the "
                                   "check errors.",
     "inputSchema": schema({"args": {"type": "array", "items": {"type": "string"}},
                            "files": {"type": "object", "description": "Extra files (modules, data): name -> text"},
                            "stdin": {"type": "string", "description": "Text for the program's standard input"},
                            "timeout": {"type": "number"}})},
    {"name": "format", "description": "Rewrite a program in the canonical layout. Returns {ok, source, changed}.",
     "inputSchema": schema()},
    {"name": "doc", "description": "Look up an error code (E0201), a built-in (print, str, list, fs.read_text), a guide "
                                   "section, or 'guide' for the whole language guide. An unknown topic lists the topics.",
     "inputSchema": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]}},
]


class BadParams(Exception):
    pass


def source_of(a):
    if isinstance(a.get("source"), str):
        return a["source"], a.get("path") or "main.tok"
    if isinstance(a.get("path"), str):
        try:
            with open(a["path"]) as fh:
                return fh.read(), a["path"]
        except OSError as e:
            raise BadParams(f"cannot read {a['path']}: {e.strerror}")
    raise BadParams("give the program as 'source' (text) or 'path' (a file)")


def call_tool(name, a):
    if name == "doc":
        if not isinstance(a.get("topic"), str):
            raise BadParams("doc needs a 'topic' string")
        text = docs.lookup(a["topic"])
        if text is None:
            return {"ok": False, "message": f"no doc for {a['topic']!r}", "topics": docs.topics()}
        return {"ok": True, "topic": a["topic"], "text": text}
    src, path = source_of(a)
    if name == "check":
        return tools.check(src, path)
    if name == "fix":
        return tools.fix(src, path)
    if name == "format":
        return tools.fmt(src)
    if name == "run":
        args, files, timeout, stdin = a.get("args", []), a.get("files"), a.get("timeout", 10.0), a.get("stdin", "")
        if not isinstance(args, list) or (files is not None and not isinstance(files, dict)) \
                or not isinstance(timeout, (int, float)) or not isinstance(stdin, str):
            raise BadParams("run takes args: [str], files: {name: text}, stdin: text, timeout: seconds")
        if "source" not in a:
            # a file on disk keeps its own folder, so its modules are found
            import os
            folder = os.path.dirname(os.path.abspath(path))
            files = dict(files or {})
            for f in os.listdir(folder):
                full = os.path.join(folder, f)
                if f.endswith(".tok") and os.path.abspath(full) != os.path.abspath(path) and f not in files:
                    with open(full) as fh:
                        files[f] = fh.read()
        return tools.run(src, args, files, min(float(timeout), 60.0), stdin=stdin)
    raise BadParams(f"unknown tool {name!r}")


def handle(msg):
    """One request -> one response dict, or None for a notification."""
    if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
        return {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                "error": {"code": -32600, "message": "invalid request"}}
    mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
    if "id" not in msg:
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        return ok({"protocolVersion": PROTOCOL, "capabilities": {"tools": {}},
                   "serverInfo": {"name": "tokc", "version": "0.2"}})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name, a = params.get("name"), params.get("arguments") or {}
        if name not in [t["name"] for t in TOOLS]:
            return err(-32602, f"unknown tool {name!r}")
        if not isinstance(a, dict):
            return err(-32602, "arguments must be an object")
        try:
            res = call_tool(name, a)
        except BadParams as e:
            return err(-32602, str(e))
        except Exception as e:  # a compiler bug must not kill the server
            return ok({"content": [{"type": "text", "text": f"internal error: {type(e).__name__}: {e}"}], "isError": True})
        return ok({"content": [{"type": "text", "text": json.dumps(res)}], "structuredContent": res,
                   "isError": not res.get("ok", False)})
    return err(-32601, f"unknown method {method!r}")


def cmd_serve(rest, stdin=None, stdout=None):
    stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            resp = handle(msg)
        if resp is not None:
            stdout.write(json.dumps(resp) + "\n")
            stdout.flush()
    return 0
