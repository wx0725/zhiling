"""首次启动引导的后端（tools/setup_ai.py、tools/keystore.py、/api/setup/*）：
推荐在各种电脑上（外接显卡坞、只有独显、只有集显、Apple 芯片、内存或磁盘不够）选得对不对，连接测试的失败归类，
能力测试打分，保存选择和密钥，安装进度。不联网、不读这台电脑的真实硬件、不碰真实的凭据管理器和设置文件。"""
import io
import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient

import server.app as appmod
from tools import keystore, llm, setup_ai

GIB = 1 << 30
CATALOG = json.loads((setup_ai.CATALOG).read_text(encoding="utf-8"))["models"]
client = TestClient(appmod.app)


def gpu(name, kind, vram, vendor="NVIDIA", ok=True):
    return {"name": name, "kind": kind, "vram": vram, "vendor": vendor, "ok": ok, "shared": kind == "integrated"}


def info(ram=32, disk=500, arch="AMD64"):
    return {"ram": ram * GIB, "ram_free": ram * GIB // 2, "disk_free": None if disk is None else disk * GIB, "arch": arch}


class Isolated(unittest.TestCase):
    """自己的设置文件、自己的“凭据管理器”"""

    def setUp(self):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        self.saved = {}
        for p in (mock.patch.object(llm, "SETTINGS", Path(d.name) / "model_settings.json"),
                  mock.patch.object(llm, "_CACHE", {}),
                  mock.patch.object(keystore, "_read", lambda name: self.saved.get(name)),
                  mock.patch.object(keystore, "put", lambda name, v: self.saved.__setitem__(name, v))):
            p.start()
            self.addCleanup(p.stop)


class Recommend(unittest.TestCase):
    def tiers(self, gpus, inf, has_ollama=True):
        r = setup_ai.recommend(gpus, inf, CATALOG, has_ollama)
        return {f["tier"]: f for f in r["fits"]}, r

    def test_dock_plus_laptop_gpu(self):
        t, _ = self.tiers([gpu("外接卡", "external", 24 * GIB), gpu("笔记本卡", "discrete", 8 * GIB),
                           gpu("集显", "integrated", None, "Intel")], info())
        self.assertEqual(list(t), ["资源占用较低", "综合平衡", "能力优先"])
        self.assertIn("拔掉外接显卡也能用", t["资源占用较低"]["why"])
        self.assertIn("笔记本卡", t["资源占用较低"]["why"])
        self.assertIn("需要接着这块外接显卡", t["能力优先"]["why"])
        self.assertLessEqual(t["能力优先"]["vram"], 24)
        self.assertLessEqual(t["资源占用较低"]["vram"], 8)

    def test_single_8gb_card(self):
        t, _ = self.tiers([gpu("8G 卡", "discrete", 8 * GIB)], info())
        self.assertTrue(all(f["vram"] <= 8 for f in t.values()))
        self.assertNotIn("外接", " ".join(f["why"] for f in t.values()))
        self.assertEqual(len({f["tag"] for f in t.values()}), len(t))  # 不重复推荐同一个

    def test_integrated_only_falls_back_to_cpu(self):
        t, _ = self.tiers([gpu("集显", "integrated", None, "Intel")], info(ram=16))
        self.assertTrue(t)
        self.assertTrue(all("只用处理器运行" in f["why"] for f in t.values()))
        self.assertTrue(all(f["ram"] <= 16 for f in t.values()))

    def test_low_memory_no_gpu(self):
        t, r = self.tiers([gpu("集显", "integrated", None, "Intel")], info(ram=4))
        self.assertFalse(t)
        self.assertTrue(any("内存只有" in x for x in r["reasons"]))
        self.assertTrue(any("独立显卡" in x for x in r["reasons"]))

    def test_disk_full(self):
        t, r = self.tiers([gpu("24G 卡", "discrete", 24 * GIB)], info(disk=2))
        self.assertFalse(t)
        self.assertTrue(any("磁盘" in x for x in r["reasons"]))

    def test_disk_limits_size(self):
        t, _ = self.tiers([gpu("24G 卡", "discrete", 24 * GIB)], info(disk=12))
        self.assertTrue(all(f["download"] + 1 <= 12 for f in t.values()))

    def test_broken_gpu_ignored(self):
        t, _ = self.tiers([gpu("坏卡", "discrete", 24 * GIB, ok=False)], info(ram=16))
        self.assertTrue(all("只用处理器运行" in f["why"] for f in t.values()))

    def test_apple_unified_memory(self):
        t, _ = self.tiers([gpu("Apple M3", "integrated", None, "Apple")], info(ram=36, arch="arm64"))
        self.assertTrue(t)
        self.assertTrue(all(f["vram"] <= 36 * 0.65 for f in t.values()))

    def test_unknown_disk_and_needs_ollama(self):
        t, _ = self.tiers([gpu("12G 卡", "discrete", 12 * GIB)], info(disk=None), has_ollama=False)
        self.assertTrue(t)
        self.assertTrue(all(f["needs_ollama"] and f["install_dir"] for f in t.values()))


class TestApi(Isolated):
    def http_error(self, code, body=""):
        return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(body.encode()))

    def test_success_lists_models(self):
        with mock.patch.object(setup_ai, "_request", return_value={"data": [{"id": "deepseek-flash"}, {"id": "x-embed"},
                                                                             {"id": "deepseek-new"}]}):
            r = setup_ai.test_api("deepseek", "sk-1")
        self.assertEqual(r, {"ok": True, "models": ["deepseek-flash", "deepseek-new"]})

    def test_failures_are_classified(self):
        for err, code in ((self.http_error(401), "key"), (self.http_error(403), "key"),
                          (self.http_error(402), "quota"), (self.http_error(429, "Insufficient balance"), "quota"),
                          (self.http_error(500), "unavailable"), (urllib.error.URLError("dns"), "network"),
                          (TimeoutError("slow"), "network")):
            with self.subTest(code=code), mock.patch.object(setup_ai, "_request", side_effect=err):
                self.assertEqual(setup_ai.test_api("deepseek", "sk-1")["code"], code)

    def test_no_models_endpoint_falls_back_to_chat(self):
        calls = []

        def fake(url, key=None, body=None, timeout=15):
            calls.append(url)
            if url.endswith("/models"):
                raise self.http_error(404)
            return {}
        with mock.patch.object(setup_ai, "_request", side_effect=fake):
            r = setup_ai.test_api("deepseek", "sk-1")
        self.assertTrue(r["ok"])
        self.assertTrue(calls[-1].endswith("/chat/completions"))

    def test_empty_key_uses_saved_one(self):
        self.saved = {"DEEPSEEK_API_KEY": "sk-saved"}
        seen = []
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(setup_ai, "_request", side_effect=lambda u, key=None, **k: seen.append(key) or {}):
            setup_ai.test_api("deepseek", "")
        self.assertEqual(seen[0], "sk-saved")
        self.saved.clear()
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(setup_ai.test_api("deepseek", "")["code"], "key")

    def test_unknown_provider(self):
        self.assertFalse(setup_ai.test_api("nope", "k")["ok"])
        self.assertFalse(setup_ai.test_api("lmstudio", "k")["ok"])  # 本机服务不走这里


