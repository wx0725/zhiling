import copy
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

import tools.generate as gen
import tools.protocol as protocol
import tools.semantic as sem
from core.registry import ROOT
from tests.test_engine import _Pages, fake_think


def example(name):
    return json.loads((ROOT / "examples" / f"{name}.workflow.json").read_text(encoding="utf-8"))


class FakeChat:
    """按顺序返回预设回答，并记下每次收到的提示。"""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, model, messages, fmt, temperature=0, url=None):
        self.calls.append(messages)
        return json.dumps(self.answers.pop(0), ensure_ascii=False), 0.1


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), _Pages)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.ok, cls.gone = f"http://127.0.0.1:{cls.server.server_port}/ok", f"http://127.0.0.1:{cls.server.server_port}/gone"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def run_check(self, fake, wf, requirement="需求"):
        with mock.patch.object(sem, "chat", fake):
            check = sem.make_checker(requirement, "judge", engine_kwargs={"overrides": {"llm.think@0": fake_think}})
            return check(wf)


class Checker(Base):
    def wrong_graph(self):
        """L3 式错误：把网址而不是网页内容交给总结节点。"""
        wf = example("summarize_many")["nodes"][0]["body"]
        wf = {"spec": "0.2", "id": "l3", "name": "x", "intent": "", "inputs": {"url": {"type": "string"}},
              "outputs": {"summary": {"type": "string"}}, "nodes": copy.deepcopy(wf["nodes"]),
              "edges": [{"from": "$in.url" if e["from"] == "$in.item" else e["from"],
                         "to": "$out.summary" if e["to"] == "$out.result" else e["to"]} for e in wf["edges"]]}
        for e in wf["edges"]:
            if e["to"] == "branch.value":
                e["from"] = "$in.url"
        return wf

    def test_trace_shows_url_reaching_summarizer_and_judge_problem_is_returned(self):
        fake = FakeChat({"samples": [{"url": self.ok}, {"url": self.gone}]},
                        {"ok": False, "problems": [{"node": "summarize", "problem": "收到的是网址", "fix": "fetch.text 接 branch.value"}]})
        problems, log = self.run_check(fake, self.wrong_graph())
        self.assertEqual(problems, ["节点 summarize：收到的是网址；改法：fetch.text 接 branch.value"])
        summarize = next(r for r in log["runs"][0]["records"] if r["path"].endswith(".summarize"))
        self.assertEqual(summarize["inputs"]["text"], self.ok)          # 运行记录里能看到错误的数据
        self.assertIn(self.ok, fake.calls[1][1]["content"])             # 审查模型看到了运行记录

    def test_ok_verdict_means_no_problems(self):
        fake = FakeChat({"samples": [{"url": self.ok}]}, {"ok": True, "problems": [{"node": "x", "problem": "措辞可以更好"}]})
        problems, log = self.run_check(fake, example("summarize_page"))
        self.assertEqual(problems, [])
        self.assertTrue(log["verdict"]["ok"])

    def test_invalid_samples_filtered_then_retried(self):
        fake = FakeChat({"samples": [{"link": self.ok}, {"url": 5}]},   # 名字不对、类型不对
                        {"samples": [{"url": self.ok}]},
                        {"ok": True, "problems": []})
        problems, log = self.run_check(fake, example("summarize_page"))
        self.assertEqual(log["samples"], [{"url": self.ok}])

    def test_runtime_failure_unrelated_to_graph_is_inconclusive(self):
        # 模型编的示例网址用错协议（ftp://）：运行失败但与图无关，记无结论，不逼着删步骤、也不当通过
        fake = FakeChat({"samples": [{"url": "ftp://nope"}]})
        problems, log = self.run_check(fake, example("summarize_page"))
        self.assertEqual(problems, [])
        self.assertEqual(log["status"], "inconclusive")
        self.assertIn("invalid_url", log["reason"])
        self.assertEqual(len(fake.calls), 1)  # 没有调用审查模型

    def test_environment_codes_split_outside_from_graph(self):
        env = sem.environment_codes(gen.load_registry())
        for c in ("network", "timeout", "invalid_url", "module_crash", "not_confirmed"):
            self.assertIn(c, env)                       # 抓取失败/超时/崩溃等 → 无结论
        for c in ("invalid_op", "type_mismatch", "type_error", "missing_input", "merge_empty"):
            self.assertNotIn(c, env)                    # 逻辑配置错、校验/类型/结构错 → 该修图

    def test_partial_runs_downgrade_pass_to_inconclusive(self):
        # 一组样例跑通、一组因环境原因（假协议网址）没跑通：审查即便说没问题，也只是局部结果，不算完整验证
        fake = FakeChat({"samples": [{"url": self.ok}, {"url": "ftp://nope"}]}, {"ok": True, "problems": []})
        problems, log = self.run_check(fake, example("summarize_page"))
        self.assertEqual((problems, log["status"]), ([], "inconclusive"))
        self.assertIn("invalid_url", log["reason"])
        self.assertTrue(log["verdict"]["ok"])            # 审查确实说了没问题
        self.assertEqual(log["inconclusive_runs"], [m for m in log["inconclusive_runs"] if "invalid_url" in m])

    def test_judge_prompt_scoped_and_within_budget_for_batch_graph(self):
        # “批量抓取并总结”这种 4-5 节点的图，审查提示要只带图里用到的模块、裁短运行记录，落在本地 6000 预算内
        wf = example("summarize_many")
        fake = FakeChat({"samples": [{"urls": [self.ok, self.ok]}]}, {"ok": True, "problems": []})
        problems, log = self.run_check(fake, wf, requirement="给我一组网址，每个输出一句中文总结")
        self.assertEqual((problems, log["status"]), ([], "passed"))
        judge_msg = fake.calls[-1]  # 第二次调用就是审查
        sys_text = judge_msg[0]["content"]
        self.assertIn("web.fetch@0", sys_text)         # 只列图里用到的模块（web.fetch / llm.think / logic.compare）
        self.assertIn("llm.think@0", sys_text)
        self.assertNotIn("table.read@0", sys_text)     # 没用到的模块不列
        tokens = protocol.estimate_tokens("".join(m["content"] for m in judge_msg))
        self.assertLess(tokens, protocol.LOCAL_INPUT_TOKENS)  # < 6000，不会 context_exhausted

    def test_no_inputs_runs_once(self):
        wf = example("summarize_page")
        wf["inputs"] = {}
        wf["nodes"][0]["config"] = {"url": self.ok}
        wf["edges"] = [e for e in wf["edges"] if e["from"] != "$in.url"]
        fake = FakeChat({"ok": True, "problems": []})
        problems, log = self.run_check(fake, wf)
        self.assertEqual((problems, log["samples"]), ([], [{}]))


