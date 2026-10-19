import json
import unittest
import urllib.request

from core.errors import ModuleError
from modules.llm_think import module

DEFAULTS = {"text": "", "model": "", "temperature": 0.0, "max_chars": 20000}


def ollama_up() -> bool:
    try:
        urllib.request.urlopen(f"{module.OLLAMA_URL}/api/tags", timeout=2)
        return True
    except OSError:
        return False


def usable_model() -> str | None:
    """列表里出现不代表能用（模型文件可能缺失），逐个试一次最短调用。"""
    if not ollama_up():
        return None
    with urllib.request.urlopen(f"{module.OLLAMA_URL}/api/tags", timeout=5) as r:
        names = [m["name"] for m in json.loads(r.read())["models"]]
    for name in names:
        try:
            module.run({**DEFAULTS, "model": name, "instruction": "回答：1"})
            return name
        except ModuleError:
            continue
    return None


MODEL = usable_model()


class OfflineErrors(unittest.TestCase):
    def test_service_unavailable(self):
        old, module.OLLAMA_URL = module.OLLAMA_URL, "http://127.0.0.1:1"
        try:
            with self.assertRaises(ModuleError) as c:
                module.run({**DEFAULTS, "model": "any", "instruction": "hi"})
            self.assertEqual(c.exception.code, "service_unavailable")
        finally:
            module.OLLAMA_URL = old

    def test_no_model_anywhere(self):
        from unittest import mock
        with mock.patch.dict("os.environ", {"WORKFLOW_THINK_MODEL": ""}),                 mock.patch.object(module.llm, "think_model", return_value=""), self.assertRaises(ModuleError) as c:
            module.run({**DEFAULTS, "instruction": "hi"})
        self.assertEqual(c.exception.code, "model_not_found")


@unittest.skipUnless(MODEL, "本机 Ollama 没有可实际调用的模型")
class WithLocalModel(unittest.TestCase):
    def test_answers(self):
        r = module.run({**DEFAULTS, "model": MODEL, "instruction": "这段文字是什么语言？只回答语言名称，用中文",
                        "text": "Bonjour tout le monde"})
        self.assertIn("法", r["answer"])


@unittest.skipUnless(ollama_up(), "本机 Ollama 未运行")
class WithService(unittest.TestCase):
    def test_model_not_found(self):
        with self.assertRaises(ModuleError) as c:
            module.run({**DEFAULTS, "instruction": "hi", "model": "no-such-model:latest"})
        self.assertEqual(c.exception.code, "model_not_found")


if __name__ == "__main__":
    unittest.main()
