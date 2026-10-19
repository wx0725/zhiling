"""生成用的模型从哪里来：本地 Ollama、云端 API 接口（OpenAI 兼容），或电脑上已登录的 AI 订阅命令行。

模型用一个字符串表示，生成、理清需求、实跑审查都传这个字符串：
- "ollama://主机:端口/模型"：某个 Ollama 服务上已装的模型（自动从各服务的 /api/tags 读出来）
- "api://提供方/模型"（如 "api://deepseek/deepseek-flash"）：调用该提供方的 API，
  先读“配置 AI”里保存到系统凭据管理器的，没有再读环境变量（见 tools/keystore.py）；带 "local": true 的提供方（如本机的 LM Studio）不需要密钥
- "cli://提供方"（如 "cli://kimi"）：调用订阅自带的命令行（例如 Kimi Code），用的是它自己的登录
- 普通名字（如 "qwen3:8b"）：默认 Ollama 服务上的这个模型（命令行工具和旧代码沿用）

代码里不写任何个人配置：
- 可选的云端提供方在 config/providers.json（公开的接口地址，随项目发布）
- 每台电脑自己的设置在项目根目录的 model_settings.json（不进 git，格式见 model_settings.example.json）：
  选了哪个模型、另外的 Ollama 服务（以及要不要由本程序启动它）、“思考”模块默认用的模型、自己添的提供方
- 默认 Ollama 地址取环境变量 OLLAMA_HOST，没有就是 http://127.0.0.1:11434；密钥不写进任何文件
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request

from core.registry import ROOT
from tools import keystore

SETTINGS = ROOT / "model_settings.json"
PROVIDERS = ROOT / "config" / "providers.json"
NO_WINDOW = 0x0800_0000  # CREATE_NO_WINDOW：子进程不弹命令行窗口
TIMEOUT = 600


class LLMError(OSError):
    """模型服务调用失败（网络、密钥、命令行出错）。是 OSError 的子类：调用方原来对“连不上 Ollama”的处理照样适用。
    category：统一错误类别（auth/format/transient/unreachable/deadline/invalid…），供上层分类显示，不影响消息文本。"""

    def __init__(self, *args, category: str = ""):
        super().__init__(*args)
        self.category = category


def _effective_timeout(timeout: float | None, deadline: float | None) -> float:
    """这次传输实际能用的超时：取 min(单次上限, 剩余期限)。已过期限就抛出，不再发起请求。
    注：socket 超时限制的是传输停顿时间；若服务持续缓慢地吐数据，整体耗时仍可能略超期限（urllib 的固有限制，
    单机小工具够用，不另起看门狗线程）。协议层在发送前后另有终止检查兜底。"""
    cap = timeout or TIMEOUT
    if deadline is None:
        return cap
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise LLMError("超过期限，已取消", category="deadline")
    return min(cap, remaining)


# ---------- 设置 ----------
def load_settings() -> dict:
    try:
        v = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return v if isinstance(v, dict) else {}


_SETTINGS_LOCK = threading.RLock()


def _write_settings(s: dict) -> None:
    """整文件原子替换：先写临时文件再改名，写到一半断电也不会留下半个文件"""
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".model_settings.", suffix=".json", dir=str(SETTINGS.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(s, ensure_ascii=False, indent=2))
        os.replace(tmp, SETTINGS)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def update_settings(fn) -> dict:
    """读-改-写 model_settings.json 一步完成（加锁）：fn(当前设置) 原地改，返回改后的全部设置。
    同时改两项（设置面板里换思考模型、加服务商）也不会互相覆盖"""
    with _SETTINGS_LOCK:
        s = load_settings()
        out = fn(s)
        s = out if isinstance(out, dict) else s
        _write_settings(s)
        return s


def save_choice(model: str) -> None:
    update_settings(lambda s: s.__setitem__("model", model))


def save_settings(**items) -> dict:
    """改 model_settings.json 里的几项（值为 None 表示删掉这一项），返回改后的全部设置"""
    def apply(s):
        for k, v in items.items():
            if v is None:
                s.pop(k, None)
            else:
                s[k] = v
    return update_settings(apply)


def _providers() -> dict:
    try:
        v = json.loads(PROVIDERS.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        v = {}
    return v if isinstance(v, dict) else {}


def _merged(kind: str, required: tuple) -> dict:
    """config/providers.json 里的，加上 model_settings.json 里自己添的（同名覆盖）；缺必需字段的忽略"""
    out = {}
    for src in (_providers().get(kind), load_settings().get(kind)):
        if isinstance(src, dict):
            for k, v in src.items():
                if isinstance(v, dict) and all(isinstance(v.get(f), str) for f in required):
                    out[k] = {"name": k, **out.get(k, {}), **v}
    return out


def api_specs() -> dict:
    return _merged("apis", ("base", "key_env"))


def builtin_api_keys() -> set:
    """config/providers.json 里内置的服务商编号（自定义的不能和它们重名）"""
    return set((_providers().get("apis") or {}).keys())


def cli_specs() -> dict:
    return _merged("clis", ("exe",))


def default_ollama() -> str:
    """默认的 Ollama 地址：OLLAMA_HOST（Ollama 自己也认这个变量），没有就是本机 11434"""
    h = os.environ.get("OLLAMA_HOST", "").strip() or "127.0.0.1:11434"
    if h.startswith("0.0.0.0"):
        h = "127.0.0.1" + h[len("0.0.0.0"):]
    return (h if "://" in h else f"http://{h}").rstrip("/")


def ollama_servers() -> list[dict]:
    """要找模型的 Ollama 服务：默认的一个，加上 model_settings.json 里 ollama 列出的（{url, start?}）"""
    out = [{"url": default_ollama()}]
    extra = load_settings().get("ollama")
    for s in extra if isinstance(extra, list) else []:
        if isinstance(s, dict) and isinstance(s.get("url"), str) and s["url"].rstrip("/") not in [x["url"] for x in out]:
            out.append({**s, "url": s["url"].rstrip("/")})
    return out


def _host(url: str) -> str:
    return url.split("://", 1)[-1].rstrip("/")


# ---------- 带过期时间的小缓存：查模型列表不必每次都联网 ----------
_CACHE: dict = {}


def _cached(key, fn, ok_ttl: float, fail_ttl: float):
    hit = _CACHE.get(key)
    if hit and time.time() < hit[0]:
        return hit[1]
    v = fn()
    _CACHE[key] = (time.time() + (ok_ttl if v else fail_ttl), v)
    return v


def _get_json(url: str, headers: dict | None = None, timeout: float = 4):
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def ollama_models(url: str) -> list[str]:
    """这个 Ollama 服务上已装的模型名；连不上为空列表"""
    def fetch():
        try:
            return [m["name"] for m in _get_json(f"{url}/api/tags", timeout=2).get("models") or [] if m.get("name")]
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return []
    return _cached(("ollama", url), fetch, 30, 5)


_SKIP = re.compile(r"embed|image|imagen|tts|audio|speech|whisper|dall|moderation|rerank|vision|video|veo|realtime|"
                   r"transcribe|search|ocr|translation|character", re.I)


def _listed(provider: str, spec: dict, key: str):
    """向提供方的 /models 查一次实际可用的对话模型；查不到为 None"""
    def fetch():
        try:
            data = _get_json(f"{spec['base'].rstrip('/')}/models", {"Authorization": f"Bearer {key}"})
            ids = [str(m.get("id", "")).removeprefix("models/") for m in data.get("data") or [] if isinstance(m, dict)]
            return [i for i in ids if i and not _SKIP.search(i)][:15] or None
        except (OSError, ValueError, AttributeError):
            return None
    return _cached(("api", provider), fetch, 3600, 60)


def api_key(spec: dict) -> str:
    """这个提供方的密钥：先找“配置 AI”保存到系统凭据管理器的，再找环境变量；没有为空字符串"""
    return keystore.get(spec.get("key_env", ""))


def forget_listed(provider: str) -> None:
    """换了密钥：下次重新向提供方查模型列表"""
    _CACHE.pop(("api", provider), None)


def _models_of(provider: str, spec: dict) -> list[str]:
    key = api_key(spec)
    listed = _listed(provider, spec, key) if (key or spec.get("local")) and spec.get("list", True) else None
    if spec.get("local"):
        return listed or []  # 本机服务：只列实际装着的
    defaults = [m for m in spec.get("models") or [] if isinstance(m, str)]
    if not listed:
        return defaults
    return [m for m in defaults if m in listed] + [m for m in listed if m not in defaults]


# ---------- 调用 ----------
def is_remote(model: str) -> bool:
    """由本模块处理的写法（带前缀）；普通名字仍由调用方当作默认 Ollama 上的模型"""
    return isinstance(model, str) and model.split("://", 1)[0] in ("api", "cli", "ollama") and "://" in model


def _split(model: str, prefix: str) -> tuple[str, str]:
    rest = model[len(prefix):]
    if prefix == "ollama://":  # 主机:端口/模型（模型名里可能也有 /）
        host, _, name = rest.partition("/")
        return host, name
    provider, _, name = rest.partition("/")
    return provider, name


def chat_ollama(url: str, model: str, messages: list, fmt, temperature: float = 0, timeout: float | None = None,
                deadline: float | None = None) -> str:
    body = {"model": model, "messages": messages, "stream": False, "think": False, "format": fmt,
            "options": {"temperature": temperature, "num_predict": 4096, "num_ctx": 16384}}
    req = urllib.request.Request(f"{url}/api/chat", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    for attempt in (1, 2):  # 模型服务偶发出错（显卡报错、刚被挤掉重新加载）：等一下重试一次，再不行说人话
        to = _effective_timeout(timeout, deadline)  # 每次（含重试）都按剩余期限重算，过期就抛出不再发
        try:
            with urllib.request.urlopen(req, timeout=to) as r:
                return json.loads(r.read().decode("utf-8"))["message"]["content"]
        except urllib.error.HTTPError as e:
            detail = _ollama_error(e)
            # 还在第一次、是暂时故障、且剩余期限够再试一次，才等一下重试；否则直接说人话
            if e.code >= 500 and attempt == 1 and (deadline is None or deadline - time.monotonic() > OLLAMA_RETRY_WAIT):
                time.sleep(OLLAMA_RETRY_WAIT)
                continue
            cat = "transient" if e.code >= 500 else "auth" if e.code in (401, 403) else "rejected"
            raise LLMError(_explain_ollama(model, e.code, detail), category=cat) from None


OLLAMA_RETRY_WAIT = 3


def _ollama_error(e) -> str:
    try:
        v = json.loads(e.read().decode("utf-8", "replace"))
        return str(v.get("error") or "") if isinstance(v, dict) else ""
    except Exception:
        return ""


def _explain_ollama(model: str, code: int, detail: str) -> str:
    """Ollama 的错误 → 使用者看得懂的一句话（原文附在后面）"""
    d = detail.lower()
    if "cuda" in d or "illegal memory" in d or "out of memory" in d:
        why = (f"本地模型 {model} 在显卡上出错了（常见于提示很长、显存不够或两个模型挤在一块显卡上）。"
               "可以稍后再试、在设置里关掉“生成后实跑检查”减少调用，或者换一个模型")
    elif "context" in d and "exceed" in d:
        why = f"本地模型 {model} 一次读不下这么长的内容"
    elif "not found" in d or code == 404:
        why = f"本地模型 {model} 没有安装，请在 Ollama 里下载，或换一个模型"
    elif code >= 500:
        why = f"本地模型服务出错了（{model}），已重试一次仍失败，稍后再试"
    else:
        why = f"本地模型服务拒绝了请求（{model}）"
    return f"{why}。Ollama 原话：{detail}" if detail else why


def _post(spec, key, body, timeout: float | None = None):
    req = urllib.request.Request(f"{spec['base'].rstrip('/')}/chat/completions", data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def _chat_api(model, messages, fmt, temperature, timeout: float | None = None, deadline: float | None = None):
    provider, name = _split(model, "api://")
    spec = api_specs().get(provider)
    if not spec:
        raise LLMError(f"没有这个 API 提供方：{provider}", category="unknown")
    name = name or (spec.get("models") or [""])[0]
    key = api_key(spec)
    if not key and not spec.get("local"):
        raise LLMError(f"没有找到 {spec['name']} 的 API Key：请在“配置 AI”里填写，或设置环境变量 {spec['key_env']}",
                       category="auth")
    extra = spec.get("extra") if isinstance(spec.get("extra"), dict) else {}
    body = {"model": name, "messages": messages, "temperature": temperature, "stream": False, **extra}
    if fmt and spec.get("json_mode", True):  # 云端不收 JSON Schema 约束，只要求输出 JSON 对象；结构由校验器把关
        body["response_format"] = {"type": "json_object"}
    to = _effective_timeout(timeout, deadline)
    try:
        try:
            data = _post(spec, key, body, to)
        except urllib.error.HTTPError as e:
            if e.code != 400 or "response_format" not in body:
                raise
            body.pop("response_format")  # 有的模型不支持 JSON 模式：去掉再试一次，提示里本来就要求只输出 JSON
            data = _post(spec, key, body, _effective_timeout(timeout, deadline))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        cat = "auth" if e.code in (401, 403) else "format" if e.code == 400 else "transient" if e.code >= 500 else "rejected"
        raise LLMError(f"{spec['name']} 返回错误 {e.code}：{detail}", category=cat) from None
    except (urllib.error.URLError, TimeoutError) as e:
        raise LLMError(f"连不上 {spec['name']}：{e}", category="unreachable") from None
    try:
        text = data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"{spec['name']} 的回答格式不对", category="invalid") from None
    return extract_json(text) if fmt else text


def _messages_as_prompt(messages: list) -> str:
    """命令行只收一段文字：把对话按角色写成一段"""
    label = {"system": "【说明】", "user": "【请求】", "assistant": "【你之前的回答】"}
    parts = [f"{label.get(m.get('role'), '')}\n{m.get('content', '')}" for m in messages]
    parts.append("只输出要求的 JSON 本身：不要解释，不要用代码块包起来，不要读写任何文件，不要运行任何命令。")
    return "\n\n".join(parts)


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> str:
    """回答可能带说明或代码块：取出其中的 JSON 对象；取不出就原样返回，交给调用方报错重试"""
    t = (text or "").strip()
    for cand in [t, *(m.strip() for m in _FENCE.findall(t))]:
        try:
            json.loads(cand)
            return cand
        except (json.JSONDecodeError, TypeError):
            pass
    a, b = t.find("{"), t.rfind("}")
    if 0 <= a < b:
        try:
            json.loads(t[a:b + 1])
            return t[a:b + 1]
        except json.JSONDecodeError:
            pass
    return t


def _chat_cli(model, messages, fmt, timeout: float | None = None, deadline: float | None = None):
    timeout = _effective_timeout(timeout, deadline)
    provider = model[len("cli://"):]
    spec = cli_specs().get(provider)
    if not spec:
        raise LLMError(f"没有这个订阅命令行：{provider}")
    exe = shutil.which(spec["exe"])
    if not exe:
        raise LLMError(f"没有找到 {spec['name']} 的命令行 {spec['exe']}")
    prompt = _messages_as_prompt(messages)
    args = [exe, *(prompt if a == "{prompt}" else a for a in spec.get("args") or [])]
    login = spec.get("login", "")
    # 在空的临时目录里跑：这些命令行本身是能读写文件的助手，不让它碰到项目文件
    with tempfile.TemporaryDirectory(prefix="zhiling-llm-") as cwd:
        secs = timeout or TIMEOUT
        try:
            p = subprocess.run(args, input=prompt if spec.get("stdin") else None, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", cwd=cwd, timeout=secs,
                               stdin=None if spec.get("stdin") else subprocess.DEVNULL,
                               creationflags=NO_WINDOW if os.name == "nt" else 0)
        except subprocess.TimeoutExpired:
            raise LLMError(f"{spec['name']} 超过 {int(secs)} 秒没有回答") from None
    if p.returncode != 0:
        msg = (p.stderr or p.stdout or "").strip().splitlines()
        hint = f"（{login}）" if login and any(w in " ".join(msg).lower() for w in ("login", "log in", "sign in", "auth")) else ""
        first = next((m for m in msg if "error" in m.lower()), msg[0] if msg else str(p.returncode))  # 第一行常是版本号
        raise LLMError(f"{spec['name']} 出错{hint}：{first.strip()}")
    return extract_json(p.stdout) if fmt else p.stdout.strip()


def chat(model: str, messages: list, fmt, temperature: float = 0, timeout: float | None = None,
         deadline: float | None = None) -> tuple[str, float]:
    """与 tools.generate.chat 相同的约定：返回 (回答文字, 用时秒数)。fmt 为真时要求输出 JSON。
    timeout：单次传输的超时上限（秒），不给用默认 TIMEOUT。deadline：这次动作的绝对期限（单调时钟），
    传输超时取 min(单次上限, 剩余期限)，已过期就抛出不再发。"""
    t = time.time()
    if model.startswith("api://"):
        text = _chat_api(model, messages, fmt, temperature, timeout, deadline)
    elif model.startswith("cli://"):
        text = _chat_cli(model, messages, fmt, timeout, deadline)
    else:
        host, name = _split(model, "ollama://")
        text = chat_ollama(f"http://{host}", name, messages, fmt, temperature, timeout, deadline)
    return text, time.time() - t


# ---------- 选择：可选的模型和当前选了哪个 ----------
def choices() -> list[dict]:
    """界面里可选的模型：{id, name, kind, ready, note}。ready 为假时 note 说明还差什么。
    本地的是各 Ollama 服务上实际装了的模型；没装 Ollama 或没下载模型时就没有本地项"""
    out = []
    servers = ollama_servers()
    for s in servers:
        for m in ollama_models(s["url"]):
            where = "" if len(servers) == 1 else f"（{_host(s['url'])}）"
            out.append({"id": f"ollama://{_host(s['url'])}/{m}", "name": f"{m}{where}", "kind": "local", "ready": True,
                        "note": "用电脑上的模型，不联网"})
    for k, spec in api_specs().items():
        if spec.get("local"):  # 本机的 OpenAI 兼容服务（LM Studio）：开着并装了模型才列出
            for m in _models_of(k, spec):
                out.append({"id": f"api://{k}/{m}", "name": f"{m}（{spec['name']}）", "kind": "local", "ready": True,
                            "note": "用电脑上的模型，不联网"})
            continue
        has_key = bool(api_key(spec))
        for m in _models_of(k, spec):
            out.append({"id": f"api://{k}/{m}", "name": f"{spec['name']} · {m}", "kind": "api", "ready": has_key,
                        "note": "调用 API，按用量或套餐额度计费" if has_key else "需要先在“配置 AI”里填写 API Key"})
    for k, spec in cli_specs().items():
        found = bool(shutil.which(spec["exe"]))
        login = spec.get("login", "")
        out.append({"id": f"cli://{k}", "name": spec["name"], "kind": "cli", "ready": found,
                    "note": (f"用订阅额度；没登录的话{login}" if login else "用订阅额度") if found else f"没有找到命令行 {spec['exe']}"})
    return out


def valid(model) -> bool:
    if not isinstance(model, str):
        return False
    if model.startswith("api://"):
        provider, name = _split(model, "api://")
        return provider in api_specs() and bool(name)  # 查到的列表会变：只要提供方还在就算有效
    if model.startswith("cli://"):
        return model[len("cli://"):] in cli_specs()
    if model.startswith("ollama://"):
        host, name = _split(model, "ollama://")
        return bool(host and name)
    return False


def label(model: str) -> str:
    """给人看的名字"""
    if not isinstance(model, str):
        return ""
    if model.startswith("api://") and valid(model):
        provider, name = _split(model, "api://")
        return f"{api_specs()[provider]['name']} · {name}"
    if model.startswith("cli://") and valid(model):
        return cli_specs()[model[len("cli://"):]]["name"]
    if model.startswith("ollama://"):
        return f"本地 · {_split(model, 'ollama://')[1]}"
    return model


def chosen() -> str | None:
    """当前选的模型 id。没选过（或选的已不存在）时：第一个能用的（先本地，再云端、订阅）；一个都没有为 None"""
    m = load_settings().get("model")
    if valid(m):
        return m
    return next((c["id"] for c in choices() if c["ready"]), None)


def think_model() -> str:
    """“思考”模块默认用的模型：model_settings.json 的 think_model，没有就是默认 Ollama 上装的第一个"""
    m = load_settings().get("think_model")
    if isinstance(m, str) and m.strip():
        return m.strip()
    names = ollama_models(default_ollama())
    return names[0] if names else ""


# ---------- 启动设置里要求由本程序启动的 Ollama 服务 ----------
def ollama_exe() -> str | None:
    found = shutil.which("ollama")
    if found:
        return found
    local = os.environ.get("LOCALAPPDATA")
    p = os.path.join(local, "Programs", "Ollama", "ollama.exe") if local else ""
    return p if p and os.path.isfile(p) else None


def start_ollama_servers() -> None:
    """model_settings.json 的 ollama 里带 start 的服务：没在运行就用 start 里的环境变量（如 OLLAMA_MODELS）在后台启动它"""
    exe = ollama_exe()
    for s in ollama_servers():
        start = s.get("start")
        if not isinstance(start, dict) or not exe:
            continue
        try:
            _get_json(f"{s['url']}/api/tags", timeout=2)
            continue  # 已经在运行
        except (OSError, ValueError):
            pass
        env = {**os.environ, **{k: str(v) for k, v in start.items()}, "OLLAMA_HOST": _host(s["url"])}
        try:
            subprocess.Popen([exe, "serve"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, creationflags=NO_WINDOW if os.name == "nt" else 0)
        except OSError:
            pass
        _CACHE.pop(("ollama", s["url"]), None)


if __name__ == "__main__":  # 启动网页.cmd 用：python -m tools.llm start
    import sys
    if sys.argv[1:] == ["start"]:
        start_ollama_servers()
