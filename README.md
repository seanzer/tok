# Tok

An LLM-optimized programming language that is still readable by humans. Token-efficient to write, native-fast to run.

"Tok" is a placeholder name. The v0 design doc lives outside this repo for now.

## Status

Walking skeleton. The whole pipeline exists end to end for a small core of the language:

```
source -> tokens -> AST -> type + effect check -> C -> cc -> native binary
```

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
| `tokc run <file>` | Compile at `-O0`, cache the binary, run it |
| `tokc build <file> [-o out]` | Compile at `-O2` to a native binary |
| `tokc check <file> [--json]` | Type and effect check only |
| `tokc fix <file>` | Apply the compiler's suggested fixes until the file checks |
| `tokc emit-c <file>` | Print the generated C |

## What the skeleton covers

- Functions with typed parameters, return types and implicit last-expression return
- `int`, `str`, `bool`, with local type inference
- Immutable bindings by default, `var` for mutable ones
- `if` / `else if` / `else`, and `for i in a..b` (inclusive range)
- String interpolation: `"sum = {a + b}"`
- Effects in signatures (`! io`), checked across calls
- Structured diagnostics: error code, span, and a fix the compiler can apply itself

```
$ python3 compiler/tokc.py check tests/bad/missing_effect.tok
tests/bad/missing_effect.tok:2:3: error[E0201]: 'print' needs effect 'io', but 'greet' does not declare it
    print("hello, {name}")
    ^^^^^^^^^^^^^^^^^^^^^^
  fix: add 'io' to the signature of 'greet': insert " ! io" at 1:20
```

## Not built yet

Everything else in the design doc, including: records, lists, optionals and `?` error propagation, generics, modules and the standard library, concurrency (`par for`, channels), C header import, the Python bridge, the formatter, and the real memory model. Strings built at runtime are currently never freed.

The compiler is written in Python to bootstrap quickly. It reports one error at a time.

## Tests

```
python3 tests/run_tests.py
```

The tests compile real programs, run the binaries, and check output and diagnostics.
