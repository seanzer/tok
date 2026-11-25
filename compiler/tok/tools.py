"""The compiler as a library: check, fix, format and run a program given as text. Used by `tokc serve` and the
benchmark runner. Nothing here prints."""
import os
import subprocess
import tempfile

from .compiler import compile_file
from .diag import Diag, apply_fix
from .fmt import format_source


def diagnostics(comp):
    return [d.to_json() for d in comp.sorted_errors()]


def check(src, path="main.tok"):
    c_code, comp = compile_file(path, src)
    return {"ok": c_code is not None, "errors": diagnostics(comp)}


def fix(src, path="main.tok"):
    """Apply the compiler's suggested fixes until the program checks or no fix is left."""
    applied = []
    for _ in range(200):
        c_code, comp = compile_file(path, src)
        errs = comp.sorted_errors()
        if not errs:
            break
        fixable = [d for d in errs if d.fix and d.file == path]
        if not fixable:
            break
        d = fixable[0]
        src = apply_fix(src, d.fix)
        applied.append({"code": d.code, "line": d.line, "note": d.fix["note"]})
    res = check(src, path)
    return {"ok": res["ok"], "source": src, "applied": applied, "errors": res["errors"]}


def fmt(src):
    try:
        new = format_source(src)
    except Diag as d:
        d.file = d.file or "main.tok"
        return {"ok": False, "errors": [d.to_json()]}
    return {"ok": True, "source": new, "changed": new != src}


SANITIZE = ["-O1", "-g", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined", "-fno-omit-frame-pointer",
            "-DTOK_LEAK_CHECK"]


def cc_flags(optimize, sanitize=False):
    """-O2 for `build`, -O0 for `run`. `sanitize` builds with AddressSanitizer (which includes the leak checker) and
    UndefinedBehaviorSanitizer. TOK_LEAK_CHECK=1 in the environment makes the program count its own allocations and
    exit with 71 if any are left at the end. TOK_SANITIZE=1 turns `sanitize` on for every build and run."""
    sanitize = sanitize or os.environ.get("TOK_SANITIZE") == "1"
    flags = list(SANITIZE) if sanitize else ["-O2" if optimize else "-O0"]
    if os.environ.get("TOK_LEAK_CHECK") == "1" and not sanitize:
        flags.append("-DTOK_LEAK_CHECK")
    return flags


def run(src, args=(), files=None, timeout=10.0, optimize=False, max_output=65536, stdin="", sanitize=False):
    """Compile and run. `sanitize` runs the program under the address, leak and undefined-behavior sanitizers. `files` maps extra file names (modules, data) to their text; they are written next to
    the program, and the program runs in that folder. `stdin` is the text on its standard input."""
    with tempfile.TemporaryDirectory(prefix="tokrun") as d:
        for name, text in (files or {}).items():
            if os.path.basename(name) != name or name in ("main.tok", "main", "main.c"):
                return {"ok": False, "stage": "setup", "errors": [], "message": f"bad file name {name!r}"}
            with open(os.path.join(d, name), "w") as fh:
                fh.write(text)
        path = os.path.join(d, "main.tok")
        with open(path, "w") as fh:
            fh.write(src)
        c_code, comp = compile_file(path, src)
        if c_code is None:
            errs = diagnostics(comp)
            for e in errs:
                e["file"] = os.path.basename(e["file"])
            return {"ok": False, "stage": "check", "errors": errs}
        c_path, exe = os.path.join(d, "main.c"), os.path.join(d, "main")
        with open(c_path, "w") as fh:
            fh.write(c_code)
        cc = subprocess.run([os.environ.get("CC", "cc"), "-std=c11"] + cc_flags(optimize, sanitize) + ["-o", exe, c_path, "-lm"],
                            capture_output=True, text=True)
        if cc.returncode != 0:
            return {"ok": False, "stage": "cc", "errors": [], "message": "internal error: the generated C did not compile",
                    "stderr": cc.stderr[:4000]}
        try:
            r = subprocess.run(["main.tok"] + [str(a) for a in args], executable=exe, cwd=d, capture_output=True,
                               text=True, errors="replace", timeout=timeout, input=stdin or "")
        except subprocess.TimeoutExpired:
            return {"ok": False, "stage": "run", "errors": [], "timed_out": True, "message": f"timed out after {timeout}s"}
        return {"ok": r.returncode == 0, "stage": "run", "errors": [], "exit_code": r.returncode,
                "stdout": r.stdout[:max_output], "stderr": r.stderr[:max_output]}
