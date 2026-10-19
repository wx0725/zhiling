import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from core.errors import ModuleError
from modules.time_now import module
from modules.time_now.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))
DEFAULTS = {"format": "datetime", "offset_days": 0}
FIXED = datetime(2026, 10, 19, 9, 30, 5)  # 示例里假设的“现在”


class Now(unittest.TestCase):
    def at(self, when=FIXED, **kw):
        with mock.patch.object(module, "_now", lambda: when):
            return run({**DEFAULTS, **kw})

    def test_examples_at_fixed_time(self):
        for ex in SPEC["examples"]:
            self.assertEqual(self.at(**ex["input"]), ex["output"], ex["title"])

    def test_presets(self):
        self.assertEqual(self.at()["text"], "2026-10-19 09:30:05")
        self.assertEqual(self.at(format="time")["text"], "09:30:05")
        self.assertEqual(self.at(format="compact")["text"], "20261019")
        self.assertEqual(self.at(format="  ")["text"], "2026-10-19 09:30:05")

    def test_custom_format_keeps_other_text(self):
        self.assertEqual(self.at(format="%Y年第%j天 100%%")["text"], "2026年第292天 100%")

    def test_offset_crosses_month_and_year(self):
        out = self.at(datetime(2026, 1, 1, 8, 0), format="date", offset_days=-1)
        self.assertEqual((out["text"], out["year"], out["month"], out["day"], out["weekday"]),
                         ("2025-12-31", 2025, 12, 31, "星期三"))

    def test_real_clock_has_right_shape(self):
        out = run(dict(DEFAULTS))
        self.assertRegex(out["text"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        self.assertTrue(out["text"].startswith(out["date"]))  # 同一时刻算出的，不会对不上
        self.assertIn(out["weekday"], module.WEEKDAYS)
        self.assertTrue(1 <= out["month"] <= 12 and 1 <= out["day"] <= 31)

    def code(self, **kw):
        with self.assertRaises(ModuleError) as c:
            self.at(**kw)
        return c.exception.code

    def test_errors(self):
        self.assertEqual(self.code(format="yyyy-mm-dd"), "invalid_format")
        self.assertEqual(self.code(format="%Q"), "invalid_format")
        self.assertEqual(self.code(format="%Y%"), "invalid_format")
        self.assertEqual(self.code(offset_days=10 ** 7), "bad_offset")


if __name__ == "__main__":
    unittest.main()
