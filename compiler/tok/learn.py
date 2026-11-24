"""The learn pack: a compact guide to Tok for a model that already knows Python and Go.

It is written as a diff: what is the same, what differs, the effect and error rules, common mistakes with their
fixes, and a few complete programs. Every code sample here is data (EXAMPLES, MISTAKES), and tests/test_learn.py
compiles and runs each one, so the guide cannot drift from the compiler.

`tokc learn --budget N` trims the guide to about N tokens. The compiler has no tokenizer (standard library only),
so the budget is counted as characters / 3, an approximation (the guide measures 2.96 characters per token with
tiktoken o200k_base). bench/count_tokens.py measures the real size.
"""

HEAD = """# Tok in one page (for a model that knows Python and Go)

Tok is a small, statically typed, compiled language. Blocks are indented like Python (2 spaces, no tabs, no colons,
no braces). Types and explicit errors are like Go. A program is one or more `fn` at the top level; it starts at
`fn main()`.
"""

SAME = """## Same as Python
- Indentation makes blocks. `if` / `else if` / `else`. `and`, `or`, `not`. `true`, `false`. `//` starts a comment.
- `for x in xs`. `"text {expr}"` is an f-string without the `f`. `+ - * / % **`, comparisons, `==` on any value.
- Lists are `[1, 2, 3]`, indexed from 0 with `xs[i]`, length `xs.len()`.

## Same as Go
- Static types, inferred for locals: `n = 5`. Parameters and results are always typed: `fn add(a: int, b: int) -> int`.
- `int` is 64-bit, `f64` is a double, no implicit numeric conversion: write `f64(n)`, `int(x)`. `7 / 2` is `3`.
- Errors are values returned to the caller, not exceptions.
"""

DIFF = """## Different from both
- A function's last expression is its result. `return x` is only needed to leave early. No `->` means no result.
- Bindings are immutable. `x = 1` creates `x`; assigning again needs `var x = 1` at creation. Parameters and loop
  variables are read-only. No shadowing: a name is defined once per function.
- Loops: `for i in a..b` (both ends included; empty when a > b), `for x in xs`, and a filter:
  `for x in xs where x > 0`. There is no `while`, `break` or `continue`: use a range, `where`, a helper function
  with `return`, or recursion.
- No `import`. No classes, methods on your own types, generics, maps/dicts, sets, tuples, or `null`.
- Records: `type P = { x: int, y: int }`, built with `P { x: 1, y: 2 }`, read with `p.x`. Records and lists are
  values: a copy is independent, and `==` compares contents.
- Lists: `var xs: [int] = []` (an empty list needs its type), `xs.push(v)`, `xs[i] = v`. Methods: `len`, `push`,
  `map`, `where`, `any`, `all`, `contains`, `index_of(v)` (gives `int?`), `sum`, `join(sep)` (strs or numbers),
  `first(n)`, `last(n)`, `drop(n)`, `reverse`, `unique`, `sort()`, `sort(x -> key)`, and `min()`, `max()`,
  `max(x -> key)`, which give an optional (`none` for an empty list). Only `push` changes the list; the others
  give a new one. For descending order: `xs.sort().reverse()`.
  Lambdas are `x -> expr`, one parameter, one expression, only as an argument of a list method.
- Strings: `+`, `len`, `contains`, `starts_with`, `ends_with`, `trim`, `upper`, `lower`, `lines`, `split(sep)`,
  `words()` (split on whitespace), `chars()`, `replace(a, b)`, `reverse`, `repeat(n)`, `first(n)`, `last(n)`,
  `drop(n)`, `find(s)` (gives `int?`), `to_int()` and `to_f64()` (give `int?` / `f64?`). `s[i]` is a one-character
  `str`; there is no character type. To loop over characters: `for c in s.chars()`.
- Numbers as text: `"{n}"`, `n.to_str(2)` (an integer in base 2 to 36), `x.fixed(2)` (an `f64` with 2 decimals).
- Built-ins: `print(v)` (one value, adds a newline), `sqrt`, `abs`, `min(a, b)`, `max(a, b)`, `mean(xs)` (optional).
- Optionals: `T?` holds a value or `none`. `v or default` unwraps. After `if v` or `if v != none`, or after
  `if v == none` followed by `return`, `v` is the plain value. A method called on an optional gives an optional:
  `s.find("x").to_str(10)` is a `str?`.
- `xs[i] or d` gives `d` when `i` is out of range, and so does a whole chain: `args[1].to_int() or 0` is `0` when
  the argument is missing or not a number. Without `or`, a bad index stops the program.
- `int` overflow, a bad index and division by zero stop the program. Printing an `f64` always shows a decimal
  point: `3.0`.
- Program arguments: `fn main(args: [str])`. `args[0]` is the program name; the first argument is `args[1]`, and
  `args.drop(1)` is all of them.
- Standard input: `io.read_all()` gives it all as a `str`; `io.read_line()` gives the next line as a `str?`
  (`none` at the end). Both need `io`.
"""

