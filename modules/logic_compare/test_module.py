import json
import unittest
from pathlib import Path

from core.errors import ModuleError
from modules.logic_compare.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))


def call(**kw):
    return run({"b": None, **kw})


class Compare(unittest.TestCase):
    def test_examples(self):
        for ex in SPEC["examples"]:
            self.assertEqual(call(**ex["input"]), ex["output"], ex["title"])

    def test_ops(self):
        self.assertTrue(call(a=1, op="ne", b=2)["result"])
        self.assertTrue(call(a=2.5, op="ge", b=2.5)["result"])
        self.assertTrue(call(a=[1, 2], op="contains", b=2)["result"])
        self.assertTrue(call(a="https://x", op="starts_with", b="https")["result"])
        self.assertFalse(call(a=[0], op="empty")["result"])

    def test_errors(self):
        with self.assertRaises(ModuleError) as c:
            call(a="10", op="gt", b=5)
        self.assertEqual(c.exception.code, "type_mismatch")
        with self.assertRaises(ModuleError) as c:
            call(a=True, op="lt", b=5)  # 布尔值不当数字
        self.assertEqual(c.exception.code, "type_mismatch")
        with self.assertRaises(ModuleError) as c:
            call(a=1, op="approx", b=1)
        self.assertEqual(c.exception.code, "invalid_op")


if __name__ == "__main__":
    unittest.main()
