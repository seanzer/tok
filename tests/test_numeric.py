"""Item 8: f32 and the fixed-width integer types."""
import os
import unittest

from helpers import ROOT, TokCase, tokc


class SizedNumberTests(TokCase):
    def test_all_types_in_records_lists_and_interpolation(self):
        src = ('type All = { a: i8, b: i16, c: i32, d: i64, e: u8, f: u16, g: u32, h: u64, x: f32, y: f64, n: int }\n\n'
               'fn main() ! io\n'
               '  lo = All { a: -128, b: -32768, c: -2147483648, d: -9223372036854775808, e: 0, f: 0, g: 0, h: 0, '
               'x: -1.5, y: -1.5, n: -9223372036854775808 }\n'
               '  hi = All { a: 127, b: 32767, c: 2147483647, d: 9223372036854775807, e: 255, f: 65535, g: 4294967295, '
               'h: 18446744073709551615, x: 0.1, y: 0.1, n: 9223372036854775807 }\n'
               '  print(lo)\n'
               '  print(hi)\n'
               '  bytes: [u8] = [1, 2, 255]\n'
               '  fs: [f32] = [0.5, 1, 2.25]\n'
               '  var grow: [i16] = []\n'
               '  grow.push(-5)\n'
               '  print("{bytes} {fs} {grow} {bytes[2]} {lo == hi} {bytes == [1, 2, 255]} {bytes.contains(255)}")\n')
        self.assertEqual(self.run_src(src),
                         "{a: -128, b: -32768, c: -2147483648, d: -9223372036854775808, e: 0, f: 0, g: 0, h: 0, "
                         "x: -1.5, y: -1.5, n: -9223372036854775808}\n"
                         "{a: 127, b: 32767, c: 2147483647, d: 9223372036854775807, e: 255, f: 65535, g: 4294967295, "
                         "h: 18446744073709551615, x: 0.1, y: 0.1, n: 9223372036854775807}\n"
                         "[1, 2, 255] [0.5, 1.0, 2.25] [-5] 255 false true true\n")

    def test_arithmetic_and_comparison_within_a_type(self):
        src = ('fn add(a: u8, b: u8) -> u8\n'
               '  a + b\n\n'
               'fn main() ! io\n'
               '  var x: u8 = 250\n'
               '  x += 10\n'
               '  a: i32 = -7\n'
               '  big: u64 = 18446744073709551615\n'
               '  small: i8 = -128\n'
               '  h: f32 = 1.5\n'
               '  third: f32 = 1.0 / 3.0\n'
               '  print("{x} {add(200, 100)} {a / 2} {a % 2} {a * a} {-a} {big + 1} {big / 2} {small - 1} {small / -1}")\n'
               '  print("{h * 2} {h / 4} {third} {1.0 / 3.0} {sqrt(h)} {a < 0} {x == 4} {big > 5} {h >= 1.5}")\n'
               '  bytes: [u8] = [200, 100]\n'
               '  print("{bytes.sum()} {max(bytes)} {min(a, 3)} {abs(a)} {mean([h, 2.5])}")\n'
               '  for i in u8(254)..u8(255)\n'
               '    print(i)\n'
               '  idx: u8 = 1\n'
               '  print(bytes[idx])\n')
        self.assertEqual(self.run_src(src),
                         "4 44 -3 -1 49 7 0 9223372036854775807 127 -128\n"
                         "3.0 0.375 0.33333334 0.3333333333333333 1.2247449 true true true true\n"
                         "44 200 -7 7 2.0\n254\n255\n100\n")

    def test_conversions(self):
        src = ('fn main() ! io\n'
               '  n = 3\n'
               '  a: i32 = -7\n'
               '  b: i64 = 9\n'
               '  f = 2.9\n'
               '  print("{i64(a) + b} {f64(n) / 2.0} {int(f)} {int(-2.9)} {u8(n)} {i8(-128)} {f32(0.1)} {f64(f32(0.1))}")\n'
               '  print("{u8(f)} {i16(f32(-3.99))} {int(b)} {i64(n)} {u64(n)} {f32(n)} {u32(u8(200))} {int(1e18)}")\n')
        self.assertEqual(self.run_src(src),
                         "2 1.5 2 -2 3 -128 0.1 0.10000000149011612\n"
                         "2 -3 9 3 3 3.0 200 1000000000000000000\n")

    def test_conversions_that_do_not_fit_panic(self):
        cases = {
            'a: i32 = -7\n  print(u8(a))\n': "-7 does not fit in u8 (f.tok:3)",
            'a = 256\n  print(u8(a))\n': "256 does not fit in u8 (f.tok:3)",
            'a: u64 = 18446744073709551615\n  print(int(a))\n': "18446744073709551615 does not fit in int (f.tok:3)",
            'a = 300.5\n  print(u8(a))\n': "300.5 does not fit in u8 (f.tok:3)",
            'a = -1.0\n  print(u32(a))\n': "-1.0 does not fit in u32 (f.tok:3)",
            'a = 1e19\n  print(int(a))\n': "does not fit in int (f.tok:3)",
            'a = 0.0\n  print(i32(a / a))\n': "does not fit in i32 (f.tok:3)",
            'a: i8 = 0\n  print(a / a)\n': "division by zero (f.tok:3)",
        }
        for body, msg in cases.items():
            with self.subTest(body=body):
                out, err = self.run_fail('fn main() ! io\n  ' + body, 70)
                self.assertIn(msg, err)

    def test_mixing_types_is_an_error_with_a_fix_when_lossless(self):
        src = ('fn main() ! io\n'
               '  a: i32 = 5\n'
               '  b: i64 = 7\n'
               '  print(a + b)\n')
        e = self.check_err(src)
        self.assertEqual(e["code"], "E0106")
        self.assertEqual(e["fix"]["edits"], [{"line": 4, "col": 9, "insert": "int("}, {"line": 4, "col": 10, "insert": ")"}])
        self.assertEqual(self.fix_and_run(src), "12\n")

    def test_fixes_for_arguments_bindings_and_right_operands(self):
        src = ('fn half(x: f64) -> f64\n'
               '  x / 2.0\n\n'
               'fn main() ! io\n'
               '  n = 9\n'
               '  small: u8 = 3\n'
               '  wide: u32 = small\n'
               '  f: f32 = 0.5\n'
               '  print("{half(n)} {wide} {half(1.0) + f} {n * small}")\n')
        self.assertEqual(self.codes(src), ["E0106"] * 2)  # one per statement
        self.assertEqual(self.fix_and_run(src), "4.5 3 1.0 27\n")

    def test_int_to_f64_fix_says_it_can_round(self):
        e = self.check_err('fn main() ! io\n  n = 3\n  print(n * 1.5 + sqrt(2.0))\n')
        self.assertEqual(e["code"], "E0106")
        self.assertIn("beyond 2^53 are rounded", e["fix"]["note"])
        self.assertEqual(self.fix_and_run('fn main() ! io\n  n = 3\n  print(n * 1.5)\n'), "4.5\n")

    def test_no_fix_when_the_conversion_could_lose_information(self):
        cases = [
            'fn main()\n  a: i64 = 5\n  b: i32 = a\n',
            'fn main()\n  a: u8 = 5\n  b: i8 = 1\n  c = a + b\n',
            'fn main()\n  a = 5\n  b: f32 = 1.0\n  c = a * b\n',
            'fn main()\n  a: i64 = 5\n  b: f32 = 2.0\n  c = a < b\n',
            'fn main()\n  a: u64 = 5\n  b = 2\n  for i in a..b\n    c = i\n',
        ]
        for src in cases:
            with self.subTest(src=src):
                e = self.check_err(src)
                self.assertEqual((e["code"], e.get("fix")), ("E0106", None))

    def test_literal_rules(self):
        cases = {
            'fn main()\n  a: u8 = 256\n': ["E0107"],
            'fn main()\n  a: i8 = -129\n': ["E0107"],
            'fn main()\n  a: u8 = -1\n': ["E0107"],
            'fn main()\n  a = u8(300)\n': ["E0107"],
            'fn main()\n  a = 9223372036854775808\n': ["E0107"],
            'fn main()\n  a: u64 = 18446744073709551616\n': ["E0107"],
            'fn main()\n  a: f32 = 1e39\n': ["E0107"],
            'fn main()\n  a: i32 = 1.5\n': ["E0106"],
            'fn main()\n  a: u8 = 1\n  b = a + 256\n': ["E0107"],
            'fn main()\n  a: u8 = 1\n  b = -a\n': ["E0103"],
            'fn main()\n  a: f32 = 1.0\n  b = a % a\n': ["E0103"],
            'fn main()\n  a = u8("7")\n': ["E0103"],
            'fn main()\n  a = u8(1, 2)\n': ["E0104"],
            'type u8 = { a: int }\n\nfn main()\n  a = 1\n': ["E0102"],
        }
        for src, codes in cases.items():
            with self.subTest(src=src):
                self.assertEqual(self.codes(src), codes)

    def test_generated_c_uses_exact_width_types(self):
        path = self.write("w.tok", 'fn f(a: u8, b: i16, c: f32, d: u64, e: i32) -> f32\n  c\n\nfn main()\n  x = f(1, 2, 3, 4, 5)\n')
        c = tokc("emit-c", path).stdout
        self.assertIn("static float tok_f0_f(uint8_t v_a TOK_UNUSED, int16_t v_b TOK_UNUSED, float v_c TOK_UNUSED, "
                      "uint64_t v_d TOK_UNUSED, int32_t v_e TOK_UNUSED)", c)

    def test_pixels_example(self):
        self.assert_clean_c(os.path.join(ROOT, "examples/pixels.tok"))
        r = tokc("run", "examples/pixels.tok", env=self.env)
        self.assertEqual(r.stdout,
                         "{r: 255, g: 128, b: 0} -> luma 151.381 -> {r: 151, g: 151, b: 151}\n"
                         "{r: 10, g: 200, b: 30} -> luma 123.81 -> {r: 123, g: 123, b: 123}\n"
                         "channel total 623\n"
                         "u8 arithmetic wraps: 250 + 10 = 4\n")


if __name__ == "__main__":
    unittest.main()
