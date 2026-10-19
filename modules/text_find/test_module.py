import json
import unittest
from pathlib import Path

from core.errors import ModuleError
from modules.text_find.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))
DEFAULTS = {"unique": True, "ignore_case": False}


def find(text, pattern, **kw):
    return run({**DEFAULTS, "text": text, "pattern": pattern, **kw})


class Find(unittest.TestCase):
    def test_examples(self):
        for ex in SPEC["examples"]:
            self.assertEqual(run({**DEFAULTS, **ex["input"]}), ex["output"], ex["title"])

    def test_url_stops_at_chinese_punctuation(self):
        out = find("见 https://example.com/a?b=1，以及 http://x.org。", "url")
        self.assertEqual(out["matches"], ["https://example.com/a?b=1", "http://x.org"])

    def test_phone(self):
        self.assertEqual(find("电话 13812345678，座机 0101234567890", "phone")["matches"], ["13812345678"])

    def test_keep_duplicates_and_ignore_case(self):
        self.assertEqual(find("Ab ab AB", "ab", unique=False, ignore_case=True)["count"], 3)

    def test_preset_name_is_case_insensitive(self):
        self.assertEqual(find("x@y.cn", "Email")["matches"], ["x@y.cn"])

    def test_empty_matches_are_dropped(self):
        self.assertEqual(find("abc", "x*")["matches"], [])

    def test_invalid_pattern(self):
        with self.assertRaises(ModuleError) as c:
            find("a", "(")
        self.assertEqual(c.exception.code, "invalid_pattern")


if __name__ == "__main__":
    unittest.main()
