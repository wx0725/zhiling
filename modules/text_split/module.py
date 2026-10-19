from core.errors import ModuleError


def run(inputs: dict) -> dict:
    sep = inputs["separator"]
    if not sep:
        raise ModuleError("empty_separator", "分隔符不能为空")
    text = inputs["text"]
    if sep == "\n":  # 按行拆时兼容 Windows 换行
        text = text.replace("\r\n", "\n").replace("\r", "\n")
    items = text.split(sep)
    if inputs["trim"]:
        items = [s.strip() for s in items]
    if inputs["skip_empty"]:
        items = [s for s in items if s]
    return {"items": items}
