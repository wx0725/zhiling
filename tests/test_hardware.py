"""显卡检测（tools/hardware.py）：种类判断、和 DXCore 对号、显存值解析、驱动版本换算、nvidia-smi 解析、排序与出错时不抛异常。
不读真实硬件。"""
import unittest
from unittest import mock

from tools import hardware as hw

GIB = 1 << 30


class ClassifyTest(unittest.TestCase):
    def test_reported_by_system(self):
        cases = [
            # 厂商, PCI, 软件显卡, 外接, 集成, 专用显存, 根总线 → 种类
            (("NVIDIA", True, False, True, False, 24 * GIB, False), "external"),    # 雷电显卡坞
            (("AMD", True, False, True, False, 16 * GIB, False), "external"),       # 外接 AMD 显卡
            (("NVIDIA", True, False, False, False, 8 * GIB, False), "discrete"),    # 笔记本内置独显
            (("Intel", True, False, False, True, 128 << 20, True), "integrated"),   # Intel 集显
            (("AMD", True, False, False, True, 4 * GIB, False), "integrated"),      # 划拨 4 GB、不在根总线的 AMD 核显
            (("AMD", True, False, False, False, None, False), "discrete"),          # 读不到显存的 AMD 独显
            (("Intel", True, False, False, False, 16 * GIB, False), "discrete"),    # Intel Arc 独显
        ]
        for args, kind in cases:
            with self.subTest(args=args):
                self.assertEqual(hw.classify(*args), kind)

    def test_inferred_when_not_reported(self):
        cases = [
            (("Intel", True, False, None, None, None, True), "integrated"),         # 根总线上的 Intel
            (("NVIDIA", True, False, None, None, None, False), "discrete"),         # 只做独显的厂商
            (("AMD", True, False, None, None, 8 * GIB, False), "discrete"),         # 显存大
            (("AMD", True, False, None, None, None, False), "unknown"),             # 证据不够，不硬猜
            (("AMD", True, False, None, None, 512 << 20, False), "unknown"),
            (("Microsoft", True, False, None, None, None, False), "virtual"),       # 基本显示适配器
            (("Microsoft", False, True, None, None, None, False), "virtual"),       # 远程桌面、虚拟显示器（ROOT\、SWD\）
            (("", False, False, None, None, None, False), "unknown"),               # 树莓派等非 PCI 的真实显卡
            (("Qualcomm", False, False, None, True, None, False), "integrated"),     # 非 PCI，但系统报告是集成的
        ]
        for args, kind in cases:
            with self.subTest(args=args):
                self.assertEqual(hw.classify(*args), kind)


class MatchTest(unittest.TestCase):
    def test_hardware_key(self):
        self.assertEqual(hw.hardware_key(r"PCI\VEN_10DE&DEV_2204&SUBSYS_87B31043&REV_A1"),
                         (0x10DE, 0x2204, 0x87B31043, 0xA1))
        self.assertEqual(hw.hardware_key(r"pci\ven_8086&dev_7d67&subsys_801217aa&rev_06"),
                         (0x8086, 0x7D67, 0x801217AA, 0x06))
        self.assertIsNone(hw.hardware_key(r"PCI\VEN_10DE&DEV_2204"))
        self.assertIsNone(hw.hardware_key(r"ROOT\BasicDisplay"))

    def test_match_dxcore(self):
        egpu = {"key": (0x10DE, 0x2204, 0x87B31043, 0xA1), "integrated": False, "detachable": True, "dedicated": 1}
        igpu = {"key": (0x8086, 0x7D67, 0x801217AA, 0x06), "integrated": True, "detachable": False, "dedicated": 1}
        self.assertIs(hw.match_dxcore(egpu["key"], [igpu, egpu]), egpu)
        self.assertIsNone(hw.match_dxcore((1, 2, 3, 4), [igpu, egpu]))
        self.assertIsNone(hw.match_dxcore(None, [igpu, egpu]))
        twin_same = dict(egpu)
        self.assertIs(hw.match_dxcore(egpu["key"], [egpu, twin_same]), egpu)       # 两张同型号、报告一致
        twin_inside = dict(egpu, detachable=False)
        self.assertIsNone(hw.match_dxcore(egpu["key"], [egpu, twin_inside]))       # 一张内置一张外接：分不清


class ValueTest(unittest.TestCase):
    def test_vram_value(self):
        self.assertEqual(hw._vram_value(25769803776), 25769803776)
        self.assertEqual(hw._vram_value((8 * GIB).to_bytes(8, "little")), 8 * GIB)
        self.assertEqual(hw._vram_value((2 * GIB).to_bytes(4, "little")), 2 * GIB)
        for bad in (None, 0, -1, b"\x01\x02", "8GB"):
            self.assertIsNone(hw._vram_value(bad))

    def test_nvidia_version(self):
        self.assertEqual(hw.nvidia_version("32.0.16.1692"), "616.92")
        self.assertEqual(hw.nvidia_version("31.0.15.5222"), "552.22")
        self.assertEqual(hw.nvidia_version(""), "")
        self.assertEqual(hw.nvidia_version("abc"), "abc")

    def test_vendor_of(self):
        self.assertEqual(hw._vendor_of(r"PCI\VEN_10DE&DEV_2204&SUBSYS_87B31043"), ("10de", "NVIDIA"))
        self.assertEqual(hw._vendor_of(r"pci\ven_8086&dev_7d67"), ("8086", "Intel"))
        self.assertEqual(hw._vendor_of(r"ROOT\BasicDisplay"), ("", ""))

    def test_pci_address(self):
        self.assertEqual(hw.pci_address("00000000:05:00.0"), "0000:05:00.0")
        self.assertEqual(hw.pci_address("0001:0A:1F.3"), "0001:0a:1f.3")
        self.assertEqual(hw.pci_address("05:00.0"), "")

    def test_nvidia_smi_keeps_full_address(self):
        out = "00000000:05:00.0, 8192\n00000001:05:00.0, 24576\nbroken line\n"
        with mock.patch.object(hw.shutil, "which", return_value="nvidia-smi"), \
                mock.patch.object(hw, "_run", return_value=out):
            self.assertEqual(hw._nvidia_smi(), {"0000:05:00.0": 8192 << 20, "0001:05:00.0": 24576 << 20})
        with mock.patch.object(hw.shutil, "which", return_value=None):
            self.assertEqual(hw._nvidia_smi(), {})


class GpusTest(unittest.TestCase):
    def test_order_external_first_then_by_vram(self):
        found = [
            {"name": "集显", "kind": "integrated", "vram": None},
            {"name": "笔记本独显", "kind": "discrete", "vram": 8 * GIB},
            {"name": "虚拟", "kind": "virtual", "vram": None},
            {"name": "显卡坞", "kind": "external", "vram": 24 * GIB},
            {"name": "说不清", "kind": "unknown", "vram": None},
            {"name": "大独显", "kind": "discrete", "vram": 16 * GIB},
        ]
        with mock.patch.object(hw.platform, "system", return_value="Windows"), \
                mock.patch.object(hw, "_windows", return_value=found):
            self.assertEqual([g["name"] for g in hw.gpus()], ["显卡坞", "大独显", "笔记本独显", "说不清", "集显", "虚拟"])

    def test_failure_returns_empty(self):
        with mock.patch.object(hw.platform, "system", return_value="Windows"), \
                mock.patch.object(hw, "_windows", side_effect=OSError("boom")):
            self.assertEqual(hw.gpus(), [])
        with mock.patch.object(hw.platform, "system", return_value="Plan9"):
            self.assertEqual(hw.gpus(), [])


if __name__ == "__main__":
    unittest.main()
