from core.errors import ModuleError

OPS = ("count", "sum", "mean", "max", "min")


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def run(inputs: dict) -> dict:
    return {**_stat(inputs), "count": len(inputs["items"])}


def _stat(inputs: dict) -> dict:
    items, op = inputs["items"], inputs["op"]
    if op not in OPS:
        raise ModuleError("invalid_op", f"不支持的统计方式：{op}（可选 {' '.join(OPS)}）")
    if op == "count":
        return {"value": len(items)}
    bad = [v for v in items if not _is_number(v)]
    if bad:
        raise ModuleError("not_number", f"{op} 要求每一项都是数字，收到 {bad[0]!r}")
    if op == "sum":
        return {"value": sum(items)}
    if not items:
        raise ModuleError("empty_list", f"列表是空的，无法求 {op}")
    if op == "mean":
        m = sum(items) / len(items)
        return {"value": int(m) if m == int(m) else m}
    return {"value": max(items) if op == "max" else min(items)}
