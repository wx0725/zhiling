"""提醒确认：弹窗、等回答这件事由执行引擎和界面完成（engine 发 ask 事件、等界面把回答填进 answer）；
这里只负责把回答整理成输出。没有回答（命令行、运行检查）时按“点了第一个按钮、什么都没勾、没写备注”处理。"""
from core.errors import ModuleError


def run(inputs: dict) -> dict:
    options = [str(o) for o in (inputs.get("options") or [])]
    if not options:
        raise ModuleError("no_options", "options 是空列表，没有按钮可按")
    items = [str(x) for x in (inputs.get("items") or [])]
    answer = inputs.get("answer") if isinstance(inputs.get("answer"), dict) else {}
    choice = answer.get("choice")
    choice = str(choice) if isinstance(choice, str) and choice in options else options[0]
    picked = {str(p) for p in (answer.get("done") if isinstance(answer.get("done"), list) else [])}
    done = [x for x in items if x in picked]  # 按清单顺序，只认清单里有的
    note = answer.get("note")
    return {"choice": choice, "approved": choice == options[0], "done": done,
            "note": note.strip() if isinstance(note, str) else ""}
