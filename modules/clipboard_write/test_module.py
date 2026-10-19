import ctypes
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from core.errors import ModuleError
from modules.clipboard_write import module
from modules.clipboard_write.module import dry_run, run

REAL_SET_TEXT = module._set_text  # setUp 里会把它换掉，这里留一份真的
SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))


class WriteClipboard(unittest.TestCase):
    """默认不碰真实剪贴板（测试不能把你复制的东西弄丢）：写入动作换成记录下来。"""

    def setUp(self):
        self.sent = []
        patches = [mock.patch.object(module, "_supported", lambda: True),
                   mock.patch.object(module, "_set_text", self.sent.append)]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def test_examples(self):
        for ex in SPEC["examples"]:
            self.assertEqual(dry_run(ex["input"]), ex["output"], ex["title"])
            self.assertEqual(self.sent, [])                      # 演练不碰剪贴板
            self.assertEqual(run(ex["input"]), ex["output"], ex["title"])
            self.assertEqual(self.sent.pop(), (ex["input"]["text"] + "\0").encode("utf-16-le"))

    def test_newlines_become_windows_style(self):
        run({"text": "a\nb\r\nc"})
        self.assertEqual(self.sent[0], "a\r\nb\r\nc\0".encode("utf-16-le"))

    def code(self, fn, text="x"):
        with self.assertRaises(ModuleError) as c:
            fn({"text": text})
        return c.exception.code

    def test_errors(self):
        for fn in (run, dry_run):
            self.assertEqual(self.code(fn, "坏\ud800字"), "write_failed")
            with mock.patch.object(module, "_supported", lambda: False):
                self.assertEqual(self.code(fn), "unsupported_platform")
        self.assertEqual(self.sent, [])

    def test_low_level_failures_free_memory(self):
        buf = ctypes.create_string_buffer(16)

        class User32:
            def OpenClipboard(self, _h):
                return False

        class Kernel32:
            def __init__(self, lock):
                self.lock, self.freed = lock, []

            def GlobalAlloc(self, _f, _n):
                return 7

            def GlobalLock(self, _h):
                return self.lock

            def GlobalUnlock(self, _h):
                return True

            def GlobalFree(self, h):
                self.freed.append(h)

        for lock, want in ((0, "write_failed"), (ctypes.addressof(buf), "clipboard_busy")):
            k = Kernel32(lock)
            with mock.patch.object(module, "_api", lambda: (User32(), k)), \
                    mock.patch.object(module.time, "sleep", lambda _s: None), \
                    self.assertRaises(ModuleError) as c:
                REAL_SET_TEXT(b"x\0\0")
            self.assertEqual((c.exception.code, k.freed), (want, [7]))


@unittest.skipUnless(sys.platform == "win32" and os.environ.get("WF_TEST_CLIPBOARD") == "1",
                     "会改动真实剪贴板，设置环境变量 WF_TEST_CLIPBOARD=1 才运行")
class RealClipboardRoundTrip(unittest.TestCase):
    def test_write_then_read_back(self):
        from modules.clipboard_read import module as reader
        before = reader._get_text()
        try:
            self.assertEqual(run({"text": "织灵 剪贴板测试 🙂\n第二行"}), {"chars": 14})
            self.assertEqual(reader.run({"max_chars": 1000}), {"text": "织灵 剪贴板测试 🙂\n第二行", "chars": 14})
        finally:
            run({"text": before})


if __name__ == "__main__":
    unittest.main()
