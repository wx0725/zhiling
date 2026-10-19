"""后端只读审核报告（core / tools / server / modules）所列问题的回归测试。"""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from core import docsafe
from core.errors import ModuleError
from core.paths import is_device_name, ref_within, safe_path
from core.registry import ROOT
from tools import llm, setup_ai


class SafePath(unittest.TestCase):
    def test_rejects_nul(self):
        with self.assertRaises(ModuleError) as c:
            safe_path("a\x00b.txt")
        self.assertEqual(c.exception.code, "bad_path")

    def test_rejects_windows_device_names(self):
        for name in ("CON", "nul", "COM1.txt", r"子\PRN.csv", "AUX", "CONIN$", "COM¹.csv", "LPT³"):
            with self.assertRaises(ModuleError, msg=name):
                safe_path(name)

    def test_allows_normal_names(self):
        self.assertEqual(str(safe_path(' "a/b/笔记.txt" ')), str(Path("a/b/笔记.txt")))
        self.assertFalse(is_device_name("console.txt"))  # 只拦完全是设备名的段
        self.assertTrue(is_device_name("CON"))


class RefWithin(unittest.TestCase):
    def test_inside_root_ok(self):
        self.assertTrue(ref_within((ROOT / "tests" / "x.workflow.json").resolve(), ROOT / "tests"))

    def test_outside_both_rejected(self):
        outside = (ROOT.parent / "x.workflow.json").resolve()
        self.assertFalse(ref_within(outside, ROOT / "tests"))

    def test_inside_base_but_outside_root(self):
        # base 自身在 ROOT 外时，base 内的文件也算允许
        base = ROOT.parent
        self.assertTrue(ref_within((base / "a.workflow.json").resolve(), base))


class Docsafe(unittest.TestCase):
    def _zip(self, sizes):
        z = mock.Mock()
        infos = []
        for i, s in enumerate(sizes):
            zi = zipfile.ZipInfo(f"m{i}")
            zi.file_size = s
            infos.append(zi)
        z.infolist.return_value = infos
        return z

    def test_doctype_rejected(self):
        for bad in (b'<?xml?><!DOCTYPE x>', b'<!ENTITY a "x">', b'  <!doctype html>'):
            with self.assertRaises(ModuleError):
                docsafe.check_no_doctype(bad)

    def test_doctype_utf16_rejected(self):
        xml = '<!DOCTYPE x [<!ENTITY a "x">]><r/>'
        for enc in ("utf-16-le", "utf-16-be"):
            with self.assertRaises(ModuleError, msg=enc):
                docsafe.check_no_doctype(xml.encode(enc))

    def test_normal_xml_ok(self):
        docsafe.check_no_doctype(b'<?xml version="1.0"?><root><a/></root>')  # 不抛

    def test_oversized_member_rejected(self):
        with self.assertRaises(ModuleError):
            docsafe.check_zip(self._zip([docsafe.MEMBER_LIMIT + 1]))

    def test_total_over_limit_rejected(self):
        chunk = docsafe.MEMBER_LIMIT // 2
        n = docsafe.TOTAL_LIMIT // chunk + 2
        with self.assertRaises(ModuleError):
            docsafe.check_zip(self._zip([chunk] * n))

    def test_normal_zip_ok(self):
        docsafe.check_zip(self._zip([1024, 2048]))  # 不抛


class EngineRefGuard(unittest.TestCase):
    def test_subflow_body_rejects_outside_ref(self):
        from core.engine import Engine, _Stop
        from core.registry import load_registry
        eng = Engine(load_registry())
        node = {"id": "s", "kind": "subflow", "ref": "../../secret.workflow.json"}
        with self.assertRaises(_Stop) as c:
            eng._subflow_body(node, ROOT / "tests")
        self.assertEqual(c.exception.code, "ref_blocked")


class SetupAiSettings(unittest.TestCase):
    def test_set_and_finish_go_through_locked_atomic_write(self):
        with tempfile.TemporaryDirectory() as d:
            settings = Path(d) / "model_settings.json"
            with mock.patch.object(llm, "SETTINGS", settings):
                setup_ai._set("think_model", "qwen3:8b")
                setup_ai.finish()
                s = llm.load_settings()
            self.assertEqual(s.get("think_model"), "qwen3:8b")
            self.assertTrue(s.get("onboarded"))
            # 整文件原子替换后留下的是完整 JSON
            self.assertEqual(json.loads(settings.read_text(encoding="utf-8")), s)


if __name__ == "__main__":
    unittest.main()
