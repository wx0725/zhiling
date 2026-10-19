import tempfile
import unittest
from pathlib import Path

from core.errors import ModuleError
from modules.file_write import module
from modules.file_write.module import dry_run, run

DEFAULTS = {"if_exists": "error"}


class Write(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, path, text="你好", **kw):
        return run({**DEFAULTS, "text": text, "path": str(path), **kw})

    def code(self, fn, **kw):
        with self.assertRaises(ModuleError) as c:
            fn({**DEFAULTS, "text": "x", **kw})
        return c.exception.code

    def test_writes_and_creates_folders(self):
        f = self.dir / "子目录" / "总结.txt"
        out = self.write(f, "第一行\n第二行")
        self.assertEqual(out, {"path": str(f.resolve()), "chars": 7})
        self.assertEqual(f.read_bytes(), "第一行\n第二行".encode("utf-8"))

    def test_existing_file_modes(self):
        f = self.dir / "a.md"
        self.write(f, "一")
        self.assertEqual(self.code(run, path=str(f)), "exists")
        self.write(f, "二", if_exists="append")
        self.assertEqual(f.read_text(encoding="utf-8"), "一二")
        self.write(f, "三", if_exists="overwrite")
        self.assertEqual(f.read_text(encoding="utf-8"), "三")
        self.assertEqual(sorted(x.name for x in self.dir.iterdir()), ["a.md"])  # 覆盖没留下临时文件

    def test_relative_path_goes_to_outputs(self):
        old = module.OUT_DIR
        module.OUT_DIR = self.dir / "outputs"
        try:
            out = self.write("报告.txt")
        finally:
            module.OUT_DIR = old
        self.assertEqual(Path(out["path"]), (self.dir / "outputs" / "报告.txt").resolve())

    def test_dry_run_writes_nothing_but_checks_the_same(self):
        f = self.dir / "新目录" / "b.txt"
        self.assertEqual(dry_run({**DEFAULTS, "text": "abc", "path": str(f)}), {"path": str(f.resolve()), "chars": 3})
        self.assertFalse(f.parent.exists())
        self.write(self.dir / "c.txt")
        self.assertEqual(self.code(dry_run, path=str(self.dir / "c.txt")), "exists")

    def test_error_mode_never_overwrites_a_file_created_after_the_check(self):
        f = self.dir / "race.txt"
        real = module._target

        def late(inputs):  # 检查通过之后、写入之前，别的运行抢先建好了文件
            p = real(inputs)
            p.write_text("别人的", encoding="utf-8")
            return p
        module._target = late
        try:
            self.assertEqual(self.code(run, path=str(f)), "exists")
        finally:
            module._target = real
        self.assertEqual(f.read_text(encoding="utf-8"), "别人的")

    def test_failed_overwrite_keeps_old_content(self):
        f = self.dir / "keep.txt"
        self.write(f, "旧内容")
        lone_surrogate = chr(0xD800)  # JSON 里合法、UTF-8 编码不了的字符
        self.assertEqual(self.code(run, path=str(f), text=lone_surrogate, if_exists="overwrite"), "write_failed")
        self.assertEqual(self.code(dry_run, path=str(f), text=lone_surrogate, if_exists="overwrite"), "write_failed")
        self.assertEqual(f.read_text(encoding="utf-8"), "旧内容")
        self.assertEqual(sorted(x.name for x in self.dir.iterdir()), ["keep.txt"])

    def test_overwrite_long_file_name(self):
        f = self.dir / ("a" * 200 + ".txt")  # 临时文件名不能比目标长，否则超出文件名长度限制
        self.write(f, "一")
        self.write(f, "二", if_exists="overwrite")
        self.assertEqual(f.read_text(encoding="utf-8"), "二")

    def test_invalid_paths_fail_in_dry_run_too(self):
        (self.dir / "a.txt").write_text("x", encoding="utf-8")
        for bad in (self.dir / "report?.txt", self.dir / "a.txt" / "b.txt", self.dir / "x<y" / "c.txt"):
            self.assertEqual(self.code(dry_run, path=str(bad)), "write_failed", bad)
            self.assertEqual(self.code(run, path=str(bad)), "write_failed", bad)

    def test_errors(self):
        self.assertEqual(self.code(run, path=str(self.dir / "a.exe")), "unsupported_type")
        self.assertEqual(self.code(run, path=str(self.dir / "a.txt"), if_exists="replace"), "invalid_option")
        (self.dir / "d.txt").mkdir()
        self.assertEqual(self.code(run, path=str(self.dir / "d.txt"), if_exists="overwrite"), "write_failed")


if __name__ == "__main__":
    unittest.main()
