"""端口类型与连线兼容规则（接口规范第 2 节）。"""
import re

SCALARS = {"string", "number", "integer", "boolean", "object", "file", "any"}
_LIST = re.compile(r"^list<(.+)>$")


def is_valid(t: str) -> bool:
    m = _LIST.match(t)
    return is_valid(m.group(1)) if m else t in SCALARS


def list_item(t: str) -> str | None:
    m = _LIST.match(t)
    return m.group(1) if m else None


def compatible(src: str, dst: str) -> bool:
    """src 端口的值能否连到 dst 端口。不做隐式转换。"""
    if src == dst or "any" in (src, dst):
        return True
    if src == "integer" and dst == "number":
        return True
    a, b = list_item(src), list_item(dst)
    if a is not None and b is not None:
        return compatible(a, b)
    return False