RULES = """## Effects and errors (the compiler checks both)
- A function lists what it does after `!`: `io` (print, standard input), `fs` (files), `err` (can fail).
  `fn save(path: str, n: int) ! fs, err`. A caller must list every effect of the functions it calls.
  `main` lists `io` / `fs` as needed and never needs `err`.
- Fail with `fail("message")`. Each call to a function with `err` must be handled, one of:
  `v = f()?` pass the error to the caller (in `main`: print `error: message`, exit code 1);
  `v = f() or default` use a default; `v = f() or e -> expr` compute the fallback from the error.
- Typed errors: declare kinds at the top level, `error NotFound, Timeout`, fail with `fail(NotFound, "message")`,
  and branch in a handler block. `e.kind` and `e.msg` describe the error; `fail(e)` passes it on. Plain
  `fail("message")` has kind `Failed`; file errors have kind `FsError`.
- Files: `fs.read_text(path)` gives `str`, `fs.write_text(path, text)`; both need `fs` and can fail.
"""

# (title, source, expected stdout, program arguments), and a fifth item when the program reads standard input
EXAMPLES = [
    ("Functions, a range loop, a filter, list methods",
     '''fn square(n: int) -> int
  n * n

fn main() ! io
  var evens: [int] = []
  for i in 1..10 where i % 2 == 0
    evens.push(square(i))
  print(evens)
  print("sum {evens.sum()}, big ones {evens.where(x -> x > 30).len()}")
  print(evens.map(x -> "{x}").join("+"))
''', '[4, 16, 36, 64, 100]\nsum 220, big ones 3\n4+16+36+64+100\n', []),
    ("Records, optionals, early return",
     '''type User = { name: str, age: int, email: str? }

fn oldest(users: [User]) -> User?
  if users.len() == 0
    return none
  var best = users[0]
  for u in users where u.age > best.age
    best = u
  best

fn main() ! io
  users = [User { name: "Ann", age: 31, email: "ann@example.com" }, User { name: "Bob", age: 45 }]
  top = oldest(users)
  if top
    print("{top.name} is oldest, email {top.email or "unknown"}")
  print(mean(users.map(u -> f64(u.age))) or 0.0)
''', 'Bob is oldest, email unknown\n38.0\n', []),
    ("Arguments, parsing, errors with kinds",
     '''error Missing, NotANumber

fn parse(args: [str], i: int) -> int ! err
  text = args[i] or ""
  if text == ""
    fail(Missing, "argument {i} is missing")
  n = text.to_int()
  if n == none
    fail(NotANumber, "'{text}' is not a number")
  n

fn main(args: [str]) ! io
  a = parse(args, 1)?
  b = parse(args, 2) or e ->
    if e.kind == Missing
      1
    else
      fail(e)
  print(a * b)
''', '42\n', ["6", "7"]),
    ("Standard input, arguments with a default, sorting",
     '''fn main(args: [str]) ! io
  nums = io.read_all().words().map(w -> w.to_int() or 0)
  top = args[1].to_int() or 3
  print(nums.sort().reverse().first(top).join(" "))
  print("{nums.len()} numbers, largest {nums.max() or 0}, squares {nums.first(2).map(n -> n ** 2)}")
''', '9 7 4\n5 numbers, largest 9, squares [16, 81]\n', [], "4 9\n1 7 2\n"),
    ("Reading a file, no loop needed",
     '''fn main(args: [str]) ! io, fs
  text = fs.read_text(args[1] or "missing.txt") or e -> ""
  words = text.lines().where(l -> l.trim() != "")
  print("{words.len()} non-empty lines")
''', '0 non-empty lines\n', []),
]

