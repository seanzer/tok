"""tokc fmt: the canonical layout. One input has exactly one formatted form.

The formatter works on tokens, line by line, so it never changes what a program
means: it only rewrites indentation, the spaces between tokens, blank lines and
the spacing of comments. String literals (including the code inside
interpolations) and number literals are copied through unchanged.

Rules:
  - 2 spaces per block level, no tabs, no trailing spaces, one newline at the end
  - one space around binary operators, '=', '->' and the '!' before effects
  - no space inside ( ) and [ ], one space inside { }, '{}' when empty
  - no space before ',' ':' '?' or around '.' and '..'; one space after ',' and ':'
  - no space between a function, method or list and its '(' or '['
  - a unary '-' is attached to its operand
  - comments start with '// ' and a trailing comment is two spaces after the code;
    a comment line is indented like the code after it, or like the block it ends
  - one blank line before each top-level fn/type; never two blank lines in a row;
    none at the start of the file or of a block
"""
from .diag import Diag
from .lexer import KEYWORDS, lex, lex_fragment

VALUE_WORDS = {"true", "false", "none"}
BINARY = {"+", "-", "*", "/", "%", "==", "!=", "<=", ">=", "<", ">", "=", "+=", "-=", "*=", "/=", "%=", "->", "!"}
CLOSERS = {")", "]", "}"}


def is_op(t, *vals):
    return t.kind == "OP" and (not vals or t.val in vals)


def is_keyword(toks, i):
    """'where' after a dot is a method name, not the keyword."""
    t = toks[i]
    return t.kind == "NAME" and t.val in KEYWORDS and not (i > 0 and is_op(toks[i - 1], "."))


def is_word(toks, i):
    """A token that ends a value: a name, a literal, or a closing bracket."""
    t = toks[i]
    if t.kind in ("INT", "FLOAT", "STR"):
        return True
    if t.kind == "NAME":
        return not is_keyword(toks, i) or t.val in VALUE_WORDS
    return is_op(t, ")", "]", "}", "?")


def is_unary_minus(toks, i):
    return is_op(toks[i], "-") and (i == 0 or not is_word(toks, i - 1))


def gap(toks, i):
    """The text between toks[i - 1] and toks[i]: '' or ' '."""
    a, b = toks[i - 1], toks[i]
    if is_op(a, "(", "[", ".", "..") or is_op(b, ")", "]", ",", ":", ".", "..", "?"):
        return ""
    if is_op(a, "{"):
        return "" if is_op(b, "}") else " "
    if is_op(b, "}"):
        return " "
    if is_unary_minus(toks, i - 1):
        return ""
    if is_op(b, "(", "["):
        # a call, a method call or an index directly follows a value; a keyword or operator does not
        callee = (a.kind == "NAME" and not is_keyword(toks, i - 1)) or a.kind == "STR" or is_op(a, ")", "]")
        return "" if callee else " "
    return " "


def format_code(text, toks):
    out = []
    for i, t in enumerate(toks):
        if i:
            out.append(gap(toks, i))
        out.append(text[t.col - 1:t.end - 1])
    return "".join(out)


def format_comment(c):
    body = c[2:].rstrip()
    if body and body[0] not in " /":
        body = " " + body
    return "//" + body


def format_source(src):
    """Returns the formatted text. Raises Diag if the file cannot be tokenized."""
    lex(src)  # tabs, bad indentation and bad tokens are reported, not reformatted
    rows, indents = [], [0]  # rows: [kind, depth, text], kind in "code" | "comment" | "blank"
    for ln, text in enumerate(src.split("\n"), 1):
        comments = []
        toks = lex_fragment(text, ln, 1, comments)
        comment = format_comment(comments[0]) if comments else None
        ind = len(text) - len(text.lstrip(" "))
        if not toks:
            own = sum(1 for level in indents if level <= ind) - 1
            rows.append(["comment", own, comment] if comment else ["blank", 0, ""])
            continue
        if ind > indents[-1]:
            indents.append(ind)
        while ind < indents[-1]:
            indents.pop()
        code = format_code(text, toks)
        rows.append(["code", len(indents) - 1, code + ("  " + comment if comment else ""), toks[0].val])
    # a comment line is indented like the next line of code, or stays in the block it was written in
    depth = 0
    for row in reversed(rows):
        if row[0] == "code":
            depth = row[1]
        elif row[0] == "comment":
            row[1] = max(row[1], depth)
    def top_comment(r):
        return r[0] == "comment" and r[1] == 0

    def starts_decl(i):
        """True if rows[i] begins a top-level fn/type, counting the comment lines directly above it."""
        if i > 0 and top_comment(rows[i - 1]):
            return False
        while i < len(rows) and top_comment(rows[i]):
            i += 1
        return i < len(rows) and rows[i][0] == "code" and rows[i][1] == 0 and rows[i][3] in ("fn", "type")

    out = []
    for i, row in enumerate(rows):
        if row[0] == "blank":
            continue
        if out and out[-1] != "":
            after_blank = rows[i - 1][0] == "blank"
            opens_block = row[1] > out[-1][1]
            if starts_decl(i) or (after_blank and not opens_block):
                out.append("")
        out.append((row[2], row[1]))
    lines = ["" if o == "" else "  " * o[1] + o[0] for o in out]
    return "\n".join(lines) + "\n" if lines else ""


def cmd_fmt(path, src, rest):
    import sys
    try:
        new = format_source(src)
    except Diag as d:
        d.file = path
        sys.stderr.write(d.to_text(src.split("\n")) + "\n")
        return 1
    if "--check" in rest:
        if new != src:
            print(f"{path}: not formatted (run: tokc fmt {path})")
            return 1
        print(f"{path}: formatted")
        return 0
    if new != src:
        with open(path, "w") as fh:
            fh.write(new)
        print(f"{path}: reformatted")
    else:
        print(f"{path}: already formatted")
    return 0
