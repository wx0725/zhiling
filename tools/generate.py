"""自然语言 → 工作流图（实验版）。

本地模型 + 模块说明与示例 + 格式约束 + 校验器反馈循环。
用法：python -m tools.generate "需求描述" [--model 名称] [--rounds 3] [--out 文件]
"""
import argparse
import json
import os
import re
import sys
import urllib.request

from core.registry import ROOT, load_registry
from tools import llm, protocol
from tools import spec as sp
from core.validate import validate_workflow

# 生成器自己的服务地址；与 llm.think 模块用的 WORKFLOW_OLLAMA_URL 分开，否则运行检查时思考模块会被带到生成模型的服务上
OLLAMA = os.environ.get("WORKFLOW_GEN_URL") or llm.default_ollama()
DEFAULT_MODEL = None  # 不指定时用 model_settings.json 里选的（见 tools/llm.py 的 chosen）
SCHEMA = json.loads((ROOT / "schemas" / "workflow.schema.json").read_text(encoding="utf-8"))
# Ollama 的格式约束不支持 allOf/if-then，用正式 schema 会禁止模型写出 module 等字段；生成时改用扁平版
GEN_SCHEMA = json.loads((ROOT / "schemas" / "workflow.gen.schema.json").read_text(encoding="utf-8"))
FORMATS = {"schema": SCHEMA, "flat": GEN_SCHEMA, "json": "json"}

RULES = """你要把用户的需求写成一张工作流图（JSON）。只能使用下面列出的模块，不能编造模块或端口。
图的格式：
- 顶层字段：spec（固定 "0.2"）、id（小写英文+下划线）、name、intent（原样抄写用户需求）、inputs、outputs、nodes、edges。
- inputs/outputs：{端口名: {"type": 类型, "description": 说明}}，类型只能是 string number integer boolean object file any 或 list<类型>。
- 节点 kind：
  - module：调用模块，写 "module": "模块id@主版本"，固定参数写在 "config" 里。
  - if：输入 condition（boolean）和可选的 value；输出 then、else。带 value 时，条件为真 value 从 then 流出，否则从 else 流出。比较由 logic.compare 模块完成。
    分支里没有数据要传时，不接 value，用控制线让 then/else 决定后续节点是否运行。
  - merge：输入 a、b，输出 value，用来把 if 的两条支路合回一条。
  - foreach：对列表每一项运行一次 body。必须写 max_items。body 有 inputs/outputs/nodes/edges，且 inputs 只有 item、outputs 只有 result；节点外部端口为输入 items、输出 results。
- 连线 edges：数据线 {"from": "节点id.输出端口", "to": "节点id.输入端口"}；工作流自己的输入写 "$in.名字"，输出写 "$out.名字"。
  控制线 {"from": "if节点id.then", "to": "节点id"}（to 不写端口）：只决定该节点是否运行，不传数据；起点只能是 if 的 then/else。
- 非文本的值（数字、真假、列表）要交给只接受文本的模块时，先用 text.format 转成文字；列表要合成一段文字用 text.join。
- 每个必需输入要么接一条线，要么写在 config 里，不能两者都有；一个输入只能接一条线；类型必须匹配，不能隐式转换。
- 如果现有模块做不到用户的需求，不要硬凑：输出 inputs、outputs、nodes、edges 都为空的图，并在 name 里写“无法实现：原因”。
只输出 JSON，不要任何解释。"""


def _first_sentence(text: str) -> str:
    """一句话说明：取 description 的第一句（到第一个句号或换行为止）"""
    s = (text or "").strip()
    cuts = [i for i in (s.find("。"), s.find("\n")) if i >= 0]
    return s[:min(cuts)].strip() if cuts else s


def _ins_line(inputs: dict) -> str:
    return ", ".join(f"{k}:{p['type']}{'' if p.get('required', True) and 'default' not in p else '(可选)'}"
                     for k, p in inputs.items())


