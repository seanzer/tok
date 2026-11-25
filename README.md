# Tok

An LLM-optimized programming language that is still readable by humans. Token-efficient to write, native-fast to run.

"Tok" is a placeholder name. The v0 design doc lives outside this repo for now.

## Status

Phase 1 of the plan (the core language) is in, with the design decisions from the first review applied, and the
Phase 2 tooling (learn pack, tool server, benchmark harness) is built. No model benchmark has been run yet.
Phase 3 so far: the memory model (reference counts, copy-on-write, nothing leaks) and a performance suite against
C and Python; see "Memory" and "Performance".
The whole pipeline runs end to end:

```
source -> tokens -> AST -> type + effect check -> C -> cc -> native binary
```

The compiler is written in Python (3.9+, standard library only) to bootstrap quickly. The generated C is C11 and
compiles without warnings under `cc -std=c11 -Wall -Wextra`; the tests enforce that for every program they run.

## Hello world

```
print("hello, world")
```

A program is a file of statements, run from top to bottom (the script form). Functions, record types and error
kinds are declared at the top level, before or after the statements that use them:

```
fn square(n: int) -> int
  n * n

for a in args
  print(square(a.to_int() or 0))
```

- `args` is the list of program arguments (a `[str]`, without the program name). With `tokc run` they come after `--`.
- The effects of the top-level statements are inferred. Functions still list theirs after `!`, and so does
  everything in a module.
- `?` and `fail` work at the top level: the error is printed as `error: <message>` on stderr and the exit code is 1.
  `return` ends the program early.
- A top-level variable belongs to the script, not to the functions: a function sees only its parameters.
- The older form still works: `fn main() ! io` or `fn main(args: [str]) ! io` as the entry point. A file has one or
  the other; statements at the top level next to `fn main` are an error (E0403), and so are statements in a module
  (E0404).

```
python3 compiler/tokc.py run examples/hello.tok      # script mode: fast compile, cached
python3 compiler/tokc.py build examples/hello.tok    # optimized native binary: ./hello
```

Requirements: Python 3.9+ and a C compiler on `PATH` as `cc` (or set `CC`).

## Commands

| Command | What it does |
|---|---|
| `tokc run <file> [-- args]` | Compile at `-O0`, cache the binary, run it with the given arguments |
| `tokc build <file> [-o out]` | Compile at `-O2` to a native binary |
| `tokc run <file> --sanitize [-- args]`, `tokc build <file> --sanitize` | The same under AddressSanitizer, LeakSanitizer and UndefinedBehaviorSanitizer (`-O1 -g`) |
| `tokc check <file> [--json]` | Type and effect check only; reports every independent error in one run |
| `tokc fix <file>` | Apply the compiler's suggested fixes until the file checks |
| `tokc fmt <file> [--check]` | Rewrite the file in the canonical layout (`--check` only reports) |
| `tokc emit-c <file>` | Print the generated C |
| `tokc learn [--budget N]` | Print the language guide written for a model (trimmed to about N tokens) |
| `tokc serve` | Tool server: JSON-RPC over stdio with the tools check, fix, run, format, doc |

## What is covered

**Basics**
- Functions with typed parameters, return types and implicit last-expression return
- `int`, `f64`, `str`, `bool`, with local type inference; `name: type = value` when you want to state the type
- Immutable bindings by default, `var` for mutable ones; `+= -= *= /= %=`
- `if` / `else if` / `else`; `for i in a..b` (inclusive range); `for x in xs`; `for x in xs where cond`
- Multiple assignment: `a, b = b, a + b`. Every value on the right is computed before anything is stored, so
  this swaps. The left side may hold variables, fields and list elements; a name that does not exist yet is
  created. `let a, b = x, y` and `var a, b = 0, 1` always create (immutable / mutable); `let x = 1` is the
  one-name form. The two sides must have the same count (`E0018`).
- `while cond`; `break` and `continue` in `while` and `for` (they act on the innermost loop). Inside
  `while v`, an optional `v` is the plain value up to the first statement that assigns it.
