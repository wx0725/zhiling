"""读这台电脑的显卡（引导页“检测电脑”用）：只读，不改任何系统设置，不联网。

gpus() 列出系统当前接着的全部显卡，每张一个字典：
- name：显卡名称；vendor：厂商（NVIDIA、AMD、Intel…）
- kind："integrated"（集成在处理器里）、"discrete"（机器内置的独立显卡）、
        "external"（可拔下的外接显卡，例如雷电 / USB4 显卡坞）、"virtual"（远程桌面、虚拟显示器等软件显卡）、
        "unknown"（判断不出来）
- vram：专用显存字节数，读不到为 None；shared：显存是否与内存共用（集成显卡）
- driver：驱动版本；ok：设备是否正常工作；problem：设备管理器里的问题代码（0 为正常）；location：总线位置

Windows：用 SetupAPI 枚举“显示适配器”类中当前存在的设备（拔掉的显卡坞不会列出），
显存读驱动写在注册表里的 HardwareInformation.qwMemorySize（64 位，超过 4 GB 也准确；WMI 的 AdapterRAM 最多只到 4 GB）。
集成还是外接，以系统图形栈 DXCore 报告的 IsIntegrated、IsDetachable 为准（Windows 10 2004 起有）；
另外设备不在“本机”容器里（Windows 把可拔下的设备单独归一个容器）也判为外接。
DXCore 不可用或对不上号时，退回按厂商、总线、可热插拔推断，推断不出就是 "unknown"。
Linux：读 /sys/class/drm；macOS：读 system_profiler。两者尽力而为。"""
import json
import os
import platform
import re
import shutil
import subprocess

NO_WINDOW = 0x0800_0000  # CREATE_NO_WINDOW
GIB = 1 << 30
VENDORS = {"10de": "NVIDIA", "1002": "AMD", "1022": "AMD", "8086": "Intel", "5143": "Qualcomm", "1414": "Microsoft",
           "106b": "Apple", "1a03": "ASPEED", "15ad": "VMware", "1ab8": "Parallels", "80ee": "VirtualBox"}
IGPU_VENDORS = {"Intel", "AMD", "Qualcomm", "Apple"}  # 这些厂商的显卡可能是集成的
VIRTUAL_VENDORS = {"Microsoft", "VMware", "Parallels", "VirtualBox"}
SOFTWARE_DRIVERS = {"vkms", "vgem", "virtio_gpu", "virtio-pci", "qxl", "bochs-drm", "vboxvideo", "vmwgfx",
                    "hyperv_drm", "cirrus", "udl", "evdi"}  # Linux 上的软件 / 虚拟机显卡驱动


def gpus() -> list[dict]:
    """当前接着的全部显卡；读取失败返回空列表（不抛异常）"""
    try:
        system = platform.system()
        if system == "Windows":
            found = _windows()
        elif system == "Linux":
            found = _linux()
        elif system == "Darwin":
            found = _mac()
        else:
            found = []
    except Exception:  # noqa: BLE001  检测只是参考，读不到就当没有，不能让引导页崩溃
        return []
    order = {"external": 0, "discrete": 1, "unknown": 2, "integrated": 3, "virtual": 4}
    return sorted(found, key=lambda g: (order.get(g["kind"], 9), -(g["vram"] or 0)))


def classify(vendor: str, pci: bool, software: bool, external, integrated, vram, root_bus: bool) -> str:
    """判断显卡种类。software 表示明确是软件显卡（远程桌面、虚拟显示器等）。
    external、integrated 是系统明确报告的结果（True / False），没有报告时为 None，
    这时才按厂商、专用显存、是否在根总线上推断；推断不出就是 "unknown"，不硬猜。
    不是 PCI 设备不等于软件显卡（例如手机芯片、树莓派里的显卡），这种情况推断不了就是 "unknown"。"""
    if software or vendor in VIRTUAL_VENDORS:
        return "virtual"
    if external:
        return "external"
    if integrated is not None:
        return "integrated" if integrated else "discrete"
    if not pci:
        return "unknown"
    if vendor in IGPU_VENDORS and root_bus:
        return "integrated"
    if vendor and vendor not in IGPU_VENDORS:
        return "discrete"  # NVIDIA 等只做独立显卡的厂商
    if vram is not None and vram >= 2 * GIB:
        return "discrete"
    return "unknown"


