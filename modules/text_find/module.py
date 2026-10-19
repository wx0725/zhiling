import re

from core.errors import ModuleError

PRESETS = {
    "email": r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}",
    "url": r"https?://[^\s<>\"'，。；、）)]+",
    "number": r"-?\d+(?:\.\d+)?",
    "phone": r"(?<!\d)1[3-9]\d{9}(?!\d)",
}


def run(inputs: dict) -> dict:
    pattern = inputs["pattern"]
    flags = re.IGNORECASE if inputs["ignore_case"] else 0
    try:
        rx = re.compile(PRESETS.get(pattern.strip().lower(), pattern), flags)
    except re.error as e:
        raise ModuleError("invalid_pattern", f"正则表达式有误：{e}") from None
    found = [m.group(0) for m in rx.finditer(inputs["text"]) if m.group(0)]
    if inputs["unique"]:
        found = list(dict.fromkeys(found))
    return {"matches": found, "count": len(found)}