- Conditional expression: `if c then a else b`, chained with `else if c2 then ...`. Only the chosen branch runs.
  Both branches have one type; a branch may be `none` (the result is optional) or `fail(...)`. The condition
  narrows optionals inside the branches. The Python order `a if c else b` is a syntax error that says so.
- String interpolation: `"sum = {a + b}"`, including nested string literals inside `{}`
- `//` comments

**Records**
- `type User = { id: int, name: str, email: str? }`, literals `User { id: 1, name: "Ann" }` (optional fields may be
  left out), field access, field assignment on a `var`, `==`, printing

**Lists**
- `[T]`, literals, indexing (out of range stops the program with the source line), element assignment and `push`
  on a `var`, `==`, printing
- Methods: `len`, `push`, `map`, `where`, `any`, `all`, `contains`, `index_of(v)` (an `int?`), `sum`,
  `join(sep)` (on lists of strs, numbers or bools), `first(n)`, `last(n)`, `drop(n)`, `reverse`, `unique`
- Order: `sort()` on numbers and strs, `sort(x -> key)` by a number or str key (stable); `min()`, `max()`,
  `min(x -> key)`, `max(x -> key)` give an optional, `none` for an empty list, and the first of equal elements.
  Descending order is `xs.sort().reverse()`.
- Only `push` changes a list. Every other method gives a new list, so none of them needs a `var`.

**Dicts** (syntax provisional)
- `{K: V}` is a map with `str` or integer keys: `d = {"a": 1, "b": 2}`, `var seen: {str: int} = {}` (an empty
  dict needs its type). Entries keep the order they were inserted in, for printing, `keys()`, `values()` and loops.
- `d[k]` is a `V?`: `none` when the key is missing, so `d[k] or 0` reads with a default and counting is
  `d[w] = (d[w] or 0) + 1`.
- On a `var`: `d[k] = v` inserts or updates. `d[k] += 1`, `d[k].push(v)` and `d[k].field = v` change an existing
  entry in place and stop the program when the key is missing.
- `d.len()`, `d.has(k)`, `d.keys()`, `d.values()`; `for k in d`, `for k, v in d`, with `where` as usual. A loop
  walks the entries the dict had when it started.
- `==` compares contents regardless of order. Printing: `{"a": 1, "b": 2}`.
- Not built: removing an entry, float / bool / record keys, a literal that spans lines.

**Value semantics**
- Records, lists and dicts are values. Copying one and changing the copy never changes the original, including through
  nested lists, function arguments and the results of `map` / `where` / `first`.

**Optionals and errors**
- `T?` with `none`; `x or default` (the default is only evaluated when needed); an optional is true in `if` and
  `where` when it has a value; `x == none`
- **Narrowing.** Once a test shows an optional has a value, it is a plain value there: inside `if u.email`, on the
  right of `x != none and ...`, in a `for ... where u.email` body, and after a guard such as
  `if m == none` + `return`. It applies to a variable or a chain of fields. A `var` is narrowed only where nothing
  in that region assigns to it. Giving a default to a value that is known to be present is error `E0205`.
- `xs[i] or default` gives the default when `i` is out of range (this is how `args[0] or "app.log"` works)
- **Checked chains.** Under `or`, every lookup in the chain on its left is checked, so
  `args[0].to_int() or 0` and `row.split(",")[2].trim() or ""` give the default when an element is missing,
  instead of stopping the program. Without `or`, a bad index still stops the program.
- **Methods on optionals.** A method called on a `T?` runs when there is a value and gives `none` otherwise, so the
  result is optional: `u.email.trim().lower()` is a `str?`. It still has to be unwrapped before use as a plain value.
- A function that can fail lists `err` after `!`: `fn half(n: int) -> int ! err`. `main` is exempt.
  A call to it must be handled: `half(n)?` (pass the error up), `half(n) or 0` (default), or a handler (below).
- `main` may use `?`: an error that reaches `main` is printed as `error: <message>` on stderr and the exit code is 1

**Typed errors** (syntax provisional)
- An error has a kind and a message. Kinds are declared at the top level: `error NotFound, Timeout`.
- `fail(NotFound, "no user {id}")` fails with that kind. `fail(NotFound)` uses the kind's name as the message.
  `fail("message")` still works and has the built-in kind `Failed`. `fs` failures have the built-in kind `FsError`.
