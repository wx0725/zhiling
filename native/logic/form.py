"""运行表单：初始值与提交值。纯函数，便于单独测试。（移植自 web/src/form.js）
表单里的值：布尔输入是 True / False / None（未选择），其余是输入框里的文字。"""
import math
import re

from ._js import js_number, js_str, json_parse, json_stringify, truthy


def _is_optional(p):
    return p.get("required") is False or "default" in p


def _is_text(type_):
    # 只有这些类型的输入框里是原样文字；其余（any、列表、object、数字）按 JSON 填写，与 parse_value 互为逆操作
    return type_ in ("string", "file")


def to_raw(type_, v):
    """表单里显示的文字（布尔值是勾选状态）"""
    if type_ == "boolean":
        return truthy(v)
    if _is_text(type_):
        return js_str(v)
    return json_stringify(v)


def _example(name, type_):
    # 有声明默认值的用默认值；可选又没默认值的留空（提交时不发送，交给后端按“没有提供”处理）；必填的给一个示例
    n = name.lower()
    if type_ == "string" and "url" in n:
        return "https://example.com"
    if type_ == "list<string>" and "url" in n:
        return '["https://example.com", "https://example.com/no-such-page"]'
    if type_ in ("integer", "number"):
        return "85"
    if type_ == "boolean":
        return False
    if isinstance(type_, str) and type_.startswith("list<"):
        return "[]"
    return ""


def initial_values(inputs):
    out = {}
    for k, p in (inputs or {}).items():
        if "default" in p and p["default"] is not None:
            out[k] = to_raw(p.get("type"), p["default"])
        elif _is_optional(p):
            out[k] = None if p.get("type") == "boolean" else ""  # None：未选择
        else:
            out[k] = _example(k, p.get("type"))
    return out


_INT = re.compile(r"-?\d+", re.ASCII)


def parse_value(type_, raw):
    """表单里的值 → 要发送的值；填得不对时抛 ValueError（消息给人看）"""
    if type_ == "boolean":
        return truthy(raw)
    if type_ == "integer":
        if not _INT.fullmatch(js_str(raw).strip()):
            raise ValueError("需要整数")
        return int(js_str(raw).strip())
    if type_ == "number":
        v = js_number(raw)
        if js_str(raw).strip() == "" or (isinstance(v, float) and math.isnan(v)):
            raise ValueError("需要数字")
        # JS 不分整数和小数：85 发出去是 85，不是 85.0
        return int(v) if isinstance(v, float) and v.is_integer() and abs(v) < 1e21 else v
    if isinstance(type_, str) and (type_.startswith("list<") or type_ in ("object", "any")):
        try:
            return json_parse(raw)
        except ValueError:
            raise ValueError('需要 JSON 列表，例如 ["a", "b"]' if type_.startswith("list<") else "需要 JSON") from None
    return raw


def collect_inputs(inputs, raw):
    """返回 {"values", "errors"}；可选输入留空（或布尔未选择）就不发送，后端会补默认值或按“没有提供”处理"""
    values = {}
    errors = {}
    for k, p in (inputs or {}).items():
        r = raw.get(k)
        if _is_optional(p) and (r is None or (p.get("type") != "boolean" and js_str(r).strip() == "")):
            continue
        try:
            values[k] = parse_value(p.get("type"), r)
        except ValueError as err:
            errors[k] = str(err)
    return {"values": values, "errors": errors}
