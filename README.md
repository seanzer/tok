# Tok

An LLM-optimized programming language that is still readable by humans. Token-efficient to write, native-fast to run.

"Tok" is a placeholder name. The v0 design doc lives outside this repo for now.

## Status

Phase 1 of the plan (the core language) is in, with the design decisions from the first review applied, and the
Phase 2 tooling (learn pack, tool server, benchmark harness) is built. No model benchmark has been run yet.
The whole pipeline runs end to end:

```
source -> tokens -> AST -> type + effect check -> C -> cc -> native binary
```

The compiler is written in Python (3.9+, standard library only) to bootstrap quickly. The generated C is C11 and
compiles without warnings under `cc -std=c11 -Wall -Wextra`; the tests enforce that for every program they run.

## Hello world

```
fn main() ! io
  print("hello, world")
```

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
- String interpolation: `"sum = {a + b}"`, including nested string literals inside `{}`
- `//` comments

**Records**
- `type User = { id: int, name: str, email: str? }`, literals `User { id: 1, name: "Ann" }` (optional fields may be
  left out), field access, field assignment on a `var`, `==`, printing

**Lists**
- `[T]`, literals, indexing (out of range stops the program with the source line), element assignment and `push`
  on a `var`, `==`, printing
- Methods: `len`, `push`, `map`, `where`, `any`, `all`, `first(n)`, `contains`, `join` (on `[str]`), `sum`

**Value semantics**
- Records and lists are values. Copying one and changing the copy never changes the original, including through
  nested lists, function arguments and the results of `map` / `where` / `first`.

**Optionals and errors**
- `T?` with `none`; `x or default` (the default is only evaluated when needed); an optional is true in `if` and
  `where` when it has a value; `x == none`
- **Narrowing.** Once a test shows an optional has a value, it is a plain value there: inside `if u.email`, on the
  right of `x != none and ...`, in a `for ... where u.email` body, and after a guard such as
  `if m == none` + `return`. It applies to a variable or a chain of fields. A `var` is narrowed only where nothing
  in that region assigns to it. Giving a default to a value that is known to be present is error `E0205`.
- `xs[i] or default` gives the default when `i` is out of range (this is how `args[1] or "app.log"` works)
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
  (ASCII), `lines`, `split`, `replace`, `find`, `to_int`, `to_f64`

**Modules and the standard library**
- No import block. `geometry.dist(a, b)` or `geometry.Point { ... }` loads `geometry.tok` from the folder of the
  file being compiled. Everything in a module is public. Errors inside a module name its file.
- `fs.read_text(path)` and `fs.write_text(path, text)`: need effect `fs`, and can fail
- `fn main(args: [str])`: `args[0]` is the program, the rest come after `--` on the `tokc run` line
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
- `int` arithmetic (`+ - *`, negation, `abs`, `sum`) stops the program on overflow, naming the source line. The
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

**This compiler leaks.** Every string and list buffer the program allocates is kept until the program exits;
nothing is freed. Value semantics are implemented by copying, with no reference counting or ownership analysis, so
programs also copy more than a real implementation would. The real memory model is Phase 3. Long-running or
allocation-heavy programs will grow without bound until then.

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
  `ref.tok`, `ref.py`. 40 tasks. The tests check that every reference passes.
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
`bench/RESULTS.md`.

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
- **Lambdas stay second-class until the memory model** (Phase 3): they are written directly as an argument to
  `map`, `where`, `any`, `all`, or as an error handler after `or`.

**Open** (the least surprising option was built so the rest could proceed)

- **Formatter indent width.** `tokc fmt` uses 2 spaces. The measurements are in `bench/RESULTS.md`; the default
  has not been changed.
- **Typed error spelling**, and whether a signature should list which kinds it can fail with.
- **Record literals are written `Name { field: value }`.** The doc shows record types but no literal.
- **No user generics.** The doc lists the generics syntax as an open decision.
- **`xs[i] or d`** is a checked lookup only when the index is written directly under `or`. Plain `xs[i]` stops the
  program when out of range.
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
- `while`-style loops, `break` / `continue`, pattern matching, enums / sum types, maps and sets, tuples, constants
- `mut` parameters, and the `simd for` / vector parts of the doc's compute examples (so `normalize_fast` from
  Example B is not covered; `normalize` is)
- Concurrency: `par for`, channels, tasks
- Effects other than as checked labels: `net`, `time`, `rand`, `py` have no library behind them
- `http`, `json`, and the rest of the standard library; stdin; directories
- C header import and the Python bridge
- Explicit imports, module search paths, packages, visibility (private functions)
- Unicode-aware string operations (strings are bytes; `len` counts bytes, `upper`/`lower` are ASCII)
- The real memory model (see "Memory"), and any optimization work. No performance measurements have been made.
- Error recovery inside a file that fails to tokenize (one such error is reported per file); `tokc fix` only edits
  the file it was given, not the modules it loads

## Layout

```
compiler/tokc.py          command line driver
compiler/tok/lexer.py     tokens, indentation
compiler/tok/parser.py    AST
compiler/tok/check.py     core: types, effects, statements, C emission
compiler/tok/f_*.py       one mixin per feature: lists, optionals + errors, methods + lambdas, modules + fs + math,
                          sized numbers, narrowing
compiler/tok/types.py     type table and the generated C helpers per type
compiler/tok/runtime.py   the C runtime that is prepended to every program
compiler/tok/fmt.py       the formatter
compiler/tok/diag.py      diagnostics and fixes
compiler/tok/learn.py     the language guide for models (tokc learn)
compiler/tok/docs.py      doc lookup: error codes, built-ins, guide sections
compiler/tok/tools.py     check / fix / format / run as library calls
compiler/tok/serve.py     the JSON-RPC tool server (tokc serve)
bench/                    benchmark tasks, runner, model adapters, token counts
```

## Tests

```
python3 tests/run_tests.py
```

The tests compile real programs, run the binaries, and check output and diagnostics. Every error code that the
checker can produce for the features above has a test.
