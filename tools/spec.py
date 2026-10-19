"""标准需求单：模型只负责把使用者的话读成这张单子，其余判断全由这里的代码完成。

单子的栏目：
- goal：一句话目标
- inputs：运行时使用者提供什么 [{name, type, desc}]；None 表示使用者还没说，[] 表示不需要输入
- outputs：最后得到什么 [{name, type, desc}]；None 表示还没说
- steps：中间按顺序做什么 [{do, module, each}]；module 是模块 id（如 "web.fetch@0"），each 表示对列表逐项做；None 表示还没说
- branches：条件分支 [{when, then, else}]；else 为 None 表示没说条件不成立时怎么办
- failures：会失败的步骤失败时怎么办 [{module, handle}]
- examples：使用者给的例子（选填，文字）
- unsupported：现有模块做不到的部分

代码负责的事：检查类型和模块是否存在、找出缺了哪一栏、决定问什么和先问哪个、一轮最多问几个、问过的不再问、
给出能直接改单子的选项（点了不必再调模型）、把单子拼成交给生成的需求文字、检查生成的图和单子是否对得上。
"""
import copy
import hashlib
import json
import re

from core.types import compatible, is_valid, list_item

MAX_QUESTIONS = 3   # 每轮最多问几个
MAX_OPTIONS = 3     # 每个问题最多几个选项（界面上另有“其他”可以自己写）
MAX_OPTION_LEN = 20  # 模型给的选项太长就不用（选项要一眼看完，长的说明请自己写）
OTHER = "其他"

TYPE_WORDS = {"string": "文字", "number": "数", "integer": "整数", "boolean": "真或假", "object": "对象",
              "file": "文件", "any": "任意值"}

# 选项 → 直接写进单子的值（不经过模型）
INPUT_CHOICES = {"一个网址": ("url", "string", "网址"), "一组网址": ("urls", "list<string>", "一组网址"),
                 "一段文字": ("text", "string", "一段文字"), "一个文件路径": ("path", "string", "文件路径"),
                 "一个数": ("number", "number", "一个数"), "一组数": ("numbers", "list<number>", "一组数")}
OUTPUT_CHOICES = {"一段文字": ("result", "string", "一段文字"), "真或假": ("result", "boolean", "真或假"),
                  "一组文字（列表）": ("results", "list<string>", "一组文字"), "一个数": ("result", "number", "一个数")}
DROP, ABANDON = "去掉这部分，用剩下的生成", "不做了"


def type_word(t) -> str:
    if not isinstance(t, str):
        return "类型未定"
    item = list_item(t)
    return f"一组{type_word(item)}" if item else TYPE_WORDS.get(t, t)


# ---------- 整理模型给的单子 ----------
def _text(v) -> str:
    return v.strip() if isinstance(v, str) else ""


def _name(v, fallback: str, used: set) -> str:
    n = re.sub(r"[^a-z0-9_]", "_", _text(v).lower()).strip("_")
    n = n if re.fullmatch(r"[a-z][a-z0-9_]*", n or "") else fallback
    base, i = n, 2
    while n in used:
        n, i = f"{base}_{i}", i + 1
    used.add(n)
    return n


def _ports(v, fallback: str):
    if v is None:
        return None
    if not isinstance(v, list):
        return None
    out, used = [], set()
    for i, p in enumerate(v):
        if not isinstance(p, dict):
            continue
        t = p.get("type")
        out.append({"name": _name(p.get("name"), f"{fallback}{i + 1}" if i else fallback, used),
                    "type": t if isinstance(t, str) and is_valid(t) else None,
                    "desc": _text(p.get("desc") or p.get("description"))})
    return out


def _strings(v) -> list[str]:
    return [s for s in (_text(x) for x in v) if s] if isinstance(v, list) else []


def _known(m, modules) -> bool:
    return isinstance(m, str) and m in modules


