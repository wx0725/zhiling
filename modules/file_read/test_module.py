import tempfile
import unittest
from unittest import mock
from pathlib import Path

from core.errors import ModuleError
from modules.file_read.module import run

DEFAULTS = {"max_chars": 200000}


class Read(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, path, **kw):
        return run({**DEFAULTS, "path": str(path), **kw})

    def code(self, path):
        with self.assertRaises(ModuleError) as c:
            self.read(path)
        return c.exception.code

    def test_utf8_with_bom_and_windows_newlines(self):
        f = self.dir / "笔记.txt"
        f.write_bytes("﻿第一行\r\n第二行".encode("utf-8"))
        self.assertEqual(self.read(f), {"text": "第一行\n第二行", "name": "笔记.txt"})

    def test_gbk(self):
        f = self.dir / "a.csv"
        f.write_bytes("名字,分数\n小王,90".encode("gbk"))
        self.assertEqual(self.read(f)["text"], "名字,分数\n小王,90")

    def test_quoted_path_and_max_chars(self):
        f = self.dir / "a.md"
        f.write_text("abcdef", encoding="utf-8")
        self.assertEqual(self.read(f'"{f}"', max_chars=2)["text"], "ab")

    def test_reads_only_what_max_chars_needs(self):
        f = self.dir / "big.log"
        f.write_text("中" * 100000, encoding="utf-8")
        with mock.patch("pathlib.Path.read_bytes", side_effect=AssertionError("不应整个读入")):
            self.assertEqual(self.read(f, max_chars=3)["text"], "中中中")
        # 截断处正好落在多字节字符中间，也照常读出（GBK 同理）
        g = self.dir / "g.txt"
        g.write_bytes(("甲乙" * 50).encode("gbk"))
        self.assertEqual(self.read(g, max_chars=5)["text"], "甲乙甲乙甲")

    def test_bad_path_nul_and_device_name(self):
        self.assertEqual(self.code("a\x00b.txt"), "bad_path")   # 含 NUL
        self.assertEqual(self.code("CON.txt"), "bad_path")      # Windows 设备名
        self.assertEqual(self.code(str(self.dir / "NUL.txt")), "bad_path")

    def test_relative_path_is_from_project_root(self):
        self.assertIn("工作流", self.read("README.md")["text"])

    def test_errors(self):
        (self.dir / "x.pdf").write_bytes(b"%PDF")
        bad = self.dir / "bad.txt"
        bad.write_bytes(b"\xff\xfe\xfa\x00\x81")
        self.assertEqual(self.code(self.dir / "x.pdf"), "unsupported_type")
        self.assertEqual(self.code(self.dir / "没有.txt"), "not_found")
        self.assertEqual(self.code(bad), "decode_error")


if __name__ == "__main__":
    unittest.main()
