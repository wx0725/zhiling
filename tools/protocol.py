"""统一模型协议：无论本地 Ollama、云端 API 还是订阅命令行，进出都是同一种格式。

- 请求 Request：任务名、说明（system）、内容（user）、要不要 JSON、温度，以及“回答该长什么样”的代码检查
- 结果 Reply：是否成功、解析好的数据、原始文字、错误、模型、来源（local / api / cli）、用时、这次用了几次调用

能用代码做的事都在这里统一做，不交给模型：取出 JSON、检查回答形状、形状不对时带着具体原因再问一次、记下调用次数。
各来源的差别（接口地址、密钥、命令行参数）只在 send 里出现，见 tools/llm.py。
"""
import json
import re
import threading
import time
import urllib.error
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable

from tools import llm


class ContextExhausted(llm.LLMError):
    """提示太长，删掉可选材料后仍放不下。是 LLMError（OSError）的子类：调用方原来对“连不上”的处理照样适用。"""


@dataclass
class Request:
    task: str                      # 任务名，只用于记录（例如 "refine"、"feasibility"）
    system: str
    user: str
    json: bool = True              # 要求回答是一个 JSON 对象
    temperature: float = 0
    check: Callable[[Any], str | None] | None = None  # 代码检查解析后的数据：合格返回 None，不合格返回原因
    attempts: int = 2              # 回答不合格时最多问几次（含第一次）


@dataclass
class Reply:
    ok: bool
    data: Any = None               # json=True 时是解析并通过检查的数据
    text: str = ""                 # 最后一次的原始回答
    error: str = ""                # 不合格的原因（ok 为假时）
    model: str = ""
    source: str = ""               # local / api / cli
    seconds: float = 0
    calls: int = 0
    category: str = ""             # 错误类别（ok 为假时）：invalid / cancelled / deadline / context_exhausted …
    errors: list = field(default_factory=list)  # 每次不合格的原因


def source_of(model: str) -> str:
    if isinstance(model, str) and model.startswith("api://"):
        return "api"
    if isinstance(model, str) and model.startswith("cli://"):
        return "cli"
    return "local"


# ---------- 调用次数：评测和界面用来看省了多少 ----------
_STATS = Counter()
_LOCK = threading.Lock()
_TASK = threading.local()  # 正在进行的任务名：call 设置，send 计数时用


def stats() -> dict:
    with _LOCK:
        return dict(_STATS)


def reset_stats() -> None:
    with _LOCK:
        _STATS.clear()


def _count(task: str) -> None:
    with _LOCK:
        _STATS["calls"] += 1
        _STATS[f"task:{task}"] += 1


# ---------- 上下文预算：别把放不下的提示发给模型（本机大模型提示一长就会 CUDA 崩溃） ----------
# 保守的 token 估算：非 ASCII（中文等）每字按 1 个 token，ASCII 按 4 字符≈1 个 token。不装分词库。
def estimate_tokens(text: str) -> int:
    ascii_n = sum(1 for c in text if ord(c) < 128)
    return (len(text) - ascii_n) + (ascii_n + 3) // 4


# 本机 Ollama 的输入上限先保守设 6000 token：本机 27B 在更长的提示下会崩。云端给一个宽松值。
# 都可在 model_settings.json 用可选项 context_budget（整数，或 {local/api/cli: 整数}）覆盖，代码里不写死个人配置。
LOCAL_INPUT_TOKENS = 6000
REMOTE_INPUT_TOKENS = 100000


def _is_local(model: str) -> bool:
    """跑在本机的模型：Ollama，或标了 local 的 OpenAI 兼容服务（如 LM Studio 的 api://.../...）。
    这些都受本机显存限制，用保守的本地上限，别用云端的宽松值。"""
    src = source_of(model)
    if src == "local":
        return True
    if src == "api" and isinstance(model, str):
        provider = model[len("api://"):].split("/", 1)[0]
        return bool(llm.api_specs().get(provider, {}).get("local"))
    return False


def input_budget(model: str) -> int:
    local = _is_local(model)
    key = "local" if local else source_of(model)
    override = llm.load_settings().get("context_budget")
    v = override.get(key) if isinstance(override, dict) else override
    if isinstance(v, (int, float)) and v > 0:
        return int(v)
    return LOCAL_INPUT_TOKENS if local else REMOTE_INPUT_TOKENS


