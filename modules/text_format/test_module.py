import json
import unittest
from pathlib import Path

from core.errors import ModuleError
from modules.text_format.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))


class Format(unittest.TestCase):
    def test_examples(self):
        for ex in SPEC["examples"]:
            self.assertEqual(run({"template": "{value}", "value": None, **ex["input"]}), ex["output"], ex["title"])

    def test_values(self):
        self.assertEqual(run({"template": "{value}", "value": 3.5})["text"], "3.5")
        self.assertEqual(run({"template": "{value}", "value": False})["text"], "否")
        self.assertEqual(run({"template": "{value}", "value": 0})["text"], "0")
        self.assertEqual(run({"template": "{value}", "value": {"k": "中"}})["text"], '{"k": "中"}')

    def test_other_braces_kept(self):
        self.assertEqual(run({"template": "{x} {value}", "value": 1})["text"], "{x} 1")

    def test_fixed_text_without_value(self):
        self.assertEqual(run({"template": "网页打不开", "value": None})["text"], "网页打不开")

    def test_missing_placeholder(self):
        with self.assertRaises(ModuleError) as c:
            run({"template": "没有占位", "value": 1})
        self.assertEqual(c.exception.code, "missing_placeholder")


if __name__ == "__main__":
    unittest.main()
