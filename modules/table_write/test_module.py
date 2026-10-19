import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from core.errors import ModuleError
from modules.table_write import module
from modules.table_write.module import dry_run, run

SPEC = json.loads((Path(__file__).parent / "module.json").read_text(encoding="utf-8"))
DEFAULTS = {"if_exists": "error"}


def local(v, base: Path):
    if isinstance(v, str) and v.startswith("D:\\"):
        return str(base.joinpath(*v[3:].split("\\")))
    if isinstance(v, dict):
        return {k: local(x, base) for k, x in v.items()}
    return v


class WriteTable(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name).resolve()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rows, name="a.csv", fn=run, **kw):
        return fn({**DEFAULTS, "rows": rows, "path": str(self.dir / name), **kw})

    def code(self, rows, name="a.csv", fn=run, **kw):
        with self.assertRaises(ModuleError) as c:
            self.write(rows, name, fn, **kw)
        return c.exception.code

    def test_examples_in_order(self):
        """第一个示例新建文件（演练先不动磁盘），第二个示例追加到它后面。"""
        f = self.dir / "资料" / "成绩汇总.csv"
        for ex in SPEC["examples"]:
            args, want = {**DEFAULTS, **local(ex["input"], self.dir)}, local(ex["output"], self.dir)
            existed = f.exists()
            self.assertEqual(dry_run(args), want, ex["title"])
            self.assertEqual(f.exists(), existed)
            self.assertEqual(run(args), want, ex["title"])
        self.assertEqual(f.read_bytes().decode("utf-8"),
                         "\ufeff名字,分数,备注\r\n小王,90,\r\n小李,85,补考\r\n小张,77,\r\n")

    def test_values_and_quoting(self):
        self.write([{"a": True, "b": None, "c": [1, 2], "d": "有,逗号\n换行"}])
        text = (self.dir / "a.csv").read_bytes().decode("utf-8-sig")
        self.assertEqual(text, 'a,b,c,d\r\n是,,"[1, 2]","有,逗号\n换行"\r\n')

    def test_overwrite_and_empty_rows(self):
        self.write([{"x": 1}])
        self.assertEqual(self.write([], if_exists="overwrite"), {"path": str(self.dir / "a.csv"), "count": 0})
        self.assertEqual((self.dir / "a.csv").read_bytes(), b"\xef\xbb\xbf")
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ["a.csv"])

    def test_append_to_gbk_file_keeps_its_header_order_and_encoding(self):
        (self.dir / "a.csv").write_bytes("分数,名字\r\n1,甲\r\n".encode("gbk"))
        self.write([{"名字": "乙"}], if_exists="append")
        self.assertEqual((self.dir / "a.csv").read_bytes().decode("gbk"), "分数,名字\r\n1,甲\r\n,乙\r\n")

    def test_append_to_missing_file_creates_it(self):
        self.write([{"x": 1}], if_exists="append")
        self.assertEqual((self.dir / "a.csv").read_bytes(), "\ufeffx\r\n1\r\n".encode("utf-8"))

    def test_relative_path_goes_to_outputs(self):
        with mock.patch.object(module, "OUT_DIR", self.dir / "outputs"):
            out = run({**DEFAULTS, "rows": [{"x": 1}], "path": "汇总.csv"})
        self.assertEqual(out["path"], str(self.dir / "outputs" / "汇总.csv"))

    def test_errors(self):
        self.write([{"x": 1}])
        for fn in (run, dry_run):
            self.assertEqual(self.code([{"x": 1}], fn=fn), "exists")
            self.assertEqual(self.code([{"x": 1}], fn=fn, if_exists="skip"), "invalid_option")
            self.assertEqual(self.code([{"x": 1}], "a.xlsx", fn=fn), "unsupported_type")
            self.assertEqual(self.code([{"x": 1}, "y"], "b.csv", fn=fn), "not_rows")
            self.assertEqual(self.code([{"新列": 1}], fn=fn, if_exists="append"), "header_mismatch")
            self.assertEqual(self.code([{"x": 1}], "a?.csv", fn=fn), "write_failed")
            self.assertEqual(self.code([{"x": 1}], "a.csv/b.csv", fn=fn), "write_failed")
        self.assertFalse((self.dir / "b.csv").exists())


if __name__ == "__main__":
    unittest.main()
