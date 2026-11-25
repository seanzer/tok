"""Lexer: source text -> tokens, with Python-style INDENT / DEDENT."""
from .diag import Diag

KEYWORDS = {"fn", "type", "var", "if", "else", "for", "in", "where", "return",
            "and", "or", "not", "true", "false", "none", "then", "while", "break", "continue", "let"}
TWO_CHAR = {"->", "..", "**", "==", "!=", "<=", ">=", "+=", "-=", "*=", "/=", "%="}
ONE_CHAR = set("()[]{}+-*/%<>=!,:.?")


class Tok:
    __slots__ = ("kind", "val", "line", "col", "end")

    def __init__(self, kind, val, line, col, end):
        self.kind, self.val, self.line, self.col, self.end = kind, val, line, col, end

    def __repr__(self):
        return f"{self.kind}:{self.val!r}@{self.line}:{self.col}"


def interp_end(text, i):
    """Index of the '}' that closes an interpolation starting at i, or -1. Skips nested strings and braces."""
    depth, n = 1, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def lex_fragment(text, line, col0=1, comments=None):
    """Tokenize one line (or one interpolation fragment). Columns are 1-based.

    If `comments` is a list, the text of a trailing // comment is appended to it.
    """
    toks, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        col = col0 + i
        if c in " \t":
            i += 1
        elif text.startswith("//", i):
            if comments is not None:
                comments.append(text[i:])
            break
        elif c.isdigit():
            j = i
            while j < n and text[j].isdigit():
                j += 1
            is_float = False
            if j + 1 < n and text[j] == "." and text[j + 1].isdigit():
                is_float = True
                j += 1
                while j < n and text[j].isdigit():
                    j += 1
            if j < n and text[j] in "eE":
                k = j + 1
                if k < n and text[k] in "+-":
                    k += 1
                if k < n and text[k].isdigit():
                    is_float = True
                    while k < n and text[k].isdigit():
                        k += 1
                    j = k
            if j < n and (text[j].isalpha() or text[j] == "_"):
                raise Diag("E0006", f"invalid number literal '{text[i:j + 1]}'", line, col, col0 + j + 1)
            if is_float:
                toks.append(Tok("FLOAT", text[i:j], line, col, col0 + j))
            else:
                toks.append(Tok("INT", int(text[i:j]), line, col, col0 + j))
            i = j
        elif c.isalpha() or c == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            toks.append(Tok("NAME", text[i:j], line, col, col0 + j))
            i = j
        elif c == '"':
            parts, buf, j = [], [], i + 1
            while True:
                if j >= n:
                    raise Diag("E0003", "unterminated string literal", line, col, col0 + n)
                ch = text[j]
                if ch == '"':
                    break
                if ch == "\\":
                    esc = text[j + 1] if j + 1 < n else ""
                    if esc == "" or esc not in 'ntr"\\{}':
                        raise Diag("E0004", f"unknown escape '\\{esc}'", line, col0 + j, col0 + j + 2)
                    buf.append({"n": "\n", "t": "\t", "r": "\r"}.get(esc, esc))
                    j += 2
                elif ch == "{":
                    k = interp_end(text, j + 1)
                    if k < 0:
                        raise Diag("E0005", "unclosed '{' in string interpolation", line, col0 + j)
                    if buf:
                        parts.append(("s", "".join(buf)))
                        buf = []
                    parts.append(("e", text[j + 1:k], col0 + j + 1))
                    j = k + 1
                else:
                    buf.append(ch)
                    j += 1
            if buf or not parts:
                parts.append(("s", "".join(buf)))
            toks.append(Tok("STR", parts, line, col, col0 + j + 1))
            i = j + 1
        elif text[i:i + 2] in TWO_CHAR:
            toks.append(Tok("OP", text[i:i + 2], line, col, col + 2))
            i += 2
        elif c in ONE_CHAR:
            toks.append(Tok("OP", c, line, col, col + 1))
            i += 1
        else:
            raise Diag("E0001", f"unexpected character {c!r}", line, col)
    return toks


def lex(src):
    toks, indents = [], [0]
    lines = src.split("\n")
    for ln, text in enumerate(lines, 1):
        line_toks = lex_fragment(text, ln)
        if not line_toks:
            continue
        lead = text[:len(text) - len(text.lstrip(" \t"))]
        if "\t" in lead:
            raise Diag("E0002", "tabs are not allowed for indentation; use spaces", ln, 1, len(lead) + 1)
        ind = len(lead)
        if ind > indents[-1]:
            indents.append(ind)
            toks.append(Tok("INDENT", None, ln, 1, ind + 1))
        while ind < indents[-1]:
            indents.pop()
            toks.append(Tok("DEDENT", None, ln, 1, ind + 1))
        if ind != indents[-1]:
            raise Diag("E0002", "indentation does not match any enclosing block", ln, 1, ind + 1)
        toks.extend(line_toks)
        toks.append(Tok("NEWLINE", None, ln, len(text) + 1, len(text) + 1))
    last = len(lines)
    while len(indents) > 1:
        indents.pop()
        toks.append(Tok("DEDENT", None, last, 1, 1))
    toks.append(Tok("EOF", None, last, 1, 1))
    return toks
