# Tok

An LLM-optimized programming language that is still readable by humans. Token-efficient to write, native-fast to run.

"Tok" is a placeholder name. The v0 design doc lives outside this repo for now.

## Status

Phase 1 of the plan: the core language. The whole pipeline runs end to end:

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
- `xs[i] or default` gives the default when `i` is out of range (this is how `args[1] or "app.log"` works)
- A function that can fail lists `err` after `!`: `fn half(n: int) -> int ! err`. It fails with `fail("message")`.
  A call to it must be handled, either `half(n)?` (pass the error up) or `half(n) or 0`.
- `main` may use `?`: an error that reaches `main` is printed as `error: <message>` on stderr and the exit code is 1

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
- Math: `sqrt`, `mean`, `min`, `max` (two numbers, or one list), `abs`

**Sized numbers**
- `f32`, `i8 i16 i32 i64`, `u8 u16 u32 u64` next to `int` (64-bit signed) and `f64`. They compile to C `float` and
  the exact-width `<stdint.h>` types, and work in records, lists, ranges, list indexes and interpolation.
- Arithmetic and comparison work within one type. There are no implicit conversions: mixing two numeric types is
  error `E0106`. When one side converts to the other exactly (or is an integer meeting an `f64`), the error carries
  a fix that `tokc fix` applies.
- A conversion is written as a call to the type: `f64(n)`, `u8(x)`, `int(f)`. Widening is exact. Narrowing an
  integer checks the range at run time and stops the program if the value does not fit. Float to integer drops the
  fraction and also checks the range. Integer to float rounds to the nearest value.
- A number literal takes the type its context expects (`x: u8 = 200`, `p.r + 1`, `f32(p.r) * 0.5`), and otherwise
  is `int` or `f64`. A literal that does not fit is compile error `E0107`.
- Integer `+ - *` wrap around on overflow at the type's width. Integer division by zero stops the program.

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

## Where this differs from the design doc, or goes beyond it

These were picked as the least surprising option to get Phase 1 running. They are open, not settled.

- **Fallible functions are marked with `err` in the effect list.** The doc says fallibility should be handled with
  `?` but its examples do not show a marker in the signature. `main` is the one exception and needs no `err`,
  which is what lets Example C compile exactly as written. Example A's `fetch_user(id: int) -> User ! net` would
  need `! net, err` here.
- **The error value is a message string.** There are no error types and no way to inspect an error except passing
  it up with `?` or replacing it with `or`.
- **Record literals are written `Name { field: value }`.** The doc shows record types but no literal.
- **Lambdas are not values.** They can only be written directly as an argument to `map`, `where`, `any`, `all`.
- **No user generics.** The doc lists the generics syntax as an open decision, and nothing in Phase 1 needed them:
  the built-in list and optional types and their methods are generic inside the compiler.
- **Optionals have no unwrap or narrowing.** Inside `if u.email` the value is still `str?`; it prints as its inner
  value. Use `or` to get a plain value.
- **`xs[i] or d`** is a checked lookup only when the index is written directly under `or`. Plain `xs[i]` stops the
  program when out of range.
- **Numeric literals and conversions** follow the rules under "Sized numbers" above. `int` and `i64` are different
  types with the same representation.
- **Built-in function names are reserved**: `print`, `fail`, `sqrt`, `mean`, `min`, `max`, `abs` and the numeric
  type names cannot be redefined.
- **A local variable wins over a module** of the same name. No variable shadowing inside a function.
- **A statement is one line.** There is no line continuation, so long record literals stay on one line.
- **`tokc fmt` does not reformat the code inside string interpolations** and does not wrap long lines.
- `mean` of an empty list, `max`/`min` of an empty list and `split("")` stop the program.

## Not built yet

- User-defined generics; first-class functions and closures; methods on user types
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
compiler/tok/f_*.py       one mixin per feature: lists, optionals + errors, methods + lambdas, modules + fs + math, sized numbers
compiler/tok/types.py     type table and the generated C helpers per type
compiler/tok/runtime.py   the C runtime that is prepended to every program
compiler/tok/fmt.py       the formatter
compiler/tok/diag.py      diagnostics and fixes
```

## Tests

```
python3 tests/run_tests.py
```

The tests compile real programs, run the binaries, and check output and diagnostics. Every error code that the
checker can produce for the features above has a test.
