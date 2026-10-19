"""bug 检查与出错说明：把校验结果、运行出错的程序化文字改写成使用者看得懂的中文，并指出说的是画布上哪些节点。
校验本身由后端 core/validate.py 完成（未接线的必填输入、类型接不上、连线成环、固定值不合法、输出没有来源等），
这里只负责“说人话”和定位；认不出的写法保留原文（只把类型、术语换成中文），不会丢信息。"""
import re

from ._js import js_str
from .flow import canvas_ids, issue_targets, issue_text
from .labels import humanize_types

KIND_NAME = {"if": "条件分支", "merge": "合并", "foreach": "逐项循环", "subflow": "子流程"}

# 运行出错的原因代码 → 一句通俗的解释（代码来自 core/engine.py 与运行页本身）
CAUSE = {
    "missing_input": "它需要的输入没有拿到",
    "input_type": "收到的数据类型和它要的不一样",
    "type_error": "收到的数据类型和它要的不一样",
    "output_type": "它产出的数据类型和说好的不一样",
    "bad_output": "它产出的结果和说好的不一样",
    "timeout": "运行太久没完成，被自动停下了",
    "module_crash": "节点内部的程序出错了",
    "not_confirmed": "它会读取或改动你电脑上的文件，需要先得到你的确认",
    "cycle": "连线绕成了一个圈，没法按顺序执行",
    "merge_empty": "合并节点一路数据也没收到",
    "too_many_items": "要逐项处理的东西太多，超过了设定的上限",
    "no_result": "这一项没有产生结果",
    "unknown_kind": "节点类型不认识",
    "invalid": "运行前的检查没有通过，工作流里还有问题",
    "network": "访问网络失败，连不上要访问的网站或服务",
    "backend_unreachable": "连不上织灵的后台服务",
    "server": "后台服务出错了",
    "cancelled": "运行被终止了",
}

_TERMS = [
    (re.compile(r"\bforeach\b", re.ASCII), "逐项循环"),
    (re.compile(r"\bmerge\b", re.ASCII), "合并"),
    (re.compile(r"\bif 节点", re.ASCII), "条件分支"),
    (re.compile(r"\bconfig\b\.?", re.ASCII), "固定值"),
    (re.compile(r"\bmax_items\b", re.ASCII), "数量上限"),
]


def node_names(flow_nodes):
    """画布节点 id → 给人看的名字（自己起的名字、模块名、节点类型；端点用标题）"""
    out = {}
    for n in flow_nodes or []:
        d = n.get("data") or {}
        node = d.get("node") or {}
        name = node.get("label") or (d.get("module") or {}).get("name") or KIND_NAME.get(node.get("kind")) or d.get("title")
        out[n["id"]] = js_str(name or n["id"].split("/")[-1])
    return out


def _name_of(token, wf, ids, names):
    t = issue_targets(f"节点 {token}", wf, ids)
    return f"「{names.get(t[0], t[0].split('/')[-1])}」" if t else f"「{token}」"


def _end(ref, wf, ids, names):
    """连线一端“a.text”“$in.url”“$out.result” → 给人看的写法"""
    nid, _, port = ref.partition(".")
    if nid == "$in":
        return f"工作流输入「{port}」"
    if nid == "$out":
        return f"工作流输出「{port}」"
    return _name_of(nid, wf, ids, names)


_REQUIRED = re.compile(r"^节点 (\S+?)：必需输入 (\S+?)（(\S+?)）没有来源$")
_NO_SOURCE = re.compile(r"^工作流输出 (\S+) 没有来源$")
_CYCLE = re.compile(r"^连线形成环：(.+?)。.*$")
_INCOMPAT = re.compile(r"^连线 \d+（(\S+) → (\S+)）：类型不兼容，(\S+) 不能接到 (\S+?)，.*$")
_EDGE = re.compile(r"(?:控制线|连线) \d+（(\S+) → (\S+)）")
_NODE = re.compile(r"节点 ([^\s：:，,；;（(]+)")


