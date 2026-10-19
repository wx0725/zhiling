import json
import unittest
from pathlib import Path

from core.errors import ModuleError
from modules.data_get.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))
DEFAULTS = {"default": None}


def get(data, path, **kw):
    return run({**DEFAULTS, "data": data, "path": path, **kw})


class Get(unittest.TestCase):
    def test_examples(self):
        for ex in SPEC["examples"]:
            self.assertEqual(run({**DEFAULTS, **ex["input"]}), ex["output"], ex["title"])

    def test_whole_value(self):
        self.assertEqual(get("[1, 2]", ""), {"value": [1, 2], "found": True})

    def test_null_value_is_found(self):
        self.assertEqual(get({"a": None}, "a"), {"value": None, "found": True})

    def test_missing_paths(self):
        for data, path in [({"a": 1}, "a.b"), ([1], "1"), ([1], "-1"), ([1], "x"), ("3", "a")]:
            self.assertEqual(get(data, path), {"value": None, "found": False}, (data, path))

    def test_invalid_json(self):
        with self.assertRaises(ModuleError) as c:
            get("{不是", "a")
        self.assertEqual(c.exception.code, "invalid_json")


if __name__ == "__main__":
    unittest.main()
