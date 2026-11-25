"""Dicts: {K: V}, a built-in map with str or integer keys, insertion order and value semantics."""
import unittest

from helpers import TokCase, tokc


class DictTests(TokCase):
    def test_counting_lookup_and_iteration_order(self):
        src = ('fn count(words: [str]) -> {str: int}\n'
               '  var d: {str: int} = {}\n'
               '  for w in words\n'
               '    d[w] = (d[w] or 0) + 1\n'
               '  d\n\n'
               'd = count("the cat the dog the end".words())\n'
               'print(d)\n'
               'print("{d["the"] or 0} {d["zebra"] or 0} {d.len()} {d.has("cat")} {d.has("cow")}")\n'
               'print(d.keys())\n'
               'print(d.values())\n'
               'for k in d\n'
               '  print("{k}={d[k]}")\n'
               'for k, v in d where v > 1\n'
               '  print("{k}: {v * 2}")\n'
               'print(d.keys().sort())\n'
               'print(d["nope"])\n')
        self.assertEqual(self.run_src(src),
                         '{"the": 3, "cat": 1, "dog": 1, "end": 1}\n3 0 4 true false\n["the", "cat", "dog", "end"]\n'
                         '[3, 1, 1, 1]\nthe=3\ncat=1\ndog=1\nend=1\nthe: 6\n["cat", "dog", "end", "the"]\nnone\n')

    def test_a_copy_is_independent_and_equality_ignores_order(self):
        src = ('fn bump(d: {str: int}, k: str) -> {str: int}\n'
               '  var out = d\n'
               '  out[k] = (out[k] or 0) + 100\n'
               '  out\n\n'
               'd = {"a": 1, "b": 2}\n'
               'e = bump(d, "a")\n'
               'var f = d\n'
               'f["c"] = 3\n'
               'print(d)\nprint(e)\nprint(f)\n'
               'print(d == e)\n'
               'print(d == {"b": 2, "a": 1})\n'
               'print(d != f)\n')
        self.assertEqual(self.run_src(src), '{"a": 1, "b": 2}\n{"a": 101, "b": 2}\n{"a": 1, "b": 2, "c": 3}\n'
                                            'false\ntrue\ntrue\n')

    def test_int_keys_update_in_place_nested_values_and_growth(self):
        src = ('type Acc = { name: str, hits: [int] }\n\n'
               'var sq = {1: "one", 2: "two"}\n'
               'sq[3] = "three"\n'
               'sq[1] = "uno"\n'
               'print(sq)\n'
               'var groups: {int: [str]} = {}\n'
               'for w in ["a", "bb", "cc", "d", "eee"]\n'
               '  if not groups.has(w.len())\n'
               '    groups[w.len()] = []\n'
               '  groups[w.len()].push(w)\n'
               'print(groups)\n'
               'var totals = {"x": 1}\n'
               'totals["x"] += 41\n'
               'print(totals)\n'
               'var accs = {"a": Acc { name: "A", hits: [] }}\n'
               'accs["a"].hits.push(3)\n'
               'accs["a"].name = "AA"\n'
               'print(accs)\n'
               'var nested = {"k": {"in": 1}}\n'
               'nested["k"]["in2"] = 2\n'
               'print(nested)\n'
               'print("{nested["k"]["in2"] or -1} {nested["zz"]["in2"] or -1}")\n'
               'var big: {int: int} = {}\n'
               'for i in 1..1000\n'
               '  big[i * 7] = i\n'
               'var total = 0\n'
               'for k, v in big\n'
               '  total += v\n'
               'print("{big.len()} {total} {big[7000] or 0} {big[6] or -1}")\n'
               'neg = {-1: "m", 0: "z"}\n'
               'print(neg[-1])\n')
        self.assertEqual(self.run_src(src),
                         '{1: "uno", 2: "two", 3: "three"}\n{1: ["a", "d"], 2: ["bb", "cc"], 3: ["eee"]}\n{"x": 42}\n'
                         '{"a": {name: "AA", hits: [3]}}\n{"k": {"in": 1, "in2": 2}}\n2 -1\n1000 500500 1000 -1\nm\n')

    def test_empty_dicts_optional_values_and_changes_during_a_loop(self):
        src = ('empty: {str: int} = {}\n'
               'print("{empty} {empty.len()} {empty.keys()} {empty == {}}")\n'
               'for k in empty\n'
               '  print(k)\n'
               'var o: {str: int?} = {"a": 1, "b": none}\n'
               'print(o)\n'
               'print("{o["a"] or -1} {o["b"] or -1} {o["c"] or -1}")\n'
               'var d = {"x": 1, "y": 2}\n'
               'for k, v in d\n'
               '  d["{k}{k}"] = v * 10\n'
               'print(d)\n'
               'a, b = {"p": 1}, {"q": 2}\n'
               'print(if d.has("xx") then a else b)\n')
        self.assertEqual(self.run_src(src), '{} 0 [] true\n{"a": 1, "b": none}\n1 -1 -1\n'
                                            '{"x": 1, "y": 2, "xx": 10, "yy": 20}\n{"p": 1}\n')

    def test_a_missing_key_stops_an_in_place_change(self):
        for line, key in (('d["zz"] += 1', '"zz"'), ('d["q"] -= 1', '"q"')):
            out, err = self.run_fail(f'var d = {{"a": 1}}\n{line}\nprint(d)\n', 70)
            self.assertEqual(out, "")
            self.assertIn(f"key {key} is not in the dict", err)
        out, err = self.run_fail('var g: {int: [int]} = {}\ng[5].push(1)\n', 70)
        self.assertIn("key 5 is not in the dict", err)

    def test_errors(self):
        cases = [
            ('d = {}\n', "E0108", "annotate it, for example 'd: {str: int} = {}'"),
            ('d = {"a": 1, "b": "x"}\n', "E0103", "dict values must all be int, found str"),
            ('d = {"a": 1, 2: 3}\n', "E0103", "dict keys must all be str, found int"),
            ('d = {1.5: 1}\n', "E0110", "a dict key must be a str or an integer, found f64"),
            ('d: {f64: int} = {}\n', "E0110", "a dict key must be a str or an integer"),
            ('d = {"a": 1}\nd["b"] = 2\n', "E0301", "'d' is immutable"),
            ('var d = {"a": 1}\nd[1] = 2\n', "E0103", "this dict has str keys"),
            ('var d = {"a": 1}\nd["b"] = "x"\n', "E0103", "this dict has int values"),
            ('d = {"a": 1}\nprint(d["a"] + 1)\n', "E0103", "found int? and int"),
            ('for k, v in [1, 2]\n  print(k)\n', "E0103", "'for k, v in' needs a dict"),
            ('d = {"a": 1}\nfor k, k in d\n  print(k)\n', "E0302", "both the key and the value"),
            ('d = {"a": 1}\nprint(d.has(1))\n', "E0103", "this dict has str keys, found int"),
            ('d = {"a": none}\n', "E0108", "cannot tell the value type"),
            ('d = {"a" 1}\n', "E0010", "between a dict key and its value"),
            ('d: {str int} = {}\n', "E0010", "between the key type and the value type"),
            ('d = {"a": 1}\nprint(d.size())\n', "E0109", "has, keys, len, values"),
            ('d = {"a": 1}\nfor k in d\n  k = "b"\n', "E0301", "'k' is immutable"),
        ]
        for src, code, text in cases:
            with self.subTest(src=src):
                e = self.check_err(src)
                self.assertEqual(e["code"], code)
                self.assertIn(text, e["message"])

    def test_the_formatter_keeps_dict_braces_tight_and_record_braces_spaced(self):
        path = self.write("f.tok", 'type P={x:int}\nd:{ str:int }={ "a":1,"b":2 }\np=P{x:1}\ne:{int:[P]}={ }\nprint({ 1:p }[1])\n')
        self.assertEqual(tokc("fmt", path).returncode, 0)
        with open(path) as f:
            text = f.read()
        self.assertEqual(text, 'type P = { x: int }\nd: {str: int} = {"a": 1, "b": 2}\np = P { x: 1 }\n'
                                            'e: {int: [P]} = {}\nprint({1: p}[1])\n')


if __name__ == "__main__":
    unittest.main()
