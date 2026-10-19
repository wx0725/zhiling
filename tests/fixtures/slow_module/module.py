"""测试用模块：先睡一会儿，再写一个标记文件。用来证明超时后模块真的被结束了（标记文件不会出现）。"""
import time
from pathlib import Path


def run(inputs: dict) -> dict:
    time.sleep(inputs["seconds"])
    Path(inputs["marker"]).write_text("模块没有被结束，仍然跑完了", encoding="utf-8")
    return {"text": "done"}