def module_catalog(reg, full: set[str] | None = None, only: set[str] | None = None) -> str:
    """给模型看的模块目录。默认是压缩视图：每个模块只有 id、名称、一句话说明、输入和输出的名字与类型，不含示例
    （示例是目录体积的主因）。full 里列出的模块附完整说明和一个示例——起草时命中的候选、或修正轮里出错涉及的模块才需要。
    only 不为 None 时只列这几个模块（实跑审查只需图里用到的模块，不必塞整份目录）。
    各处（生成、理清需求、实跑审查）都调用这同一个函数，保证口径一致。"""
    full = full or set()
    lines = []
    for key, spec in sorted(reg.items()):
        if only is not None and key not in only:
            continue
        d = spec.data
        ins, outs = _ins_line(d["inputs"]), ", ".join(f"{k}:{p['type']}" for k, p in d["outputs"].items())
        if key in full:
            ex = d["examples"][0]
            lines.append(f"- {key}  {d['name']}：{d['description']}\n  输入 {ins}\n  输出 {outs}\n"
                         f"  例：输入 {json.dumps(ex['input'], ensure_ascii=False)} → 输出 {json.dumps(ex['output'], ensure_ascii=False)}")
        else:
            lines.append(f"- {key}  {d['name']}：{_first_sentence(d['description'])}\n  输入 {ins} ｜ 输出 {outs}")
    return "\n".join(lines)


def module_details(reg, keys) -> str:
    """修正轮里附上涉及模块的完整说明和示例，帮模型改对（只列这几个，不是整本目录）。"""
    keys = [k for k in dict.fromkeys(keys) if k in reg]
    if not keys:
        return ""
    out = []
    for key in keys:
        d = reg[key].data
        ex = d["examples"][0]
        out.append(f"- {key}  {d['name']}：{d['description']}\n"
                   f"  例：输入 {json.dumps(ex['input'], ensure_ascii=False)} → 输出 {json.dumps(ex['output'], ensure_ascii=False)}")
    return "涉及模块的完整说明：\n" + "\n".join(out)


def _wf_modules(wf) -> list[str]:
    """图里用到的模块 id（含循环体），用于修正时附它们的完整说明"""
    out = []

    def walk(g):
        # 这里会处理没通过校验的模型输出（nodes 可能是 null、节点可能不是字典），要防御性处理，别让修正轮崩掉
        nodes = g.get("nodes") if isinstance(g, dict) else None
        for n in nodes if isinstance(nodes, list) else []:
            if not isinstance(n, dict):
                continue
            if n.get("kind") == "module" and isinstance(n.get("module"), str):
                out.append(n["module"])
            if isinstance(n.get("body"), dict):
                walk(n["body"])
    walk(wf)
    return out


# 教学例子与测试题刻意用不同场景，避免把答案直接教给模型
EXAMPLE_FILES = ["summarize_page.workflow.json", "teach/classify_reply.workflow.json", "teach/translate_list.workflow.json"]

# 校验错误里的关键词 → 具体改法。只靠原始错误，8B 模型往往原样重复上一次的输出
HINTS = [
    ("没有输入端口 condition", "条件判断必须用 kind 为 if 的节点：logic.compare 的 result 接 if.condition，要往下传的数据接 if.value，if.then / if.else 分别接两条支路，最后用 merge 节点合并。"),
    ("已经有来源", "一个输入（包括 $out.xxx）只能接一条线。两条支路要先分别接到 merge 节点的 a、b，再由 merge.value 接到输出。"),
    ("既有连线又有 config", "这个输入已经接了线，就把它从 config 里删掉。"),
    ("max_items", "foreach 节点必须写 \"max_items\"（整数，例如 20）。"),
    ("does not match", "连线两端必须写成“节点id.端口名”，工作流输入写 $in.名字，输出写 $out.名字；循环体里每项结果写到 $out.result。"),
    ("不存在的模块", "只能用“可用模块”列表里的模块 id，写法是 模块id@0。做不到的需求要按规则输出“无法实现”。"),
    ("必需输入", "每个必需输入要么接一条线，要么写进 config。"),
    ("类型不兼容", "连线两端类型必须一致，不能直接把数字接到文本、单个值接到列表。"),
]

FEASIBILITY = """先不要画图。判断用下面这些模块能不能实现用户需求。
把需求拆成几个必要的能力，逐个写出由哪个模块完成；如果某个能力没有任何模块能做（例如发邮件、发消息、读写数据库、定时运行、读 PDF 或 Word），就判定为不可实现；要仔细对照模块说明里写明的“不能做什么”。
条件判断、循环、合并由图本身的 if / foreach / merge 节点完成，不需要模块；使用者运行时提供的值（网址、数字、文字等）由工作流的输入接收，最后的结果由工作流的输出给出，这两件事也不需要模块。
只输出 JSON：{"steps": [{"need": "能力", "module": "模块id@0 或 null"}], "feasible": true 或 false, "reason": "不可实现时写原因"}"""


