import json
import unittest
from pathlib import Path

from core.errors import ModuleError
from modules.list_to_numbers.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))
DEFAULTS = {"percent": True, "skip_bad": False}


class ToNumbers(unittest.TestCase):
    def test_examples(self):
        for ex in SPEC["examples"]:
            self.assertEqual(run({**DEFAULTS, **ex["input"]}), ex["output"], ex["title"])

    def test_empty_list(self):
        self.assertEqual(run({**DEFAULTS, "items": []}), {"values": [], "count": 0})

    def test_mixed_numbers_and_text(self):
        self.assertEqual(run({**DEFAULTS, "items": [1, "2", 3.5, "-4元"]})["values"], [1, 2, 3.5, -4])

    def test_percent_off(self):
        self.assertEqual(run({**DEFAULTS, "items": ["50%"], "percent": False})["values"], [50])

    def test_bad_item_names_its_position(self):
        with self.assertRaises(ModuleError) as c:
            run({**DEFAULTS, "items": ["1", "2", "缺考"]})
        self.assertEqual(c.exception.code, "not_number")
        self.assertIn("第 3 项", c.exception.message)
        self.assertIn("缺考", c.exception.message)

    def test_booleans_and_none_are_not_numbers(self):
        for bad in [True, None, {"a": 1}]:
            with self.assertRaises(ModuleError):
                run({**DEFAULTS, "items": [bad]})
        self.assertEqual(run({**DEFAULTS, "items": [True, None, "3"], "skip_bad": True})["values"], [3])


if __name__ == "__main__":
    unittest.main()
