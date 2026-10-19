import json
import unittest
from pathlib import Path
from unittest import mock

from core.errors import ModuleError
from modules.clipboard_read import module
from modules.clipboard_read.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))
DEFAULTS = {"max_chars": 200000}


class ReadClipboard(unittest.TestCase):
    """不碰真实剪贴板（测试不能把你复制的东西弄丢）；真实读写的往返测试在 clipboard_write 里，需手动打开。"""

    def read(self, content, **kw):
        with mock.patch.object(module, "_supported", lambda: True), mock.patch.object(module, "_get_text", lambda: content):
            return run({**DEFAULTS, **kw})

    def test_examples(self):
        for ex in SPEC["examples"]:
            self.assertEqual(self.read("你好，世界", **ex["input"]), ex["output"], ex["title"])

    def test_newlines_and_empty(self):
        self.assertEqual(self.read("a\r\nb"), {"text": "a\nb", "chars": 3})
        self.assertEqual(self.read(""), {"text": "", "chars": 0})

    def test_unsupported_platform(self):
        with mock.patch.object(module, "_supported", lambda: False), self.assertRaises(ModuleError) as c:
            run(dict(DEFAULTS))
        self.assertEqual(c.exception.code, "unsupported_platform")

    def test_busy(self):
        class Busy:
            def OpenClipboard(self, _h):
                return False
        with mock.patch.object(module.time, "sleep", lambda _s: None), self.assertRaises(ModuleError) as c:
            module._open(Busy())
        self.assertEqual(c.exception.code, "clipboard_busy")


if __name__ == "__main__":
    unittest.main()