# 可选材料（示例、完整模块说明）用这对私有区字符包起来：放得下就原样（去掉记号）发出，
# 放不下先把这些材料整段删掉再试，仍放不下才报“内容太长”。真正的需求、已确认的决定、当前图不包进来。
OPT_OPEN, OPT_CLOSE = "", ""
_OPT = re.compile(OPT_OPEN + r".*?" + OPT_CLOSE, re.S)


def optional(text: str) -> str:
    """把一段可选材料标记起来：上下文放不下时最先删它。"""
    return f"{OPT_OPEN}{text}{OPT_CLOSE}"


def strip_optional(text: str) -> str:
    """去掉可选材料的记号，保留内容（真正发送或统计长度前都要去掉记号）。"""
    return text.replace(OPT_OPEN, "").replace(OPT_CLOSE, "")


def _fit(model: str, messages: list) -> list:
    """按模型来源的上限裁剪：先按原样（去掉记号），放不下就整段删掉可选材料，仍放不下报“内容太长”。"""
    budget = input_budget(model)
    full = [{**m, "content": strip_optional(m.get("content", ""))} for m in messages]
    if sum(estimate_tokens(m["content"]) for m in full) <= budget:
        return full
    trimmed = [{**m, "content": strip_optional(_OPT.sub("", m.get("content", "")))} for m in messages]
    size = sum(estimate_tokens(m["content"]) for m in trimmed)
    if size <= budget:
        return trimmed
    raise ContextExhausted(f"内容太长，放不下（估算 {size} 个 token，上限 {budget}）。"
                           "请减少需求里的材料，或换一个能读更长内容的模型")


def error_category(exc: BaseException) -> str:
    """把异常归成统一类别：内容太长 / 认证失败 / 格式不支持 / 暂时故障 / 连不上 / 其他。
    只有“暂时故障”（5xx）值得重试一次——这件事已在 llm.chat_ollama 里做了，别在这里再重试一层。
    各适配器把 HTTP 错误包装成带 category 的 LLMError（见 tools/llm.py），所以优先读它带的类别。"""
    cat = getattr(exc, "category", "")
    if cat:
        return cat
    if isinstance(exc, ContextExhausted):
        return "context_exhausted"
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code in (401, 403):
            return "auth"
        if exc.code == 400:
            return "format"
        if exc.code >= 500:
            return "transient"
        return "rejected"
    if isinstance(exc, (urllib.error.URLError, ConnectionError, TimeoutError, OSError)):
        return "unreachable"
    return "unknown"


# ---------- 单次传输的超时：按任务分级 ----------
# 本地模型卡死（显卡挂住）时，一次请求不该让使用者等满 10 分钟：回答短的任务 2 分钟，起草 5 分钟，其余用 llm.TIMEOUT。
# 可在 model_settings.json 用可选项 timeouts（{任务名: 秒数}）覆盖，代码里不写死个人配置。
TASK_TIMEOUTS = {"feasibility": 120, "refine": 120, "inputs": 120, "judge": 120, "draft": 300}


class task_scope:
    """with protocol.task_scope("draft"): ... —— 这段里经 send 发出的请求都算这个任务（计数、超时档位）。
    替身传输（测试里换掉 chat）照常工作，因为任务名不经参数传递。"""

    def __init__(self, name: str):
        self.name = name

    def __enter__(self):
        self.prev = getattr(_TASK, "name", None)
        _TASK.name = self.name

    def __exit__(self, *exc):
        _TASK.name = self.prev


def task_timeout(task: str | None) -> float:
    override = llm.load_settings().get("timeouts")
    if isinstance(override, dict):
        v = override.get(task)
        if isinstance(v, (int, float)) and v > 0:
            return float(v)
    return float(TASK_TIMEOUTS.get(task or "", llm.TIMEOUT))


