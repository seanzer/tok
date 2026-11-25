"""Built-ins added to close the token gap: list order and pieces, `**`, checked chains under `or`,
methods on optionals, string indexing and pieces, numbers as text, and standard input."""
import unittest

from helpers import TokCase, tokc

MAIN = "fn main() ! io\n"


def body(*lines):
    return MAIN + "".join(f"  {l}\n" for l in lines)


class ListBuiltinTests(TokCase):
    def test_sort_numbers_strings_and_floats(self):
        src = body('print([3, 1, 2, 3, 1].sort())', 'print(["b", "a", "C"].sort())', 'print([2.5, -1.0, 0.0].sort())',
                   'e: [int] = []', 'print(e.sort())', 'xs: [u8] = [200, 3, 77]', 'print(xs.sort())')
        self.assertEqual(self.run_src(src), '[1, 1, 2, 3, 3]\n["C", "a", "b"]\n[-1.0, 0.0, 2.5]\n[]\n[3, 77, 200]\n')

    def test_sort_by_key_is_stable_and_leaves_the_original(self):
        src = ('type P = { name: str, age: int }\n\n' + MAIN +
               '  ps = [P { name: "Ann", age: 31 }, P { name: "Bob", age: 45 }, P { name: "Cy", age: 31 }]\n'
               '  print(ps.sort(p -> p.age).map(p -> p.name))\n'
               '  print(ps.sort(p -> -p.age).map(p -> p.name))\n'
               '  print(ps.sort(p -> p.name).reverse().map(p -> p.name))\n'
               '  print(ps.map(p -> p.name))\n')
        self.assertEqual(self.run_src(src), '["Ann", "Cy", "Bob"]\n["Bob", "Ann", "Cy"]\n["Cy", "Bob", "Ann"]\n["Ann", "Bob", "Cy"]\n')

    def test_sort_large_list_matches_python(self):
        n = 2000
        src = body('var xs: [int] = []', 'var v = 7', f'for i in 1..{n}', '  v = (v * 1103515245 + 12345) % 2147483648',
                   '  xs.push(v % 1000)', 'print(xs.sort().join(","))')
        v, xs = 7, []
        for _ in range(n):
            v = (v * 1103515245 + 12345) % 2147483648
            xs.append(v % 1000)
        self.assertEqual(self.run_src(src), ",".join(str(x) for x in sorted(xs)) + "\n")

    def test_sorted_copy_is_independent_of_nested_lists(self):
        src = body('var rows = [[3, 1], [2]]', 'sorted = rows.sort(r -> r.len())', 'rows[0].push(9)', 'print(sorted)', 'print(rows)')
        self.assertEqual(self.run_src(src), "[[2], [3, 1]]\n[[3, 1, 9], [2]]\n")

    def test_reverse_drop_last_first_unique_index_of(self):
        src = body('xs = [3, 1, 2, 3, 1]', 'print(xs.reverse())', 'print(xs.drop(2))', 'print(xs.drop(9))', 'print(xs.drop(-1))',
                   'print(xs.last(2))', 'print(xs.last(9))', 'print(xs.last(-1))', 'print(xs.unique())',
                   'print(xs.index_of(2))', 'print(xs.index_of(7))', 'print(xs.index_of(3) or -1)', 'print(xs)')
        self.assertEqual(self.run_src(src), "[1, 3, 2, 1, 3]\n[2, 3, 1]\n[]\n[3, 1, 2, 3, 1]\n[3, 1]\n[3, 1, 2, 3, 1]\n[]\n"
                                            "[3, 1, 2]\n2\nnone\n0\n[3, 1, 2, 3, 1]\n")

    def test_min_max_methods_with_and_without_key(self):
        src = body('xs = [3, 9, 2]', 'print("{xs.min()} {xs.max()} {min(xs)} {max(xs)} {xs.sum()}")',
                   'ws = ["pear", "fig", "plum", "kiwi"]', 'print(ws.max(w -> w.len()) or "")', 'print(ws.min(w -> w.len()) or "")',
                   'print(ws.max() or "")', 'e: [str] = []', 'print(e.max(w -> w.len()))', 'print(e.min())')
        self.assertEqual(self.run_src(src), "2 9 2 9 14\npear\nfig\nplum\nnone\nnone\n")

    def test_join_takes_numbers_and_bools(self):
        src = body('print([1, 2, 3].join("+"))', 'print([1.5, 2.0].join(" "))', 'print([true, false].join(","))', 'e: [int] = []',
                   'print("[{e.join(",")}]")')
        self.assertEqual(self.run_src(src), "1+2+3\n1.5 2.0\ntrue,false\n[]\n")

    def test_list_builtin_errors(self):
        rec = 'type P = { n: int }\n\n'
        cases = [
            (rec + body('print([P { n: 1 }].sort())'), "E0109", "give a key"),
            (rec + body('print([P { n: 1 }].sort(p -> p))'), "E0103", "must be a number or a str"),
            (rec + body('print([P { n: 1 }].max())'), "E0109", "give a key"),
            (body('print([1].sort(1))'), "E0113", "needs a lambda"),
            (body('print([1].sort(x -> x, 2))'), "E0104", "takes 1 argument"),
            (body('print([1].drop("a"))'), "E0103", "int count"),
            (body('print([1].index_of("a"))'), "E0103", "needs int"),
            (body('print([1].reverse(1))'), "E0104", "takes 0 argument"),
            (rec + body('print([P { n: 1 }].join(","))'), "E0109", "strs, numbers or bools"),
        ]
        for src, code, text in cases:
            with self.subTest(src=src):
                e = self.check_err(src)
                self.assertEqual(e["code"], code, e)
                self.assertIn(text, e["message"])


