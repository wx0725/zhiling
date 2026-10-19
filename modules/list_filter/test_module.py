import json
import unittest
from pathlib import Path

from core.errors import ModuleError
from modules.list_filter.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))


class Filter(unittest.TestCase):
    def test_examples(self):
        for ex in SPEC["examples"]:
            self.assertEqual(run(ex["input"]), ex["output"], ex["title"])

    def test_empty(self):
        self.assertEqual(run({"items": [], "keep": []}), {"items": [], "count": 0})

    def test_length_mismatch(self):
        with self.assertRaises(ModuleError) as c:
            run({"items": [1, 2], "keep": [True]})
        self.assertEqual(c.exception.code, "length_mismatch")


if __name__ == "__main__":
    unittest.main()
