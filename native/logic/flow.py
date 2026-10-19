"""前端流程上的小规则，单独放出来便于测试。（移植自 web/src/flow.js）"""
import re

from ._js import js_str, truthy


def _is_obj(v):
    return isinstance(v, dict)


def _length(v):
    # JS 的 (x || []).length：不是列表或文字（例如数字）时没有长度
    v = v if truthy(v) else []
    return len(v) if isinstance(v, (list, str, dict)) else None


def is_refusal(wf):
    """生成器的“无法实现”回答：没有节点，名字以“无法实现”开头"""
    if not _is_obj(wf):
        return False
    if wf.get("id") == "not_feasible":
        return True
    name = wf.get("name")
    return _length(wf.get("nodes")) == 0 and js_str(name if truthy(name) else "").startswith("无法实现")


def drawable(ev):
    """生成过程中的工作流能否交给画布：只有通过结构校验的版本才画（未通过的可能缺字段，画出来会出错）
    没有模块节点、只有输入直通输出的图也是合法工作流；只有“无法实现”的回答不画"""
    wf = ev.get("workflow") if _is_obj(ev) else None
    if not truthy(wf) or not isinstance(wf, dict) or not isinstance(wf.get("nodes"), list) or is_refusal(wf):
        return False
    if ev.get("type") == "validated":
        return isinstance(ev.get("errors"), list) and len(ev["errors"]) == 0
    if ev.get("type") == "done":
        return ev.get("valid") is True
    return False


class _Latest:
    def __init__(self):
        self._n = 0

    def next(self):
        self._n += 1
        return self._n

    def is_current(self, t):
        return t == self._n


def latest():
    """只认最新一次请求：切换工作流或重新运行后，旧请求晚到的事件被丢弃。返回带 next()、is_current(令牌) 的对象"""
    return _Latest()


_MISSING = object()


def inspect_workflow(wf):
    """读进来的工作流先整理成页面能安全使用的形状：坏的端口声明不交给表单和画布，记为问题显示出来。
    运行和保存仍发送原文，由后端给出完整的校验结果。返回 {"wf": 整理后的, "problems": [文字]}"""
    problems = []
    if not _is_obj(wf):
        return {"wf": {"id": "", "name": "无法读取的工作流", "intent": "", "inputs": {}, "outputs": {}, "nodes": [], "edges": []},
                "problems": ["文件内容不是一个工作流对象"]}

    def ports(decl, which):
        if not _is_obj(decl):
            problems.append(f"缺少 {which}" if decl is _MISSING else f"{which} 应当是对象")
            return {}
        out = {}
        for k, p in decl.items():
            if _is_obj(p) and isinstance(p.get("type"), str):
                out[k] = p
            else:
                problems.append(f'{which}.{k} 的声明不合法，应当写成 {{"type": "类型"}}')
        return out
    clean = dict(wf)
    clean.update({
        "id": wf["id"] if isinstance(wf.get("id"), str) else "",
        "name": wf["name"] if isinstance(wf.get("name"), str) else "未命名工作流",
        "intent": wf["intent"] if isinstance(wf.get("intent"), str) else "",
        "inputs": ports(wf.get("inputs", _MISSING), "inputs"),
        "outputs": ports(wf.get("outputs", _MISSING), "outputs"),
    })
    if not isinstance(wf.get("nodes"), list):
        problems.append("nodes 应当是列表")
    if not isinstance(wf.get("edges"), list):
        problems.append("edges 应当是列表")
    return {"wf": clean, "problems": problems}


def canvas_ids(wf):
    """画布上的节点 id：顶层是 "fetch"，循环体里是 "each/fetch" """
    ids = []

    def walk(g, prefix):
        for n in (g.get("nodes") if isinstance(g, dict) and isinstance(g.get("nodes"), list) else []):
            if not _is_obj(n) or not isinstance(n.get("id"), str):
                continue
            cid = f"{prefix}/{n['id']}" if prefix else n["id"]
            ids.append(cid)
            if n.get("kind") == "foreach" and _is_obj(n.get("body")):
                walk(n["body"], cid)
    walk(wf, "")
    return ids


