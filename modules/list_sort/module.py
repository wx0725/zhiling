import json
import re

from core.errors import ModuleError
from core.text import to_text

ORDERS = ("asc", "desc", "none")
BYS = ("auto", "number", "text")
_CHUNK = re.compile(r"(\d+)")


def _field(item, path: str, i: int):
    cur = item
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            raise ModuleError("missing_field", f"第 {i} 项没有字段“{path}”：{to_text(item)[:80]}")
    return cur


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _as_number(v, i: int):
    if _is_num(v):
        return v
    if isinstance(v, str):
        try:
            return float(v.strip().replace(",", ""))
        except ValueError:
            pass
    raise ModuleError("not_number", f"第 {i} 项“{to_text(v)}”不是数字，不能按数字排序；可以先用“转数字列表”转换，或把 by 设为 text")


def _natural(v) -> tuple:
    """文字的自然顺序：文件2 排在 文件10 前面；英文不分大小写。"""
    return tuple((0, int(c), "") if c.isdigit() else (1, 0, c) for c in _CHUNK.split(to_text(v).casefold()) if c)


def run(inputs: dict) -> dict:
    items, order, by = inputs["items"], inputs["order"], inputs["by"]
    field = inputs["field"].strip()
    if order not in ORDERS:
        raise ModuleError("invalid_option", f"order 只能是 {' / '.join(ORDERS)}，收到 {order!r}")
    if by not in BYS:
        raise ModuleError("invalid_option", f"by 只能是 {' / '.join(BYS)}，收到 {by!r}")
    keys = [_field(x, field, i) if field else x for i, x in enumerate(items, 1)]
    rows = list(zip(range(1, len(items) + 1), keys, items))  # （原来的序号, 排序依据, 原项）
    if inputs["unique"]:  # 按排序依据去重，保留第一次出现的那项
        seen, kept = set(), []
        for r in rows:
            sig = json.dumps(r[1], ensure_ascii=False, sort_keys=True, default=str)
            if sig not in seen:
                seen.add(sig)
                kept.append(r)
        rows = kept
    if order != "none" and rows:
        if by == "auto":
            ks = [r[1] for r in rows]
            if all(_is_num(k) for k in ks):
                by = "number"
            elif all(isinstance(k, str) for k in ks):
                by = "text"
            else:
                raise ModuleError("not_comparable", "要排序的值里混着数字、文字或其他类型，没法自动决定怎么比；请把 by 设为 number（按数字）或 text（按文字）")
        if by == "number":
            nums = [_as_number(k, i) for i, k, _ in rows]
            rows = [r for _, r in sorted(zip(nums, rows), key=lambda t: t[0], reverse=order == "desc")]
        else:
            rows = sorted(rows, key=lambda r: _natural(r[1]), reverse=order == "desc")
    out = [r[2] for r in rows]
    return {"items": out, "count": len(out)}
