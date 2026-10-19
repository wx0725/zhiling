"""给使用者看的类型名称：界面上不直接显示 integer、list<string> 这类程序写法（悬停时仍可看到原写法）。（移植自 web/src/labels.js）
节点上的端口也用这些简短中文词显示，不显示程序里的英文端口名。"""
import re

BASE = {
    "string": "文本",
    "integer": "整数",
    "number": "数字",
    "boolean": "是或否",
    "object": "数据",
    "file": "文件",
    "any": "任意",
}

_LIST = re.compile(r"list<(.+)>")
_TYPE_WORD = re.compile(r"list<[^<>\s]*(?:<[^<>\s]*>)*>|\b(?:string|integer|number|boolean|object|file|any)\b", re.ASCII)


def type_label(t):
    if not isinstance(t, str):
        return "任意"
    m = _LIST.fullmatch(t)
    if m:
        return f"{type_label(m.group(1))}列表"
    return BASE.get(t) or t


def port_labels(ports):
    """一侧端口在节点上显示的文字：模块说明里给这个端口起的中文短名（label，如“网址”“状态码”）；没起名的用类型的中文短词，
    同一侧有几个同类端口时按顺序加上 1、2……以便区分"""
    words = [(p or {}).get("label") if isinstance((p or {}).get("label"), str) and (p or {}).get("label")
             else type_label((p or {}).get("type")) for p in ports]
    total = {w: words.count(w) for w in words}
    seen = {}
    out = []
    for w in words:
        if total[w] > 1:
            seen[w] = seen.get(w, 0) + 1
            out.append(f"{w}{seen[w]}")
        else:
            out.append(w)
    return out


def humanize_types(text):
    """把一段文字里的程序类型写法（string、list<integer>）换成中文短词"""
    return _TYPE_WORD.sub(lambda m: type_label(m.group(0)), text if isinstance(text, str) else str(text))