_NODE_REF = re.compile(r"节点\s*([^\s：:，,；;（(]+)")
_EDGE_REF = re.compile(r"（([^\s.（）]+)\.[^\s]+\s*→\s*([^\s.（）]+)[.）]")
_ITEM = re.compile(r"\[\d+\]", re.ASCII)


def issue_targets(issue, wf, ids=None):
    """一条问题文字（结构校验、运行出错、审查意见）说的是图上哪些节点（画布 id 列表）
    认得的写法：“节点 fetch：”“节点 wf.each[1].fetch：”“连线 3（a.text → b.value）”"""
    if ids is None:
        ids = canvas_ids(wf)
    known = set(ids)
    found = {}  # 按找到的先后，不重复
    wf_id = wf.get("id") if isinstance(wf, dict) else None

    def resolve(token):
        t = re.sub(r"[(（].*", "", js_str(token), count=1)
        if truthy(wf_id) and t.startswith(f"{js_str(wf_id)}."):
            t = t[len(js_str(wf_id)) + 1:]
        t = "/".join(_ITEM.sub("", t).split("."))
        if not t or t.startswith("$"):
            return
        if t in known:
            found[t] = True
            return
        last = t.split("/")[-1]
        same = [i for i in ids if i.split("/")[-1] == last]
        if len(same) == 1:
            found[same[0]] = True
    text = js_str(issue)
    for m in _NODE_REF.finditer(text):
        resolve(m.group(1))
    for m in _EDGE_REF.finditer(text):
        resolve(m.group(1))
        resolve(m.group(2))
    return list(found)


_PREFIX = re.compile(r"^[A-Za-z_][\w-]*((?: > [^：]+)*)：", re.ASCII)
_SCOPE = re.compile(r" > ([\w-]+)\.body", re.ASCII)


def issue_text(issue):
    """问题文字给人看的写法：去掉开头程序里的位置前缀（工作流 id、“> loop.body”），改成“循环 loop 里：”"""
    t = js_str(issue)
    m = _PREFIX.match(t)
    if not m:
        return t
    scopes = [x.group(1) for x in _SCOPE.finditer(m.group(1))]
    rest = t[m.end():]
    if m.group(1) and not scopes:
        return t  # 认不出的写法保持原样
    return ("、".join(f"循环 {s} 里" for s in scopes) + "：" if scopes else "") + rest


def gen_outcome(events):
    """生成结束后一句话说明没成功的原因（成功、或认不出结果时返回空文字）"""
    events = events if isinstance(events, list) else []
    done = next((e for e in reversed(events) if _is_obj(e) and e.get("type") == "done"), None)
    if done is not None:
        if is_refusal(done.get("workflow")):  # 后端对“无法实现”的回答也是 ok=True
            name = js_str((done.get("workflow") or {}).get("name") or "").strip()
            return f"模型认为这个需求做不到：{name}" if name else "模型认为这个需求做不到"
        if done.get("ok") or done.get("stopped"):
            return ""
        if done.get("inconclusive"):
            reason = js_str(done.get("reason") or "").strip()
            return "实跑检查没能得出结论" + (f"（{reason}）" if reason else "") + "：画布上是草稿，可以运行看看，但不能保存"
        if done.get("valid"):
            return "生成的工作流没有通过实跑检查：画布上是草稿，可以运行看看，但不能保存"
        return "生成的工作流还有结构问题，没能画出来；可以把需求说得更具体些再试"
    err = next((e for e in reversed(events) if _is_obj(e) and e.get("type") == "error"), None)
    if err is not None:
        return f"没能生成：{js_str(err.get('message') or '没有拿到具体原因')}"
    return ""


# ---------- 开始页：理清需求 ----------
CARD_PARTS = [
    ("given", "给它什么"),
    ("wanted", "要它产出什么"),
    ("steps", "中间怎么做"),
    ("cases", "特殊情况"),
    ("examples", "例子（选填）"),
]