# (what went wrong, wrong source, error code the compiler gives, corrected source, stdout of the corrected one)
MISTAKES = [
    ("Printing without the `io` effect",
     'fn show(n: int)\n  print(n)\n\nfn main() ! io\n  show(1)\n', "E0201",
     'fn show(n: int) ! io\n  print(n)\n\nfn main() ! io\n  show(1)\n', '1\n'),
    ("Reassigning a binding that is not `var`",
     'fn main() ! io\n  total = 0\n  for i in 1..3\n    total += i\n  print(total)\n', "E0301",
     'fn main() ! io\n  var total = 0\n  for i in 1..3\n    total += i\n  print(total)\n', '6\n'),
    ("Mixing `int` and `f64`",
     'fn main() ! io\n  n = 3\n  print(n * 0.5)\n', "E0106",
     'fn main() ! io\n  n = 3\n  print(f64(n) * 0.5)\n', '1.5\n'),
    ("Ignoring that a call can fail",
     'fn half(n: int) -> int ! err\n  if n % 2 != 0\n    fail("{n} is odd")\n  n / 2\n\nfn main() ! io\n  print(half(8))\n',
     "E0203",
     'fn half(n: int) -> int ! err\n  if n % 2 != 0\n    fail("{n} is odd")\n  n / 2\n\nfn main() ! io\n  print(half(8)?)\n',
     '4\n'),
    ("Using an optional as if it were the value",
     'fn main() ! io\n  n = "12".to_int()\n  print(n + 1)\n', "E0103",
     'fn main() ! io\n  n = "12".to_int() or 0\n  print(n + 1)\n', '13\n'),
    ("An empty list with no type",
     'fn main() ! io\n  var xs = []\n  xs.push(1)\n  print(xs)\n', "E0108",
     'fn main() ! io\n  var xs: [int] = []\n  xs.push(1)\n  print(xs)\n', '[1]\n'),
    ("A value-returning function whose last statement is a loop or an `if` without `else`",
     'fn sign(n: int) -> int\n  if n < 0\n    return -1\n  if n > 0\n    return 1\n\nfn main() ! io\n  print(sign(-4))\n', "E0105",
     'fn sign(n: int) -> int\n  if n < 0\n    return -1\n  if n > 0\n    return 1\n  0\n\nfn main() ! io\n  print(sign(-4))\n', '-1\n'),
    ("Writing `while`: count with a range, or recurse",
     'fn main() ! io\n  var n = 27\n  var steps = 0\n  while n != 1\n    steps += 1\n  print(steps)\n', "E0010",
     'fn collatz(n: int, steps: int) -> int\n  if n == 1\n    return steps\n  if n % 2 == 0\n    return collatz(n / 2, steps + 1)\n'
     '  collatz(3 * n + 1, steps + 1)\n\nfn main() ! io\n  print(collatz(27, 0))\n', '111\n'),
]

TAIL = """## Checklist before you answer
1. Every function that prints has `! io`; every caller of it too. 2. Anything reassigned is `var`.
3. Calls to `err` functions end in `?` or have `or`. 4. No `while`, `break`, `import`, `null` or dicts.
5. Numbers of different types are converted explicitly. 6. Print exactly what the task asks for.
"""


def fence(src):
    return "```\n" + src.rstrip("\n") + "\n```\n"


def example_text(ex):
    title, src, out, args = ex[:4]
    shown = out.rstrip("\n").replace("\n", " | ")
    with_args = f" (run with arguments: {' '.join(args)})" if args else ""
    if len(ex) > 4:
        with_args = f" (standard input: {ex[4].rstrip(chr(10)).replace(chr(10), ' | ')})"
    return f"### {title}\n{fence(src)}Prints{with_args}: `{shown}`\n"


def mistake_text(m):
    what, bad, code, good, _ = m
    return f"### {what} ({code})\nWrong:\n{fence(bad)}Right:\n{fence(good)}"


def sections():
    """(key, title, text, priority). Lower priority numbers are kept longer under a budget."""
    out = [("intro", "Overview", HEAD, 0), ("same", "Same as Python and Go", SAME, 0),
           ("different", "Different from both", DIFF, 0), ("effects", "Effects and errors", RULES, 0)]
    for i, m in enumerate(MISTAKES):
        out.append((f"mistake{i + 1}", m[0], mistake_text(m), 2))
    for i, ex in enumerate(EXAMPLES):
        out.append((f"example{i + 1}", ex[0], example_text(ex), 1 if i == 0 else 3))
    out.append(("checklist", "Checklist", TAIL, 1))
    return out


def approx_tokens(text):
    return (len(text) + 2) // 3


def render(budget=None):
    """The guide as markdown. With a budget, drop the lowest-priority pieces (last first) until it fits."""
    parts = sections()
    def text(ps):
        out, seen = [], set()
        for key, _, body, _ in ps:
            group = "mistake" if key.startswith("mistake") else "example" if key.startswith("example") else None
            if group and group not in seen:
                seen.add(group)
                out.append("## Common mistakes and their fixes\n" if group == "mistake" else "## Complete programs\n")
            out.append(body)
        return "\n".join(out)
    if budget is not None:
        while approx_tokens(text(parts)) > budget:
            worst = max(p[3] for p in parts)
            if worst == 0:
                break
            for i in range(len(parts) - 1, -1, -1):
                if parts[i][3] == worst:
                    del parts[i]
                    break
    return text(parts)


def cmd_learn(rest):
    import sys
    budget = None
    if "--budget" in rest:
        i = rest.index("--budget")
        try:
            budget = int(rest[i + 1])
        except (IndexError, ValueError):
            sys.stderr.write("tokc: --budget needs a number of tokens\n")
            return 64
    out = render(budget)
    sys.stdout.write(out)
    if budget is not None and approx_tokens(out) > budget:
        sys.stderr.write(f"tokc: the core of the guide is about {approx_tokens(out)} tokens (characters / 3) and "
                         f"cannot be trimmed to {budget}\n")
    return 0