def _vendor_of(hwid: str) -> tuple[str, str]:
    m = re.search(r"VEN_([0-9A-F]{4})", hwid or "", re.I)
    code = m.group(1).lower() if m else ""
    return code, VENDORS.get(code, "")


def nvidia_version(win: str) -> str:
    """Windows 驱动版本号 → NVIDIA 常用的写法：32.0.16.1692 → 616.92（取最后五位数字）"""
    digits = "".join(win.split(".")[-2:]) if win.count(".") == 3 else ""
    return f"{int(digits[-5:-2])}.{digits[-2:]}" if len(digits) >= 5 and digits.isdigit() else win


def _run(args: list[str], timeout: float = 8) -> str:
    try:
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=NO_WINDOW if os.name == "nt" else 0)
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def pci_address(text: str) -> str:
    """统一 PCI 地址写法：00000000:05:00.0 或 0000:05:00.0 → 0000:05:00.0（小写）"""
    m = re.fullmatch(r"([0-9A-Fa-f]+):([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2})\.([0-7])", text.strip())
    return f"{int(m.group(1), 16):04x}:{m.group(2).lower()}:{m.group(3).lower()}.{m.group(4)}" if m else ""


def _nvidia_smi() -> dict[str, int]:
    """nvidia-smi 报告的显存：完整 PCI 地址（域:总线:设备.功能）→ 字节数"""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {}
    out = {}
    for line in _run([exe, "--query-gpu=pci.bus_id,memory.total", "--format=csv,noheader,nounits"]).splitlines():
        parts = [p.strip() for p in line.split(",")]
        addr = pci_address(parts[0]) if len(parts) == 2 else ""
        if addr and parts[1].isdigit():
            out[addr] = int(parts[1]) * (1 << 20)
    return out


def hardware_key(hwid: str):
    """PCI 硬件 ID → (厂商, 设备, 子系统, 修订号)，用来和 DXCore 报告的显卡对号；解析不了为 None"""
    m = re.search(r"VEN_([0-9A-F]{4})&DEV_([0-9A-F]{4})&SUBSYS_([0-9A-F]{8})&REV_([0-9A-F]{2})", hwid or "", re.I)
    return tuple(int(x, 16) for x in m.groups()) if m else None


def match_dxcore(key, adapters: list[dict]):
    """在 DXCore 报告的显卡里找硬件 ID 相同的；找不到，或几张同型号显卡报告不一致时返回 None"""
    same = [a for a in adapters if a["key"] == key] if key else []
    if not same or any((a["integrated"], a["detachable"]) != (same[0]["integrated"], same[0]["detachable"])
                       for a in same):
        return None
    return same[0]


