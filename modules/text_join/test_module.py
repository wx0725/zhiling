import json
import unittest
from pathlib import Path

from modules.text_join.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))
DEFAULTS = {"separator": "\n", "numbered": False}


class Join(unittest.TestCase):
    def test_examples(self):
        for ex in SPEC["examples"]:
            self.assertEqual(run({**DEFAULTS, **ex["input"]}), ex["output"], ex["title"])

    def test_empty_and_mixed(self):
        self.assertEqual(run({**DEFAULTS, "items": []})["text"], "")
        self.assertEqual(run({**DEFAULTS, "items": [True, None, {"a": 1}]})["text"], '是\n\n{"a": 1}')


if __name__ == "__main__":
    unittest.main()
