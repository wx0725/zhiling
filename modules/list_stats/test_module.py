import json
import unittest
from pathlib import Path

from core.errors import ModuleError
from modules.list_stats.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))


class Stats(unittest.TestCase):
    def test_examples(self):
        for ex in SPEC["examples"]:
            self.assertEqual(run(ex["input"]), ex["output"], ex["title"])

    def test_max_min_and_fractional_mean(self):
        self.assertEqual(run({"items": [3, -1, 7], "op": "max"})["value"], 7)
        self.assertEqual(run({"items": [3, -1, 7], "op": "min"})["value"], -1)
        self.assertAlmostEqual(run({"items": [1, 2], "op": "mean"})["value"], 1.5)

    def test_count_is_always_an_integer(self):
        self.assertEqual(run({"items": [1.5, 2], "op": "max"}), {"value": 2, "count": 2})

    def test_empty_list(self):
        self.assertEqual(run({"items": [], "op": "count"})["value"], 0)
        self.assertEqual(run({"items": [], "op": "sum"})["value"], 0)
        with self.assertRaises(ModuleError) as c:
            run({"items": [], "op": "mean"})
        self.assertEqual(c.exception.code, "empty_list")

    def test_rejects_text_and_booleans(self):
        for bad in (["1", 2], [True, 2]):
            with self.assertRaises(ModuleError) as c:
                run({"items": bad, "op": "sum"})
            self.assertEqual(c.exception.code, "not_number")

    def test_invalid_op(self):
        with self.assertRaises(ModuleError) as c:
            run({"items": [1], "op": "avg"})
        self.assertEqual(c.exception.code, "invalid_op")


if __name__ == "__main__":
    unittest.main()
