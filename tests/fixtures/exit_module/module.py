"""测试用模块：直接结束自己所在的进程（模拟模块把进程搞崩）。"""
import os


def run(inputs: dict) -> dict:
    os._exit(3)
