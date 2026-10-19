import re

from core.errors import ModuleError


def run(inputs: dict) -> dict:
    text, find, new = inputs["text"], inputs["find"], inputs["replace_with"]
    if find == "":
        raise ModuleError("empty_find", "要找的文字是空的，不知道替换哪里")
    flags = re.IGNORECASE if inputs["ignore_case"] else 0
    limit = 0 if inputs["all"] else 1
    if inputs["regex"]:
        try:
            out, n = re.subn(find, new, text, count=limit, flags=flags)
        except re.error as e:
            raise ModuleError("bad_pattern", f"正则表达式写得不对：{find}（{e}）") from None
        except IndexError as e:  # 例如 \1 引用了不存在的组（部分 Python 版本抛这个）
            raise ModuleError("bad_pattern", f"替换内容引用了不存在的分组：{new}（{e}）") from None
    elif not flags:
        n = text.count(find) if limit == 0 else min(text.count(find), 1)
        out = text.replace(find, new) if limit == 0 else text.replace(find, new, 1)
    else:  # 普通文字 + 不分大小写：按字面查找，替换内容也按字面写入
        out, n = re.subn(re.escape(find), lambda _m: new, text, count=limit, flags=flags)
    return {"text": out, "count": n}