# ---------- Windows ----------
def _windows() -> list[dict]:
    import ctypes
    import winreg
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    class SP_DEVINFO_DATA(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("ClassGuid", GUID), ("DevInst", wintypes.DWORD),
                    ("Reserved", ctypes.c_void_p)]

    display = GUID(0x4D36E968, 0xE325, 0x11CE, (ctypes.c_ubyte * 8)(0xBF, 0xC1, 0x08, 0x00, 0x2B, 0xE1, 0x03, 0x18))
    DIGCF_PRESENT = 0x2
    SPDRP_DEVICEDESC, SPDRP_HARDWAREID, SPDRP_DRIVER, SPDRP_CAPABILITIES = 0x0, 0x1, 0x9, 0xF
    SPDRP_LOCATION_INFORMATION, SPDRP_FRIENDLYNAME, SPDRP_BUSNUMBER, SPDRP_REMOVAL_POLICY = 0xD, 0xC, 0x15, 0x1F
    CM_DEVCAP_REMOVABLE = 0x4
    REMOVAL_EXPECT = (2, 3)  # CM_REMOVAL_POLICY_EXPECT_ORDERLY_REMOVAL、_SURPRISE_REMOVAL：可热插拔
    CR_SUCCESS, CR_NO_SUCH_DEVNODE = 0, 0xD
    INVALID = ctypes.c_void_p(-1).value

    class DEVPROPKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.ULONG)]

    # DEVPKEY_Device_InLocalMachineContainer：设备是否属于“本机”这个容器（外接设备单独成一个容器）
    in_local = DEVPROPKEY(GUID(0x8C7ED206, 0x3F8A, 0x4827,
                               (ctypes.c_ubyte * 8)(0xB3, 0xAB, 0xAE, 0x9E, 0x1F, 0xAE, 0xFC, 0x6C)), 4)

    setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
    cfgmgr = ctypes.WinDLL("cfgmgr32")
    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    setupapi.SetupDiEnumDeviceInfo.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(SP_DEVINFO_DATA)]
    setupapi.SetupDiGetDeviceRegistryPropertyW.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    setupapi.SetupDiGetDeviceInstanceIdW.argtypes = [ctypes.c_void_p, ctypes.POINTER(SP_DEVINFO_DATA), wintypes.LPWSTR,
                                                    wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]
    cfgmgr.CM_Get_DevNode_Status.argtypes = [ctypes.POINTER(wintypes.ULONG), ctypes.POINTER(wintypes.ULONG),
                                             wintypes.DWORD, wintypes.ULONG]
    cfgmgr.CM_Get_DevNode_Status.restype = wintypes.ULONG
    cfgmgr.CM_Get_DevNode_PropertyW.argtypes = [wintypes.DWORD, ctypes.POINTER(DEVPROPKEY), ctypes.POINTER(wintypes.ULONG),
                                                ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.ULONG]
    cfgmgr.CM_Get_DevNode_PropertyW.restype = wintypes.ULONG

    def local_container(devinst):
        """设备在“本机”容器里返回 True，单独成容器（外接）返回 False，读不到返回 None"""
        kind, val, size = wintypes.ULONG(), ctypes.c_ubyte(), wintypes.ULONG(1)
        if cfgmgr.CM_Get_DevNode_PropertyW(devinst, ctypes.byref(in_local), ctypes.byref(kind), ctypes.byref(val),
                                           ctypes.byref(size), 0) != CR_SUCCESS or kind.value != 0x11:  # BOOLEAN
            return None
        return val.value != 0

    adapters = _dxcore()

    def prop(h, data, key):
        kind, need = wintypes.DWORD(), wintypes.DWORD()
        setupapi.SetupDiGetDeviceRegistryPropertyW(h, ctypes.byref(data), key, ctypes.byref(kind), None, 0,
                                                   ctypes.byref(need))
        if not need.value:
            return None
        buf = ctypes.create_string_buffer(need.value)
        if not setupapi.SetupDiGetDeviceRegistryPropertyW(h, ctypes.byref(data), key, ctypes.byref(kind), buf,
                                                          need.value, None):
            return None
        if kind.value == 4:  # REG_DWORD
            return int.from_bytes(buf.raw[:4], "little")
        text = buf.raw.decode("utf-16-le", errors="replace")
        return text.split("\0")[0] if kind.value != 7 else [s for s in text.split("\0") if s]  # 7 = REG_MULTI_SZ

    h = setupapi.SetupDiGetClassDevsW(ctypes.byref(display), None, None, DIGCF_PRESENT)
    if not h or h == INVALID:
        return []
    out = []
    try:
        i = 0
        while True:
            data = SP_DEVINFO_DATA()
            data.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
            if not setupapi.SetupDiEnumDeviceInfo(h, i, ctypes.byref(data)):
                break
            i += 1
            idbuf = ctypes.create_unicode_buffer(512)
            setupapi.SetupDiGetDeviceInstanceIdW(h, ctypes.byref(data), idbuf, 512, None)
            inst = idbuf.value
            hwids = prop(h, data, SPDRP_HARDWAREID) or [inst]
            _, vendor = _vendor_of(hwids[0])
            status, problem = wintypes.ULONG(), wintypes.ULONG()
            cr = cfgmgr.CM_Get_DevNode_Status(ctypes.byref(status), ctypes.byref(problem), data.DevInst, 0)
            if cr == CR_NO_SUCH_DEVNODE:
                continue  # 列出之后刚被拔掉
            ok = cr == CR_SUCCESS
            caps = prop(h, data, SPDRP_CAPABILITIES) or 0
            policy = prop(h, data, SPDRP_REMOVAL_POLICY) or 0
            bus = prop(h, data, SPDRP_BUSNUMBER)

            name, driver, vram = None, "", None
            key = prop(h, data, SPDRP_DRIVER)  # 例如 {4d36e968-...}\0001
            if key:
                try:
                    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                        rf"SYSTEM\CurrentControlSet\Control\Class\{key}") as k:
                        name = _reg(winreg, k, "DriverDesc")
                        driver = _reg(winreg, k, "DriverVersion") or ""
                        vram = _vram_value(_reg(winreg, k, "HardwareInformation.qwMemorySize"))
                        legacy = _vram_value(_reg(winreg, k, "HardwareInformation.MemorySize"))
                except OSError:
                    legacy = None
            else:
                legacy = None
            dx = match_dxcore(hardware_key(hwids[0]), adapters)
            # 显存优先级：64 位的 qwMemorySize → DXCore 报告的专用显存 → 旧的 32 位 MemorySize（单位字节，最多约 4 GB）
            if vram is None:
                vram = (dx["dedicated"] if dx and dx["dedicated"] else None) or legacy
            if vendor == "NVIDIA":
                driver = nvidia_version(driver)
            local = local_container(data.DevInst)
            if dx:  # 系统明确报告了：可拔下或不在本机容器 → 外接
                external = dx["detachable"] or local is False
            else:  # 没有报告：不在本机容器，或自身可热插拔
                external = local is False or bool(caps & CM_DEVCAP_REMOVABLE) or policy in REMOVAL_EXPECT
            upper = inst.upper()
            software = upper.startswith(("ROOT\\", "SWD\\"))  # 系统虚拟出来的设备
            kind = classify(vendor, upper.startswith("PCI\\"), software, external,
                            dx["integrated"] if dx else None, vram, bus == 0)
            out.append({
                "name": name or prop(h, data, SPDRP_FRIENDLYNAME) or prop(h, data, SPDRP_DEVICEDESC) or inst,
                "vendor": vendor, "kind": kind, "vram": vram if kind != "integrated" else None,
                "shared": kind == "integrated", "driver": driver,
                "ok": ok and problem.value == 0, "problem": problem.value if ok else -1,
                "location": prop(h, data, SPDRP_LOCATION_INFORMATION) or "",
            })
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(h)
    return out


