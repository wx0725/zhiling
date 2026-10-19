import json
import unittest
from pathlib import Path

from core.errors import ModuleError
from modules.list_sort.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))
DEFAULTS = {"order": "asc", "field": "", "by": "auto", "unique": False}


def srt(items, **kw):
    return run({**DEFAULTS, "items": items, **kw})["items"]


class Sort(unittest.TestCase):
    def test_examples(self):
        for ex in SPEC["examples"]:
            self.assertEqual(run({**DEFAULTS, **ex["input"]}), ex["output"], ex["title"])

    def test_empty(self):
        self.assertEqual(run({**DEFAULTS, "items": []}), {"items": [], "count": 0})

    def test_stable_for_equal_values(self):
        rows = [{"n": "a", "s": 1}, {"n": "b", "s": 2}, {"n": "c", "s": 1}]
        self.assertEqual([r["n"] for r in srt(rows, field="s")], ["a", "c", "b"])
        self.assertEqual([r["n"] for r in srt(rows, field="s", order="desc")], ["b", "a", "c"])

    def test_text_ignores_case_and_nested_field(self):
        self.assertEqual(srt(["b", "B2", "a"]), ["a", "b", "B2"])
        rows = [{"u": {"age": 30}}, {"u": {"age": 20}}]
        self.assertEqual(srt(rows, field="u.age"), [{"u": {"age": 20}}, {"u": {"age": 30}}])

    def test_number_mode_accepts_numeric_text(self):
        self.assertEqual(srt(["10", "9", "1,200", 3], by="number"), [3, "9", "10", "1,200"])

    def test_text_mode_handles_mixed(self):
        self.assertEqual(srt([10, "9", 2], by="text"), [2, "9", 10])

    def test_unique_by_field_keeps_first(self):
        rows = [{"n": "小王", "v": 1}, {"n": "小李", "v": 2}, {"n": "小王", "v": 3}]
        out = run({**DEFAULTS, "items": rows, "field": "n", "unique": True, "order": "none"})
        self.assertEqual(out, {"items": rows[:2], "count": 2})
        self.assertEqual(srt([3, 1, 3, 2, 1], unique=True), [1, 2, 3])
        self.assertEqual(srt([{"a": 1}, {"a": 1}], unique=True, order="none"), [{"a": 1}])

    def code(self, items, **kw):
        with self.assertRaises(ModuleError) as c:
            srt(items, **kw)
        return c.exception

    def test_errors(self):
        self.assertEqual(self.code([1], order="up").code, "invalid_option")
        self.assertEqual(self.code([1], by="pinyin").code, "invalid_option")
        e = self.code([{"a": 1}, {"b": 2}], field="a")
        self.assertEqual((e.code, "第 2 项" in e.message), ("missing_field", True))
        self.assertEqual(self.code([1, "a"]).code, "not_comparable")
        self.assertEqual(self.code([True, False]).code, "not_comparable")
        e = self.code([1, "x"], by="number")
        self.assertEqual((e.code, "第 2 项" in e.message), ("not_number", True))


if __name__ == "__main__":
    unittest.main()
