import json
import unittest
from pathlib import Path

from core.errors import ModuleError
from modules.user_confirm.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))
DEFAULTS = {k: p["default"] for k, p in SPEC["inputs"].items() if "default" in p}


class Confirm(unittest.TestCase):
    def test_examples(self):
        for ex in SPEC["examples"]:
            self.assertEqual(run({**DEFAULTS, **ex["input"]}), ex["output"], ex["title"])

    def test_second_button_is_not_approved(self):
        out = run({**DEFAULTS, "title": "审核", "options": ["通过", "不通过"], "answer": {"choice": "不通过"}})
        self.assertEqual(out, {"choice": "不通过", "approved": False, "done": [], "note": ""})

    def test_unknown_choice_and_items_fall_back(self):
        out = run({**DEFAULTS, "title": "t", "items": ["a", "b"], "answer": {"choice": "乱点", "done": ["b", "没有的"], "note": 3}})
        self.assertEqual(out, {"choice": "好的", "approved": True, "done": ["b"], "note": ""})

    def test_no_options(self):
        with self.assertRaises(ModuleError) as c:
            run({**DEFAULTS, "title": "t", "options": []})
        self.assertEqual(c.exception.code, "no_options")