- A handler binds the error: `load(id) or e -> "fallback: {e.msg}"`. `e` is an `Error` with `e.kind` and `e.msg`.
- To branch on what went wrong, end the line at `->` and write a block. Each path ends in the value to use,
  or leaves with `return` or `fail`; `fail(e)` passes the same error on.

```
error NotFound, Timeout

fn name(id: int) -> str ! err
  user = load(id) or e ->
    if e.kind == NotFound
      "anonymous"
    else
      fail(e)
  user
```

- A handler also works on a call that returns nothing: `save(x) or e -> print("not saved: {e.msg}")`.
- `Error` and `ErrorKind` are ordinary value types: they can be parameters, list elements, compared and printed.
  A kind from another module is written `store.Missing`. `?` keeps the kind as the error travels up.

**Lambdas**
- `x -> expr` or `(x) -> expr`, as the argument of `map`, `where`, `any`, `all`. They can read the surrounding
  variables and can use `?`.

**Strings**
- `+`, comparisons, and the methods `len` (bytes), `contains`, `starts_with`, `ends_with`, `trim`, `upper`, `lower`
  (ASCII), `lines`, `split`, `words` (split on runs of whitespace), `chars`, `replace`, `reverse`, `repeat(n)`,
  `first(n)`, `last(n)`, `drop(n)`, `find`, `to_int`, `to_f64`
- `s[i]` is a one-character `str` (there is no character type). Out of range stops the program; `s[i] or d` does not.
  Strings are bytes, so these all count bytes. A string cannot be changed in place.
- Numbers as text: `"{n}"`; `n.to_str(base)` for an integer in base 2 to 36 (`5.to_str(2)` is `"101"`);
  `x.fixed(digits)` for a float with exactly that many decimals (`3.14159.fixed(2)` is `"3.14"`)

**Modules and the standard library**
- No import block. `geometry.dist(a, b)` or `geometry.Point { ... }` loads `geometry.tok` from the folder of the
  file being compiled. Everything in a module is public. Errors inside a module name its file.
- `fs.read_text(path)` and `fs.write_text(path, text)`: need effect `fs`, and can fail
- `io.read_all()` gives the rest of standard input as a `str`; `io.read_line()` gives the next line without its
  newline as a `str?`, `none` at the end. Both need effect `io`.
- `args` (or the parameter of `fn main(args: [str])`) is the list of program arguments, without the program name:
  the first argument is `args[0]`. They come after `--` on the `tokc run` line.
- Math: `sqrt`, `mean`, `min`, `max` (two numbers, or one list), `abs`. `mean`, `min` and `max` of a list give an
  optional, `none` for an empty list: `mean(xs) or 0.0`

**Sized numbers**
- `f32`, `i8 i16 i32`, `u8 u16 u32 u64` next to `int` (64-bit signed) and `f64`. `i64` is another spelling of
  `int`, the same type; `tokc fmt` writes `int`. They compile to C `float` and
  the exact-width `<stdint.h>` types, and work in records, lists, ranges, list indexes and interpolation.
- Arithmetic and comparison work within one type. There are no implicit conversions: mixing two numeric types is
  error `E0106`. When one side converts to the other exactly (or is an integer meeting an `f64`), the error carries
  a fix that `tokc fix` applies.
- A conversion is written as a call to the type: `f64(n)`, `u8(x)`, `int(f)`. Widening is exact. Narrowing an
  integer checks the range at run time and stops the program if the value does not fit. Float to integer drops the
  fraction and also checks the range. Integer to float rounds to the nearest value.
- A number literal takes the type its context expects (`x: u8 = 200`, `p.r + 1`, `f32(p.r) * 0.5`), and otherwise
  is `int` or `f64`. A literal that does not fit is compile error `E0107`.
- `a ** b` is power. It binds tighter than a unary minus on its left and groups to the right, as in Python:
  `-2 ** 2` is `-4`, `2 ** 3 ** 2` is `512`. Both sides have the same type. On `int` a negative exponent stops
  the program; floats use C `pow`.
