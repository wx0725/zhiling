"""照着 JS 的几条语义写的小工具：真假判断、Number.isFinite、Math.round、String()、JSON.stringify、Number()。
网页版的逻辑依赖这些语义（例如空对象为真、0.5 四舍五入为 1），移植时统一从这里取，免得各处各写一套。"""
import json
import math
import re


class _Unset:
    """“没有给值”：对应 JS 的 undefined，与 None（JSON 的 null）区分开"""

    _inst = None

    def __new__(cls):
        if cls._inst is None:
            cls._inst = super().__new__(cls)
        return cls._inst

    def __repr__(self):
        return "UNSET"

    def __bool__(self):
        return False


UNSET = _Unset()


def truthy(v):
    # JS 的真假：None、False、0、NaN、"" 为假；空字典、空列表为真（与 Python 不同）
    if v is None or v is False or v is UNSET:
        return False
    if v is True:
        return True
    if isinstance(v, (int, float)):
        return v == v and v != 0
    if isinstance(v, str):
        return v != ""
    return True


def is_finite(v):
    # Number.isFinite：只认真正的数字，布尔、NaN、无穷都不算
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def js_round(x):
    # Math.round：.5 往正无穷方向进位（Python 的 round 是银行家舍入）
    if not is_finite(x):
        return x
    return int(math.floor(x + 0.5))


def _num_str(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if math.isnan(v):
        return "NaN"
    if math.isinf(v):
        return "Infinity" if v > 0 else "-Infinity"
    if v.is_integer() and abs(v) < 1e21:
        return str(int(v))
    return repr(v)


def js_str(v):
    # String(v)
    if v is None:
        return "null"
    if v is UNSET:
        return "undefined"
    if isinstance(v, str):
        return v
    if isinstance(v, (bool, int, float)):
        return _num_str(v)
    if isinstance(v, (list, tuple)):
        return ",".join("" if x is None or x is UNSET else js_str(x) for x in v)
    return "[object Object]"


def _norm(v):
    # 整数值的浮点数按 JS 写成整数；NaN、无穷写成 null
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, float):
        if not math.isfinite(v):
            return None
        if v.is_integer() and abs(v) < 1e21:
            return int(v)
        return v
    if isinstance(v, dict):
        return {js_str(k) if not isinstance(k, str) else k: _norm(x) for k, x in v.items() if x is not UNSET}
    if isinstance(v, (list, tuple)):
        return [None if x is UNSET else _norm(x) for x in v]
    return v


def json_stringify(v):
    # JSON.stringify(v)：紧凑写法，中文原样
    return json.dumps(_norm(v), ensure_ascii=False, separators=(",", ":"))


def _parse_float(s):
    f = float(s)
    return int(f) if f.is_integer() and abs(f) < 1e21 else f


def _no_constant(name):
    raise ValueError(f"JSON 里不能写 {name}")


def json_parse(text):
    # JSON.parse：不接受 NaN、Infinity；1.0 与 1 相同（JS 不分整数和小数）
    return json.loads(js_str(text), parse_float=_parse_float, parse_constant=_no_constant)


_DEC = re.compile(r"[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?", re.ASCII)
_RADIX = re.compile(r"0([xXoObB])([0-9a-fA-F]+)", re.ASCII)


def js_number(v):
    # Number(v)：字符串去掉两端空白后按 JS 的规则读；读不出是 NaN
    if v is None:
        return 0
    if v is UNSET:
        return math.nan
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, (int, float)):
        return v
    if not isinstance(v, str):
        return math.nan
    s = v.strip()
    if s == "":
        return 0
    if _DEC.fullmatch(s):
        return _parse_float(s)
    if s in ("Infinity", "+Infinity"):
        return math.inf
    if s == "-Infinity":
        return -math.inf
    m = _RADIX.fullmatch(s)
    if m:
        base = {"x": 16, "o": 8, "b": 2}[m.group(1).lower()]
        try:
            return int(m.group(2), base)
        except ValueError:
            return math.nan
    return math.nan


def js_div(a, b):
    # 除以零得到 ±Infinity 或 NaN，与 JS 相同，不抛错
    if b == 0:
        if a == 0 or a != a:
            return math.nan
        return math.copysign(math.inf, a) * (math.copysign(1, b) if isinstance(b, float) else 1)
    return a / b
