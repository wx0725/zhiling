import json

from core.errors import ModuleError

_MISSING = object()


def _step(cur, key: str):
    if isinstance(cur, dict):
        return cur.get(key, _MISSING)
    if isinstance(cur, list):
        try:
            i = int(key)
        except ValueError:
            return _MISSING
        return cur[i] if 0 <= i < len(cur) else _MISSING
    return _MISSING


def run(inputs: dict) -> dict:
    data = inputs["data"]
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise ModuleError("invalid_json", f"data 不是合法的 JSON：{e}") from None
    cur = data
    for key in (k for k in inputs["path"].split(".") if k != ""):
        cur = _step(cur, key)
        if cur is _MISSING:
            return {"value": inputs["default"], "found": False}
    return {"value": cur, "found": True}