- `int` arithmetic (`+ - * **`, negation, `abs`, `sum`) stops the program on overflow, naming the source line. The
  sized integer types wrap around at their width. Integer division by zero stops the program.

**Effects and diagnostics**
- Effects in signatures (`! io, fs`), checked across calls and across modules
- Every diagnostic is structured: error code, file, span, message, and where it is mechanical a fix that the
  compiler can apply itself. `check --json` prints `{"ok": false, "errors": [...]}`.

```
$ python3 compiler/tokc.py check tests/bad/missing_effect.tok
tests/bad/missing_effect.tok:2:3: error[E0201]: 'print' needs effect 'io', but 'greet' does not declare it
    print("hello, {name}")
    ^^^^^^^^^^^^^^^^^^^^^^
  fix: add 'io' to the signature of 'greet': insert " ! io" at 1:20
```

## Examples

| File | What it shows |
|---|---|
| `examples/logscan.tok` | Design doc Example C, as written. `tokc run examples/logscan.tok -- examples/data/server.log` |
| `examples/normalize.tok` | Design doc Example B (`normalize`), as written, plus a `main` |
| `examples/users.tok` | Design doc Example A without networking and `par for`: the record and the filtered loop, on local data |
| `examples/modules/main.tok` | A second file used through `geometry.` |
| `examples/pixels.tok` | `u8`, `f32`, `u32` and explicit conversions |
| `examples/hello.tok`, `examples/fizzbuzz.tok` | The original skeleton programs |

## Memory

Nothing in the language mentions ownership. Records, lists, dicts and strings are values: a copy is independent.
A dict is one pointer to a counted block (keys and values in insertion order, plus a hash index), shared and
copied on write like a list buffer. Underneath:

**Built**

- **Reference counts.** Every heap string and every list buffer has a count in a small header in front of its
  bytes. Records, optionals and errors are plain C structs; they hold counted strings and lists. Scalars
  (`int`, `f64`, `u8`, ...) are stored unboxed, in lists too: `[f64]` is one array of doubles.
- **Sharing, then copy-on-write.** `ys = xs` adds an owner and copies nothing. A write through a list that has more
  than one owner (`ys.push(v)`, `ys[i] = v`, `ys[i].name = v`, at any depth) copies that list first, so the other
  owners never see the change. A list with one owner is changed in place.
- **Deterministic frees.** A variable releases its value when its scope ends, and on every early exit: `return`,
  `?`, `fail`, a handler block that leaves. Temporaries are released at the end of their statement. The last owner
  frees the buffer at once; there is no collector and no pause.
- **Borrowing.** Parameters, loop variables and call arguments are borrowed: passing a value to a function that
  only reads it touches no count. A `for x in xs` loop holds one reference to the list it walks, so the body may
  change `xs`; the loop keeps walking the list as it was.
- **Function summaries and moves.** A second pass over each function records which parameters it consumes (stores
  or returns as they are, at their last use). Those are handed over instead of borrowed: without counting when the
  argument is a local read for the last time, and in `x = f(x)` the variable gives its value up for the call. So
  `xs = with_item(xs, v)`, where `with_item` copies its parameter into a `var` and pushes, appends in place.
  Returning a local moves it.
- **Strings.** Literals are static and never counted. `s = s + a`, `s += a` and `s = "{s}..."` append in place when
  `s` has one owner. Interpolation builds in a stack buffer and allocates the result once; `print` of an interpolated string
  allocates nothing. `s[i]` and `chars()` return shared one-byte strings.
- **No cycles, so counting is complete.** There are no references in the language, only values, and a stored value
  is always finished and owned before the place it goes into is made unique. A list can therefore never come to
  contain itself: `n.kids.push(n)` and `n.kids[0] = n` store a snapshot of `n` (tests/mem/self_store.tok). With no
  cycles, a count that reaches zero is the only way memory is freed, and it always is.
- **Counts are not atomic.** There is no concurrency yet. When there is, values that cross a task boundary will
  need atomic counts or a copy.
