import re
from datetime import datetime, timedelta

from core.errors import ModuleError

WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")
PRESETS = {"date": "%Y-%m-%d", "datetime": "%Y-%m-%d %H:%M:%S", "time": "%H:%M:%S",
           "chinese": "%Y年%m月%d日", "compact": "%Y%m%d"}
_ALLOWED = set("YmdHMSyjIpABab")  # 允许的 strftime 代码（各系统都一样的那些）
_TOKEN = re.compile(r"%(.)", re.S)


def _now() -> datetime:
    return datetime.now()  # 测试时替换成固定时间


def _format(dt: datetime, fmt: str) -> str:
    """只替换 %X 代码，其余文字原样保留：中文等字符不经过系统的 strftime，各系统结果一致。"""
    def sub(m):
        c = m.group(1)
        if c == "%":
            return "%"
        if c not in _ALLOWED:
            raise ModuleError("invalid_format", f"格式里的 %{c} 不认识；可用 %Y 年 %m 月 %d 日 %H 时 %M 分 %S 秒 等")
        return dt.strftime("%" + c)
    if fmt.endswith("%") and not fmt.endswith("%%"):
        raise ModuleError("invalid_format", "格式最后多了一个 %")
    return _TOKEN.sub(sub, fmt)


def run(inputs: dict) -> dict:
    fmt = inputs["format"].strip() or "datetime"
    if fmt in PRESETS:
        fmt = PRESETS[fmt]
    elif "%" not in fmt:
        raise ModuleError("invalid_format", f"format 只能是 {' / '.join(PRESETS)}，或含 % 的自定义格式（例如 %Y年%m月），收到 {fmt!r}")
    try:
        dt = _now() + timedelta(days=inputs["offset_days"])
    except OverflowError:
        raise ModuleError("bad_offset", f"偏移 {inputs['offset_days']} 天超出了能表示的日期范围") from None
    return {"text": _format(dt, fmt), "date": f"{dt.year:04d}-{dt.month:02d}-{dt.day:02d}", "year": dt.year, "month": dt.month,
            "day": dt.day, "weekday": WEEKDAYS[dt.weekday()]}