def card_to_requirement(card):
    """使用者改过卡片后，把卡片写成一段需求交给生成（模型给的描述已不再对应卡片）"""
    def part(k):
        v = card.get(k) if isinstance(card, dict) else None
        v = v if truthy(v) and isinstance(v, list) else []
        return [s for s in (js_str(x).strip() for x in v) if s]
    out = []
    if part("given"):
        out.append(f"给它：{'；'.join(part('given'))}。")
    if part("wanted"):
        out.append(f"要它产出：{'；'.join(part('wanted'))}。")
    if part("steps"):
        out.append(f"做法：{'，然后'.join(part('steps'))}。")
    if part("cases"):
        out.append(f"特殊情况：{'；'.join(part('cases'))}。")
    if part("examples"):
        out.append(f"例子：{'；'.join(part('examples'))}。")
    return "".join(out)


def _chosen_at(chosen, i):
    if isinstance(chosen, dict):
        v = chosen.get(i)
        return chosen.get(str(i)) if v is None else v
    if isinstance(chosen, list):
        return chosen[i] if i < len(chosen) else None
    return None


def merge_answers(previous, questions, chosen):
    """本轮的回答（问题序号 → 选的或自己写的答案）并入之前的问答；同一个问题以最新回答为准。
    问题带栏目（slot，标准需求单里缺的那一栏）时回答也带上，按栏目认同一个问题"""
    def key(x):
        return ("slot", x.get("slot")) if x.get("slot") else ("ask", x.get("ask"))
    keys = [key(q) for q in questions]
    nxt = [a for a in (previous or []) if key(a) not in keys]
    for i, q in enumerate(questions):
        v = _chosen_at(chosen, i)
        answer = js_str("" if v is None else v).strip()
        if answer:
            nxt.append({**({"slot": q["slot"]} if q.get("slot") else {}), "ask": q.get("ask"), "answer": answer})
    return nxt


# ---------- 从空白开始：画布上的分步清单 ----------
def guide_steps(wf, check_errors=(), ran_once=False):
    """按程序当前的状态判断每一步是否完成；current 是第一个没完成的步骤（全完成为 -1）。返回 {"steps", "current"}"""
    wf = wf if isinstance(wf, dict) else {}
    ins = list(wf["inputs"]) if _is_obj(wf.get("inputs")) else []
    outs = list(wf["outputs"]) if _is_obj(wf.get("outputs")) else []
    nodes = wf["nodes"] if isinstance(wf.get("nodes"), list) else []
    edges = wf["edges"] if isinstance(wf.get("edges"), list) else []

    def get(e, k):
        return e.get(k) if isinstance(e, dict) else None

    def fed(o):
        return any(get(e, "to") == f"$out.{o}" for e in edges)
    steps = [
        {"key": "io", "title": "设好输入和输出", "how": "点画布上的“输入”“输出”，在右侧“节点”页改名字、类型和说明：输入是运行时你要提供的，输出是最后要得到的。",
         "done": len(ins) > 0 and len(outs) > 0},
        {"key": "node", "title": "加一个节点", "how": "点上方“添加节点”，选一个模块，例如“抓取网页”“思考”“转成文字”。",
         "done": len(nodes) > 0},
        {"key": "wire", "title": "把线连起来", "how": "从左边节点右侧的插孔拖到右边节点左侧的插孔：从“输入”连进节点，再从节点连到“输出”。",
         "done": len(nodes) > 0 and len(outs) > 0 and all(fed(o) for o in outs)
         and any(js_str(get(e, "from")).startswith("$in.") for e in edges)},
        {"key": "valid", "title": "改到没有问题", "how": "按标题下列出的问题修改（右侧“运行”里的“运行前检查”会逐条说明并能定位）：必填的输入要么接线、要么点节点在右侧“节点”页填固定值。",
         "done": False},
        {"key": "run", "title": "运行一次", "how": "在右侧“运行”里填好输入，点“运行”，看节点上的灯和结果。",
         "done": ran_once},
    ]
    steps[3]["done"] = steps[2]["done"] and len(check_errors) == 0
    current = next((i for i, s in enumerate(steps) if not s["done"]), -1)
    return {"steps": steps, "current": current}
