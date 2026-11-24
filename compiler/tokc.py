#!/usr/bin/env python3
"""tokc - bootstrap compiler for Tok (placeholder name).

Pipeline: source -> tokens -> AST -> check (types + effects) -> C -> cc -> native binary.
The compiler itself lives in the `tok` package next to this file.
"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tok.compiler import compile_file  # noqa: E402
from tok.diag import apply_fix  # noqa: E402
from tok.fmt import cmd_fmt  # noqa: E402


def cc_build(c_code, out_path, optimize):
    cc = os.environ.get("CC", "cc")
    with tempfile.TemporaryDirectory() as d:
        c_path = os.path.join(d, "out.c")
        with open(c_path, "w") as fh:
            fh.write(c_code)
        cmd = [cc, "-std=c11", "-O2" if optimize else "-O0", "-o", out_path, c_path, "-lm"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            sys.stderr.write("tokc: internal error: the generated C did not compile. This is a compiler bug.\n")
            sys.stderr.write(r.stderr)
            sys.exit(70)


def show_errors(comp, as_json):
    errs = comp.sorted_errors()
    if as_json:
        print(json.dumps({"ok": False, "errors": [d.to_json() for d in errs]}))
        return
    lines = {m.path: m.src.split("\n") for m in comp.modlist}
    for d in errs:
        sys.stderr.write(d.to_text(lines.get(d.file, [])) + "\n")
    if len(errs) > 1:
        sys.stderr.write(f"{len(errs)} errors\n")


USAGE = """usage:
  tokc run <file.tok> [-- args]    compile fast (-O0, cached) and run
  tokc build <file.tok> [-o out]   compile optimized (-O2) to a native binary
  tokc check <file.tok> [--json]   type and effect check only; reports every independent error
  tokc fix <file.tok>              apply suggested fixes until the file checks
  tokc fmt <file.tok> [--check]    rewrite the file in the canonical layout (--check: only report)
  tokc emit-c <file.tok>           print the generated C
"""

COMMANDS = {"run", "build", "check", "fix", "emit-c"}
EXTRA = {"fmt": cmd_fmt}


def cmd_fix(path, src):
    applied = 0
    for _ in range(200):
        _, comp = compile_file(path, src)
        errs = comp.sorted_errors()
        if not errs:
            break
        fixable = [d for d in errs if d.fix and d.file == path]
        if not fixable:
            show_errors(comp, False)
            sys.stderr.write(f"tokc: applied {applied} fix(es); the remaining error(s) have no automatic fix\n")
            if applied:
                with open(path, "w") as fh:
                    fh.write(src)
            return 1
        d = fixable[0]
        src = apply_fix(src, d.fix)
        applied += 1
        print(f"fixed {d.code} at {path}:{d.line}: {d.fix['note']}")
    with open(path, "w") as fh:
        fh.write(src)
    print(f"{path}: ok ({applied} fix(es) applied)")
    return 0


def main(argv):
    if len(argv) < 2 or argv[0] not in COMMANDS | set(EXTRA):
        sys.stderr.write(USAGE)
        return 64
    cmd, path, rest = argv[0], argv[1], argv[2:]
    try:
        with open(path) as fh:
            src = fh.read()
    except OSError as e:
        sys.stderr.write(f"tokc: cannot read {path}: {e.strerror}\n")
        return 66
    if cmd in EXTRA:
        return EXTRA[cmd](path, src, rest)
    if cmd == "fix":
        return cmd_fix(path, src)
    as_json = cmd == "check" and "--json" in rest
    c_code, comp = compile_file(path, src)
    if c_code is None:
        show_errors(comp, as_json)
        return 1
    if cmd == "check":
        print(json.dumps({"ok": True, "errors": []}) if as_json else f"{path}: ok")
        return 0
    if cmd == "emit-c":
        sys.stdout.write(c_code)
        return 0
    if cmd == "build":
        out = rest[rest.index("-o") + 1] if "-o" in rest and rest.index("-o") + 1 < len(rest) else os.path.splitext(os.path.basename(path))[0]
        cc_build(c_code, out, optimize=True)
        print(f"built {out}")
        return 0
    # run: cache the debug binary by a hash of the generated C
    cache = os.path.join(os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")), "tok")
    os.makedirs(cache, exist_ok=True)
    exe = os.path.join(cache, hashlib.sha256(c_code.encode()).hexdigest()[:24])
    if not os.path.exists(exe):
        tmp_exe = exe + f".tmp{os.getpid()}"
        cc_build(c_code, tmp_exe, optimize=False)
        os.replace(tmp_exe, exe)
    args = rest[1:] if rest[:1] == ["--"] else rest
    return subprocess.run([path] + args, executable=exe).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
