import json
import unittest
from pathlib import Path

from core.errors import ModuleError
from modules.text_split.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))
DEFAULTS = {"separator": "\n", "trim": True, "skip_empty": True}


class Split(unittest.TestCase):
    def test_examples(self):
        for ex in SPEC["examples"]:
            self.assertEqual(run({**DEFAULTS, **ex["input"]}), ex["output"], ex["title"])

    def test_windows_newlines(self):
        self.assertEqual(run({**DEFAULTS, "text": "a\r\nb\rc"})["items"], ["a", "b", "c"])

    def test_keep_empty_and_spaces(self):
        out = run({**DEFAULTS, "text": " a ,,b", "separator": ",", "trim": False, "skip_empty": False})
        self.assertEqual(out["items"], [" a ", "", "b"])

    def test_empty_text(self):
        self.assertEqual(run({**DEFAULTS, "text": ""})["items"], [])

    def test_empty_separator(self):
        with self.assertRaises(ModuleError) as c:
            run({**DEFAULTS, "text": "a", "separator": ""})
        self.assertEqual(c.exception.code, "empty_separator")


if __name__ == "__main__":
    unittest.main()
