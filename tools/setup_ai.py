"""首次启动引导（配置 AI）用到的后端能力。界面在 native/onboarding，接口在 server/setup_api.py。

- 云端：列出可接入的服务商、测试 API Key、检查订阅命令行、保存（Key 进系统凭据管理器，见 tools/keystore.py）
- 本地：问 Ollama、LM Studio 的标准接口列出已装的模型（不扫描整个电脑）；能力测试
- 检测电脑：读硬件（tools/hardware.py），按 config/local_models.json 的模型目录推荐，安装 Ollama 和模型，实际推理测试
- 初始化是否已完成：model_settings.json 的 onboarded
所有函数不改系统设置；只有 install() 会下载并安装（界面上先经过使用者确认）。"""
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

from core.registry import ROOT
from tools import hardware, keystore, llm

CATALOG = ROOT / "config" / "local_models.json"
GIB = 1 << 30


# ---------- 初始化进度 ----------
def _set(key, value) -> None:
    # 走 llm 的带锁 + 原子替换写入，避免与设置面板 / 顶栏保存并发时互相覆盖或写坏 model_settings.json
    llm.update_settings(lambda s: s.__setitem__(key, value))


def done() -> bool:
    return bool(llm.load_settings().get("onboarded"))


def finish() -> None:
    _set("onboarded", True)


# ---------- 云端 ----------
def providers() -> dict:
    apis = [{"id": k, "name": s["name"], "console": s.get("console", ""), "models": s.get("models") or [],
             "has_key": bool(llm.api_key(s))}
            for k, s in llm.api_specs().items() if not s.get("local")]
    clis = [{"id": k, "name": s["name"], "exe": s["exe"], "login": s.get("login", "")} for k, s in llm.cli_specs().items()]
    return {"apis": apis, "clis": clis, "keystore": keystore.available()}


def _request(url, key=None, body=None, timeout=15):
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8") or "{}")


def _failure(e) -> dict:
    """把连接测试的失败归成四类：key（Key 不对）、quota（额度不足）、network（连不上）、unavailable（接口不可用）"""
    if isinstance(e, urllib.error.HTTPError):
        detail = e.read().decode("utf-8", "replace")[:300]
        low = detail.lower()
        if e.code in (401, 403):
            code = "key"
        elif e.code == 402 or any(w in low for w in ("quota", "insufficient", "balance", "credit", "余额", "额度", "欠费")):
            code = "quota"
        else:
            code = "unavailable"
        return {"ok": False, "code": code, "detail": f"{e.code} {detail}".strip()}
    return {"ok": False, "code": "network", "detail": str(getattr(e, "reason", e))}


def test_api(provider: str, key: str) -> dict:
    """用这个 Key 向服务商要一次模型列表（不支持列表的发一句最短的对话），成功时返回能用的模型"""
    spec = llm.api_specs().get(provider)
    if not spec or spec.get("local"):
        return {"ok": False, "code": "unavailable", "detail": "没有这个服务商"}
    key = (key or "").strip() or llm.api_key(spec)  # 没填新的：用已经保存的
    if not key:
        return {"ok": False, "code": "key", "detail": "API Key 是空的"}
    base = spec["base"].rstrip("/")
    defaults = [m for m in spec.get("models") or [] if isinstance(m, str)]
    try:
        if spec.get("list", True):
            try:
                data = _request(f"{base}/models", key)
                ids = [str(m.get("id", "")).removeprefix("models/") for m in data.get("data") or [] if isinstance(m, dict)]
                listed = [i for i in ids if i and not llm._SKIP.search(i)]
                models = [m for m in defaults if m in listed] + [m for m in listed if m not in defaults]
                return {"ok": True, "models": (models or defaults)[:15]}
            except urllib.error.HTTPError as e:
                if e.code != 404:  # 不支持列出模型：改发一句对话
                    raise
        _request(f"{base}/chat/completions", key,
                 {"model": defaults[0] if defaults else "", "messages": [{"role": "user", "content": "hi"}],
                  "max_tokens": 1, "stream": False})
        return {"ok": True, "models": defaults}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return _failure(e)


def check_cli(cid: str) -> dict:
    """订阅命令行有没有装。登录状态没有通用的检查办法，如实返回 None（界面提示怎么登录）"""
    spec = llm.cli_specs().get(cid)
    if not spec:
        return {"found": False, "logged_in": None}
    return {"found": bool(shutil.which(spec["exe"])), "logged_in": None}