- **A panic exits without freeing** (exit code 70): bounds, overflow, division by zero. An error that reaches
  `main` frees everything and exits with 1.

**How it is checked**

- Every program the tests run is built with `TOK_LEAK_CHECK=1`: the runtime counts live blocks and the program
  exits with 71 if any are left.
- `tests/test_memory.py` builds a corpus with `--sanitize` (gcc AddressSanitizer + LeakSanitizer +
  UndefinedBehaviorSanitizer) and fails on any report: the examples, all 64 benchmark reference solutions on every
  test case, the performance kernels, and the programs in `tests/mem`, whose expected output came from the old
  copy-everything compiler.
- `TOK_SANITIZE=1 python3 tests/run_tests.py` puts every program of the whole suite under the sanitizers.

**Designed, not built**

- Reusing the input list of `xs.map(...)` when it has one owner (same-shaped output), and the same for records.
- Last-use moves inside loops and branches. Today a move needs a statement directly in the function body; inside
  a loop only the `x = f(x)` form moves. `x = f(x)?` and `x = f(x) or ...` do not move yet (they copy on write).
- Summaries seen through a call: a function that passes its parameter straight on to a consuming function is not
  itself marked as consuming. Purity and "allocates" summaries are not computed; nothing uses them yet.
- Hoisting bounds checks out of loops, and removing overflow checks that can be proven unnecessary.
- String slices that share the parent's buffer (`split`, `lines`, `trim` copy today); small-string storage; arenas.
- First-class lambdas and closures (see "Decisions"): still second-class.

## Performance

`bench/perf/` holds 11 small kernels, each written in Tok, hand-written C (`cc -O2`) and Python, and a runner that
checks the outputs agree and records wall time and peak memory. `bench/perf/RESULTS.md` has the tables: the old
copy-everything compiler, this one, C and Python, with the machine and commands, and a plain list of where Tok is
still slower than C and why.

```
python3 bench/perf/run.py --save current     # measure
python3 bench/perf/run.py --report           # rewrite bench/perf/RESULTS.md
```

## Phase 2: measure early

**Learn pack.** `tokc learn` prints a one-page guide for a model that knows Python and Go: what is the same, what
differs, the effect and error rules, common mistakes with their fixes, and complete programs. Every code sample in
it is compiled and run by `tests/test_learn.py`. `--budget N` drops the lowest-priority pieces; the compiler has no
tokenizer, so N is counted as characters / 3. Real sizes are in `bench/RESULTS.md`.

**Compiler as a tool.** `tokc serve` reads one JSON-RPC 2.0 message per line on stdin and answers on stdout, using
the Model Context Protocol method names (`initialize`, `tools/list`, `tools/call`, `ping`). Tools: `check`
(diagnostics with spans and apply-able fixes), `fix`, `run` (with `args`, extra `files`, `timeout`), `format`, and
`doc` (an error code, a built-in, a guide section, or `guide`).

```
$ echo '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"check","arguments":{"source":"fn main()\n  print(1)\n"}}}' \
    | python3 compiler/tokc.py serve
```