# ---------- 传输：各来源在这里变成同一种 (文字, 用时) ----------
def send(model: str, messages: list, fmt, temperature: float = 0, url: str | None = None,
         task: str = "chat", deadline: float | None = None) -> tuple[str, float]:
    """发一次请求（唯一真正调用模型、也是唯一计数的地方）。model 的写法见 tools/llm.py；
    普通名字（不带前缀）发到 url 指定的 Ollama，没给就是默认的。
    deadline 是单调时钟的截止点：传输超时取 min(单次上限, 剩余期限)，别让一次请求拖过整次动作的期限。
    单次上限按任务分级（task_timeout）：协议 call 里的任务名优先，否则用调用方给的 task。"""
    messages = _fit(model, messages)  # 放不下就在这里报“内容太长”，不把超长提示发出去；记号也在这里去掉
    name = getattr(_TASK, "name", None) or task
    _count(name)
    timeout = task_timeout(name)
    # 传输层按 deadline 自己算 min(单次上限, 剩余期限)，并在每次重试前重算、过期即止（见 tools/llm._effective_timeout）
    if llm.is_remote(model):
        return llm.chat(model, messages, fmt, temperature, timeout=timeout, deadline=deadline)
    t = time.time()
    return (llm.chat_ollama(url or llm.default_ollama(), model, messages, fmt, temperature, timeout, deadline),
            time.time() - t)


def parse_json(text: str):
    """取出回答里的 JSON（可能带说明或代码块）；取不出返回 (None, 原因)"""
    try:
        return json.loads(llm.extract_json(text)), ""
    except (json.JSONDecodeError, TypeError) as e:
        return None, f"不是合法 JSON：{e}"


def _terminated(reply: Reply, should_stop, deadline) -> bool:
    """取消或超过期限都是明确终止：发送前查一次，拿到结果、提交前再查一次——迟到的结果不算成功。"""
    if should_stop and should_stop():
        reply.ok, reply.error, reply.category = False, "已取消", "cancelled"
        return True
    if deadline is not None and time.monotonic() >= deadline:
        reply.ok, reply.error, reply.category = False, "超过期限", "deadline"
        return True
    return False


def call(model: str, req: Request, url: str | None = None, send_fn=None,
         should_stop=None, deadline: float | None = None) -> Reply:
    """按统一协议问一次模型。连不上等传输错误照常抛出（OSError），交给调用方报给使用者。
    send_fn：替换传输（签名同 send，不带 task），各模块用它保留自己的 chat，测试里可以换成替身。
    should_stop/deadline：每次发送前、拿到结果提交前都检查；取消或超期返回明确的终止结果，不把迟到结果当成功。
    注：deadline 只透传给默认传输（send→llm，用来算单次超时并在重试前重算）；自定义 send_fn 的单次超时由它自己管，
    deadline 在协议层仍把关每次发送前后的终止。"""
    sender = send_fn or (lambda m, msgs, fmt, temp, u: send(m, msgs, fmt, temp, u, deadline=deadline))
    base = [{"role": "system", "content": req.system}, {"role": "user", "content": req.user}]
    messages = base
    reply = Reply(ok=False, model=model, source=source_of(model))
    outer = getattr(_TASK, "name", None)
    for _ in range(max(1, req.attempts)):
        if _terminated(reply, should_stop, deadline):  # 发送前：已取消/已超期就不再发起请求
            return reply
        _TASK.name = req.task
        try:
            text, secs = sender(model, messages, "json" if req.json else None, req.temperature, url)
        except Exception:
            # 取消/超期后连接报错、或因剩余期限耗尽而超时：先给出明确的终止结果，否则按原契约抛出传输错误
            if _terminated(reply, should_stop, deadline):
                return reply
            raise
        finally:
            _TASK.name = outer
        reply.calls += 1
        reply.seconds += secs
        reply.text = text
        if _terminated(reply, should_stop, deadline):  # 提交前再查：迟到的结果不算这次动作的成功
            return reply
        if not req.json:
            reply.ok, reply.data = True, text
            return reply
        data, why = parse_json(text)
        if why == "" and req.check:
            why = req.check(data) or ""
        if _terminated(reply, should_stop, deadline):  # 解析/校验期间被取消或超期：不论校验结果都按终止处理
            return reply
        if not why:
            reply.ok, reply.data, reply.error, reply.category = True, data, "", ""
            return reply
        reply.error = why
        reply.category = "invalid"
        reply.errors.append(why)
        # 带着具体原因再问一次，而不是原样重复
        messages = base + [{"role": "assistant", "content": text},
                           {"role": "user", "content": f"上一次的回答不合格：{why}。请按要求重新输出，只输出 JSON。"}]
    return reply
