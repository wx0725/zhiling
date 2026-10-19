import json
import unittest
from pathlib import Path

from core.errors import ModuleError
from modules.text_replace.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))
DEFAULTS = {"replace_with": "", "all": True, "regex": False, "ignore_case": False}


def rep(text, find, **kw):
    return run({**DEFAULTS, "text": text, "find": find, **kw})


class Replace(unittest.TestCase):
    def test_examples(self):
        for ex in SPEC["examples"]:
            self.assertEqual(run({**DEFAULTS, **ex["input"]}), ex["output"], ex["title"])

    def test_not_found_is_fine(self):
        self.assertEqual(rep("abc", "x", replace_with="y"), {"text": "abc", "count": 0})
        self.assertEqual(rep("abc", "x", all=False), {"text": "abc", "count": 0})

    def test_ignore_case_literal(self):
        self.assertEqual(rep("Cat cat CAT", "cat", replace_with="狗", ignore_case=True), {"text": "狗 狗 狗", "count": 3})
        # 普通文字模式下替换内容里的反斜杠按字面写入
        self.assertEqual(rep("A.b", "a.", replace_with="\\1", ignore_case=True)["text"], "\\1b")

    def test_literal_mode_does_not_treat_dot_as_regex(self):
        self.assertEqual(rep("a.b axb", ".", replace_with="-"), {"text": "a-b axb", "count": 1})

    def test_regex_groups(self):
        out = rep("2026-10-19", r"(\d+)-(\d+)-(\d+)", replace_with=r"\1年\2月\3日", regex=True)
        self.assertEqual(out, {"text": "2026年10月19日", "count": 1})

    def test_regex_first_only_and_ignore_case(self):
        self.assertEqual(rep("AbAb", "ab", replace_with="x", regex=True, all=False, ignore_case=True),
                         {"text": "xAb", "count": 1})

    def code(self, **kw):
        with self.assertRaises(ModuleError) as c:
            rep(**{"text": "abc", "find": "a", **kw})
        return c.exception.code

    def test_errors(self):
        self.assertEqual(self.code(find=""), "empty_find")
        self.assertEqual(self.code(find="(", regex=True), "bad_pattern")
        self.assertEqual(self.code(find="(a)", replace_with=r"\2", regex=True), "bad_pattern")


if __name__ == "__main__":
    unittest.main()