**Tokens-to-green benchmark** (`bench/`)
- `bench/tasks/<id>/`: `task.json` (prompt and tests; the first test is shown to the model, the rest are hidden),
  `ref.tok`, `ref.py`. 64 tasks: the 40 original ones (arguments and files) and 24 added ones (`"group": "added"`;
  12 read standard input through a test's `stdin` text, 12 work on strings). The tests check that every reference
  passes. Two earlier points are kept for comparison: `bench/baseline.json` (the 40 originals before the built-ins)
  and `bench/baseline_builtins.json` (all 64 after the built-ins, before script form and the other syntax additions).
- `bench/harness.py`: generate, check and run the tests, send back structured feedback (compiler diagnostics as
  JSON, or the first failing run), retry up to a cap. It records attempts, first-try passes, and input, output and
  thinking tokens as reported by the provider.
- `bench/adapters.py`: Anthropic and Gemini adapters. The key is never in this process: requests carry a surrogate
  from Muse's credential helper. `python3 bench/adapters.py anthropic models` lists model ids.
- `bench/run.py`: the runner. `--adapter reference` replays the reference solutions for free. A paid adapter needs
  `--confirm-spend`.
- `bench/count_tokens.py`: static counts with real tokenizers, written to `bench/RESULTS.md`. Needs the venv:
  `python3 -m venv .venv && .venv/bin/pip install tiktoken tokenizers`.

No model run has been made yet, so there are no tokens-to-green numbers. The static counts are in
`bench/RESULTS.md`: over the 64 tasks the Tok references are 2110 tokens against Python's 2006 on o200k (1.05x),
down from 1.53x after the built-ins; the 40 originals went 1.99x, 1.47x, 1.00x. The result depends on the tokenizer:
1.05x on both OpenAI tokenizers and Qwen, 0.98x on DeepSeek Coder, 0.88x on GPT-2. The added stdin and string tasks
are still 1.18x on o200k. The file breaks down what is left; the largest construct is the explicit default on a
lookup or a parse (`args[0].to_int() or 0`).

## Decisions

**Settled** (reviewed and approved; they differ from the v0 design doc or go beyond it)

- **Fallible functions list `err` in the effect list**, as in `! net, err`. `main` is exempt, which is what lets the
  doc's Example C compile as written. The doc's Example A signature `fetch_user(id: int) -> User ! net` needs
  `! net, err` here.
- **Errors are typed by kind** (see "Typed errors"). What is settled is that a program can declare kinds and branch
  on them; the spelling (`error A, B`, `fail(A, "msg")`, `or e ->`) is provisional.
- **`int` stops the program on overflow; sized integers wrap.**
- **Optionals narrow** after a test, as described under "Optionals and errors".
- **`int` and `i64` are one type.**
- **`mean`, `min`, `max` of an empty list are `none`**, not a crash. `examples/normalize.tok` therefore reads
  `mean(xs) or 0.0`, a small departure from the doc's text.
- **Lambdas stay second-class** (first-class lambdas were left out of Phase 3 so far): they are written directly
  as an argument to `map`, `where`, `any`, `all`, or as an error handler after `or`.

**Open** (the least surprising option was built so the rest could proceed)

- **Script form (built, provisional).** A file may hold statements at the top level, standing for the body of
  `main` with `args` in scope; its effects are inferred. Open: top-level variables are not visible inside functions
  (no globals), which a model used to Python may trip over; the compiler says so in the error.
- **`args` has no program name** (changed): `args[0]` is the first argument, as in Python's `sys.argv[1:]`.
- **Conditional expression is `if c then a else b`** (built, provisional): the condition comes first, as in
  the statement, and `then` is a new keyword. It has no parentheses of its own, so each branch runs to the end
  of the expression: write `(if c then a else b) + 1` to add to the whole.
- **`while`, `break`, `continue`** (built). There are no loop labels: a jump acts on the innermost loop.
- **Multiple assignment without tuples** (built, provisional). `a, b = x, y` pairs the two sides by position
  at compile time. There is no tuple type, so a function cannot return two values and `a, b = f()` is an
  error; a record does that job. A general tuple did not fall out cheaply: it needs its own type syntax,
  layout, equality and printing. `let` is a new keyword that always creates; plain `a, b = ...` creates the
  names that are new and assigns the ones that exist, like the one-name form.
- **Dicts are `{K: V}` with literal `{"a": 1}`** (built, provisional), mirroring `[T]` and `[1, 2]`. The
  formatter writes dict braces tight and record braces spaced (`P { x: 1 }`). Open: `d[k]` is always optional,
  even where the key was just tested with `has`; there is no `remove`; whether `for k, v in xs` should also
  give index and element for a list (today it is dict-only).
- Character codes, ranges as values: each is a measured cost in the reference solutions and neither is built.
- **Formatter indent width.** `tokc fmt` uses 2 spaces. The measurements are in `bench/RESULTS.md`; the default
  has not been changed.
- **Typed error spelling**, and whether a signature should list which kinds it can fail with.
- **Record literals are written `Name { field: value }`.** The doc shows record types but no literal.
- **No user generics.** The doc lists the generics syntax as an open decision.
- **`or` checks the lookups in the chain on its left** (`xs[i] or d`, `xs[i].m() or d`, `xs[i][j] or d`). Plain
  `xs[i]` stops the program when out of range. A chain that ends in a `bool` is not checked, because there `or` is
  the logical operator. The alternative was an explicit marker such as `xs.get(i)` or `?.`; the implicit rule was
  built because it makes the obvious spelling the safe one.
- **A method on an optional gives an optional** without a `?.` marker. Field access on an optional record is still
  an error (narrow it first).
- **No free `sum(xs)`**: `sum` is too common a variable name to reserve. `xs.sum()`, `xs.min()`, `xs.max()` are
  the method spellings; `min(a, b)` / `max(a, b)` and the older `min(xs)` / `max(xs)` remain.
- **`sort` returns a new list** (like Python's `sorted`); there is no in-place sort.
- **Numeric literals and conversions** follow the rules under "Sized numbers". Narrowing conversions stop the
  program when the value does not fit, rather than wrapping or giving an optional.
- **Built-in names are reserved**: `print`, `fail`, `sqrt`, `mean`, `min`, `max`, `abs`, the numeric type names,
  `Error`, `ErrorKind`, and the kinds `Failed` and `FsError` cannot be redefined.
- **A local variable wins over a module** of the same name. No variable shadowing inside a function.
- **A statement is one line**, except for a handler block after `or e ->`. There is no line continuation.
- **`tokc fmt` does not reformat the code inside string interpolations** and does not wrap long lines.
- **Modules**: everything is public, and a module is only looked up next to the entry file.
- `split("")` stops the program.

## Not built yet

- User-defined generics; first-class functions and closures; methods on user types
- Errors that carry data beyond a kind and a message; listing error kinds in a signature
- Pattern matching, enums / sum types, sets, tuples, constants, loop labels; removing a dict entry
- Ranges as values (`(a..b).sum()`), `zip`, string padding
- `mut` parameters, and the `simd for` / vector parts of the doc's compute examples (so `normalize_fast` from
  Example B is not covered; `normalize` is)
- Concurrency: `par for`, channels, tasks
- Effects other than as checked labels: `net`, `time`, `rand`, `py` have no library behind them
- `http`, `json`, and the rest of the standard library; directories
- C header import and the Python bridge
- Explicit imports, module search paths, packages, visibility (private functions)
- Unicode-aware string operations (strings are bytes; `len` counts bytes, `upper`/`lower` are ASCII)
- The parts of the memory model listed under "Memory: designed, not built"
- Error recovery inside a file that fails to tokenize (one such error is reported per file); `tokc fix` only edits
  the file it was given, not the modules it loads

## Layout

```
compiler/tokc.py          command line driver
compiler/tok/lexer.py     tokens, indentation
compiler/tok/parser.py    AST
compiler/tok/check.py     core: types, effects, statements, C emission
compiler/tok/f_*.py       one mixin per feature: lists, optionals + errors, methods + lambdas, modules + fs + math,
                          sized numbers, narrowing, built-ins (sort, pieces, numbers as text, stdin)
compiler/tok/types.py     type table and the generated C helpers per type
compiler/tok/runtime.py   the C runtime that is prepended to every program
compiler/tok/fmt.py       the formatter
compiler/tok/diag.py      diagnostics and fixes
compiler/tok/learn.py     the language guide for models (tokc learn)
compiler/tok/docs.py      doc lookup: error codes, built-ins, guide sections
compiler/tok/tools.py     check / fix / format / run as library calls
compiler/tok/serve.py     the JSON-RPC tool server (tokc serve)
bench/                    benchmark tasks, runner, model adapters, token counts
bench/perf/               performance kernels in Tok, C and Python; runner; RESULTS.md
tests/mem/                programs that go after sharing, copy-on-write and early exits
```

## Tests

```
python3 tests/run_tests.py
```

The tests compile real programs, run the binaries, and check output and diagnostics. Every error code that the
checker can produce for the features above has a test. Every program run by a test must also free everything it
allocated; see "Memory: how it is checked".
