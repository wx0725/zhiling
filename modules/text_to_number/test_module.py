import json
import unittest
from pathlib import Path

from core.errors import ModuleError
from modules.text_to_number.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))
DEFAULTS = {"percent": True}


def conv(text, **kw):
    return run({**DEFAULTS, "value": text, **kw})["number"]


class ToNumber(unittest.TestCase):
    def test_examples(self):
        for ex in SPEC["examples"]:
            self.assertEqual(run({**DEFAULTS, **ex["input"]}), ex["output"], ex["title"])

    def test_common_forms(self):
        cases = {"1,234.5": 1234.5, "-3": -3, "+7": 7, "12": 12, "0.25": 0.25, ".5": 0.5, "1e3": 1000,
                 "$3.5": 3.5, "3.5 USD": 3.5, "人民币 100 元": 100, "-￥12": -12, "￥-12": -12,
                 "１２３": 123, "１，２３４": 1234, "  42  ": 42, "2亿": 200000000, "1.1%": 0.011,
                 "12.0": 12, "1,000,000": 1000000}
        for text, want in cases.items():
            got = conv(text)
            self.assertEqual(got, want, text)
            self.assertNotIsInstance(got, bool)

    def test_integer_results_are_int(self):
        self.assertIsInstance(conv("12元"), int)
        self.assertIsInstance(conv("1,234.5"), float)

    def test_numbers_pass_through(self):
        self.assertEqual(conv(7), 7)
        self.assertEqual(conv(2.5), 2.5)

    def test_not_number(self):
        for bad in ["", "abc", "约十二", "12-15", "1,23", "1.2.3", "%", "元", True, None, [1], "nan", "inf"]:
            with self.assertRaises(ModuleError, msg=repr(bad)) as c:
                conv(bad)
            self.assertEqual(c.exception.code, "not_number")
        with self.assertRaises(ModuleError) as c:
            conv("约十二")
        self.assertIn("约十二", c.exception.message)


if __name__ == "__main__":
    unittest.main()