class GenerateLoop(Base):
    def test_check_problems_fed_back_and_fixed(self):
        good = example("summarize_page")
        bad = copy.deepcopy(good)
        bad["nodes"][1]["config"]["instruction"] = "随便写点什么"
        gen_chat = FakeChat(bad, good)
        verdicts = iter([(["节点 think：指令与需求无关；改法：改成总结网页"], {"n": 1}), ([], {"n": 2})])
        with mock.patch.object(gen, "chat", gen_chat):
            res = gen.generate("给我一个网址，三句话总结", "m", rounds=3, feasibility=False, post_check=lambda wf: next(verdicts))
        self.assertTrue(res["ok"])
        self.assertEqual(res["rounds"], 2)
        self.assertIn("指令与需求无关", gen_chat.calls[1][-1]["content"])  # 问题被反馈给生成模型
        self.assertIn("涉及模块的完整说明", gen_chat.calls[1][-1]["content"])  # 修正轮附上涉及模块的完整说明
        self.assertEqual([l.get("check") for l in res["log"]], [{"n": 1}, {"n": 2}])
        self.assertTrue(all(l["prompt_chars"] > 0 for l in res["log"]))  # 每轮提示字符数都记了下来


class Catalog(unittest.TestCase):
    def test_compact_catalog_drops_examples_and_shrinks(self):
        reg = gen.load_registry()
        compact = gen.module_catalog(reg)
        self.assertNotIn("例：", compact)               # 默认不含示例
        self.assertIn("web.fetch@0", compact)           # 仍列出每个模块的 id
        full = gen.module_catalog(reg, full=set(reg))   # 全量带示例的旧视图
        self.assertIn("例：", gen.module_catalog(reg, full={"web.fetch@0"}))
        self.assertLess(len(compact), len(full) * 0.4)  # 压到全量的四成以下（实测约 35%）

    def test_module_details_lists_only_named_modules(self):
        reg = gen.load_registry()
        d = gen.module_details(reg, ["web.fetch@0", "不存在@0"])
        self.assertIn("web.fetch@0", d)
        self.assertIn("例：", d)
        self.assertEqual(gen.module_details(reg, []), "")

    def test_wf_modules_tolerates_invalid_graphs(self):
        # 修正轮会处理没通过校验的模型输出：nodes 为 null、节点不是字典都不能崩
        self.assertEqual(gen._wf_modules({"nodes": None}), [])
        self.assertEqual(gen._wf_modules({"nodes": [None, "x"]}), [])
        self.assertEqual(gen._wf_modules(None), [])
        self.assertEqual(gen._wf_modules({"nodes": [{"kind": "module", "module": "web.fetch@0"}]}), ["web.fetch@0"])

    def test_gives_up_after_rounds(self):
        good = example("summarize_page")
        with mock.patch.object(gen, "chat", FakeChat(good, good)):
            res = gen.generate("x", "m", rounds=2, feasibility=False, post_check=lambda wf: (["还是不对"], {}))
        self.assertFalse(res["ok"])


if __name__ == "__main__":
    unittest.main()