def few_shot() -> str:
    parts = []
    for name in EXAMPLE_FILES:
        wf = json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))
        wf.pop("layout", None)
        parts.append(f"需求：{wf['intent']}\n{json.dumps(wf, ensure_ascii=False)}")
    return "\n\n".join(parts)


def chat(model: str, messages: list, fmt, temperature: float = 0, url: str | None = None) -> tuple[str, float]:
    """所有模型调用都经统一协议的传输（tools/protocol.py）；url 只对普通名字（默认 Ollama 上的模型）有意义。
    任务名（决定计数和超时档位）由 protocol.call 或 protocol.task_scope 设定，不经参数传递"""
    return protocol.send(model, messages, fmt, temperature, url or OLLAMA)


def resolve(model: str | None) -> str:
    """没指定模型时用设置里选的；一个能用的都没有就说明怎么办"""
    m = model or llm.chosen()
    if not m:
        raise llm.LLMError("没有可用的模型：请安装 Ollama 并下载一个模型，或设置云端 API 的密钥，或登录订阅命令行")
    return m


_CONFLICT = re.compile(r"节点 (\w+)：输入 (\w+) 既有连线又有 config")


def hints_for(errors: list[str]) -> list[str]:
    tips = [h for key, h in HINTS if any(key in e for e in errors) and key != "既有连线又有 config"]
    for e in errors:  # 冲突要点名具体端口，否则模型常把整个 config 删掉
        m = _CONFLICT.search(e)
        if m:
            tips.append(f"节点 {m.group(1)} 的 config 里只删除 \"{m.group(2)}\" 这一项，其余固定值（如 op、b、instruction）必须保留。")
    return tips


def _plan_error(plan) -> str | None:
    if not isinstance(plan, dict) or not isinstance(plan.get("feasible"), bool):
        return "feasible 必须是 true 或 false"
    return None


def check_feasible(requirement: str, model: str, catalog: str) -> tuple[bool, str, dict]:
    req = protocol.Request("feasibility", f"{FEASIBILITY}\n\n可用模块：\n{catalog}", f"需求：{requirement}",
                           check=_plan_error, attempts=1)
    try:
        reply = protocol.call(model, req, send_fn=lambda *a: chat(*a))
    except protocol.ContextExhausted as e:
        # 判断能不能做这一步的提示都放不下：不拦截，交给生成与校验（并把原因记下来）
        return True, "", {"raw": str(e), "invalid": True, "context_exhausted": True}
    secs, plan = reply.seconds, reply.data
    # 回答不合格（不是对象、feasible 不是真假值）时不拦截，交给生成与校验；不把字符串 "false" 强转成真
    if not reply.ok:
        return True, "", {"raw": reply.text, "invalid": True, "seconds": round(secs, 1)}
    # 以模型的结论为准：组装列表、合并分支等步骤由图结构完成，module 为空不代表缺能力
    feasible = plan["feasible"]
    steps = plan.get("steps") if isinstance(plan.get("steps"), list) else []
    missing = [s.get("need") for s in steps if isinstance(s, dict) and not s.get("module")]
    reason = plan.get("reason") if isinstance(plan.get("reason"), str) and plan.get("reason") else ""
    reason = reason or ("缺少能力：" + "、".join(map(str, missing)) if missing else "")
    return feasible, reason, {"plan": plan, "seconds": round(secs, 1)}


def refusal(requirement: str, reason: str) -> dict:
    return {"spec": "0.2", "id": "not_feasible", "name": f"无法实现：{reason}", "intent": requirement,
            "inputs": {}, "outputs": {}, "nodes": [], "edges": []}


def prefill(wf, requirement: str):
    """代码能直接填的固定字段不让模型操心：spec 版本、intent 原样抄需求"""
    if isinstance(wf, dict):
        wf["spec"] = "0.2"
        if not isinstance(wf.get("intent"), str):
            wf["intent"] = requirement
    return wf


