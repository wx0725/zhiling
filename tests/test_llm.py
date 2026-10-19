"""生成模型的来源（tools/llm.py）：本地 Ollama、云端 API、订阅命令行、选择的存取，以及 /api/models 接口。不真的联网或调用命令行。"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import server.app as appmod
import tools.generate as gen
from tools import llm

client = TestClient(appmod.app)


class FakeResp:
    def __init__(self, data):
        self.data = json.dumps(data).encode("utf-8")

    def read(self):
        return self.data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Settings(unittest.TestCase):
    """每个测试用自己的设置文件，不碰项目根目录的 model_settings.json；不真的去查 Ollama 和提供方的模型列表"""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.local = {}  # Ollama 地址 → 已装的模型
        self.saved = {}  # 凭据管理器里保存的密钥（不碰这台电脑真实的凭据管理器）
        for p in (mock.patch.object(llm, "SETTINGS", Path(d.name) / "model_settings.json"),
                  mock.patch.object(llm, "_CACHE", {}),
                  mock.patch.object(llm, "_listed", lambda *a: None),
                  mock.patch.object(llm, "ollama_models", lambda url: self.local.get(url, [])),
                  mock.patch.object(llm.keystore, "_read", lambda name: self.saved.get(name)),
                  mock.patch.dict(os.environ, {"OLLAMA_HOST": ""})):
            p.start()
            self.addCleanup(p.stop)

    def settings(self, **v):
        llm.SETTINGS.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")


class ExtractJson(unittest.TestCase):
    def test_plain_fenced_and_wrapped(self):
        self.assertEqual(llm.extract_json('{"a": 1}'), '{"a": 1}')
        self.assertEqual(json.loads(llm.extract_json('好的：\n```json\n{"a": 1}\n```\n')), {"a": 1})
        self.assertEqual(json.loads(llm.extract_json('结果是 {"a": {"b": 2}} 。')), {"a": {"b": 2}})

    def test_unparseable_returned_as_is(self):
        self.assertEqual(llm.extract_json("没有 JSON"), "没有 JSON")


class Api(Settings):
    def test_request_shape_and_answer(self):
        sent = {}

        def urlopen(req, timeout):
            sent.update(url=req.full_url, body=json.loads(req.data), auth=req.headers["Authorization"])
            return FakeResp({"choices": [{"message": {"content": '```json\n{"ok": true}\n```'}}]})
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "k123"}), mock.patch.object(llm.urllib.request, "urlopen", urlopen):
            text, _ = gen.chat("api://deepseek/deepseek-flash", [{"role": "user", "content": "hi"}], "json", 0.2, "http://ignored")
        self.assertEqual(json.loads(text), {"ok": True})
        self.assertEqual(sent["url"], "https://api.deepseek.com/chat/completions")
        self.assertEqual(sent["auth"], "Bearer k123")
        self.assertEqual(sent["body"]["model"], "deepseek-flash")
        self.assertEqual(sent["body"]["response_format"], {"type": "json_object"})
        self.assertEqual(sent["body"]["temperature"], 0.2)

    def test_schema_format_falls_back_to_json_mode(self):
        sent = {}

        def urlopen(req, timeout):
            sent.update(json.loads(req.data))
            return FakeResp({"choices": [{"message": {"content": "{}"}}]})
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "k"}), mock.patch.object(llm.urllib.request, "urlopen", urlopen):
            llm.chat("api://deepseek/deepseek-flash", [], {"type": "object"})
        self.assertEqual(sent["response_format"], {"type": "json_object"})

    def test_missing_key_is_os_error_naming_the_variable(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(OSError) as e:
            llm.chat("api://deepseek/deepseek-flash", [], "json")
        self.assertIn("DEEPSEEK_API_KEY", str(e.exception))
        self.assertEqual(getattr(e.exception, "category", ""), "auth")  # 缺密钥归为“认证失败”

    def test_unknown_provider(self):
        with self.assertRaises(llm.LLMError):
            llm.chat("api://nope/x", [], "json")

    def test_custom_provider_from_settings(self):
        self.settings(apis={"mine": {"base": "https://x.test/v1", "key_env": "MY_KEY", "models": ["m1"], "json_mode": False}})
        sent = {}

        def urlopen(req, timeout):
            sent.update(url=req.full_url, body=json.loads(req.data))
            return FakeResp({"choices": [{"message": {"content": "{}"}}]})
        with mock.patch.dict(os.environ, {"MY_KEY": "k"}), mock.patch.object(llm.urllib.request, "urlopen", urlopen):
            llm.chat("api://mine/m1", [], "json")
            self.assertIn("api://mine/m1", [c["id"] for c in llm.choices()])
        self.assertEqual(sent["url"], "https://x.test/v1/chat/completions")
        self.assertNotIn("response_format", sent["body"])  # 声明了不支持 JSON 模式


class Cli(Settings):
    def run_with(self, returncode=0, stdout="", stderr=""):
        calls = []

        def run(args, **kw):
            calls.append((args, kw))
            return mock.Mock(returncode=returncode, stdout=stdout, stderr=stderr)
        return calls, run

    def test_prompt_passed_as_argument_in_empty_dir(self):
        calls, run = self.run_with(stdout='好的\n{"ok": 1}\n')
        with mock.patch.object(llm.shutil, "which", return_value="C:/bin/kimi.exe"), mock.patch.object(llm.subprocess, "run", run):
            text, _ = llm.chat("cli://kimi", [{"role": "system", "content": "规则"}, {"role": "user", "content": "需求：x"}], "json")
        self.assertEqual(json.loads(text), {"ok": 1})
        args, kw = calls[0]
        self.assertEqual(args[0], "C:/bin/kimi.exe")
        self.assertEqual(args[1], "-p")
        self.assertIn("规则", args[2])
        self.assertIn("需求：x", args[2])
        self.assertNotEqual(Path(kw["cwd"]).resolve(), Path.cwd().resolve())  # 不在项目目录里跑
        self.assertIsNone(kw["input"])

    def test_stdin_cli(self):
        calls, run = self.run_with(stdout="{}")
        with mock.patch.object(llm.shutil, "which", return_value="codex"), mock.patch.object(llm.subprocess, "run", run):
            llm.chat("cli://codex", [{"role": "user", "content": "需求"}], "json")
        self.assertIn("需求", calls[0][1]["input"])

    def test_failure_mentions_login(self):
        _, run = self.run_with(returncode=1, stderr="error: Please run /login first")
        with mock.patch.object(llm.shutil, "which", return_value="kimi"), mock.patch.object(llm.subprocess, "run", run), \
                self.assertRaises(OSError) as e:
            llm.chat("cli://kimi", [], "json")
        self.assertIn("/login", str(e.exception))

    def test_not_installed(self):
        with mock.patch.object(llm.shutil, "which", return_value=None), self.assertRaises(llm.LLMError):
            llm.chat("cli://kimi", [], "json")


class Listing(unittest.TestCase):
    def setUp(self):
        p = mock.patch.object(llm, "_CACHE", {})
        p.start()
        self.addCleanup(p.stop)

    def test_listed_models_follow_defaults_and_skip_non_chat(self):
        calls = []

        def urlopen(req, timeout):
            calls.append(req.full_url)
            return FakeResp({"data": [{"id": "models/gemini-9-pro"}, {"id": "gemini-3.8-flash"}, {"id": "text-embedding-4"}]})
        spec = llm.api_specs()["gemini"]
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k"}), mock.patch.object(llm.urllib.request, "urlopen", urlopen):
            self.assertEqual(llm._models_of("gemini", spec), ["gemini-3.8-flash", "gemini-9-pro"])
            llm._models_of("gemini", spec)
        self.assertEqual(len(calls), 1)  # 查过就记住

    def test_listing_failure_or_no_key_uses_defaults(self):
        def urlopen(req, timeout):
            raise OSError("断了")
        specs = llm.api_specs()
        with mock.patch.dict(os.environ, {"XAI_API_KEY": "k"}), mock.patch.object(llm.urllib.request, "urlopen", urlopen):
            self.assertEqual(llm._models_of("grok", specs["grok"]), specs["grok"]["models"])
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(llm._models_of("zhipu", specs["zhipu"]), specs["zhipu"]["models"])

    def test_ollama_models_read_from_tags(self):
        def urlopen(req, timeout):
            self.assertEqual(req.full_url, "http://h:1/api/tags")
            return FakeResp({"models": [{"name": "qwen3:8b"}, {"name": "llama4"}]})
        with mock.patch.object(llm.urllib.request, "urlopen", urlopen):
            self.assertEqual(llm.ollama_models("http://h:1"), ["qwen3:8b", "llama4"])

        def down(req, timeout):
            raise OSError("没开")
        with mock.patch.object(llm.urllib.request, "urlopen", down):
            self.assertEqual(llm.ollama_models("http://h:2"), [])


class Local(Settings):
    def test_installed_models_become_choices(self):
        self.local = {"http://127.0.0.1:11434": ["qwen3:8b"], "http://127.0.0.1:11435": ["big:27b"]}
        self.settings(ollama=[{"url": "http://127.0.0.1:11435"}])
        ids = [c["id"] for c in llm.choices() if c["kind"] == "local"]
        self.assertEqual(ids, ["ollama://127.0.0.1:11434/qwen3:8b", "ollama://127.0.0.1:11435/big:27b"])
        self.assertEqual(llm.chosen(), "ollama://127.0.0.1:11434/qwen3:8b")  # 没选过：第一个能用的
        self.assertEqual(llm.label("ollama://127.0.0.1:11435/big:27b"), "本地 · big:27b")

    def test_ollama_server_error_retried_then_explained(self):
        """Ollama 返回 5xx：等一下重试一次；再失败就把 CUDA 这类错误翻成人话，原话附在后面"""
        import io
        import urllib.error
        from unittest import mock
        calls = []

        def boom(req, timeout=0):
            calls.append(req)
            raise urllib.error.HTTPError(req.full_url, 500, "err", {}, io.BytesIO(
                b'{"error":"an error was encountered while running the model: CUDA error: an illegal memory access"}'))
        with mock.patch.object(llm.urllib.request, "urlopen", boom), mock.patch.object(llm, "OLLAMA_RETRY_WAIT", 0):
            with self.assertRaises(llm.LLMError) as c:
                llm.chat_ollama("http://127.0.0.1:1", "big:27b", [{"role": "user", "content": "x"}], None)
        self.assertEqual(len(calls), 2)
        self.assertIn("在显卡上出错了", str(c.exception))
        self.assertIn("illegal memory", str(c.exception))
        with mock.patch.object(llm.urllib.request, "urlopen", lambda req, timeout=0: (_ for _ in ()).throw(
                urllib.error.HTTPError(req.full_url, 404, "nf", {}, io.BytesIO(b'{"error":"model not found"}')))):
            with self.assertRaises(llm.LLMError) as c:
                llm.chat_ollama("http://127.0.0.1:1", "x:1", [], None)
        self.assertIn("没有安装", str(c.exception))

    def test_task_timeouts_tiered(self):
        """单次传输超时按任务分级：可行性/理清/示例/审查 2 分钟、起草 5 分钟，其余默认；model_settings.json 可覆盖"""
        from unittest import mock
        from tools import protocol
        self.assertEqual(protocol.task_timeout("judge"), 120)
        self.assertEqual(protocol.task_timeout("draft"), 300)
        self.assertEqual(protocol.task_timeout("chat"), llm.TIMEOUT)
        with mock.patch.object(llm, "load_settings", return_value={"timeouts": {"draft": 30}}):
            self.assertEqual(protocol.task_timeout("draft"), 30)
        seen = {}

        def fake(url, model, messages, fmt, temperature=0, timeout=None, deadline=None):
            seen["timeout"] = timeout
            return "{}"
        with mock.patch.object(llm, "chat_ollama", fake), mock.patch.object(llm, "load_settings", return_value={}):
            protocol.send("ollama://h/m", [{"role": "user", "content": "x"}], "json", task="draft")
            self.assertEqual(seen["timeout"], 300)
            protocol.send("ollama://h/m", [{"role": "user", "content": "x"}], "json")
            self.assertEqual(seen["timeout"], llm.TIMEOUT)

    def test_ollama_host_env(self):
        with mock.patch.dict(os.environ, {"OLLAMA_HOST": "0.0.0.0:9999"}):
            self.assertEqual(llm.default_ollama(), "http://127.0.0.1:9999")
        self.assertEqual(llm.default_ollama(), "http://127.0.0.1:11434")

    def test_chat_goes_to_that_server(self):
        sent = {}

        def urlopen(req, timeout):
            sent.update(url=req.full_url, body=json.loads(req.data))
            return FakeResp({"message": {"content": "{}"}})
        with mock.patch.object(llm.urllib.request, "urlopen", urlopen):
            gen.chat("ollama://127.0.0.1:11435/big:27b", [], "json")
        self.assertEqual(sent["url"], "http://127.0.0.1:11435/api/chat")
        self.assertEqual(sent["body"]["model"], "big:27b")

    def test_timeout_passed_to_transport(self):
        """传输超时可由调用方指定（期限剩余时间），不给用默认 TIMEOUT"""
        seen = {}

        def urlopen(req, timeout):
            seen["timeout"] = timeout
            return FakeResp({"message": {"content": "{}"}})
        with mock.patch.object(llm.urllib.request, "urlopen", urlopen):
            llm.chat_ollama("http://h:1", "m", [], None, timeout=7)
            self.assertEqual(seen["timeout"], 7)
            llm.chat_ollama("http://h:1", "m", [], None)
            self.assertEqual(seen["timeout"], llm.TIMEOUT)

    def test_deadline_caps_timeout_and_blocks_when_expired(self):
        import time as _t
        seen = {}

        def urlopen(req, timeout):
            seen["timeout"] = timeout
            return FakeResp({"message": {"content": "{}"}})
        with mock.patch.object(llm.urllib.request, "urlopen", urlopen):
            llm.chat_ollama("http://h:1", "m", [], None, deadline=_t.monotonic() + 5)  # 剩余 5 秒 < 单次上限
            self.assertLessEqual(seen["timeout"], 5)
        with self.assertRaises(llm.LLMError) as c:  # 已过期限：不再发起请求
            llm.chat_ollama("http://h:1", "m", [], None, deadline=_t.monotonic() - 1)
        self.assertEqual(c.exception.category, "deadline")

    def test_nothing_available(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(llm.shutil, "which", return_value=None):
            self.assertIsNone(llm.chosen())
            with self.assertRaises(OSError) as e:
                gen.resolve(None)
            self.assertIn("Ollama", str(e.exception))

    def test_think_model(self):
        self.local = {"http://127.0.0.1:11434": ["a:1", "b:2"]}
        self.assertEqual(llm.think_model(), "a:1")
        self.settings(think_model="b:2")
        self.assertEqual(llm.think_model(), "b:2")

    def test_start_only_configured_servers_with_start(self):
        self.settings(ollama=[{"url": "http://127.0.0.1:11435", "start": {"OLLAMA_MODELS": "X:/m"}},
                              {"url": "http://127.0.0.1:11436"}])
        started = []

        def down(req, timeout):
            raise OSError("没开")
        with mock.patch.object(llm, "ollama_exe", return_value="ollama"), \
                mock.patch.object(llm.urllib.request, "urlopen", down), \
                mock.patch.object(llm.subprocess, "Popen", lambda args, env, **kw: started.append(env)):
            llm.start_ollama_servers()
        self.assertEqual(len(started), 1)
        self.assertEqual((started[0]["OLLAMA_HOST"], started[0]["OLLAMA_MODELS"]), ("127.0.0.1:11435", "X:/m"))


class JsonModeFallback(Settings):
    def test_retry_without_json_mode_on_400(self):
        bodies = []

        def urlopen(req, timeout):
            body = json.loads(req.data)
            bodies.append(body)
            if "response_format" in body:
                raise llm.urllib.error.HTTPError(req.full_url, 400, "bad", {}, None)
            return FakeResp({"choices": [{"message": {"content": '{"ok": 1}'}}]})
        with mock.patch.dict(os.environ, {"MIMO_API_KEY": "k"}), mock.patch.object(llm.urllib.request, "urlopen", urlopen):
            text, _ = llm.chat("api://mimo/mimo-v2.6-flash", [], "json")
        self.assertEqual(json.loads(text), {"ok": 1})
        self.assertEqual(len(bodies), 2)


class Choice(Settings):
    def test_saved_choice(self):
        self.local = {"http://127.0.0.1:11434": ["qwen3:8b"]}
        llm.save_choice("cli://kimi")
        self.assertEqual(llm.chosen(), "cli://kimi")
        llm.save_choice("api://gone/x")  # 已不存在的选择：退回第一个能用的
        self.assertEqual(llm.chosen(), "ollama://127.0.0.1:11434/qwen3:8b")
        llm.save_choice("api://grok/grok-new")  # 查到的列表会变：提供方还在就有效
        self.assertEqual(llm.chosen(), "api://grok/grok-new")

    def test_mainstream_providers_listed_and_no_personal_defaults(self):
        c = llm.choices()
        names = " ".join(x["name"] for x in c)
        for n in ("MiMo", "智谱", "豆包", "Grok", "Gemini", "DeepSeek", "Kimi"):
            self.assertIn(n, names)
        self.assertFalse([x for x in c if x["kind"] == "local"])  # 本地的只来自实际装了的模型
        self.assertTrue(all(x["id"] for x in c))

    def test_ready_follows_key_and_cli(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(llm.shutil, "which", return_value=None):
            c = {x["id"]: x for x in llm.choices()}
        self.assertFalse(c["api://deepseek/deepseek-flash"]["ready"])
        self.assertIn("配置 AI", c["api://deepseek/deepseek-flash"]["note"])
        self.assertFalse(c["cli://kimi"]["ready"])

    def test_key_saved_in_keystore_counts(self):
        self.saved = {"DEEPSEEK_API_KEY": "sk-saved"}
        with mock.patch.dict(os.environ, {}, clear=True):
            c = {x["id"]: x for x in llm.choices()}
            self.assertTrue(c["api://deepseek/deepseek-flash"]["ready"])
            self.assertEqual(llm.api_key(llm.api_specs()["deepseek"]), "sk-saved")
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-env"}):  # 界面里保存的优先于环境变量
            self.assertEqual(llm.api_key(llm.api_specs()["deepseek"]), "sk-saved")
        self.saved = {}
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-env"}):  # 没保存过就用环境变量
            self.assertEqual(llm.api_key(llm.api_specs()["deepseek"]), "sk-env")

    def test_model_config_uses_choice(self):
        llm.save_choice("api://deepseek/deepseek-flash")
        cfg = appmod.model_config()
        self.assertEqual((cfg["generator"], cfg["judge"]), ("api://deepseek/deepseek-flash",) * 2)
        self.assertEqual(llm.label("api://deepseek/deepseek-flash"), "DeepSeek · deepseek-flash")

    def test_models_endpoint(self):
        self.local = {"http://127.0.0.1:11434": ["qwen3:8b"]}
        r = client.get("/api/models")
        self.assertEqual(r.json()["chosen"], "ollama://127.0.0.1:11434/qwen3:8b")
        r = client.put("/api/models", json={"model": "cli://kimi"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(client.get("/api/models").json()["chosen"], "cli://kimi")
        self.assertEqual(client.put("/api/models", json={"model": "api://nope/x"}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