class Probe(unittest.TestCase):
    def chat_with(self, answers):
        it = iter(answers)

        def chat(model, messages, fmt):
            a = next(it)
            if isinstance(a, Exception):
                raise a
            return a, 0.1
        return chat

    def right(self):
        return [json.dumps({"modules": list(must)}) for _, must, _ in setup_ai.PROBE_CASES]

    def test_order_and_duplicates_count(self):
        must = ("read_file", "translate", "write_file")
        self.assertTrue(setup_ai.probe_answer_right(["read_file", "translate", "write_file"], must, set()))
        self.assertFalse(setup_ai.probe_answer_right(["write_file", "translate", "read_file"], must, set()))  # 顺序反了
        self.assertFalse(setup_ai.probe_answer_right(["read_file", "read_file", "translate", "write_file"], must, set()))
        self.assertFalse(setup_ai.probe_answer_right(["read_file", "translate", "write_file", "fetch_url"], must,
                                                     {"fetch_url"}))

    def test_wrong_order_is_not_pass(self):
        answers = [json.dumps({"modules": list(reversed(must))}) for _, must, _ in setup_ai.PROBE_CASES]
        self.assertEqual(setup_ai.probe("m", chat=self.chat_with(answers))["result"], "unstable")

    def test_all_right_passes(self):
        r = setup_ai.probe("m", chat=self.chat_with(self.right()))
        self.assertEqual((r["result"], r["score"]), ("pass", "5/5"))

    def test_wrong_picks_unstable(self):
        answers = self.right()
        answers[1] = json.dumps({"modules": ["fetch_url"]})
        answers[2] = "我觉得应该先读表格"  # 格式不对
        answers[3] = json.dumps({"modules": ["read_file", "summarize", "send_notice", "made_up"]})  # 编造模块
        r = setup_ai.probe("m", chat=self.chat_with(answers))
        self.assertEqual(r["result"], "unstable")
        self.assertIn("格式不对", r["detail"])
        self.assertIn("选错", r["detail"])

    def test_cannot_start_is_fail(self):
        r = setup_ai.probe("m", chat=self.chat_with([llm.LLMError("model requires more system memory")]))
        self.assertEqual(r["result"], "fail")
        self.assertIn("memory", r["detail"])

    def test_run_test(self):
        self.assertTrue(setup_ai.run_test("m", chat=self.chat_with(["你好"]))["ok"])
        self.assertFalse(setup_ai.run_test("m", chat=self.chat_with(["  "]))["ok"])
        self.assertFalse(setup_ai.run_test("m", chat=self.chat_with([OSError("连不上")]))["ok"])