class PowerTests(TokCase):
    def test_power_precedence_and_types(self):
        src = body('print(2 ** 10)', 'print(-2 ** 2)', 'print(2 ** 3 ** 2)', 'print(2 * 3 ** 2)', 'print((-2) ** 3)', 'print(7 ** 0)',
                   'print(2.0 ** 0.5)', 'print(2.0 ** -1.0)', 'x: u8 = 3', 'print(x ** 5)', 'y: i32 = -3', 'print(y ** 3)',
                   'f: f32 = 1.5', 'print(f ** 2.0)', 'print(0 ** 0)')
        self.assertEqual(self.run_src(src), "1024\n-4\n512\n18\n-8\n1\n1.4142135623730951\n0.5\n243\n-27\n2.25\n1\n")

    def test_int_power_stops_on_overflow_and_negative_exponent(self):
        _, err = self.run_fail(body('n = 10', 'print(n ** 19)'), 70)
        self.assertIn("integer overflow", err)
        self.assertIn("f.tok:3", err)
        _, err = self.run_fail(body('n = -1', 'print(2 ** n)'), 70)
        self.assertIn("negative exponent -1", err)
        _, err = self.run_fail(body('n: i8 = -1', 'b: i8 = 2', 'print(b ** n)'), 70)
        self.assertIn("negative exponent -1", err)
        self.assertEqual(self.run_src(body('n = 2', 'print(n ** 62)', 'print((-2) ** 63)')), "4611686018427387904\n-9223372036854775808\n")

    def test_power_type_errors(self):
        self.assertEqual(self.check_err(body('print(2 ** 0.5)'))["code"], "E0106")
        self.assertEqual(self.check_err(body('print("a" ** 2)'))["code"], "E0103")
        e = self.check_err(body('n = 2', 'print(n ** 2.0)'))
        self.assertEqual(e["code"], "E0106")

    def test_power_is_formatted_with_spaces(self):
        path = self.write("p.tok", MAIN + "  print(2**10 - -2  **  2)\n")
        self.assertEqual(tokc("fmt", path).returncode, 0)
        with open(path) as fh:
            self.assertEqual(fh.read(), MAIN + "  print(2 ** 10 - -2 ** 2)\n")
        self.assertEqual(tokc("fmt", path, "--check").returncode, 0)


