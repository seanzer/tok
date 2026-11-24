"""Structured diagnostics: data first, prose second."""


class Diag(Exception):
    def __init__(self, code, message, line, col, end_col=None, fix=None):
        super().__init__(message)
        self.code, self.message, self.line, self.col = code, message, line, col
        self.end_col = end_col if end_col is not None else col + 1
        self.fix = fix  # see make_fix()
        self.file = None

    def to_json(self):
        d = {"code": self.code, "message": self.message, "file": self.file,
             "line": self.line, "col": self.col, "end_col": self.end_col}
        if self.fix:
            d["fix"] = self.fix
        return d

    def to_text(self, src_lines):
        out = [f"{self.file}:{self.line}:{self.col}: error[{self.code}]: {self.message}"]
        if 1 <= self.line <= len(src_lines):
            out.append("  " + src_lines[self.line - 1])
            out.append("  " + " " * (self.col - 1) + "^" * max(1, self.end_col - self.col))
        if self.fix:
            out.append(f"  fix: {self.fix['note']}")
        return "\n".join(out)


def make_fix(note, edits):
    """edits: list of (line, col, insert[, delete]). Columns are 1-based.

    The JSON shape keeps line/col/insert of the first edit at the top level,
    and always carries the full list under "edits".
    """
    es = []
    for e in edits:
        d = {"line": e[0], "col": e[1], "insert": e[2]}
        if len(e) > 3 and e[3]:
            d["delete"] = e[3]
        es.append(d)
    fix = {"line": es[0]["line"], "col": es[0]["col"], "insert": es[0]["insert"], "note": note, "edits": es}
    return fix


def apply_fix(src, fix):
    lines = src.split("\n")
    for e in sorted(fix["edits"], key=lambda e: (e["line"], e["col"]), reverse=True):
        text = lines[e["line"] - 1]
        c = e["col"] - 1
        lines[e["line"] - 1] = text[:c] + e["insert"] + text[c + e.get("delete", 0):]
    return "\n".join(lines)