def save(model: str, key: str | None = None) -> None:
    """保存选择：带 Key 时先存进系统凭据管理器；然后记下选的模型，并记为已完成初始化"""
    if not llm.valid(model):
        raise ValueError("没有这个模型")
    if key:
        provider, _ = llm._split(model, "api://")
        spec = llm.api_specs().get(provider) if model.startswith("api://") else None
        if not spec:
            raise ValueError("只有 API 接入需要保存 Key")
        keystore.put(spec["key_env"], key)
        llm.forget_listed(provider)
    llm.save_choice(model)
    finish()


# ---------- 本地模型 ----------
def _size_text(n) -> str:
    if not n:
        return ""
    return f"{n / GIB:.1f} GB" if n >= GIB else f"{n / (1 << 20):.0f} MB"


def _ctx_text(n) -> str:
    return f"{n // 1024}K" if isinstance(n, int) and n >= 1024 else (str(n) if n else "")


def _ollama_running(url: str) -> bool:
    try:
        _request(f"{url}/api/tags", timeout=2)
        return True
    except (OSError, ValueError):
        return False


def ensure_ollama(url: str | None = None, wait: float = 15) -> bool:
    """默认的 Ollama 服务开着就返回真；装了但没开就在后台启动它并等一会儿"""
    url = url or llm.default_ollama()
    if _ollama_running(url):
        return True
    exe = llm.ollama_exe()
    if not exe:
        return False
    try:
        subprocess.Popen([exe, "serve"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=llm.NO_WINDOW if os.name == "nt" else 0)
    except OSError:
        return False
    end = time.time() + wait
    while time.time() < end:
        if _ollama_running(url):
            llm._CACHE.pop(("ollama", url), None)
            return True
        time.sleep(0.5)
    return False


def local_models() -> dict:
    runtimes, models = [], []
    servers = llm.ollama_servers()
    for i, s in enumerate(servers):
        url = s["url"]
        up = ensure_ollama(url) if i == 0 else _ollama_running(url)
        host = llm._host(url)
        if not up:
            installed = bool(llm.ollama_exe())
            runtimes.append({"name": "Ollama", "found": False,
                             "detail": f"已安装，但 {host} 上的服务没能启动" if installed else "没有安装"})
            continue
        try:
            tags = _request(f"{url}/api/tags", timeout=4).get("models") or []
        except (OSError, ValueError):
            tags = []
        runtimes.append({"name": "Ollama", "found": True, "detail": host})
        for m in tags:
            name = m.get("name")
            if not name:
                continue
            d = m.get("details") or {}
            ctx = None
            try:
                info = _request(f"{url}/api/show", body={"model": name}, timeout=6).get("model_info") or {}
                ctx = next((v for k, v in info.items() if k.endswith(".context_length")), None)
            except (OSError, ValueError):
                pass
            if "embed" in name.lower() or "bert" in str(d.get("family", "")).lower():
                continue  # 向量模型不会对话
            models.append({"id": f"ollama://{host}/{name}", "name": name, "runtime": "Ollama",
                           "params": d.get("parameter_size", ""), "quant": d.get("quantization_level", ""),
                           "ctx": _ctx_text(ctx), "size": _size_text(m.get("size"))})
    spec = llm.api_specs().get("lmstudio")
    if spec:
        _lmstudio(spec, runtimes, models)
    return {"runtimes": runtimes, "models": models}


def _lmstudio(spec: dict, runtimes: list, models: list) -> None:
    """LM Studio：地址和密钥都按 providers.json / model_settings.json 里的 lmstudio，和实际调用时一样"""
    base = spec["base"].rstrip("/")
    root = base[:-3] if base.endswith("/v1") else base
    key = llm.api_key(spec)
    where = root.split("://", 1)[-1]
    try:
        try:
            data = _request(f"{root}/api/v0/models", key, timeout=2).get("data") or []
        except urllib.error.HTTPError:
            data = _request(f"{base}/models", key, timeout=2).get("data") or []
        runtimes.append({"name": "LM Studio", "found": True, "detail": where})
        for m in data:
            if m.get("type") not in (None, "llm", "vlm") or not m.get("id"):
                continue
            models.append({"id": f"api://lmstudio/{m['id']}", "name": m["id"], "runtime": "LM Studio", "params": "",
                           "quant": m.get("quantization", ""), "ctx": _ctx_text(m.get("max_context_length")), "size": ""})
        llm.forget_listed("lmstudio")
    except (OSError, ValueError):
        runtimes.append({"name": "LM Studio", "found": False, "detail": f"没有在 {where} 找到"})


# ---------- 能力测试 ----------
PROBE_MODULES = {
    "read_file": "读取一个文本文件的内容",
    "write_file": "把文字保存成文件",
    "fetch_url": "下载一个网页的正文",
    "summarize": "用 AI 把一段文字总结成几句话",
    "translate": "用 AI 把文字翻译成另一种语言",
    "filter_rows": "按条件筛选表格里的行",
    "read_table": "读取 Excel 或 CSV 表格",
    "send_notice": "在桌面弹出一条提醒",
}
PROBE_CASES = [  # 需求、必须选的模块（按执行先后）、不该选的模块
    ("给我一个网址，用一句中文总结网页内容", ("fetch_url", "summarize"), {"read_table", "filter_rows"}),
    ("把 notes.txt 翻译成英文后另存为 notes_en.txt", ("read_file", "translate", "write_file"), {"fetch_url"}),
    ("从销售表里找出金额大于一万的订单，存成新文件", ("read_table", "filter_rows", "write_file"), {"translate"}),
    ("读一篇本地文章，总结后弹窗提醒我", ("read_file", "summarize", "send_notice"), {"read_table"}),
    ("下载一个英文网页并翻译成中文保存", ("fetch_url", "translate", "write_file"), {"filter_rows"}),
]


def probe_answer_right(picked: list, must: tuple, never: set) -> bool:
    """选的都是候选模块、不重复、没选不该选的，必须的都选了并且先后顺序对"""
    if len(set(picked)) != len(picked) or not set(picked) <= set(PROBE_MODULES) or never & set(picked):
        return False
    if not set(must) <= set(picked):
        return False
    order = [picked.index(m) for m in must]
    return order == sorted(order)
PROBE_SCHEMA = {"type": "object", "properties": {"modules": {"type": "array", "items": {"type": "string"}}},
                "required": ["modules"]}


def _probe_messages(requirement: str) -> list:
    listing = "\n".join(f"- {k}：{v}" for k, v in PROBE_MODULES.items())
    return [{"role": "system", "content": "你负责为自动化工作流挑选模块。只能从给定的候选模块里选，按执行顺序排列，"
                                          "只输出 JSON：{\"modules\": [\"模块id\", ...]}，不要解释。\n候选模块：\n" + listing},
            {"role": "user", "content": f"需求：{requirement}"}]


def _error_text(e) -> str:
    if isinstance(e, urllib.error.HTTPError):
        try:
            body = json.loads(e.read().decode("utf-8", "replace"))
            return str(body.get("error") or body)[:300]
        except (ValueError, OSError):
            return f"服务返回 {e.code}"
    return str(e)[:300]


def probe(model: str, chat=None) -> dict:
    """能力测试：5 道小题，每题给需求和有限的候选模块，看模型选得对不对、输出格式对不对。
    返回 {"result": "pass" | "unstable" | "fail", "score": "4/5", "detail"}；fail 表示模型没能运行"""
    chat = chat or llm.chat
    right, bad_format = 0, 0
    for i, (req, must, never) in enumerate(PROBE_CASES):
        try:
            text, _ = chat(model, _probe_messages(req), PROBE_SCHEMA)
        except (OSError, ValueError, KeyError) as e:
            if i == 0:
                return {"result": "fail", "score": "", "detail": _error_text(e)}
            bad_format += 1
            continue
        try:
            picked = json.loads(llm.extract_json(text)).get("modules")
            if not isinstance(picked, list) or not all(isinstance(x, str) for x in picked):
                raise ValueError
        except (ValueError, AttributeError):
            bad_format += 1
            continue
        if probe_answer_right(picked, must, never):
            right += 1
    total = len(PROBE_CASES)
    score = f"{right}/{total}"
    if right >= total - 1 and bad_format == 0:
        return {"result": "pass", "score": score, "detail": ""}
    parts = []
    if total - right - bad_format:
        parts.append(f"有 {total - right - bad_format} 题选错了模块")
    if bad_format:
        parts.append(f"有 {bad_format} 题输出格式不对")
    return {"result": "unstable", "score": score, "detail": "，".join(parts)}


def run_test(model: str, chat=None) -> dict:
    """装好后的实际推理测试：问一句最简单的话，能回答就算通过"""
    chat = chat or llm.chat
    try:
        text, secs = chat(model, [{"role": "user", "content": "请只回答两个字：你好"}], None)
    except (OSError, ValueError, KeyError) as e:
        return {"ok": False, "detail": _error_text(e)}
    if not str(text).strip():
        return {"ok": False, "detail": "模型没有给出回答"}
    return {"ok": True, "seconds": round(secs, 1)}


# ---------- 检测电脑与推荐 ----------
def catalog() -> dict:
    try:
        v = json.loads(CATALOG.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        v = {}
    return v if isinstance(v, dict) else {}


def models_dir() -> str:
    """Ollama 把模型放在哪：环境变量 OLLAMA_MODELS，没有就是用户目录下的 .ollama\\models"""
    return os.environ.get("OLLAMA_MODELS") or os.path.join(os.path.expanduser("~"), ".ollama", "models")


def _gb(n) -> str:
    return f"{n / GIB:.0f} GB" if n else "未知"


def recommend(gpus: list, info: dict, models: list, has_ollama: bool) -> dict:
    """按硬件从模型目录里挑三档：资源占用较低、综合平衡、能力优先。每个推荐都写明原因。
    放不下任何模型时 fits 为空，reasons 说明为什么。"""
    usable = [g for g in gpus if g.get("ok", True) and g.get("vram") and g.get("kind") in ("discrete", "external",
                                                                                            "unknown")]
    best = max(usable, key=lambda g: g["vram"], default=None)
    inside = max((g for g in usable if g.get("kind") != "external"), key=lambda g: g["vram"], default=None)
    ram = info.get("ram") or 0
    disk = info.get("disk_free")
    apple = any(g.get("vendor") == "Apple" for g in gpus) and not usable
    budget = ram * 0.65 / GIB if apple else 0  # Apple 芯片可以拿出大约三分之二内存给模型

    def fits_gpu(m, g):
        return g is not None and m["vram"] <= g["vram"] / GIB

    def disk_ok(m):
        return disk is None or (m["download"] + 1) * GIB <= disk

    order = [m for m in models if isinstance(m, dict) and m.get("tag")]
    fits, reasons = [], []
    on_disk = [m for m in order if disk_ok(m)]
    if order and not on_disk:
        reasons.append(f"模型所在磁盘只剩 {_gb(disk)}，最小的推荐模型下载后也需要约 {order[0]['download'] + 1:.0f} GB")

    def entry(tier, m, why):
        fits.append({**m, "tier": tier, "why": why, "download_text": f"{m['download']:g} GB",
                     "disk_text": f"约 {m['download'] * 1.05:.1f} GB", "install_dir": models_dir(),
                     "needs_ollama": not has_ollama})

    if best is not None:
        gpu_ok = [m for m in on_disk if fits_gpu(m, best)]
        if gpu_ok:
            ext_note = "（需要接着这块外接显卡）" if best.get("kind") == "external" else ""
            top = gpu_ok[-1]
            balanced = next((m for m in reversed(gpu_ok) if m["vram"] <= best["vram"] / GIB * 0.75 and m is not top),
                            None)
            low_pool = [m for m in on_disk if fits_gpu(m, inside)] if best.get("kind") == "external" else []
            low = (low_pool or gpu_ok)[0]
            if low_pool:
                where = f"能放进机器自带的 {inside['name']}（{_gb(inside['vram'])} 显存），拔掉外接显卡也能用"
            else:
                where = f"只占 {best['name']} 的一小部分显存{ext_note}，同时开别的软件也不卡"
            entry("资源占用较低", low, f"{where}；适合{low['good_for']}。")
            if balanced is not None and balanced is not low:
                entry("综合平衡", balanced, f"完全放进 {best['name']} 的 {_gb(best['vram'])} 显存并留有余量{ext_note}，"
                                         f"适合{balanced['good_for']}。")
            if top is not low and top is not balanced:
                entry("能力优先", top, f"用满 {best['name']} 的 {_gb(best['vram'])} 显存{ext_note}，适合{top['good_for']}；"
                                    "回答比小模型慢一些。")
    if not fits and apple and budget:
        ok = [m for m in on_disk if m["vram"] <= budget]
        if ok:
            if len(ok) > 1:
                entry("资源占用较低", ok[0], f"只占一小部分内存，适合{ok[0]['good_for']}。")
            entry("能力优先" if len(ok) > 1 else "综合平衡", ok[-1],
                  f"Apple 芯片的显存和内存共用，{_gb(ram)} 内存能放下它，适合{ok[-1]['good_for']}。")
    if not fits and on_disk:
        cpu_ok = [m for m in on_disk if m["ram"] * GIB <= ram]
        if cpu_ok:
            slow = "没有能用来跑模型的独立显卡，只用处理器运行，回答会比较慢"
            entry("资源占用较低", cpu_ok[0], f"{slow}；内存 {_gb(ram)} 够用，适合{cpu_ok[0]['good_for']}。")
            if len(cpu_ok) > 1:
                entry("综合平衡", cpu_ok[1], f"{slow}，比上一档更慢但更稳；适合{cpu_ok[1]['good_for']}。")
        elif order:
            reasons.append(f"内存只有 {_gb(ram)}，最小的推荐模型只用处理器运行也需要约 {order[0]['ram']} GB")
            if best is None:
                reasons.append("没有找到能用来运行模型的独立显卡")
            else:
                reasons.append(f"{best['name']} 的 {_gb(best['vram'])} 显存放不下最小的推荐模型")
    return {"fits": fits, "reasons": reasons}


def scan() -> dict:
    """检测电脑并给出推荐。hw 是给界面看的文字（显卡是列表），fits / reasons 见 recommend()"""
    gpus = hardware.gpus()
    info = hardware.system(models_dir())
    cat = catalog()
    exe = llm.ollama_exe()
    running = _ollama_running(llm.default_ollama())
    drivers = sorted({f"{g['vendor']} 驱动 {g['driver']}" for g in gpus if g.get("driver") and g.get("vendor") in
                      ("NVIDIA", "AMD")})
    runtime = ["Ollama 已安装" + ("并在运行" if running else "，但没有运行") if exe else "未安装 Ollama", *drivers]
    ram = info.get("ram")
    hw = {
        "cpu": f"{info['cpu'] or '未知处理器'}（{info['threads']} 个逻辑处理器）" if info.get("threads") else info.get("cpu"),
        "gpus": gpus,
        "ram": f"{_gb(ram)}（可用 {_gb(info.get('ram_free'))}）" if ram and info.get("ram_free") else _gb(ram),
        "disk": f"{info['disk_path']} 所在磁盘可用 {_gb(info['disk_free'])}" if info.get("disk_free") else "未知",
        "os": info.get("os") or "未知",
        "runtime": "；".join(runtime),
    }
    rec = recommend(gpus, info, cat.get("models") or [], bool(exe))
    return {"hw": hw, **rec, "runtime": cat.get("runtime") or {}}


# ---------- 安装 ----------
class Cancelled(Exception):
    pass


_INSTALLING = threading.Lock()  # 同一时间只装一个：取消后安装程序可能还在跑完，这时再点安装要等它结束


def _download(url, dest, on_progress, should_stop):
    with urllib.request.urlopen(url, timeout=30) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            if should_stop():
                raise Cancelled()
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            if total:
                on_progress(min(99, got * 100 // total))


def _signed_by(path: str, signer: str) -> bool:
    """Windows：安装程序带有效的数字签名，并且证书的名称（CN）和组织（O）都正好是 signer"""
    ps = ("$s = Get-AuthenticodeSignature -LiteralPath $env:WEAVLING_FILE; "
          "if ($s.Status -eq 'Valid') { $c = $s.SignerCertificate; "
          "$c.GetNameInfo('SimpleName', $false); "
          r"($c.Subject -split ',\s*' | Where-Object { $_ -like 'O=*' } | Select-Object -First 1) -replace '^O=', '' }")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True,
                           text=True, timeout=60, env={**os.environ, "WEAVLING_FILE": path},
                           creationflags=llm.NO_WINDOW)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and signed_as(r.stdout, signer)


def signed_as(output: str, signer: str) -> bool:
    """PowerShell 输出的两行（CN、O）都正好等于 signer"""
    lines = [x.strip() for x in output.strip().splitlines()]
    return bool(signer) and len(lines) == 2 and lines[0] == signer and lines[1].strip('"') == signer


def _install_ollama(rt: dict, emit, should_stop) -> None:
    if os.name != "nt":
        raise RuntimeError(f"请先从 {rt.get('download_page', 'https://ollama.com/download')} 安装 Ollama，装好后再试")
    url = rt.get("windows_installer")
    if not url:
        raise RuntimeError("模型目录里没有写 Ollama 安装程序的地址")
    with tempfile.TemporaryDirectory(prefix="weavling-ollama-") as d:
        exe = os.path.join(d, "OllamaSetup.exe")
        _download(url, exe, lambda p: emit({"step": "runtime", "pct": p * 0.7, "text": "下载 Ollama 安装程序"}), should_stop)
        emit({"step": "runtime", "pct": 72, "text": "检查安装程序的数字签名"})
        if not _signed_by(exe, rt.get("signer", "")):
            raise RuntimeError("Ollama 安装程序的数字签名不对，已停止安装")
        if should_stop():
            raise Cancelled()
        emit({"step": "runtime", "pct": 75, "text": "安装 Ollama（这一步开始后不能中途取消）"})
        # 安装程序一旦开始就让它装完：中途强行结束会留下装了一半的 Ollama
        p = subprocess.run([exe, "/VERYSILENT", "/NORESTART", "/SUPPRESSMSGBOXES"], timeout=900)
        if p.returncode != 0:
            raise RuntimeError(f"Ollama 安装程序出错（退出代码 {p.returncode}）")
        if should_stop():
            raise Cancelled()


def install(model: dict, emit, should_stop) -> None:
    """安装运行环境（没有 Ollama 时）、下载模型（Ollama 自带断点续传和完整性校验）、确认装好。
    emit({"step", "pct", "text"})；step 为 runtime / download / verify / config。出错抛异常，取消抛 Cancelled。"""
    tag = model.get("tag")
    if not tag:
        raise ValueError("没有指定模型")
    if not _INSTALLING.acquire(blocking=False):
        raise RuntimeError("上一次安装还没有结束（Ollama 安装程序开始后要等它装完），请稍后再试")
    try:
        _install(tag, emit, should_stop)
    finally:
        _INSTALLING.release()


def _install(tag: str, emit, should_stop) -> None:
    url = llm.default_ollama()
    if not llm.ollama_exe():
        _install_ollama(catalog().get("runtime") or {}, emit, should_stop)
    emit({"step": "runtime", "pct": 90, "text": "启动 Ollama"})
    if not ensure_ollama(url, wait=30):
        raise RuntimeError("Ollama 没能启动")
    emit({"step": "runtime", "pct": 100, "text": "运行环境就绪"})
    if should_stop():
        raise Cancelled()

    req = urllib.request.Request(f"{url}/api/pull", data=json.dumps({"model": tag, "stream": True}).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    parts: dict = {}
    with urllib.request.urlopen(req, timeout=60) as r:
        for line in r:
            if should_stop():
                raise Cancelled()  # 关掉连接，Ollama 留着已下载的部分，下次接着下
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("error"):
                raise RuntimeError(f"下载模型出错：{ev['error']}")
            status = str(ev.get("status", ""))
            if ev.get("digest") and ev.get("total"):
                parts[ev["digest"]] = (ev.get("completed") or 0, ev["total"])
                got, total = sum(c for c, _ in parts.values()), sum(t for _, t in parts.values())
                emit({"step": "download", "pct": min(99, got * 100 // max(total, 1)),
                      "text": f"{_size_text(got)} / {_size_text(total)}"})
            elif status.startswith("verifying"):
                emit({"step": "download", "pct": 100, "text": "下载完成"})
                emit({"step": "verify", "pct": 50, "text": "校验文件完整性"})
            elif status == "success":
                emit({"step": "download", "pct": 100, "text": "下载完成"})
                emit({"step": "verify", "pct": 100, "text": "校验通过"})
    llm._CACHE.pop(("ollama", url), None)
    emit({"step": "config", "pct": 50, "text": "确认模型已装好"})
    if not any(n == tag or n == f"{tag}:latest" for n in llm.ollama_models(url)):
        raise RuntimeError("下载完成了，但 Ollama 里找不到这个模型")
    emit({"step": "config", "pct": 100, "text": "完成", "model": f"ollama://{llm._host(url)}/{tag}"})
