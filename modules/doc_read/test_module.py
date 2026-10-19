import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from core.errors import ModuleError
from modules.doc_read import module
from modules.doc_read.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))
DEFAULTS = {"max_chars": 200000}
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def local(v, base: Path):
    if isinstance(v, str) and v.startswith("D:\\"):
        return str(base.joinpath(*v[3:].split("\\")))
    if isinstance(v, dict):
        return {k: local(x, base) for k, x in v.items()}
    return v


def p(*runs, ppr=""):
    return f"<w:p>{ppr}" + "".join(f"<w:r>{r}</w:r>" for r in runs) + "</w:p>"


def t(s):
    return f'<w:t xml:space="preserve">{s}</w:t>'


def table(*rows):
    return "<w:tbl><w:tblPr/>" + "".join(
        "<w:tr>" + "".join(f"<w:tc><w:tcPr/>{c}</w:tc>" for c in row) + "</w:tr>" for row in rows) + "</w:tbl>"


def make_docx(path: Path, body: str):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("word/document.xml", f"<w:document {W}><w:body>{body}<w:sectPr/></w:body></w:document>")


NOTICE = (p(t("关于放假"), t("的通知"), ppr='<w:pPr><w:tabs><w:tab w:val="left" w:pos="420"/></w:tabs></w:pPr>')
          + p(t("全体同事："), "<w:tab/>", t("国庆放假七天。"))
          + table([p(t("日期")), p(t("安排"))], [p(t("10月1日")), p(t("放假"))]))


class ReadDoc(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, name, **kw):
        return run({**DEFAULTS, "path": str(self.dir / name), **kw})

    def code(self, name):
        with self.assertRaises(ModuleError) as c:
            self.read(name)
        return c.exception.code

    def test_examples(self):
        (self.dir / "资料").mkdir()
        make_docx(self.dir / "资料" / "通知.docx", NOTICE)
        for ex in SPEC["examples"]:
            self.assertEqual(run({**DEFAULTS, **local(ex["input"], self.dir)}), ex["output"], ex["title"])

    def test_breaks_deleted_text_and_wrappers(self):
        body = (p(t("第一行"), "<w:br/>", t("第二行"), "<w:delText>删掉的</w:delText>")
                + "<w:sdt><w:sdtContent>" + p(t("控件里的字")) + "</w:sdtContent></w:sdt>"
                + p()
                + '<w:p><w:hyperlink><w:r><w:t>链接文字</w:t></w:r></w:hyperlink></w:p>'
                + table([p(t("甲")) + p(t("乙")), ""]))
        make_docx(self.dir / "a.docx", body)
        self.assertEqual(self.read("a.docx")["text"], "第一行\n第二行\n控件里的字\n\n链接文字\n甲 乙\t")

    def test_rejects_doctype(self):
        """带 DOCTYPE/ENTITY 的 document.xml 解析前就拒绝（防实体膨胀）。"""
        with zipfile.ZipFile(self.dir / "d.docx", "w") as z:
            z.writestr("[Content_Types].xml", "<Types/>")
            z.writestr("word/document.xml",
                       f'<!DOCTYPE w:document [<!ENTITY a "x">]><w:document {W}><w:body/></w:document>')
        self.assertEqual(self.code("d.docx"), "bad_file")

    def test_rejects_oversized_member(self):
        """内部文件超过上限（解压炸弹）时拒绝。"""
        make_docx(self.dir / "big.docx", p(t("你好")))
        with mock.patch("core.docsafe.MEMBER_LIMIT", 1):
            self.assertEqual(self.code("big.docx"), "bad_file")

    def test_bad_path(self):
        self.assertEqual(self.code("CON.docx"), "bad_path")

    def test_relative_to_project(self):
        make_docx(self.dir / "a.docx", p(t("你好")))
        with mock.patch.object(module, "ROOT", self.dir):
            self.assertEqual(run({**DEFAULTS, "path": "a.docx"}), {"text": "你好", "name": "a.docx"})

    def test_errors(self):
        self.assertEqual(self.code("没有.docx"), "not_found")
        self.assertEqual(self.code("a.doc"), "unsupported_type")
        self.assertEqual(self.code("a.pdf"), "unsupported_type")
        (self.dir / "bad.docx").write_text("不是压缩包", encoding="utf-8")
        self.assertEqual(self.code("bad.docx"), "bad_file")
        with zipfile.ZipFile(self.dir / "nodoc.docx", "w") as z:
            z.writestr("x.txt", "")
        self.assertEqual(self.code("nodoc.docx"), "bad_file")


if __name__ == "__main__":
    unittest.main()
