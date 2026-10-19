"""值 → 文字的统一规则（text.format、text.join 共用；模块之间不互相调用）。"""
import json


def to_text(v) -> str:
    if isinstance(v, bool):
        return "是" if v else "否"
    if v is None:
        return ""
    if isinstance(v, (list, dict)):
        return json.dumps(v, ensure_ascii=False)
    return str(v)