class ChainTests(TokCase):
    ARGS = "fn main(args: [str]) ! io\n"

    def test_parse_or_default_never_stops_the_program(self):
        src = self.ARGS + '  print(args[0].to_int() or 0)\n  print(args[1].trim().upper() or "none")\n  print(args[8].len() or -1)\n'
        path = self.write("c.tok", src)
        self.assertEqual(self.run_path(path, "--", "41", " ab "), "41\nAB\n-1\n")
        self.assertEqual(self.run_path(path, "--", "x"), "0\nnone\n-1\n")
        self.assertEqual(self.run_path(path), "0\nnone\n-1\n")

    def test_old_spelling_still_works_and_gives_the_same_answer(self):
        src = self.ARGS + '  print((args[0] or "0").to_int() or 0)\n  print(args[0].to_int() or 0)\n'
        path = self.write("o.tok", src)
        self.assertEqual(self.run_path(path, "--", "12"), "12\n12\n")
        self.assertEqual(self.run_path(path), "0\n0\n")

    def test_without_or_a_missing_element_still_stops_the_program(self):
        _, err = self.run_fail(MAIN + '  xs = ["1"]\n  n = xs[3].to_int()\n  print(n)\n', 70)
        self.assertIn("index 3 out of bounds", err)

    def test_bool_chain_keeps_its_meaning(self):
        # `a.contains(x) or b` is a logical or, so the lookup is not checked
        src = body('xs = ["ab"]', 'print(xs[0].contains("z") or true)', 'print(xs[0].contains("a") or false)')
        self.assertEqual(self.run_src(src), "true\ntrue\n")
        _, err = self.run_fail(body('xs = ["ab"]', 'print(xs[4].contains("z") or true)'), 70)
        self.assertIn("out of bounds", err)

    def test_method_on_optional_gives_optional(self):
        src = ('type U = { name: str, email: str? }\n\n' + MAIN +
               '  a = U { name: "a", email: " X@Y " }\n  b = U { name: "b" }\n'
               '  print(a.email.trim().lower())\n  print(b.email.trim().lower())\n'
               '  print(b.email.len() or 0)\n  print("12".find("2").to_str(2) or "?")\n'
               '  n: int? = "7".to_int()\n  print(n.to_str(2))\n'
               '  print("a,b".find(",").to_str(10).len() or 0)\n')
        self.assertEqual(self.run_src(src), "x@y\nnone\n0\n1\n111\n1\n")

    def test_optional_result_must_still_be_handled(self):
        e = self.check_err(body('s: str? = "a"', 'print(s.len() + 1)'))
        self.assertEqual(e["code"], "E0103")
        e = self.check_err(body('var xs: [int]? = [1]', 'xs.push(2)'))
        self.assertEqual((e["code"], "may be none" in e["message"]), ("E0103", True))
        e = self.check_err(body('s: str? = "a"', 'print(s.nope())'))
        self.assertEqual(e["code"], "E0109")

    def test_lookup_on_an_optional_list_inside_a_chain(self):
        src = body('ws = ["ann:31", "bob:45"]', 'e: [str] = []', 'print(ws.max(w -> w.len()).split(":")[0] or "?")',
                   'print(e.max(w -> w.len()).split(":")[0] or "?")', 'print(ws.max(w -> w.len()).split(":")[5] or "?")')
        self.assertEqual(self.run_src(src), "ann\n?\n?\n")

    def test_chain_through_a_lambda_and_nested_or(self):
        src = body('rows = ["1,2", "x", "5"]', 'print(rows.map(r -> r.split(",")[1].to_int() or -1))',
                   'print(rows[7].split(",")[0].to_int() or (rows[0].split(",")[0].to_int() or 0))',
                   'print(rows[9][0] or "?")', 'print(rows[1][5] or "?")', 'print(rows[0][2] or "?")',
                   'grid = [[1, 2], [3]]', 'print("{grid[1][1] or 0} {grid[5][0] or 0} {grid[0][1] or 0}")')
        self.assertEqual(self.run_src(src), "[2, -1, -1]\n1\n?\n?\n2\n0 0 2\n")