def normalize(data, modules: set[str]) -> dict:
    """把模型的回答整理成标准需求单：类型不合法当作没说，不存在的模块当作没对应上（module 为 None）"""
    d = data if isinstance(data, dict) else {}
    steps = None
    if isinstance(d.get("steps"), list):
        steps = []
        for s in d["steps"]:
            if isinstance(s, str):
                s = {"do": s}
            if isinstance(s, dict) and _text(s.get("do")):
                m = s.get("module")
                steps.append({"do": _text(s["do"]), "module": m if _known(m, modules) else None, "each": s.get("each") is True})
    branches = [{"when": _text(b.get("when")), "then": _text(b.get("then")), "else": _text(b.get("else")) or None}
                for b in d.get("branches") or [] if isinstance(b, dict) and _text(b.get("when"))] \
        if isinstance(d.get("branches"), list) else []
    failures = [{"module": f.get("module"), "handle": _text(f.get("handle"))}
                for f in d.get("failures") or [] if isinstance(f, dict) and _known(f.get("module"), modules) and _text(f.get("handle"))] \
        if isinstance(d.get("failures"), list) else []
    options = {}
    if isinstance(d.get("options"), dict):
        for k, v in d["options"].items():
            opts = [o for o in _strings(v) if o != OTHER and len(o) <= MAX_OPTION_LEN][:MAX_OPTIONS]
            if isinstance(k, str) and len(opts) >= 2:
                options[k] = opts
    return {"goal": _text(d.get("goal")), "inputs": _ports(d.get("inputs"), "input"),
            "outputs": _ports(d.get("outputs"), "result"), "steps": steps, "branches": branches,
            "failures": failures, "examples": _strings(d.get("examples")), "unsupported": _strings(d.get("unsupported")),
            "options": options}


def shape_error(data) -> str | None:
    """给统一协议用的形状检查：只看大结构，细节由 normalize 宽松整理"""
    if not isinstance(data, dict):
        return "回答必须是一个 JSON 对象"
    for k in ("inputs", "outputs", "steps"):
        if k not in data:
            return f"缺少 {k}（没说清就写 null）"
        if data[k] is not None and not isinstance(data[k], list):
            return f"{k} 必须是列表或 null"
    for k in ("branches", "failures", "examples", "unsupported"):
        if data.get(k) is not None and not isinstance(data[k], list):
            return f"{k} 必须是列表"
    return None


def carry(spec: dict, prev: dict | None) -> dict:
    """代码自己记的栏目跨轮保留，不交给模型：对应的想法、问过的、同意去掉的做不到的部分、选项写进单子的决定"""
    prev = prev if isinstance(prev, dict) else {}
    answered = prev.get("answered")
    spec["idea"] = prev.get("idea") if isinstance(prev.get("idea"), str) else ""
    spec["answered"] = {k: v for k, v in answered.items() if isinstance(k, str) and isinstance(v, str)} \
        if isinstance(answered, dict) else {}
    spec["dropped"] = _strings(prev.get("dropped"))
    spec["abandoned"] = prev.get("abandoned") is True
    patches = prev.get("patches")
    spec["patches"] = {k: v for k, v in patches.items() if isinstance(k, str) and _valid_patch(v)} \
        if isinstance(patches, dict) else {}
    for patch in spec["patches"].values():  # 代码写进单子的回答是使用者的决定：模型重读后也要保持
        _patch(spec, patch)
    return spec


def clean_spec(spec, modules: set[str]) -> dict | None:
    """界面送回来的单子：形状不对的不用（例如空对象，不能拿来跳过检查），其余重新整理一遍，不信任其中的任何值"""
    if not isinstance(spec, dict) or shape_error(spec):
        return None
    return carry(normalize(spec, modules), spec)


# ---------- 找缺项、出问题 ----------
def _risky(reg) -> dict:
    """会失败的模块：去外面拿东西的（类别“获取”，例如抓网页、读文件）。从模块说明读，不写死模块名"""
    out = {}
    for key, s in reg.items():
        if s.data.get("category") == "获取":
            out[key] = s.data.get("name") or key
    return out


def _numeric(desc: str) -> bool:
    return bool(re.search(r"数|分|金额|价格|年龄|数量|number|score", desc, re.I))


def branch_id(when: str) -> str:
    """条件分支的稳定标识（栏目名用它，不用位置：模型重读后顺序可能变）"""
    return hashlib.sha1(when.encode("utf-8")).hexdigest()[:8]