class SaveAndState(Isolated):
    def test_save_api_key_goes_to_keystore_not_settings(self):
        self.assertFalse(setup_ai.done())
        setup_ai.save("api://deepseek/deepseek-flash", "sk-secret")
        self.assertEqual(self.saved["DEEPSEEK_API_KEY"], "sk-secret")
        text = llm.SETTINGS.read_text(encoding="utf-8")
        self.assertNotIn("sk-secret", text)
        self.assertEqual(json.loads(text)["model"], "api://deepseek/deepseek-flash")
        self.assertTrue(setup_ai.done())

    def test_save_rejects_bad_model_and_key_for_non_api(self):
        with self.assertRaises(ValueError):
            setup_ai.save("api://nope/x")
        with self.assertRaises(ValueError):
            setup_ai.save("cli://kimi", "sk-1")
        self.assertFalse(setup_ai.done())

    def test_endpoints(self):
        self.assertEqual(client.get("/api/health").json(), {"ok": True})
        self.assertEqual(client.get("/api/setup/state").json(), {"done": False})
        self.assertEqual(client.post("/api/setup/finish").json(), {"done": True})
        self.assertEqual(client.get("/api/setup/state").json(), {"done": True})
        p = client.get("/api/setup/providers").json()
        self.assertIn("deepseek", [a["id"] for a in p["apis"]])
        self.assertNotIn("lmstudio", [a["id"] for a in p["apis"]])
        self.assertTrue(all(a["console"].startswith("https://") for a in p["apis"]))
        self.assertEqual(client.post("/api/setup/save", json={"model": "api://nope/x"}).status_code, 400)
        r = client.post("/api/setup/save", json={"model": "api://deepseek/deepseek-flash", "key": "sk-2"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.saved["DEEPSEEK_API_KEY"], "sk-2")
        self.assertEqual(client.post("/api/setup/install", json={"tag": "not-in-catalog"}).status_code, 400)

    def test_health_works_without_any_model(self):
        with mock.patch.object(llm, "chosen", return_value=None):
            self.assertEqual(client.get("/api/health").status_code, 200)


class Install(Isolated):
    def pull_lines(self):
        return [json.dumps(x).encode() + b"\n" for x in (
            {"status": "pulling manifest"},
            {"status": "pulling abc", "digest": "sha256:a", "total": 100, "completed": 50},
            {"status": "pulling abc", "digest": "sha256:a", "total": 100, "completed": 100},
            {"status": "verifying sha256 digest"}, {"status": "writing manifest"}, {"status": "success"})]

    def test_install_reports_steps(self):
        events = []

        class Resp(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        with mock.patch.object(llm, "ollama_exe", return_value="ollama"), \
                mock.patch.object(setup_ai, "ensure_ollama", return_value=True), \
                mock.patch.object(setup_ai.urllib.request, "urlopen", return_value=Resp(b"".join(self.pull_lines()))), \
                mock.patch.object(llm, "ollama_models", return_value=["qwen3:8b"]):
            setup_ai.install({"tag": "qwen3:8b"}, events.append, lambda: False)
        steps = [e["step"] for e in events]
        self.assertEqual(steps[0], "runtime")
        self.assertIn(50, [e["pct"] for e in events if e["step"] == "download"])
        self.assertIn({"step": "verify", "pct": 100, "text": "校验通过"}, events)
        self.assertTrue(events[-1]["model"].endswith("/qwen3:8b"))

    def test_cancel_and_missing_runtime(self):
        with mock.patch.object(llm, "ollama_exe", return_value="ollama"), \
                mock.patch.object(setup_ai, "ensure_ollama", return_value=True):
            with self.assertRaises(setup_ai.Cancelled):
                setup_ai.install({"tag": "qwen3:8b"}, lambda e: None, lambda: True)
        with mock.patch.object(llm, "ollama_exe", return_value="ollama"), \
                mock.patch.object(setup_ai, "ensure_ollama", return_value=False):
            with self.assertRaises(RuntimeError):
                setup_ai.install({"tag": "qwen3:8b"}, lambda e: None, lambda: False)

    def test_only_one_install_at_a_time(self):
        setup_ai._INSTALLING.acquire()
        try:
            with self.assertRaises(RuntimeError) as cm:
                setup_ai.install({"tag": "qwen3:8b"}, lambda e: None, lambda: False)
            self.assertIn("还没有结束", str(cm.exception))
        finally:
            setup_ai._INSTALLING.release()
        with mock.patch.object(setup_ai, "_install", side_effect=setup_ai.Cancelled):
            with self.assertRaises(setup_ai.Cancelled):
                setup_ai.install({"tag": "qwen3:8b"}, lambda e: None, lambda: True)
        self.assertTrue(setup_ai._INSTALLING.acquire(blocking=False))  # 出错或取消后锁会放开
        setup_ai._INSTALLING.release()

    def test_bad_installer_signature_stops(self):
        with mock.patch.object(llm, "ollama_exe", return_value=None), mock.patch.object(setup_ai.os, "name", "nt"), \
                mock.patch.object(setup_ai, "_download"), mock.patch.object(setup_ai, "_signed_by", return_value=False), \
                mock.patch.object(setup_ai.subprocess, "run") as run:
            with self.assertRaises(RuntimeError) as cm:
                setup_ai.install({"tag": "qwen3:8b"}, lambda e: None, lambda: False)
        self.assertIn("数字签名", str(cm.exception))
        run.assert_not_called()  # 签名不对就不运行安装程序


class Signature(unittest.TestCase):
    def test_exact_signer_only(self):
        self.assertTrue(setup_ai.signed_as("Ollama Inc.\nOllama Inc.\n", "Ollama Inc."))
        self.assertTrue(setup_ai.signed_as('Ollama Inc.\n"Ollama Inc."\n', "Ollama Inc."))
        self.assertFalse(setup_ai.signed_as("Evil Corp\nOllama compatibility tools\n", "Ollama Inc."))
        self.assertFalse(setup_ai.signed_as("Ollama Inc. Fake\nOllama Inc.\n", "Ollama Inc."))
        self.assertFalse(setup_ai.signed_as("", "Ollama Inc."))
        self.assertFalse(setup_ai.signed_as("x\nx\n", ""))

    def test_cancel_after_signature_check_does_not_run_installer(self):
        stops = iter([True])  # 下载（替身）不问取消；查完签名时已经取消了
        with mock.patch.object(llm, "ollama_exe", return_value=None), mock.patch.object(setup_ai.os, "name", "nt"), \
                mock.patch.object(setup_ai, "_download"), mock.patch.object(setup_ai, "_signed_by", return_value=True), \
                mock.patch.object(setup_ai.subprocess, "run") as run:
            with self.assertRaises(setup_ai.Cancelled):
                setup_ai._install_ollama(setup_ai.catalog()["runtime"], lambda e: None, lambda: next(stops))
        run.assert_not_called()


class Keystore(unittest.TestCase):
    def test_saved_wins_then_env_and_missing_is_empty(self):
        with mock.patch.object(keystore, "_read", return_value="saved"), mock.patch.dict(os.environ, {"X_KEY": "env"}):
            self.assertEqual(keystore.get("X_KEY"), "saved")  # 界面里新填的 Key 才是要用的，旧环境变量不能盖过它
        with mock.patch.object(keystore, "_read", return_value=None), mock.patch.dict(os.environ, {"X_KEY": "env"}):
            self.assertEqual(keystore.get("X_KEY"), "env")
        with mock.patch.object(keystore, "_read", return_value="saved"), mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(keystore.get("X_KEY"), "saved")
        with mock.patch.object(keystore, "_read", side_effect=OSError), mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(keystore.get("X_KEY"), "")
        self.assertEqual(keystore.get(""), "")

    def test_put_rejects_empty(self):
        with self.assertRaises(keystore.KeystoreError):
            keystore.put("X_KEY", "  ")


if __name__ == "__main__":
    unittest.main()
