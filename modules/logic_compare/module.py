import operator

from core.errors import ModuleError

_ORDER = {"gt": operator.gt, "ge": operator.ge, "lt": operator.lt, "le": operator.le}


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def run(inputs: dict) -> dict:
    a, op, b = inputs["a"], inputs["op"], inputs["b"]
    if op == "eq":
        return {"result": a == b}
    if op == "ne":
        return {"result": a != b}
    if op in _ORDER:
        if not (_is_number(a) and _is_number(b)):
            raise ModuleError("type_mismatch", f"{op} 只能比较数字，收到 {type(a).__name__} 和 {type(b).__name__}")
        return {"result": _ORDER[op](a, b)}
    if op == "contains":
        if (isinstance(a, str) and isinstance(b, str)) or isinstance(a, list):
            return {"result": b in a}
        raise ModuleError("type_mismatch", "contains 要求 a 是文本（b 也是文本）或列表")
    if op == "starts_with":
        if isinstance(a, str) and isinstance(b, str):
            return {"result": a.startswith(b)}
        raise ModuleError("type_mismatch", "starts_with 要求 a 和 b 都是文本")
    if op == "empty":
        return {"result": a is None or (hasattr(a, "__len__") and len(a) == 0)}
    raise ModuleError("invalid_op", f"不支持的比较方式：{op}")