def _dxcore() -> list[dict]:
    """问 DXCore（系统图形栈）有哪些硬件显卡、各自是否集成、是否可拔下、专用显存多少；不可用时返回空列表"""
    import ctypes
    import uuid
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("b", ctypes.c_ubyte * 16)]

    def guid(text):
        g = GUID()
        ctypes.memmove(g.b, uuid.UUID(text).bytes_le, 16)
        return g

    def method(obj, index, *argtypes):
        vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
        return ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, *argtypes)(vtbl[index])

    def release(obj):
        if obj:
            vtbl = ctypes.cast(obj, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
            ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(vtbl[2])(obj)

    try:
        dll = ctypes.WinDLL("dxcore")
    except OSError:
        return []
    IID_FACTORY, IID_LIST = "78ee5945-c36e-4b13-a669-005dd11c0f06", "526c7776-40e9-459b-b711-f32ad76dfc28"
    IID_ADAPTER, CORE_COMPUTE = "f0db4c7f-fe5a-42a2-bd62-f2a6cf6fc83e", "248e2800-a793-4724-abaa-23a6de1be090"
    IS_HARDWARE, IS_INTEGRATED, IS_DETACHABLE, DEDICATED, HWID_PARTS = 11, 12, 13, 7, 14  # DXCoreAdapterProperty
    factory, lst, out = ctypes.c_void_p(), ctypes.c_void_p(), []
    try:
        if dll.DXCoreCreateAdapterFactory(ctypes.byref(guid(IID_FACTORY)), ctypes.byref(factory)) != 0:
            return []
        P = ctypes.POINTER
        if method(factory, 3, wintypes.UINT, P(GUID), P(GUID), P(ctypes.c_void_p))(
                factory, 1, ctypes.byref(guid(CORE_COMPUTE)), ctypes.byref(guid(IID_LIST)), ctypes.byref(lst)) != 0:
            return []
        count = method(lst, 4)(lst) & 0xFFFFFFFF  # GetAdapterCount 返回 UINT
        for i in range(count):
            a = ctypes.c_void_p()
            if method(lst, 3, wintypes.UINT, P(GUID), P(ctypes.c_void_p))(
                    lst, i, ctypes.byref(guid(IID_ADAPTER)), ctypes.byref(a)) != 0:
                continue
            try:
                get = method(a, 6, ctypes.c_int, ctypes.c_size_t, ctypes.c_void_p)

                def read(prop, ctype):
                    v = ctype()
                    return v if get(a, prop, ctypes.sizeof(v), ctypes.byref(v)) == 0 else None
                hw, parts = read(IS_HARDWARE, ctypes.c_bool), read(HWID_PARTS, ctypes.c_uint32 * 5)
                if hw is None or not hw.value or parts is None:
                    continue
                integ, detach = read(IS_INTEGRATED, ctypes.c_bool), read(IS_DETACHABLE, ctypes.c_bool)
                ded = read(DEDICATED, ctypes.c_uint64)
                vendor, device, subsys, subvendor, rev = list(parts)
                out.append({"key": (vendor, device, (subsys << 16) | subvendor, rev),
                            "integrated": bool(integ.value) if integ is not None else None,
                            "detachable": bool(detach.value) if detach is not None else False,
                            "dedicated": ded.value if ded is not None and ded.value else None})
            finally:
                release(a)
    except (OSError, ValueError):
        return []
    finally:
        release(lst)
        release(factory)
    return out


def _reg(winreg, key, name):
    try:
        return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _vram_value(v):
    """注册表里的显存值：QWORD、DWORD 或 4 / 8 字节的二进制"""
    if isinstance(v, bytes) and len(v) in (4, 8):
        v = int.from_bytes(v, "little")
    return v if isinstance(v, int) and v > 0 else None


# ---------- Linux ----------
def _linux() -> list[dict]:
    base = "/sys/class/drm"
    if not os.path.isdir(base):
        return []
    smi = None
    out = []
    for card in sorted(os.listdir(base)):
        if not re.fullmatch(r"card\d+", card):
            continue
        dev = os.path.realpath(os.path.join(base, card, "device"))
        read = lambda f: _read(os.path.join(dev, f))  # noqa: E731
        code = (read("vendor") or "").lower().removeprefix("0x")
        vendor = VENDORS.get(code, code.upper())
        slot = os.path.basename(dev)
        pci = bool(re.fullmatch(r"[0-9a-f]{4}:[0-9a-f]{2}:[0-9a-f]{2}\.\d", slot))
        vram = int(read("mem_info_vram_total") or 0) or None
        if vram is None and vendor == "NVIDIA" and pci:
            smi = _nvidia_smi() if smi is None else smi
            vram = smi.get(pci_address(slot))
        removable = read("removable") == "removable" or "thunderbolt" in dev.lower()
        name = ""
        if pci and shutil.which("lspci"):
            m = re.search(r'"[^"]*" "[^"]*" "([^"]*)"', _run(["lspci", "-mm", "-s", slot]))
            name = m.group(1) if m else ""
        software = os.path.basename(os.path.realpath(os.path.join(dev, "driver"))) in SOFTWARE_DRIVERS
        kind = classify(vendor, pci, software, removable or None, None, vram, pci and slot.split(":")[1] == "00")
        out.append({"name": name or f"{vendor} 显卡", "vendor": vendor, "kind": kind,
                    "vram": vram if kind != "integrated" else None, "shared": kind == "integrated",
                    "driver": os.path.basename(os.path.realpath(os.path.join(dev, "driver"))) if os.path.exists(
                        os.path.join(dev, "driver")) else "",
                    "ok": os.path.exists(os.path.join(dev, "driver")), "problem": 0, "location": slot})
    return out


def _read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return None


# ---------- macOS ----------
def _mac() -> list[dict]:
    try:
        items = json.loads(_run(["system_profiler", "SPDisplaysDataType", "-json"], timeout=15) or "{}")
    except ValueError:
        return []
    out = []
    for g in items.get("SPDisplaysDataType", []):
        bus = g.get("sppci_bus", "")
        vendor = next((v for v in VENDORS.values() if v.lower() in str(g.get("spdisplays_vendor", "")).lower()), "")
        m = re.match(r"(\d+)\s*(GB|MB)", str(g.get("spdisplays_vram") or g.get("_spdisplays_vram") or ""))
        vram = int(m.group(1)) * (GIB if m.group(2) == "GB" else 1 << 20) if m else None
        kind = ("external" if "thunderbolt" in bus or g.get("spdisplays_egpu") else
                "integrated" if "builtin" in bus or vram is None else "discrete")
        out.append({"name": g.get("sppci_model") or g.get("_name", "显卡"), "vendor": vendor, "kind": kind,
                    "vram": vram if kind != "integrated" else None, "shared": kind == "integrated", "driver": "",
                    "ok": True, "problem": 0, "location": bus})
    return out


if __name__ == "__main__":
    print(json.dumps(gpus(), ensure_ascii=False, indent=2))


# ---------- 处理器、内存、磁盘、系统 ----------
def system(disk_path: str | None = None) -> dict:
    """处理器、内存、磁盘、操作系统。每项读不到就是 None / 空字符串，不抛异常。
    disk_path：看哪个位置所在磁盘的可用空间（模型会装到那里），默认用户主目录"""
    out = {"cpu": "", "threads": os.cpu_count() or 0, "ram": None, "ram_free": None, "disk_path": "",
           "disk_free": None, "os": "", "arch": platform.machine()}
    for key, fn in (("cpu", _cpu_name), ("os", _os_name)):
        try:
            out[key] = fn() or ""
        except Exception:  # noqa: BLE001
            pass
    try:
        out["ram"], out["ram_free"] = _memory()
    except Exception:  # noqa: BLE001
        pass
    path = disk_path or os.path.expanduser("~")
    try:
        while path and not os.path.exists(path):  # 还没建的文件夹：看它所在的磁盘
            parent = os.path.dirname(path)
            path = "" if parent == path else parent
        if path:
            out["disk_path"] = path
            out["disk_free"] = shutil.disk_usage(path).free
    except OSError:
        pass
    return out


def _cpu_name() -> str:
    system = platform.system()
    if system == "Windows":
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            return " ".join(str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).split())
    if system == "Linux":
        for line in (_read("/proc/cpuinfo") or "").splitlines():
            if line.lower().startswith(("model name", "hardware")):
                return line.split(":", 1)[1].strip()
    if system == "Darwin":
        return _run(["sysctl", "-n", "machdep.cpu.brand_string"]).strip()
    return platform.processor()