def friendly(issue, wf, names=None, ids=None):
    """一条校验或出错文字 → 通俗中文"""
    names = names or {}
    ids = ids if ids is not None else canvas_ids(wf)
    t = issue_text(js_str(issue)).strip()
    m = _REQUIRED.match(t)
    if m:
        return f"{_name_of(m.group(1), wf, ids, names)}还缺一个必填的输入（{humanize_types(m.group(3))}）：要么接一条线进来，要么填一个固定值"
    m = _NO_SOURCE.match(t)
    if m:
        return f"工作流输出「{m.group(1)}」还没有接上任何节点，运行完会拿不到结果"
    m = _CYCLE.match(t)
    if m:
        loop = " → ".join(_name_of(x.strip(), wf, ids, names) for x in m.group(1).split("→"))
        return f"连线绕成了一个圈（{loop}），没法按顺序执行；要重复做一件事请用“逐项循环”"
    m = _INCOMPAT.match(t)
    if m:
        return (f"{_end(m.group(1), wf, ids, names)}送出的是{humanize_types(m.group(3))}，"
                f"但{_end(m.group(2), wf, ids, names)}要的是{humanize_types(m.group(4))}，接不上；中间需要加一个转换的节点")
    t = _EDGE.sub(lambda x: f"从{_end(x.group(1), wf, ids, names)}到{_end(x.group(2), wf, ids, names)}的连线", t)
    t = _NODE.sub(lambda x: _name_of(x.group(1), wf, ids, names), t)
    for rx, word in _TERMS:
        t = rx.sub(word, t)
    return humanize_types(t)


def check_items(errors, problems, wf, names=None):
    """bug 检查的结果：[{"text": 通俗说明, "raw": 原文, "targets": [画布节点 id]}]；先列读不进来的问题，再列校验结果"""
    ids = canvas_ids(wf)
    out = []
    for p in problems or []:
        out.append({"text": humanize_types(js_str(p)), "raw": js_str(p), "targets": []})
    for e in errors or []:
        out.append({"text": friendly(e, wf, names, ids), "raw": js_str(e), "targets": issue_targets(e, wf, ids)})
    return out


def _path_target(path, wf, ids):
    """运行出错的位置“w.each[1].fetch” → 画布上最贴近的节点（严格按路径；子流程里的节点画布上没有，就指向那个子流程）"""
    wf_id = js_str(wf.get("id") or "") if isinstance(wf, dict) else ""
    t = path[len(wf_id) + 1:] if wf_id and path.startswith(f"{wf_id}.") else path
    parts = [x for x in re.sub(r"\[\d+\]", "", t).split(".") if x]
    known = set(ids)
    while parts:
        cid = "/".join(parts)
        if cid in known:
            return [cid]
        parts.pop()
    return []


def explain_error(err, wf, names=None):
    """运行出错（result["error"]：{code, message, path}）→ {"title": 哪里出错, "cause": 为什么, "detail": 细节, "targets": [...]}"""
    err = err or {}
    names = names or {}
    ids = canvas_ids(wf)
    path = js_str(err.get("path") or "")
    targets = _path_target(path, wf, ids)
    where = f"「{names.get(targets[0], targets[0].split('/')[-1])}」" if targets else ""
    code = js_str(err.get("code") or "")
    msg = js_str(err.get("message") if err.get("message") is not None else "")
    title = f"{where}这一步出错了" if where else "运行出错了"
    cause = CAUSE.get(code, "")
    if code == "invalid":  # 运行前校验没通过：逐条说明
        lines = [x for x in msg.split("\n") if x.strip()]
        detail = "\n".join(f"· {friendly(x, wf, names, ids)}" for x in lines)
        for x in lines:
            for i in issue_targets(x, wf, ids):
                if i not in targets:
                    targets.append(i)
    else:
        detail = friendly(msg, wf, names, ids) if msg else ""
    if not cause and not detail:
        cause = "没有拿到具体原因"
    return {"title": title, "cause": cause, "detail": detail, "targets": targets}