class StringTests(TokCase):
    def test_index_chars_words_and_pieces(self):
        src = body('s = "hello"', 'print(s[0] + s[4])', 'print(s[9] or "?")', 'print(s.chars())', 'print("  a b\\tc\\n d ".words())',
                   'print("".words().len())', 'print(s.reverse())', 'print("ab".repeat(3) + "|" + "ab".repeat(0) + "|" + "ab".repeat(-2))',
                   'print(s.first(2) + " " + s.last(3) + " " + s.drop(1) + " " + s.first(99) + " " + s.drop(99) + "." + s.last(0))',
                   'print(s.chars().map(c -> c.upper()).join("-"))', 'print(s == s.reverse())', 'print("level" == "level".reverse())',
                   'for c in "abc".chars()', '  print(c)')
        self.assertEqual(self.run_src(src), 'ho\n?\n["h", "e", "l", "l", "o"]\n["a", "b", "c", "d"]\n0\nolleh\nababab||\n'
                                            'he llo ello hello .\nH-E-L-L-O\nfalse\ntrue\na\nb\nc\n')

    def test_string_index_out_of_range_and_errors(self):
        _, err = self.run_fail(body('s = "abc"', 'print(s[3])'), 70)
        self.assertIn("index 3 out of bounds (len 3)", err)
        e = self.check_err(body('var s = "abc"', 's[0] = "x"'))
        self.assertEqual((e["code"], "cannot be changed in place" in e["message"]), ("E0103", True))
        e = self.check_err(body('for c in "abc"', '  print(c)'))
        self.assertEqual((e["code"], ".chars()" in e["message"]), ("E0103", True))
        e = self.check_err(body('print(5[0])'))
        self.assertEqual((e["code"], "only lists, dicts and strs" in e["message"]), ("E0103", True))

    def test_numbers_as_text(self):
        src = body('print(255.to_str(2) + " " + 255.to_str(16) + " " + (-5).to_str(2) + " " + 0.to_str(2) + " " + 35.to_str(36))',
                   'n = 10', 'print(n.to_str(10))', 'big: u64 = 18446744073709551615', 'print(big.to_str(16))',
                   'm = -9223372036854775807 - 1', 'print(m.to_str(16))',
                   'print(3.14159.fixed(2) + " " + 2.0.fixed(0) + " " + 2.5.fixed(3) + " " + (-0.05).fixed(1))', 'f: f32 = 1.5',
                   'print(f.fixed(2))')
        self.assertEqual(self.run_src(src), "11111111 ff -101 0 z\n10\nffffffffffffffff\n-8000000000000000\n3.14 2 2.500 -0.1\n1.50\n")
        _, err = self.run_fail(body('b = 1', 'print(5.to_str(b))'), 70)
        self.assertIn("base from 2 to 36", err)
        _, err = self.run_fail(body('d = -1', 'print(1.5.fixed(d))'), 70)
        self.assertIn("0 to 100 digits", err)
        self.assertEqual(self.check_err(body('print(5.fixed(2))'))["code"], "E0109")
        self.assertEqual(self.check_err(body('print(5.to_str())'))["code"], "E0104")


class StdinTests(TokCase):
    def feed(self, src, text):
        return self.run_src(src, name="in.tok", stdin=text)

    def test_read_line_then_read_all(self):
        src = body('first = io.read_line()', 'print(first or "(none)")', 'rest = io.read_all()', 'print(rest.lines())',
                   'print(io.read_line())', 'print(io.read_all().len())')
        self.assertEqual(self.feed(src, "one\r\ntwo\n\nthree"), 'one\n["two", "", "three"]\nnone\n0\n')
        self.assertEqual(self.feed(src, ""), '(none)\n[]\nnone\n0\n')

    def test_read_line_loop_by_recursion_and_long_lines(self):
        src = ('fn total(sum: int) -> int ! io\n  line = io.read_line()\n  if line == none\n    return sum\n'
               '  total(sum + (line.trim().to_int() or 0))\n\n' + MAIN + '  print(total(0))\n')
        self.assertEqual(self.feed(src, "1\n2\nx\n 4 \n"), "7\n")
        src = body('print((io.read_line() or "").len())', 'print((io.read_line() or "").len())')
        self.assertEqual(self.feed(src, "a" * 5000 + "\n" + "b" * 3), "5000\n3\n")

    def test_stdin_needs_the_io_effect(self):
        e = self.check_err('fn get() -> str\n  io.read_all()\n\nfn main() ! io\n  print(get())\n')
        self.assertEqual((e["code"], e["fix"]["insert"]), ("E0201", " ! io"))
        e = self.check_err(body('print(io.read_everything())'))
        self.assertEqual((e["code"], "read_all, read_line" in e["message"]), ("E0101", True))
        e = self.check_err(body('print(io.read_all(1))'))
        self.assertEqual(e["code"], "E0104")


if __name__ == "__main__":
    unittest.main()