def _memory() -> tuple:
    """(总内存, 可用内存) 字节数"""
    system = platform.system()
    if system == "Windows":
        import ctypes

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
        m = MEMORYSTATUSEX(dwLength=ctypes.sizeof(MEMORYSTATUSEX))
        if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
            return None, None
        return m.ullTotalPhys, m.ullAvailPhys
    if system == "Linux":
        info = {}
        for line in (_read("/proc/meminfo") or "").splitlines():
            k, _, v = line.partition(":")
            if v.strip().endswith("kB"):
                info[k] = int(v.split()[0]) * 1024
        return info.get("MemTotal"), info.get("MemAvailable")
    if system == "Darwin":
        total = _run(["sysctl", "-n", "hw.memsize"]).strip()
        return (int(total) if total.isdigit() else None), None
    return None, None


EDITIONS = {"Core": "家庭版", "CoreCountrySpecific": "家庭中文版", "CoreSingleLanguage": "家庭单语言版",
            "Professional": "专业版", "ProfessionalWorkstation": "专业工作站版", "Education": "教育版",
            "ProfessionalEducation": "专业教育版", "Enterprise": "企业版", "EnterpriseS": "企业版 LTSC",
            "IoTEnterprise": "IoT 企业版", "ServerStandard": "Server 标准版", "ServerDatacenter": "Server 数据中心版"}


def _os_name() -> str:
    system = platform.system()
    if system == "Windows":
        import sys
        import winreg
        build = sys.getwindowsversion().build
        name = "Windows 11" if build >= 22000 else f"Windows {platform.release()}"
        try:
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
                edition = winreg.QueryValueEx(k, "EditionID")[0]
            name = f"{name} {EDITIONS.get(edition, edition)}"
        except OSError:
            pass
        return f"{name}（版本号 {build}，{'64' if platform.machine().endswith('64') else '32'} 位）"
    if system == "Linux":
        for line in (_read("/etc/os-release") or "").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
        return f"Linux {platform.release()}"
    if system == "Darwin":
        return f"macOS {platform.mac_ver()[0]}"
    return platform.platform()
