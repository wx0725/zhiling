"""转数字列表：把一个列表里的每一项文字（如“1,234.5”“￥12”“50%”）转成数字。
解析规则与 text.to_number 相同（模块之间不互相调用，所以各留一份）。"""
import re
from decimal import Decimal, InvalidOperation

from core.errors import ModuleError

_FULL = str.maketrans("０１２３４５６７８９．，－＋％　", "0123456789.,-+% ")
_CURRENCY = ("人民币", "RMB", "CNY", "USD", "￥", "¥", "$", "€", "£", "元", "块")
_SCALE = {"万": Decimal(10000), "亿": Decimal(100000000)}
_PLAIN = re.compile(r"^(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$")
_GROUPED = re.compile(r"^\d{1,3}(,\d{3})+(\.\d+)?$")


def _strip_currency(s: str) -> str:
    changed = True
    while changed:
        changed = False
        for c in _CURRENCY:
            if s.upper().startswith(c.upper()):
                s, changed = s[len(c):].strip(), True
            if s.upper().endswith(c.upper()):
                s, changed = s[:-len(c)].strip(), True
    return s


def parse_number(v, percent: bool):
    """成功返回 int 或 float；转不了抛 ValueError。"""
    if isinstance(v, bool):
        raise ValueError
    if isinstance(v, (int, float)):
        return v
    if not isinstance(v, str):
        raise ValueError
    s = re.sub(r"\s+", "", v.translate(_FULL))
    s = _strip_currency(s)
    sign = 1
    if s[:1] in "+-" and s:
        sign = -1 if s[0] == "-" else 1
        s = _strip_currency(s[1:])
    pct = s.endswith("%")
    if pct:
        s = s[:-1]
    scale = Decimal(1)
    if s[-1:] in _SCALE:
        scale, s = _SCALE[s[-1]], s[:-1]
    if "," in s:
        if not _GROUPED.match(s):
            raise ValueError
        s = s.replace(",", "")
    if not _PLAIN.match(s):
        raise ValueError
    try:
        d = Decimal(s) * scale * sign
    except InvalidOperation:
        raise ValueError from None
    if pct and percent:
        d = d / 100
    if d == d.to_integral_value() and abs(d) < Decimal(10) ** 15:
        return int(d)
    return float(d)


def run(inputs: dict) -> dict:
    items = inputs["items"]
    if not isinstance(items, list):
        raise ModuleError("not_number", "要转的不是列表；单个值请用“转成数字”（text.to_number）")
    values = []
    for i, v in enumerate(items, 1):
        try:
            values.append(parse_number(v, inputs["percent"]))
        except ValueError:
            if inputs["skip_bad"]:
                continue
            shown = v if isinstance(v, str) else repr(v)
            raise ModuleError("not_number", f"第 {i} 项“{shown}”不是数字，转不了；想跳过这样的项可以把 skip_bad 设为 true") from None
    return {"values": values, "count": len(values)}