def generate(requirement: str, model: str = DEFAULT_MODEL, rounds: int = 3, fmt="json", feasibility: bool = True,
             post_check=None, on_event=None, should_stop=None, spec: dict | None = None) -> dict:
    """post_check(wf) -> (问题列表, 记录)：校验通过后的进一步检查（例如实际运行后审查），有问题就反馈重写。
    on_event(dict)：每一步的进度（前端实时显示用）。
    should_stop()：返回真时在下一步之前停下（用户点了停止或断开连接）。正在进行的单次模型调用无法中途打断。
    spec：理清需求得到的标准需求单（见 tools/spec.py）。有它时代码直接判断：有做不到的部分且没同意去掉就拒绝，
    否则不再问模型能不能做；生成的图输入输出类型要和单子一致。"""
    emit = on_event or (lambda e: None)
    log = []
    if spec is not None:
        spec = sp.clean_spec(spec, set(load_registry()))
        reason = sp.blocked(spec) if spec else ""
        if reason:  # 代码直接拒绝，不调用模型
            log.append({"round": 0, "step": "可行性判断", "by": "代码", "reason": reason})
            emit({"type": "feasibility", "ok": False, "reason": reason})
            return {"ok": True, "rounds": 0, "workflow": refusal(requirement, reason), "log": log}
        if spec:  # 理清需求时模型已经对照模块读出了做不到的部分，单子里没有就不再问一遍
            feasibility = False
            log.append({"round": 0, "step": "可行性判断", "by": "代码", "skipped": "需求单里没有做不到的部分"})
    model = resolve(model)
    stopped = lambda: bool(should_stop and should_stop())  # noqa: E731
    reg = load_registry()
    catalog = module_catalog(reg)
    if stopped():  # 停止后不再发起新的模型请求
        return {"ok": False, "stopped": True, "rounds": 0, "workflow": None, "log": log}
    if feasibility:
        ok, reason, info = check_feasible(requirement, model, catalog)
        log.append({"round": 0, "step": "可行性判断", **info})
        emit({"type": "feasibility", "ok": ok, "reason": reason})
        if not ok:
            return {"ok": True, "rounds": 0, "workflow": refusal(requirement, reason), "log": log}

    # 示例是可选材料：上下文放不下时协议层会先把它删掉（见 tools/protocol.optional）
    system = f"{RULES}\n\n可用模块：\n{catalog}" + protocol.optional(f"\n\n示例：\n{few_shot()}")
    base = [{"role": "system", "content": system}, {"role": "user", "content": f"需求：{requirement}"}]
    messages = base
    fmt_value = FORMATS[fmt]
    wf = None
    for i in range(1, rounds + 1):
        temperature = 0 if i == 1 else 0.4  # 重试时加一点随机性，避免原样重复
        if stopped():
            return {"ok": False, "stopped": True, "rounds": i - 1, "workflow": wf, "log": log}
        prompt_chars = sum(len(protocol.strip_optional(m["content"])) for m in messages)
        emit({"type": "drafting", "round": i, "prompt_chars": prompt_chars})
        try:
            with protocol.task_scope("draft"):
                text, secs = chat(model, messages, fmt_value, temperature)
        except urllib.error.HTTPError:
            if fmt_value == "json":
                raise
            fmt, fmt_value = "json", "json"  # 模型服务不支持该 schema 时退回普通 JSON 模式
            if stopped():
                return {"ok": False, "stopped": True, "rounds": i - 1, "workflow": wf, "log": log}
            with protocol.task_scope("draft"):
                text, secs = chat(model, messages, fmt_value, temperature)
        except protocol.ContextExhausted as e:
            # 删掉可选材料后仍放不下：如实报告，别把超长提示发出去让本地模型崩
            emit({"type": "context_exhausted", "round": i, "reason": str(e)})
            return {"ok": False, "context_exhausted": True, "reason": str(e), "rounds": i - 1, "workflow": wf, "log": log}
        if stopped():  # 迟到的结果不算这次动作的成功：拿到回答后、用它之前再查一次
            return {"ok": False, "stopped": True, "rounds": i - 1, "workflow": wf, "log": log}
        try:
            wf = json.loads(text)
            if isinstance(wf, dict) and not wf.get("nodes") and str(wf.get("name", "")).startswith("无法实现"):
                # 生成阶段才发现做不到（例如类型接不上），按拒绝处理，不再拿去校验
                log.append({"round": i, "seconds": round(secs, 1), "errors": [], "format": fmt, "refused": True})
                reason = str(wf["name"]).split("：", 1)[-1]
                emit({"type": "feasibility", "ok": False, "reason": reason})
                return {"ok": True, "rounds": i, "workflow": refusal(requirement, reason), "log": log}
            prefill(wf, requirement)
            errors = validate_workflow(wf, reg) if isinstance(wf, dict) else ["输出不是 JSON 对象"]
            if not errors and spec:
                errors = sp.conformance(wf, spec)
        except json.JSONDecodeError as e:
            wf, errors = None, [f"不是合法 JSON：{e}"]
        entry = {"round": i, "seconds": round(secs, 1), "errors": errors, "format": fmt, "prompt_chars": prompt_chars}
        log.append(entry)
        emit({"type": "validated", "round": i, "errors": errors, "seconds": round(secs, 1),
              "workflow": wf if isinstance(wf, dict) else None})
        if not errors and post_check is None:
            return {"ok": True, "rounds": i, "workflow": wf, "log": log}
        if not errors:
            if stopped():
                return {"ok": False, "stopped": True, "rounds": i, "workflow": wf, "log": log}
            emit({"type": "checking", "round": i})
            problems, check_log = post_check(wf)
            entry["check"] = check_log
            status = check_log.get("status") or ("failed" if problems else "passed")
            emit({"type": "checked", "round": i, "status": status, "problems": problems,
                  "reason": check_log.get("reason", ""), "samples": check_log.get("samples", []),
                  "seconds": check_log.get("seconds")})
            if status == "inconclusive":
                # 没能得出结论不等于通过；图本身未必有错，所以不交回重写，而是如实报告
                return {"ok": False, "inconclusive": True, "reason": check_log.get("reason", ""),
                        "rounds": i, "workflow": wf, "log": log}
            if status == "passed":
                return {"ok": True, "rounds": i, "workflow": wf, "log": log}
            # 结构合法但运行检查发现问题：把具体问题反馈回去
            fix = ("上一次输出的图结构合法，但用示例输入实际运行后发现问题。\n上一次的输出：\n" + text +
                   "\n\n运行检查发现的问题：\n" + "\n".join(f"- {p}" for p in problems))
        else:
            # 重新组织一次对话：上一版 + 错误 + 改法，而不是把整段历史堆在后面
            fix = "上一次输出的图没有通过校验。\n上一次的输出：\n" + text + "\n\n错误：\n" + "\n".join(errors)
            tips = hints_for(errors)
            if tips:
                fix += "\n\n改法：\n" + "\n".join(f"- {t}" for t in tips)
        # 修正轮才附上出错涉及模块的完整说明（压缩目录里没有示例）；它是可选材料，放不下时协议层会先删掉
        detail = module_details(reg, _wf_modules(wf))
        if detail:
            fix += protocol.optional("\n\n" + detail)
        messages = base + [{"role": "user", "content": fix + "\n\n请输出修正后的完整 JSON。"}]
    return {"ok": False, "rounds": rounds, "workflow": wf, "log": log}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("requirement")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--format", choices=list(FORMATS), default="json")
    ap.add_argument("--out")
    ap.add_argument("--check", action="store_true", help="校验通过后用示例输入实际运行，再由审查模型检查")
    ap.add_argument("--judge-model", default=None, help="审查用的模型，默认与生成相同")
    ap.add_argument("--judge-url", default=None)
    ap.add_argument("--hint", default="", help="编示例输入时的提示，例如可用的网址")
    a = ap.parse_args(argv)
    a.model = resolve(a.model)
    a.judge_model = a.judge_model or a.model
    checker = None
    if a.check:
        from tools.semantic import make_checker  # 避免循环导入
        checker = make_checker(a.requirement, a.judge_model, a.judge_url, a.hint)
    res = generate(a.requirement, a.model, a.rounds, a.format, post_check=checker)
    print(json.dumps({k: v for k, v in res.items() if k != "workflow"}, ensure_ascii=False, indent=1))
    if a.out:
        open(a.out, "w", encoding="utf-8").write(json.dumps(res["workflow"], ensure_ascii=False, indent=2))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