def gaps(spec: dict, reg) -> list[dict]:
    """按标准流程的顺序列出缺的栏目，每项是一个问题 {slot, ask, why, options, patches}。
    patches：选项 → 怎么直接改单子；不在里面的回答（模型给的选项、自己写的）需要模型重新读一遍"""
    out = []
    answered = spec.get("answered") or {}

    def add(slot, ask, why, options, patches=None, free=None, again=False):
        if slot in answered and not again:
            return  # 问过就不再问：回答已经交给模型读过，或已经直接写进单子
        opts = [o for o in dict.fromkeys(spec.get("options", {}).get(slot, []) + list(options)) if o and o != OTHER]
        out.append({"slot": slot, "ask": ask, "why": why, "options": opts[:MAX_OPTIONS],
                    "patches": patches or {}, "free": free})

    pending = undecided(spec)
    if pending and not spec.get("abandoned"):  # 没有明确同意就一直问（新出现的做不到的部分要重新确认）
        parts = "、".join(pending)
        add("unsupported", f"“{parts}”现有模块做不到，怎么办？", "做不到的部分不会出现在工作流里",
            [DROP, ABANDON], {DROP: ("drop", pending), ABANDON: ("abandon",)}, again=True)
    if spec["inputs"] is None:
        add("inputs", "运行时你会给它什么？", "需求单里还没有“输入”", list(INPUT_CHOICES)[:3],
            {k: ("inputs", v) for k, v in INPUT_CHOICES.items()})
    for p in spec["inputs"] or []:
        if p["type"] is None:
            what = p["desc"] or p["name"]
            one, many = ("number", "list<number>") if _numeric(what) else ("string", "list<string>")
            a, b = f"一个（{type_word(one)}）", f"一组（{type_word(many)}，逐个处理）"
            add(f"inputs.{p['name']}.type", f"“{what}”是一个还是一组？", "一个还是一组决定要不要逐项循环",
                [a, b], {a: ("type", "inputs", p["name"], one), b: ("type", "inputs", p["name"], many)})
    if spec["outputs"] is None:
        add("outputs", "最后要得到什么？", "需求单里还没有“输出”", list(OUTPUT_CHOICES)[:3],
            {k: ("outputs", v) for k, v in OUTPUT_CHOICES.items()})
    many_in = any(isinstance(p["type"], str) and p["type"].startswith("list<") for p in spec["inputs"] or [])
    for p in spec["outputs"] or []:
        if p["type"] is None:
            what = p["desc"] or p["name"]
            if many_in:
                a, b = "每项各一条（列表）", "合成一段文字"
                add(f"outputs.{p['name']}.type", f"“{what}”要每项各一条，还是合成一段？", "输入是一组，结果可以逐项给也可以合并",
                    [a, b], {a: ("type", "outputs", p["name"], "list<string>"), b: ("type", "outputs", p["name"], "string")})
            else:
                a, b, c = "一段文字", "真或假", "一个数"
                add(f"outputs.{p['name']}.type", f"“{what}”是什么样的结果？", "结果的类型决定最后一步怎么接",
                    [a, b, c], {a: ("type", "outputs", p["name"], "string"), b: ("type", "outputs", p["name"], "boolean"),
                                c: ("type", "outputs", p["name"], "number")})
    if spec["steps"] is None:
        add("steps", "中间要怎么处理？", "需求单里还没有“步骤”", ["用模型总结", "用模型翻译", "原样输出"])
    for b in spec["branches"]:
        if b["else"] is None:
            add(f"branches.{branch_id(b['when'])}.else", f"“{b['when']}”不成立时怎么办？", "条件分支两边都要有去处",
                ["输出一句说明", "原样输出", "什么都不输出"], free=("else", b["when"]))
    risky = _risky(reg)
    handled = {f["module"] for f in spec["failures"]}
    each = any(s["each"] for s in spec["steps"] or [])
    for m in dict.fromkeys(s["module"] for s in spec["steps"] or [] if s["module"] in risky):
        if m not in handled:
            name = risky[m]
            opts = ["输出一句说明原因", "跳过这一项，继续处理其他的" if each else "直接报错停下"]
            add(f"failures.{m}", f"“{name}”失败时（打不开、读不到、出错）怎么办？", "这一步要从外面拿东西，可能失败",
                opts + (["直接报错停下"] if each else []), free=("fail", m))
    return out


