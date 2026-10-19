from core.errors import ModuleError
from core.text import to_text


def run(inputs: dict) -> dict:
    template, value = inputs["template"], inputs["value"]
    if value is None:  # 不接 value：输出固定文字
        return {"text": template}
    if "{value}" not in template:
        raise ModuleError("missing_placeholder", f"接了 value，但模板里没有 {{value}}：{template}")
    # 只替换 {value}，模板里的其他花括号原样保留
    return {"text": template.replace("{value}", to_text(value))}
