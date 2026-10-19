import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.errors import ModuleError
from modules.file_list import module
from modules.file_list.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))
DEFAULTS = {"recursive": False, "extensions": "", "keyword": "", "max_items": 1000}
FILES = ["发票/3月电费.pdf", "发票/4月房租.pdf", "发票/说明.txt", "2025/租房合同.docx", "劳动合同.pdf", "合同模板/空.txt"]


def local(v, base: Path):
    """把示例里的 D:\\资料\\… 换成临时文件夹里的路径，示例就能在任何电脑上验证。"""
    if isinstance(v, str) and v.startswith("D:\\资料"):
        return str(base.joinpath(*v[len("D:\\资料"):].split("\\")[1:]))
    if isinstance(v, list):
        return [local(x, base) for x in v]
    if isinstance(v, dict):
        return {k: local(x, base) for k, x in v.items()}
    return v


class ListFiles(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for f in FILES:
            (self.dir / f).parent.mkdir(parents=True, exist_ok=True)
            (self.dir / f).write_text("x", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def ls(self, **kw):
        return run({**DEFAULTS, "folder": str(self.dir), **kw})

    def test_examples(self):
        for ex in SPEC["examples"]:
            self.assertEqual(run({**DEFAULTS, **local(ex["input"], self.dir)}), local(ex["output"], self.dir), ex["title"])

    def test_top_level_only_lists_files(self):
        self.assertEqual(self.ls()["names"], ["劳动合同.pdf"])

    def test_recursive_and_filters(self):
        self.assertEqual(self.ls(recursive=True)["count"], 6)
        self.assertEqual(self.ls(recursive=True, extensions="PDF docx")["count"], 4)
        self.assertEqual(self.ls(recursive=True, extensions="*.txt")["names"], ["说明.txt", "空.txt"])  # 按完整路径排
        self.assertEqual(self.ls(recursive=True, keyword="月 电")["names"], ["3月电费.pdf"])
        self.assertEqual(self.ls(recursive=True, max_items=2)["count"], 2)

    def test_relative_to_project(self):
        with mock.patch.object(module, "ROOT", self.dir):
            out = run({**DEFAULTS, "folder": "发票", "extensions": ".pdf"})
        self.assertEqual(out["paths"][0], str(self.dir / "发票" / "3月电费.pdf"))

    def code(self, folder):
        with self.assertRaises(ModuleError) as c:
            run({**DEFAULTS, "folder": str(folder)})
        return c.exception

    def test_errors(self):
        e = self.code(self.dir / "没有这个")
        self.assertEqual(e.code, "not_found")
        self.assertIn("没有这个", e.message)
        self.assertEqual(self.code(self.dir / "劳动合同.pdf").code, "not_folder")


if __name__ == "__main__":
    unittest.main()
