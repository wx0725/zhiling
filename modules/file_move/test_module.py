import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.errors import ModuleError
from modules.file_move import module
from modules.file_move.module import dry_run, run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))
DEFAULTS = {"to_folder": "", "new_name": "", "make_folder": True, "if_exists": "error"}
FILES = {"下载/电费.pdf": "电", "下载/IMG_0012.jpg": "照", "下载/合同.pdf": "新", "资料/合同.pdf": "旧"}


def local(v, base: Path):
    """把示例里的 D:\\… 换成临时文件夹里的路径，示例就能在任何电脑上验证。"""
    if isinstance(v, str) and v.startswith("D:\\"):
        return str(base.joinpath(*v[3:].split("\\")))
    if isinstance(v, dict):
        return {k: local(x, base) for k, x in v.items()}
    return v


class Move(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name).resolve()
        for f, text in FILES.items():
            (self.dir / f).parent.mkdir(parents=True, exist_ok=True)
            (self.dir / f).write_text(text, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def mv(self, fn=run, **kw):
        kw["source"] = str(self.dir / kw["source"])
        return fn({**DEFAULTS, **kw})

    def code(self, fn=run, **kw):
        with self.assertRaises(ModuleError) as c:
            self.mv(fn, **kw)
        return c.exception.code

    def test_examples_dry_run_then_run(self):
        for ex in SPEC["examples"]:
            args, want = {**DEFAULTS, **local(ex["input"], self.dir)}, local(ex["output"], self.dir)
            self.assertEqual(dry_run(args), want, ex["title"])
            self.assertTrue(Path(args["source"]).exists())          # 演练不动文件
            self.assertEqual(run(args), want, ex["title"])
            self.assertFalse(Path(args["source"]).exists())
            self.assertTrue(Path(want["path"]).is_file())
        self.assertEqual((self.dir / "资料" / "合同.pdf").read_text(encoding="utf-8"), "旧")   # 旧文件没被覆盖
        self.assertEqual((self.dir / "资料" / "合同 (1).pdf").read_text(encoding="utf-8"), "新")

    def test_dry_run_does_not_create_folders(self):
        out = self.mv(dry_run, source="下载/电费.pdf", to_folder=str(self.dir / "新" / "子"))
        self.assertEqual(out["path"], str(self.dir / "新" / "子" / "电费.pdf"))
        self.assertFalse((self.dir / "新").exists())

    def test_relative_folder_is_next_to_source(self):
        out = self.mv(source="下载/电费.pdf", to_folder="已归档")
        self.assertEqual(out["path"], str(self.dir / "下载" / "已归档" / "电费.pdf"))

    def test_relative_source_from_project(self):
        with mock.patch.object(module, "ROOT", self.dir):
            out = run({**DEFAULTS, "source": "下载/电费.pdf", "new_name": "a.txt"})
        self.assertEqual(out, {"path": str(self.dir / "下载" / "a.txt"), "name": "a.txt"})

    def test_overwrite(self):
        out = self.mv(source="下载/合同.pdf", to_folder=str(self.dir / "资料"), if_exists="overwrite")
        self.assertEqual(Path(out["path"]).read_text(encoding="utf-8"), "新")
        self.assertEqual(sorted(os.listdir(self.dir / "资料")), ["合同.pdf"])

    def test_rename_counts_up(self):
        (self.dir / "资料" / "合同 (1).pdf").write_text("x", encoding="utf-8")
        out = self.mv(source="下载/合同.pdf", to_folder=str(self.dir / "资料"), if_exists="rename")
        self.assertEqual(out["name"], "合同 (2).pdf")

    def test_same_place_is_a_no_op(self):
        out = self.mv(source="下载/电费.pdf", to_folder=str(self.dir / "下载"))
        self.assertEqual(out["path"], str(self.dir / "下载" / "电费.pdf"))
        self.assertTrue((self.dir / "下载" / "电费.pdf").exists())

    def test_case_only_rename(self):
        out = self.mv(source="下载/IMG_0012.jpg", new_name="img_0012.JPG")
        self.assertEqual(out["name"], "img_0012.JPG")
        self.assertIn("img_0012.JPG", os.listdir(self.dir / "下载"))

    def test_errors(self):
        self.assertEqual(self.code(source="下载/没有.pdf"), "not_found")
        self.assertEqual(self.code(source="下载"), "not_found")
        for fn in (run, dry_run):
            self.assertEqual(self.code(fn, source="下载/合同.pdf", to_folder=str(self.dir / "资料")), "exists")
            self.assertEqual(self.code(fn, source="下载/电费.pdf", to_folder=str(self.dir / "无"), make_folder=False), "no_folder")
            self.assertEqual(self.code(fn, source="下载/电费.pdf", new_name="a/b.pdf"), "bad_name")
            self.assertEqual(self.code(fn, source="下载/电费.pdf", new_name="a?.pdf"), "bad_name")
            self.assertEqual(self.code(fn, source="下载/电费.pdf", new_name="a."), "bad_name")
            self.assertEqual(self.code(fn, source="下载/电费.pdf", if_exists="skip"), "invalid_option")
            self.assertEqual(self.code(fn, source="下载/电费.pdf", to_folder=str(self.dir / "下载" / "合同.pdf")), "move_failed")
        (self.dir / "资料" / "电费.pdf").mkdir()
        self.assertEqual(self.code(source="下载/电费.pdf", to_folder=str(self.dir / "资料"), if_exists="overwrite"), "move_failed")
        self.assertTrue((self.dir / "下载" / "电费.pdf").exists())  # 出错时原文件还在


if __name__ == "__main__":
    unittest.main()
