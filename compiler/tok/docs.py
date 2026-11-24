"""Doc lookup for tools: error codes, guide sections, and built-in names."""
from . import learn

# One line per error code. tests/test_serve.py checks that every code the compiler can raise is listed here.
CODES = {
    "E0001": "A character that is not part of the language.",
    "E0002": "Bad indentation: use spaces, and indent a block consistently.",
    "E0003": "A string literal is not closed on its line.",
    "E0004": "Unknown escape in a string. Known: \\n \\t \\\\ \\\" \\{ \\}.",
    "E0005": "A '{' in a string interpolation is not closed. Write \\{ for a literal brace.",
    "E0006": "A number literal is malformed.",
    "E0010": "Syntax error: the parser found something it did not expect. Often a construct Tok does not have "
             "(while, break, import, a colon after if/for/fn).",
    "E0012": "A keyword is used as a name.",
    "E0013": "Unknown effect after '!'. Known effects: io, fs, net, time, rand, py, err.",
    "E0014": "Empty '{}' in a string. Write \\{ for a literal brace.",
    "E0016": "The left side of an assignment is not a variable, a field, or a list element.",
    "E0101": "Unknown name: no variable, function, method, or module function with that name.",
    "E0102": "The name is already taken (a function, type, error kind, built-in, or an earlier variable).",
    "E0103": "Type mismatch: the value has a different type from what is needed here. Optionals need `or default` "
             "or a test first.",
    "E0104": "Wrong number of arguments.",
    "E0105": "A function (or error handler) that gives a value has a path that does not end in one. End with an "
             "expression, or add an `else`.",
    "E0106": "Two different numeric types in one operation. Convert one side, for example f64(n).",
    "E0107": "A number literal does not fit its type.",
    "E0108": "The type cannot be inferred from `[]` or `none`. Annotate: `xs: [int] = []`, `x: int? = none`.",
    "E0109": "The method or field does not exist on this type.",
    "E0110": "Bad type: unknown type name, an optional of an optional, or a record literal of a non-record.",
    "E0111": "A record literal has a missing, repeated or unknown field.",
    "E0112": "A record type contains itself.",
    "E0113": "A lambda in a place that does not take one. Lambdas go directly in map/where/any/all, or after "
             "`or` as an error handler.",
    "E0201": "The function uses an effect (io, fs) that its signature does not list. Add it after '!'.",
    "E0202": "The function can fail (uses `fail` or `?`) but does not list `err` after '!'.",
    "E0203": "A call that can fail is not handled. Add `?`, `or default`, or `or e -> ...`.",
    "E0204": "`?` is used on something that cannot fail, or where the error cannot be passed up.",
    "E0205": "A test or default on an optional that is already known to have a value here.",
    "E0301": "Assignment to an immutable binding. Declare it with `var`. Parameters and loop variables are read-only.",
    "E0302": "`var` or a type annotation on a name that already exists. Drop it to assign.",
    "E0401": "The program has no `main` function.",
    "E0402": "`main` takes no parameters, or one `[str]` parameter.",
    "E0501": "Unknown name or module: no variable, built-in module, or `<name>.tok` file next to the program.",
    "E0900": "The feature is not supported by this compiler.",
}

BUILTINS = {
    "print": "print(v) ! io: prints one value of any type and a newline.",
    "fail": "fail(\"msg\") | fail(Kind, \"msg\") | fail(Kind) | fail(e): return an error to the caller. Needs `err`.",
    "sqrt": "sqrt(x: f64) -> f64", "abs": "abs(x) -> same numeric type",
    "min": "min(a, b) -> the smaller; min(xs) -> optional, none for an empty list",
    "max": "max(a, b) -> the larger; max(xs) -> optional, none for an empty list",
    "mean": "mean(xs: [f64]) -> f64?, none for an empty list",
    "io.read_line": "io.read_line() -> str? ! io: the next line of standard input without its newline, none at the end.",
    "io.read_all": "io.read_all() -> str ! io: the rest of standard input.",
    "**": "a ** b: power. Binds tighter than a unary minus on its left (-2 ** 2 is -4) and groups to the right. "
          "int stops the program on overflow or a negative exponent; f64 uses pow.",
    "int": "integer methods: to_str(base) -> str, the digits in base 2 to 36. \"{n}\" gives the decimal text.",
    "f64": "float methods: fixed(digits) -> str, exactly that many digits after the point.",
    "fs.read_text": "fs.read_text(path: str) -> str ! fs, err",
    "fs.write_text": "fs.write_text(path: str, text: str) ! fs, err",
    "str": "str methods: len, contains(s), starts_with(s), ends_with(s), trim, upper, lower, lines, split(sep), words, "
           "chars, replace(a, b), reverse, repeat(n), first(n), last(n), drop(n), find(s) -> int?, to_int() -> int?, "
           "to_f64() -> f64?. s[i] is a one-character str (bytes; out of range stops the program, `s[i] or d` does not).",
    "list": "list methods: len, push(v), map(x -> e), where(x -> cond), any(x -> cond), all(x -> cond), contains(v), "
            "index_of(v) -> int?, sum, join(sep) on strs, numbers and bools, first(n), last(n), drop(n), reverse, unique, "
            "sort() on numbers and strs, sort(x -> key) (stable), min(), max(), min(x -> key), max(x -> key) -> optional. "
            "Only push changes the list. xs[i], and `xs[i] or d` for a checked lookup.",
    "or": "`v or d`: the value of an optional, or d. `call() or d`: the default when a call fails. Under `or`, every "
          "lookup in the chain on the left is checked: `args[1].to_int() or 0` gives 0 when the argument is missing. "
          "A method called on an optional gives an optional.",
    "Error": "Error: a caught error. Fields: kind (ErrorKind), msg (str). Bound by `call() or e -> ...`.",
}


def topics():
    return sorted(CODES) + sorted(BUILTINS) + [k for k, _, _, _ in learn.sections()] + ["guide"]


def lookup(topic):
    """Text for a topic, or None. A topic is an error code, a built-in name, a guide section key, or 'guide'."""
    t = topic.strip()
    if t.upper() in CODES:
        return f"{t.upper()}: {CODES[t.upper()]}"
    if t in BUILTINS:
        return BUILTINS[t]
    if t == "guide":
        return learn.render()
    low = t.lower()
    for key, title, body, _ in learn.sections():
        if low == key or low == title.lower():
            return body
    hits = [body for key, title, body, _ in learn.sections() if low in title.lower()]
    hits += [v for k, v in BUILTINS.items() if low in k.lower()]
    return "\n".join(hits) if hits else None