def checklist(spec: dict, reg) -> list[dict]:
    """标准流程每一栏的状态：给界面显示“还缺什么”"""
    slots = [g["slot"] for g in gaps({**spec, "answered": {}}, reg)]
    answered = spec.get("answered") or {}

    def state(prefix):
        if prefix == "unsupported":  # 只有明确同意去掉才算清楚；选了“不做了”也不能生成
            return "missing" if spec.get("abandoned") or undecided(spec) else "ok"
        hit = [s for s in slots if s == prefix or s.startswith(prefix + ".")]
        if not hit:
            return "ok"
        return "default" if all(s in answered for s in hit) else "missing"
    return [{"key": k, "label": label, "state": state(k)} for k, label in
            (("inputs", "输入"), ("outputs", "输出"), ("steps", "步骤"), ("branches", "条件分支"),
             ("failures", "失败怎么办"), ("unsupported", "做不到的部分"))]


def questions(spec: dict, reg) -> list[dict]:
    """这一轮要问的（给界面）：最多 MAX_QUESTIONS 个，按标准流程的顺序"""
    return [{k: g[k] for k in ("slot", "ask", "why", "options")} for g in gaps(spec, reg)[:MAX_QUESTIONS]]


# ---------- 回答写进单子 ----------
def apply_answers(spec: dict, answers: list, reg) -> tuple[dict, list]:
    """能直接改单子的回答由代码写进去；其余的（自己写的、模型给的选项）返回，交给模型重新读。
    所有回答都记进 answered：问过的不再问。"""
    spec = copy.deepcopy(spec)
    pending = {g["slot"]: g for g in gaps({**spec, "answered": {}}, reg)}
    left = []
    for a in answers if isinstance(answers, list) else []:
        if not isinstance(a, dict):
            continue
        slot, answer = a.get("slot"), _text(a.get("answer"))
        if not isinstance(slot, str) or not answer:
            if answer:
                left.append(a)  # 没有栏目的回答（旧界面）：交给模型
            continue
        spec["answered"][slot] = answer
        g = pending.get(slot)
        patch = g["patches"].get(answer) if g else None
        if g and not patch and g["free"]:
            patch = (*g["free"], answer)  # 内容本来就是一句话的栏目（否则怎么办、失败怎么办）：回答原样写进去
        if patch:
            patch = json.loads(json.dumps(patch))  # 与界面送回来的形状一致（元组变列表）
            spec["patches"][slot] = patch
            _patch(spec, patch)
        elif g:
            left.append(a)
    return spec, left


_NAME = re.compile(r"[a-z][a-z0-9_]*")


def _valid_patch(p) -> bool:
    """界面送回来的 patches 不可信：只认代码会产生的几种形状。
    补丁按名字、条件原文、模块 id 找要改的地方，不按位置：模型重读后顺序可能变"""
    if not isinstance(p, list) or not p or not all(isinstance(x, (str, list)) for x in p):
        return False
    kind, args = p[0], p[1:]
    strs = all(isinstance(x, str) and x.strip() for x in args)
    if kind == "abandon":
        return not args
    if kind == "drop":
        return len(args) == 1 and isinstance(args[0], list) and bool(_strings(args[0]))
    if kind in ("else", "fail"):
        return len(args) == 2 and strs
    if kind in ("inputs", "outputs"):
        v = args[0] if len(args) == 1 else None
        return isinstance(v, list) and len(v) == 3 and all(isinstance(x, str) for x in v) and is_valid(v[1]) \
            and bool(_NAME.fullmatch(v[0]))
    if kind == "type":
        return len(args) == 3 and strs and args[0] in ("inputs", "outputs") and is_valid(args[2])
    return False


