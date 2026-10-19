import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from core.errors import ModuleError
from modules.table_read import module
from modules.table_read.module import run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))
DEFAULTS = {"sheet": "", "max_rows": 10000}
NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
RNS = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'


def local(v, base: Path):
    if isinstance(v, str) and v.startswith("D:\\"):
        return str(base.joinpath(*v[3:].split("\\")))
    if isinstance(v, dict):
        return {k: local(x, base) for k, x in v.items()}
    return v


def make_xlsx(path: Path, sheets: dict[str, str], shared: list[str]):
    """用标准库拼一个最小的 xlsx：sheets 是 {工作表名: <sheetData> 里的 XML}。"""
    names = list(sheets)
    wb = f'<workbook {NS} {RNS}><sheets>' + "".join(
        f'<sheet name="{n}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i, n in enumerate(names)) + "</sheets></workbook>"
    rels = '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + "".join(
        f'<Relationship Id="rId{i + 1}" Type="x" Target="worksheets/sheet{i + 1}.xml"/>' for i in range(len(names))) + "</Relationships>"
    sst = f"<sst {NS}>" + "".join(s if s.startswith("<si>") else f"<si><t>{s}</t></si>" for s in shared) + "</sst>"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml", wb)
        z.writestr("xl/_rels/workbook.xml.rels", rels)
        z.writestr("xl/sharedStrings.xml", sst)
        for i, n in enumerate(names):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", f"<worksheet {NS}><sheetData>{sheets[n]}</sheetData></worksheet>")


class ReadTable(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, name, **kw):
        return run({**DEFAULTS, "path": str(self.dir / name), **kw})

    def code(self, name, **kw):
        with self.assertRaises(ModuleError) as c:
            self.read(name, **kw)
        return c.exception

    def test_examples(self):
        (self.dir / "资料").mkdir()
        (self.dir / "资料" / "成绩.csv").write_text("名字,分数,学号\n小王,90,007\n小李,85.5,012\n", encoding="gbk")
        make_xlsx(self.dir / "资料" / "账单.xlsx", {
            "一月": '<row r="1"><c r="A1" t="s"><v>0</v></c></row>',
            "三月": '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="B1" t="s"><v>1</v></c></row>'
                    '<row r="2"><c r="A2" t="s"><v>2</v></c><c r="B2"><v>120.5</v></c></row>'
                    '<row r="3"><c r="A3" t="inlineStr"><is><t>房租</t></is></c><c r="B3"><v>2000</v></c></row>',
        }, ["项目", "金额", "电费"])
        for ex in SPEC["examples"]:
            self.assertEqual(run({**DEFAULTS, **local(ex["input"], self.dir)}), ex["output"], ex["title"])

    def test_csv_utf8_bom_tab_and_quotes(self):
        (self.dir / "a.csv").write_bytes("\ufeff名字\t备注\n小王\t\"有,逗号\"\n".encode("utf-8"))
        self.assertEqual(self.read("a.csv")["rows"], [{"名字": "小王", "备注": "有,逗号"}])
        (self.dir / "b.tsv").write_text("x\ty\n1\t2\n", encoding="utf-8")
        self.assertEqual(self.read("b.tsv")["rows"], [{"x": 1, "y": 2}])

    def test_ragged_rows_blank_and_duplicate_headers(self):
        (self.dir / "a.csv").write_text("名字,,名字\n小王\n\n,,\n小李,1,2,3\n", encoding="utf-8")
        out = self.read("a.csv")
        self.assertEqual(out["headers"], ["名字", "列2", "名字_2", "列4"])
        self.assertEqual(out["rows"], [{"名字": "小王", "列2": "", "名字_2": "", "列4": ""},
                                       {"名字": "小李", "列2": 1, "名字_2": 2, "列4": 3}])
        self.assertEqual(self.read("a.csv", max_rows=1)["count"], 1)

    def test_numbers_that_stay_text(self):
        (self.dir / "a.csv").write_text("v\n007\n13800138000\n1e5\n-3\n12345678901234567\n", encoding="utf-8")
        self.assertEqual([r["v"] for r in self.read("a.csv")["rows"]], ["007", 13800138000, "1e5", -3, "12345678901234567"])

    def test_empty_csv(self):
        (self.dir / "a.csv").write_text("", encoding="utf-8")
        self.assertEqual(self.read("a.csv"), {"rows": [], "headers": [], "count": 0})

    def test_xlsx_cells(self):
        make_xlsx(self.dir / "t.xlsx", {"S": (
            '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row>'
            '<row r="3"><c r="A3" t="b"><v>1</v></c><c r="B3"><v>45000</v></c><c r="C3" t="s"><v>2</v></c>'
            '<c r="D3" s="1"/></row>'
            '<row r="4"><c r="A4" t="str"><v>公式结果</v></c><c r="C4"><v>0.1</v></c></row>')},
            ["名", "值", '<si><r><t>富</t></r><r><t>文本</t></r><rPh><t>fu</t></rPh></si>'])
        out = self.read("t.xlsx")
        self.assertEqual(out["headers"], ["名", "列2", "值"])
        self.assertEqual(out["rows"], [{"名": True, "列2": 45000, "值": "富文本"}, {"名": "公式结果", "列2": "", "值": 0.1}])

    def test_rejects_doctype(self):
        with zipfile.ZipFile(self.dir / "x.xlsx", "w") as z:
            z.writestr("xl/workbook.xml", f'<!DOCTYPE x [<!ENTITY a "x">]><workbook {NS} {RNS}><sheets/></workbook>')
            z.writestr("xl/_rels/workbook.xml.rels", "<Relationships/>")
        self.assertEqual(self.code("x.xlsx").code, "bad_file")

    def test_rejects_oversized_member(self):
        make_xlsx(self.dir / "big.xlsx", {"S1": '<row r="1"><c r="A1" t="str"><v>x</v></c></row>'}, [])
        with mock.patch("core.docsafe.MEMBER_LIMIT", 1):
            self.assertEqual(self.code("big.xlsx").code, "bad_file")

    def test_bad_path(self):
        self.assertEqual(self.code("CON.csv").code, "bad_path")

    def test_relative_to_project(self):
        (self.dir / "a.csv").write_text("x\n1\n", encoding="utf-8")
        with mock.patch.object(module, "ROOT", self.dir):
            self.assertEqual(run({**DEFAULTS, "path": "a.csv"})["count"], 1)

    def test_errors(self):
        self.assertEqual(self.code("没有.csv").code, "not_found")
        self.assertEqual(self.code("a.xls").code, "unsupported_type")
        self.assertEqual(self.code("a.pdf").code, "unsupported_type")
        (self.dir / "gb.csv").write_bytes(b"\xff\xfe\x00\xd8")
        self.assertEqual(self.code("gb.csv").code, "decode_error")
        (self.dir / "bad.xlsx").write_text("不是压缩包", encoding="utf-8")
        self.assertEqual(self.code("bad.xlsx").code, "bad_file")
        with zipfile.ZipFile(self.dir / "empty.xlsx", "w") as z:
            z.writestr("x.txt", "")
        self.assertEqual(self.code("empty.xlsx").code, "bad_file")
        make_xlsx(self.dir / "t.xlsx", {"一月": "", "二月": ""}, [])
        e = self.code("t.xlsx", sheet="三月")
        self.assertEqual(e.code, "no_sheet")
        self.assertIn("一月、二月", e.message)


if __name__ == "__main__":
    unittest.main()