def _patch(spec, patch):
    kind = patch[0]
    if kind == "drop":
        spec["dropped"] = list(dict.fromkeys(spec["dropped"] + _strings(patch[1])))
    elif kind == "abandon":
        spec["abandoned"] = True
    elif kind in ("inputs", "outputs"):
        name, t, desc = patch[1]
        same = [p for p in spec[kind] or [] if p["name"] == name]
        if spec[kind] is None:  # 模型重读后又说不清了：用使用者选的
            spec[kind] = [{"name": name, "type": t, "desc": desc}]
        elif same:  # 结构以模型（或使用者改的卡片）为准，只保证这一项的类型是使用者选的
            same[0]["type"] = t
        elif len(spec[kind]) == 1 and spec[kind][0]["type"] is None:  # 只有一项且类型没定：就是问的那一项
            spec[kind][0]["type"] = t
    elif kind == "type":
        _, part, name, t = patch
        for p in spec[part] or []:
            if p["name"] == name:
                p["type"] = t
    elif kind == "else":
        for b in spec["branches"]:
            if b["when"] == patch[1]:
                b["else"] = patch[2]
    elif kind == "fail":
        spec["failures"] = [f for f in spec["failures"] if f["module"] != patch[1]] + [{"module": patch[1], "handle": patch[2]}]


def undecided(spec: dict) -> list[str]:
    """还没同意去掉的做不到的部分"""
    return [u for u in spec["unsupported"] if u not in spec.get("dropped", [])]


# ---------- 卡片与需求文字 ----------
def _port_line(p) -> str:
    return f"{p['name']}（{type_word(p['type'])}）" + (f"：{p['desc']}" if p["desc"] else "")


def card(spec: dict, reg) -> dict:
    """给使用者看、可以改的卡片（与旧版相同的四栏，外加例子）"""
    names = {k: s.data.get("name", k) for k, s in reg.items()}
    steps = [("逐项：" if s["each"] else "") + s["do"] + (f"（{names.get(s['module'], s['module'])}）" if s["module"] else "")
             for s in spec["steps"] or []]
    cases = [f"如果{b['when']}：{b['then'] or '继续'}；否则：{b['else'] or '（没说）'}" for b in spec["branches"]]
    cases += [f"{names.get(f['module'], f['module'])}失败时：{f['handle']}" for f in spec["failures"]]
    return {"given": [_port_line(p) for p in spec["inputs"] or []] or (["不需要输入"] if spec["inputs"] == [] else []),
            "wanted": [_port_line(p) for p in spec["outputs"] or []], "steps": steps, "cases": cases,
            "examples": list(spec["examples"])}


def requirement(idea: str, spec: dict, reg) -> str:
    """把需求单拼成交给生成的需求文字（代码拼，不让模型再写一遍）"""
    names = {k: s.data.get("name", k) for k, s in reg.items()}
    lines = [spec["goal"] or idea.strip()]
    if spec["inputs"] == []:
        lines.append("输入：不需要，运行时不用提供任何值。")
    elif spec["inputs"]:
        lines.append("输入：" + "；".join(_port_line(p) for p in spec["inputs"]) + "。")
    if spec["outputs"]:
        lines.append("输出：" + "；".join(_port_line(p) for p in spec["outputs"]) + "。")
    if spec["steps"]:
        lines.append("步骤：" + "；".join(
            f"{i}. {s['do']}{'（对列表逐项做）' if s['each'] else ''}" + (f"（用 {s['module']} {names.get(s['module'], '')}）" if s["module"] else "")
            for i, s in enumerate(spec["steps"], 1)) + "。")
    for b in spec["branches"]:
        lines.append(f"条件：如果{b['when']}，就{b['then'] or '继续'}；否则{b['else'] or '按常规处理'}。")
    for f in spec["failures"]:
        lines.append(f"{names.get(f['module'], f['module'])}失败时：{f['handle']}。")
    if spec["examples"]:
        lines.append("例子：" + "；".join(spec["examples"]) + "。")
    if spec["unsupported"] and not undecided(spec):
        lines.append("不包括：" + "、".join(spec["unsupported"]) + "（现有模块做不到，已去掉，不要因此拒绝）。")
    left = [_default(g["slot"], spec, names) for g in gaps({**spec, "answered": {}}, reg)
            if g["slot"] != "unsupported" and g["slot"] not in _applied(spec)]
    if left:
        lines.append("没有说明的部分（按默认做法）：" + "；".join(left) + "。")
    notes = [f"{k}：{v}" for k, v in (spec.get("answered") or {}).items() if k not in _applied(spec)]
    if notes:
        lines.append("使用者的补充回答：" + "；".join(notes))
    lines.append(f"使用者原话：{idea.strip()}")
    return "\n".join(lines)


def _default(slot: str, spec: dict, names: dict) -> str:
    """使用者没回答的栏目按什么默认做法写进需求：写成能直接照做的话，不把问题本身丢给生成"""
    if slot in ("inputs", "outputs", "steps"):
        return {"inputs": "输入按原话推断", "outputs": "输出按原话推断", "steps": "做法按原话用最直接的方式"}[slot]
    part, _, rest = slot.partition(".")
    key = rest.rsplit(".", 1)[0]
    if part in ("inputs", "outputs"):
        return f"{key} 是一个还是一组按原话推断"
    if part == "branches":
        when = next((b["when"] for b in spec["branches"] if branch_id(b["when"]) == key), "")
        return f"“{when}”不成立时不用额外处理"
    if part == "failures":
        return f"{names.get(rest, rest)}出错时不用特别处理（照常运行，出错就停下报错），不要因此拒绝"
    return ""


def _applied(spec) -> set:
    """已经体现在单子里的回答（不必在需求文字里重复）"""
    return {k for k in spec.get("answered") or {} if not _still_missing(spec, k)}


def _still_missing(spec, slot) -> bool:
    if slot == "unsupported":
        return False
    if slot in ("inputs", "outputs", "steps"):
        return spec[slot] is None
    part, _, rest = slot.partition(".")
    m = re.fullmatch(r"(\w+)\.(type|else)", rest)
    key = m.group(1) if m else None  # 栏目名来自界面，认不出的当作仍缺
    if part in ("inputs", "outputs"):
        return not any(p["name"] == key and p["type"] for p in spec[part] or [])
    if part == "branches":
        return not any(branch_id(b["when"]) == key and b["else"] for b in spec["branches"])
    if part == "failures":
        return rest not in {f["module"] for f in spec["failures"]}
    return True


# ---------- 生成阶段：代码能直接判断的事 ----------
def blocked(spec: dict) -> str:
    """有做不到的部分而使用者没同意去掉（或选了不做了）时，返回拒绝的原因；否则为空"""
    if spec.get("abandoned"):
        return "使用者选择不做了：" + "、".join(spec["unsupported"]) if spec["unsupported"] else "使用者选择不做了"
    if undecided(spec):
        return "现有模块做不到：" + "、".join(undecided(spec))
    return ""


def conformance(wf: dict, spec: dict) -> list[str]:
    """生成的图和需求单的输入输出类型对不对得上（只比类型，名字可以不同）"""
    errs = []
    for part, label in (("inputs", "输入"), ("outputs", "输出")):
        have = [p.get("type") for p in (wf.get(part) or {}).values() if isinstance(p, dict)]
        for p in spec[part] or []:
            if not p["type"]:
                continue
            hit = next((t for t in have if t == p["type"]), None) or \
                next((t for t in have if isinstance(t, str) and _alike(t, p["type"])), None)
            if hit is None:
                errs.append(f"需求单要求{label} {p['name']} 的类型是 {p['type']}（{type_word(p['type'])}），"
                            f"图里的{label}类型是 {have or '空'}")
            else:
                have.remove(hit)
    if spec["inputs"] == [] and wf.get("inputs"):
        errs.append("需求单说明不需要输入，图里却有输入：固定的值写进节点的 config")
    return errs


def _alike(have: str, want: str) -> bool:
    """同类就算对上：需求单的类型是模型从使用者的话里读的，不必细到这一层。
    整数和数算同类；对象、任意值、文件这些模型很难从话里读准，只比是一个还是一组"""
    a, b = list_item(have), list_item(want)
    if (a is None) != (b is None):
        return False
    a, b = a or have, b or want
    if {a, b} & {"object", "any", "file"}:
        return True
    return compatible(a, b) or compatible(b, a)


def to_json(spec) -> str:
    return json.dumps({k: v for k, v in spec.items() if k != "options"}, ensure_ascii=False)
