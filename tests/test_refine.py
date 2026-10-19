"""理清需求（tools/refine.py、tools/spec.py、/api/refine）与统一模型协议（tools/protocol.py）：用替身模型测。"""
import json
import time
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import server.app as appmod
import tools.generate as gen
import tools.protocol as protocol
import tools.refine as rf
import tools.spec as sp
from core.registry import load_registry
from native.logic.flow import merge_answers
from tests.test_semantic import FakeChat

client = TestClient(appmod.app)
CFG = {"generator": "g", "generator_url": "u", "judge": "j", "judge_url": "u"}
REG = load_registry()
MODS = set(REG)

# 模型读出来的需求单：一组网址逐个抓取总结，没说结果要分条还是合并、没说打不开怎么办
VAGUE = {"goal": "看看这几个网页讲了什么", "inputs": None, "outputs": None, "steps": None,
         "branches": [], "failures": [], "examples": [], "unsupported": [],
         "options": {"steps": ["逐个总结", "合起来总结"]}}
URLS = {"goal": "逐个总结网页", "inputs": [{"name": "urls", "type": "list<string>", "desc": "一组网址"}],
        "outputs": [{"name": "summary", "type": None, "desc": "总结"}],
        "steps": [{"do": "抓取网页", "module": "web.fetch@0", "each": True},
                  {"do": "用一句中文总结", "module": "llm.think@0", "each": True}],
        "branches": [], "failures": [], "examples": [], "unsupported": [], "options": {}}
SCORE = {"goal": "按分数写一句话", "inputs": [{"name": "score", "type": "integer", "desc": "分数"}],
         "outputs": [{"name": "message", "type": "string", "desc": "一句话"}],
         "steps": [{"do": "比较分数", "module": "logic.compare@0"}, {"do": "写祝贺的话", "module": "llm.think@0"}],
         "branches": [{"when": "分数大于等于 60", "then": "写祝贺的话", "else": None}],
         "failures": [], "examples": [], "unsupported": [], "options": {}}
MAIL = {**SCORE, "inputs": [{"name": "url", "type": "string", "desc": "网址"}], "branches": [],
        "outputs": [{"name": "text", "type": "string", "desc": "英文"}],
        "steps": [{"do": "抓取网页", "module": "web.fetch@0"}, {"do": "翻译", "module": "llm.think@0"}],
        "failures": [{"module": "web.fetch@0", "handle": "说明原因"}], "unsupported": ["发邮件"]}


def spec_of(data):
    return sp.carry(sp.normalize(data, MODS), None)


class Protocol(unittest.TestCase):
    def test_same_reply_shape_and_retry_with_reason(self):
        fake = FakeChat({"x": 1}, {"ok": True})
        req = protocol.Request("t", "说明", "内容", check=lambda d: None if d.get("ok") else "缺少 ok")
        reply = protocol.call("api://p/m", req, send_fn=fake)
        self.assertTrue(reply.ok)
        self.assertEqual((reply.data, reply.calls, reply.source), ({"ok": True}, 2, "api"))
        self.assertIn("缺少 ok", fake.calls[1][-1]["content"])  # 第二次带着具体原因问
        self.assertEqual(fake.calls[1][-2]["role"], "assistant")

    def test_json_inside_text_is_extracted_and_failures_reported(self):
        send = mock.Mock(side_effect=[("好的：```json\n{\"a\": 1}\n```", 0.1)])
        reply = protocol.call("cli://kimi", protocol.Request("t", "s", "u"), send_fn=send)
        self.assertEqual((reply.ok, reply.data, reply.source), (True, {"a": 1}, "cli"))
        send = mock.Mock(return_value=("不是 JSON", 0.1))
        reply = protocol.call("m", protocol.Request("t", "s", "u", attempts=2), send_fn=send)
        self.assertFalse(reply.ok)
        self.assertEqual(reply.calls, 2)
        self.assertIn("不是合法 JSON", reply.error)

    def test_calls_are_counted_once_at_transport_with_task_name(self):
        protocol.reset_stats()
        with mock.patch.object(protocol.llm, "chat_ollama", return_value="{}"):
            protocol.call("m", protocol.Request("refine", "s", "u"))                   # 直接走传输
            protocol.call("m", protocol.Request("feasibility", "s", "u"), send_fn=gen.chat)  # 经各模块自己的 chat
            gen.chat("m", [], None)                                                      # 不经协议的普通调用
        self.assertEqual(protocol.stats(), {"calls": 3, "task:refine": 1, "task:feasibility": 1, "task:chat": 1})

    def test_transport_errors_propagate(self):
        send = mock.Mock(side_effect=ConnectionRefusedError("拒绝连接"))
        with self.assertRaises(OSError):
            protocol.call("m", protocol.Request("t", "s", "u"), send_fn=send)

    def test_context_budget_drops_optional_then_reports_too_long(self):
        sent = {}
        with mock.patch.object(protocol.llm, "chat_ollama", lambda *a, **k: sent.update(msgs=a[2]) or "{}"), \
                mock.patch.object(protocol, "input_budget", lambda m: 50):
            # 可选材料删掉后放得下：照常发送；记号和被删内容都不会发给模型
            protocol.send("m", [{"role": "user", "content": "需求很短" + protocol.optional("废话" * 500)}], None)
            self.assertNotIn(protocol.OPT_OPEN, sent["msgs"][0]["content"])
            self.assertNotIn("废话", sent["msgs"][0]["content"])
            self.assertIn("需求很短", sent["msgs"][0]["content"])
            # 必需内容本身就超了：报“内容太长”，不把超长提示发出去
            with self.assertRaises(protocol.ContextExhausted):
                protocol.send("m", [{"role": "user", "content": "需" * 1000}], None)

    def test_cancel_and_deadline_are_terminal(self):
        # 发送前已取消：一次都不发，返回“已取消”
        send = mock.Mock(return_value=('{"ok": true}', 0.1))
        reply = protocol.call("m", protocol.Request("t", "s", "u"), send_fn=send, should_stop=lambda: True)
        self.assertFalse(reply.ok)
        self.assertEqual((reply.category, send.call_count), ("cancelled", 0))
        # 发送后才取消：迟到的有效结果也不算成功
        flag = {"stop": False}

        def send2(*a):
            flag["stop"] = True
            return '{"ok": true}', 0.1
        reply = protocol.call("m", protocol.Request("t", "s", "u"), send_fn=send2, should_stop=lambda: flag["stop"])
        self.assertEqual((reply.ok, reply.category), (False, "cancelled"))
        # 已过期限：终止
        reply = protocol.call("m", protocol.Request("t", "s", "u"), send_fn=send, deadline=time.monotonic() - 1)
        self.assertEqual((reply.ok, reply.category), (False, "deadline"))

    def test_error_category(self):
        import urllib.error
        self.assertEqual(protocol.error_category(protocol.ContextExhausted("x")), "context_exhausted")
        self.assertEqual(protocol.error_category(urllib.error.HTTPError("u", 401, "a", {}, None)), "auth")
        self.assertEqual(protocol.error_category(urllib.error.HTTPError("u", 400, "a", {}, None)), "format")
        self.assertEqual(protocol.error_category(urllib.error.HTTPError("u", 503, "a", {}, None)), "transient")
        self.assertEqual(protocol.error_category(ConnectionRefusedError("x")), "unreachable")
        # 各适配器把 HTTP 错误包装成带 category 的 LLMError：优先读它带的类别
        self.assertEqual(protocol.error_category(protocol.llm.LLMError("连不上", category="auth")), "auth")

    def test_local_api_provider_uses_local_budget(self):
        # 标了 local 的 OpenAI 兼容服务（LM Studio）受本机显存限制，用本地保守上限，不用云端的宽松值
        with mock.patch.object(protocol.llm, "api_specs", lambda: {"lmstudio": {"local": True}}), \
                mock.patch.object(protocol.llm, "load_settings", lambda: {}):
            self.assertEqual(protocol.input_budget("api://lmstudio/m"), protocol.LOCAL_INPUT_TOKENS)
            self.assertEqual(protocol.input_budget("api://deepseek/m"), protocol.REMOTE_INPUT_TOKENS)
            self.assertEqual(protocol.input_budget("qwen3:8b"), protocol.LOCAL_INPUT_TOKENS)
        with mock.patch.object(protocol.llm, "load_settings", lambda: {"context_budget": {"local": 8000}}):
            self.assertEqual(protocol.input_budget("qwen3:8b"), 8000)  # 可被 model_settings.json 覆盖

    def test_transport_error_after_cancel_returns_terminal(self):
        state = {"stop": False}

        def boom(*a):
            state["stop"] = True
            raise ConnectionResetError("连接断开")
        reply = protocol.call("m", protocol.Request("t", "s", "u"), send_fn=boom, should_stop=lambda: state["stop"])
        self.assertEqual((reply.ok, reply.category), (False, "cancelled"))

    def test_cancel_during_check_is_not_success(self):
        state = {"stop": False}

        def chk(d):
            state["stop"] = True  # 校验期间另一处取消了任务
            return None           # 校验本身通过
        reply = protocol.call("m", protocol.Request("t", "s", "u", check=chk),
                              send_fn=mock.Mock(return_value=('{"ok": true}', 0.1)), should_stop=lambda: state["stop"])
        self.assertEqual((reply.ok, reply.category), (False, "cancelled"))

    def test_cancel_during_failed_check_is_terminal(self):
        # 校验不合格的那一次里被取消：也算终止（cancelled），不是“无效回答”
        state = {"stop": False}

        def chk(d):
            state["stop"] = True
            return "不合格"
        reply = protocol.call("m", protocol.Request("t", "s", "u", check=chk, attempts=1),
                              send_fn=mock.Mock(return_value=('{"x": 1}', 0.1)), should_stop=lambda: state["stop"])
        self.assertEqual((reply.ok, reply.category), (False, "cancelled"))


class Spec(unittest.TestCase):
    def test_normalize_drops_what_code_can_reject(self):
        s = sp.normalize({"inputs": [{"name": "URL 地址", "type": "网址", "desc": "x"}, {"name": "url", "type": "string"}],
                          "outputs": None, "steps": [{"do": "发邮件", "module": "mail.send@0"}, "总结", {"do": ""}],
                          "failures": [{"module": "nope@0", "handle": "x"}],
                          "options": {"inputs": ["一个", "其他", "x" * 50], "outputs": ["只有一个"]}}, MODS)
        self.assertEqual([p["type"] for p in s["inputs"]], [None, "string"])     # 不合法的类型当作没说
        self.assertEqual(len({p["name"] for p in s["inputs"]}), 2)                # 名字不重复
        self.assertEqual(s["steps"], [{"do": "发邮件", "module": None, "each": False},
                                      {"do": "总结", "module": None, "each": False}])
        self.assertEqual(s["failures"], [])
        self.assertEqual(s["options"], {})  # 去掉“其他”和太长的后不足两个选项
        self.assertIsNone(s["outputs"])

    def test_shape_error(self):
        self.assertIsNone(sp.shape_error(VAGUE))
        self.assertIn("inputs", sp.shape_error({"outputs": None, "steps": None}))
        self.assertIn("列表", sp.shape_error({**VAGUE, "steps": "抓取"}))
        self.assertIsNotNone(sp.shape_error([]))

    def test_gaps_follow_standard_order_and_limit(self):
        qs = sp.questions(spec_of(VAGUE), REG)
        self.assertEqual([q["slot"] for q in qs], ["inputs", "outputs", "steps"])
        self.assertEqual(qs[2]["options"][:2], ["逐个总结", "合起来总结"])  # 模型给的选项排在前面
        self.assertTrue(all(2 <= len(q["options"]) <= sp.MAX_OPTIONS and q["why"] for q in qs))
        self.assertNotIn(sp.OTHER, sum((q["options"] for q in qs), []))  # “其他”由界面加

    def test_risky_module_without_failure_handling_is_asked(self):
        slots = [q["slot"] for q in sp.questions(spec_of(URLS), REG)]
        self.assertEqual(slots, ["outputs.summary.type", "failures.web.fetch@0"])
        q = sp.questions(spec_of(URLS), REG)[0]
        self.assertIn("每项各一条（列表）", q["options"])  # 输入是一组：问逐项还是合并

    def test_branch_without_else_is_asked(self):
        self.assertEqual([q["slot"] for q in sp.questions(spec_of(SCORE), REG)], [f"branches.{sp.branch_id('分数大于等于 60')}.else"])

    def test_code_options_patch_spec_without_model(self):
        s, left = sp.apply_answers(spec_of(URLS), [
            {"slot": "outputs.summary.type", "answer": "合成一段文字"},
            {"slot": "failures.web.fetch@0", "answer": "自己写的：跳过打不开的"}], REG)
        self.assertEqual(left, [])
        self.assertEqual(s["outputs"][0]["type"], "string")
        self.assertEqual(s["failures"], [{"module": "web.fetch@0", "handle": "自己写的：跳过打不开的"}])
        self.assertEqual(sp.questions(s, REG), [])
        self.assertTrue(all(c["state"] == "ok" for c in sp.checklist(s, REG)))

    def test_answers_needing_understanding_go_to_model_and_are_not_asked_again(self):
        s, left = sp.apply_answers(spec_of(VAGUE), [{"slot": "inputs", "answer": "三个新闻网站"},
                                                    {"slot": "outputs", "answer": "一段文字"}], REG)
        self.assertEqual([a["slot"] for a in left], ["inputs"])
        self.assertEqual(s["outputs"][0]["type"], "string")
        self.assertNotIn("inputs", [q["slot"] for q in sp.questions(s, REG)])
        self.assertEqual({c["key"]: c["state"] for c in sp.checklist(s, REG)}["inputs"], "default")

    def test_unsupported_needs_consent(self):
        s = spec_of(MAIL)
        self.assertEqual(sp.questions(s, REG)[0]["slot"], "unsupported")
        self.assertIn("发邮件", sp.blocked(s))
        dropped, _ = sp.apply_answers(s, [{"slot": "unsupported", "answer": sp.DROP}], REG)
        self.assertEqual(sp.blocked(dropped), "")
        self.assertIn("不包括：发邮件", sp.requirement("x", dropped, REG))
        gave_up, _ = sp.apply_answers(s, [{"slot": "unsupported", "answer": sp.ABANDON}], REG)
        self.assertIn("不做了", sp.blocked(gave_up))

    def test_requirement_text_is_built_by_code(self):
        s, _ = sp.apply_answers(spec_of(SCORE), [{"slot": f"branches.{sp.branch_id('分数大于等于 60')}.else", "answer": "写一句鼓励的话"}], REG)
        text = sp.requirement("分数及格就祝贺", s, REG)
        self.assertIn("输入：score（整数）：分数。", text)
        self.assertIn("否则写一句鼓励的话", text)
        self.assertIn("logic.compare@0", text)
        self.assertTrue(text.endswith("使用者原话：分数及格就祝贺"))
        self.assertIn("“分数大于等于 60”不成立时不用额外处理", sp.requirement("x", spec_of(SCORE), REG))
        self.assertIn("抓取网页出错时不用特别处理", sp.requirement("x", spec_of(URLS), REG))

    def test_untrusted_spec_from_client(self):
        bad = {**URLS, "answered": {"inputs.x.type": "a", 3: "b", "outputs.9.type": "c"}, "dropped": "yes",
               "patches": {"a": ["type", "inputs", "urls", "evil<>"], "b": ["inputs", ["Bad Name", "string", "x"]],
                           "c": ["drop", 1], "d": ["type", "outputs", True, "string"], "e": "x", "f": ["else", "x"],
                           "g": ["fail", "web.fetch@0", {"x": 1}], "h": ["drop", []]}}
        s = sp.clean_spec(bad, MODS)
        self.assertEqual(s["patches"], {})
        self.assertEqual(s["dropped"], [])
        sp.requirement("x", s, REG)  # 认不出的栏目名不出错
        sp.checklist(s, REG)
        self.assertIsNone(sp.clean_spec("x", MODS))

    def test_conformance_compares_types(self):
        s = spec_of({**URLS, "outputs": [{"name": "s", "type": "list<string>", "desc": ""}]})
        wf = {"inputs": {"u": {"type": "string"}}, "outputs": {"r": {"type": "list<string>"}}}
        errs = sp.conformance(wf, s)
        self.assertEqual(len(errs), 1)
        self.assertIn("list<string>", errs[0])
        self.assertEqual(sp.conformance({"inputs": {"u": {"type": "list<string>"}}, "outputs": wf["outputs"]}, s), [])
        num = spec_of({**SCORE, "outputs": [{"name": "sum", "type": "integer", "desc": ""}]})
        self.assertEqual(sp.conformance({"inputs": {"s": {"type": "integer"}}, "outputs": {"t": {"type": "number"}}}, num), [])
        objs = spec_of({**URLS, "outputs": [{"name": "r", "type": "list<object>", "desc": ""}]})
        self.assertEqual(sp.conformance({"inputs": {"u": {"type": "list<string>"}}, "outputs": {"r": {"type": "list<string>"}}}, objs), [])
        self.assertEqual(len(sp.conformance({"inputs": {"u": {"type": "list<string>"}}, "outputs": {"r": {"type": "string"}}}, objs)), 1)
        none_in = spec_of({**SCORE, "inputs": []})
        self.assertIn("不需要输入", sp.conformance({"inputs": {"x": {"type": "integer"}}, "outputs": {"m": {"type": "string"}}},
                                                   none_in)[0])


class Refine(unittest.TestCase):
    def test_first_round_asks_about_missing_parts(self):
        fake = FakeChat(VAGUE)
        with mock.patch.object(rf, "chat", fake):
            r = rf.refine("帮我看看这几个网页")
        self.assertEqual(r["calls"], 1)
        self.assertEqual([q["slot"] for q in r["questions"]], ["inputs", "outputs", "steps"])
        self.assertFalse(r["complete"])
        self.assertIn("web.fetch@0", fake.calls[0][0]["content"])  # 模块清单在提示词里
        self.assertEqual(set(r["card"]), {"given", "wanted", "steps", "cases", "examples"})

    def test_code_options_need_no_model_call(self):
        with mock.patch.object(rf, "chat", FakeChat(URLS)):
            r = rf.refine("逐个总结网页")
        answers = merge_answers([], r["questions"], {0: "每项各一条（列表）", 1: "输出一句说明原因"})
        spec = json.loads(json.dumps(r["spec"]))  # 界面送回来的是 JSON
        with mock.patch.object(rf, "chat", mock.Mock(side_effect=AssertionError("不该调用模型"))):
            r2 = rf.refine("逐个总结网页", answers, None, spec)
        self.assertEqual(r2["calls"], 0)
        self.assertTrue(r2["complete"])
        self.assertEqual(r2["spec"]["outputs"][0]["type"], "list<string>")

    def test_code_decisions_survive_model_reread(self):
        with mock.patch.object(rf, "chat", FakeChat(VAGUE)):
            r = rf.refine("看网页")
        answers = merge_answers([], r["questions"], {0: "一组网址", 1: "一段文字", 2: "逐个总结"})
        reread = {**URLS, "inputs": [{"name": "urls", "type": None, "desc": "网址"}]}  # 模型重读时把类型丢了
        fake = FakeChat(reread)
        with mock.patch.object(rf, "chat", fake):
            r2 = rf.refine("看网页", answers, None, json.loads(json.dumps(r["spec"])))
        self.assertEqual(r2["calls"], 1)
        user = fake.calls[0][-1]["content"]
        self.assertIn("逐个总结", user)          # 需要理解的回答交给模型
        self.assertNotIn("：一组网址", user)     # 代码已写进单子的不再交给模型
        self.assertEqual(r2["spec"]["inputs"], [{"name": "urls", "type": "list<string>", "desc": "网址"}])
        self.assertEqual(r2["spec"]["outputs"][0]["type"], "string")
        self.assertNotIn("steps", [q["slot"] for q in r2["questions"]])

    def test_changed_idea_discards_old_spec(self):
        with mock.patch.object(rf, "chat", FakeChat(URLS)):
            r = rf.refine("总结网页")
        fake = FakeChat(SCORE)
        with mock.patch.object(rf, "chat", fake):
            r2 = rf.refine("按分数写一句话", [], None, json.loads(json.dumps(r["spec"])))
        self.assertEqual(r2["calls"], 1)
        self.assertNotIn("之前的需求单", fake.calls[0][-1]["content"])
        self.assertEqual(r2["spec"]["idea"], "按分数写一句话")

    def test_rerun_without_new_answers_rereads(self):
        with mock.patch.object(rf, "chat", FakeChat(URLS)):
            r = rf.refine("总结网页")
        fake = FakeChat(URLS)
        with mock.patch.object(rf, "chat", fake):
            self.assertEqual(rf.refine("总结网页", [], None, r["spec"])["calls"], 1)
        self.assertIn("之前的需求单", fake.calls[0][-1]["content"])

    def test_card_edit_keeps_drop_decision(self):
        with mock.patch.object(rf, "chat", FakeChat(MAIL)):
            r = rf.refine("翻译后发邮件")
        answers = merge_answers([], r["questions"], {0: sp.DROP})
        with mock.patch.object(rf, "chat", mock.Mock(side_effect=AssertionError)):
            r = rf.refine("翻译后发邮件", answers, None, json.loads(json.dumps(r["spec"])))
        self.assertTrue(r["complete"])
        with mock.patch.object(rf, "chat", FakeChat(MAIL)):  # 改了卡片，模型重读后仍列出发邮件
            r2 = rf.refine("翻译后发邮件", answers, {"steps": ["抓取", "翻译"]}, json.loads(json.dumps(r["spec"])))
        self.assertEqual(sp.blocked(r2["spec"]), "")
        self.assertTrue(r2["complete"])

    def test_new_unsupported_part_after_drop_is_asked(self):
        with mock.patch.object(rf, "chat", FakeChat(MAIL)):
            r = rf.refine("翻译后发邮件")
        answers = merge_answers([], r["questions"], {0: sp.DROP})
        r = rf.refine("翻译后发邮件", answers, None, json.loads(json.dumps(r["spec"])))
        more = {**MAIL, "unsupported": ["发邮件", "每天定时运行"]}
        with mock.patch.object(rf, "chat", FakeChat(more)):
            r2 = rf.refine("翻译后发邮件", answers, {"cases": ["每天定时运行"]}, json.loads(json.dumps(r["spec"])))
        self.assertIn("每天定时运行", sp.blocked(r2["spec"]))
        self.assertNotIn("发邮件", sp.blocked(r2["spec"]))
        self.assertEqual(r2["questions"][0]["slot"], "unsupported")
        self.assertFalse(r2["complete"])
        self.assertEqual({c["key"]: c["state"] for c in r2["checklist"]}["unsupported"], "missing")

    def test_decisions_follow_names_not_positions(self):
        two = {**URLS, "inputs": [{"name": "urls", "type": None, "desc": "网址"}, {"name": "n", "type": "integer", "desc": "个数"}]}
        with mock.patch.object(rf, "chat", FakeChat(two)):
            r = rf.refine("总结网页")
        q = r["questions"][0]
        self.assertEqual(q["slot"], "inputs.urls.type")
        answers = merge_answers([], [q], {0: q["options"][1]})  # 一组
        swapped = {**two, "inputs": [two["inputs"][1], {**two["inputs"][0]}]}  # 模型重读后把顺序换了
        with mock.patch.object(rf, "chat", FakeChat(swapped)):
            r2 = rf.refine("总结网页", answers + [{"slot": "outputs.summary.type", "ask": "x", "answer": "自己写"}], None,
                           json.loads(json.dumps(r["spec"])))
        self.assertEqual({p["name"]: p["type"] for p in r2["spec"]["inputs"]}, {"n": "integer", "urls": "list<string>"})

    def test_new_port_at_old_position_is_still_asked(self):
        one = {**URLS, "outputs": [{"name": "summary", "type": "string", "desc": ""}],
               "failures": [{"module": "web.fetch@0", "handle": "说明"}],
               "inputs": [{"name": "a", "type": None, "desc": "网址"}]}
        with mock.patch.object(rf, "chat", FakeChat(one)):
            r = rf.refine("x")
        answers = merge_answers([], r["questions"], {0: r["questions"][0]["options"][0]})
        added = {**one, "inputs": [{"name": "b", "type": None, "desc": "关键词"}, {"name": "a", "type": None, "desc": "网址"}]}
        with mock.patch.object(rf, "chat", FakeChat(added)):
            r2 = rf.refine("x", answers, {"given": ["加一个关键词"]}, json.loads(json.dumps(r["spec"])))
        self.assertEqual([q["slot"] for q in r2["questions"]], ["inputs.b.type"])
        self.assertFalse(r2["complete"])

    def test_card_edit_overrides_earlier_option(self):
        with mock.patch.object(rf, "chat", FakeChat({**URLS, "inputs": [{"name": "urls", "type": None, "desc": "网址"}]})):
            r = rf.refine("x")
        answers = merge_answers([], r["questions"][:1], {0: r["questions"][0]["options"][1]})  # 一组
        r = rf.refine("x", answers, None, json.loads(json.dumps(r["spec"])))
        self.assertEqual(r["spec"]["inputs"][0]["type"], "list<string>")
        one = {**URLS, "inputs": [{"name": "urls", "type": "string", "desc": "一个网址"}]}
        with mock.patch.object(rf, "chat", FakeChat(one)):  # 卡片改成只给一个网址
            r2 = rf.refine("x", answers, {"given": ["urls（文字）：一个网址"]}, json.loads(json.dumps(r["spec"])))
        self.assertEqual(r2["spec"]["inputs"][0]["type"], "string")

    def test_changed_idea_drops_old_answers(self):
        with mock.patch.object(rf, "chat", FakeChat(VAGUE)):
            r = rf.refine("看网页")
        answers = merge_answers([], r["questions"], {0: "一组网址"})
        fake = FakeChat(VAGUE)
        with mock.patch.object(rf, "chat", fake):
            r2 = rf.refine("翻译一段文字", answers, None, json.loads(json.dumps(r["spec"])))
        self.assertNotIn("一组网址", fake.calls[0][-1]["content"])
        self.assertIn("inputs", [q["slot"] for q in r2["questions"]])

    def test_legacy_answers_without_slot_are_not_asked_again(self):
        with mock.patch.object(rf, "chat", FakeChat(VAGUE)):
            r = rf.refine("看网页")
        old = [{"ask": q["ask"], "answer": "按常规处理"} for q in r["questions"]]  # 旧网页界面：没有栏目，也不送回单子
        with mock.patch.object(rf, "chat", FakeChat(VAGUE)):
            r2 = rf.refine("看网页", old)
        self.assertEqual(r2["questions"], [])

    def test_bad_client_spec_does_not_crash(self):
        for bad in ({"answered": [1]}, {**URLS, "answered": [1]}, {**URLS, "steps": [{"do": "x", "module": []}]},
                    {**URLS, "failures": [{"module": {}, "handle": "x"}]}, {**URLS, "dropped": {"a": 1}}):
            with mock.patch.object(rf, "chat", FakeChat(URLS)):
                rf.refine("x", [{"slot": 1, "answer": "a"}, {"slot": "inputs", "answer": 2}], None, bad)

    def test_edited_card_goes_to_model(self):
        fake = FakeChat(URLS)
        with mock.patch.object(rf, "chat", fake):
            rf.refine("看网页", [], {"given": ["三个网址"]}, {**VAGUE, "idea": "看网页"})
        self.assertIn("三个网址", fake.calls[0][-1]["content"])

    def test_bad_answer_retried_then_clear_error(self):
        fake = FakeChat({"inputs": "x"}, VAGUE)
        with mock.patch.object(rf, "chat", fake):
            self.assertEqual(rf.refine("看网页")["calls"], 2)
        self.assertIn("inputs", fake.calls[1][-1]["content"])
        with mock.patch.object(rf, "chat", FakeChat([], [])):
            with self.assertRaises(rf.RefineError):
                rf.refine("看网页")


class GenerateWithSpec(unittest.TestCase):
    def test_unsupported_without_consent_refused_by_code(self):
        with mock.patch.object(gen, "chat", mock.Mock(side_effect=AssertionError("不该调用模型"))):
            res = gen.generate("x", "m", spec=spec_of(MAIL))
        self.assertTrue(res["ok"])
        self.assertIn("无法实现：现有模块做不到：发邮件", res["workflow"]["name"])

    def test_spec_skips_feasibility_and_types_are_checked(self):
        wf = json.loads((gen.ROOT / "examples" / "score_message.workflow.json").read_text(encoding="utf-8"))
        wrong = {**wf, "inputs": {k: {**p, "type": "string"} for k, p in wf["inputs"].items()}}
        spec = spec_of({**SCORE, "branches": [{"when": "及格", "then": "祝贺", "else": "鼓励"}]})
        spec["inputs"][0]["type"] = next(iter(wf["inputs"].values()))["type"]
        spec["outputs"][0]["type"] = next(iter(wf["outputs"].values()))["type"]
        fake = FakeChat(wrong, wf)  # 第一版输入类型和需求单对不上
        with mock.patch.object(gen, "chat", fake):
            res = gen.generate("x", "m", spec=spec)
        self.assertTrue(res["ok"], res["log"])
        self.assertEqual(len(fake.calls), 2)  # 没有可行性判断，第二次带着类型问题重写
        self.assertIn("需求单要求输入", fake.calls[1][-1]["content"])
        self.assertEqual(res["log"][0]["by"], "代码")

    def test_empty_or_bad_spec_does_not_skip_feasibility(self):
        wf = json.loads((gen.ROOT / "examples" / "score_message.workflow.json").read_text(encoding="utf-8"))
        for bad in ({}, {"inputs": "x", "outputs": None, "steps": None}):
            fake = FakeChat({"steps": [], "feasible": True}, wf)
            with mock.patch.object(gen, "chat", fake):
                gen.generate("x", "m", spec=bad)
            self.assertIn("判断用下面这些模块能不能实现", fake.calls[0][0]["content"])

    def test_prefill_fixed_fields(self):
        wf = gen.prefill({"spec": "0.1"}, "需求")
        self.assertEqual((wf["spec"], wf["intent"]), ("0.2", "需求"))


class Route(unittest.TestCase):
    def post(self, body, chat):
        with mock.patch.object(appmod, "model_config", return_value=CFG), mock.patch.object(rf, "chat", chat):
            return client.post("/api/refine", json=body)

    def test_ok(self):
        r = self.post({"idea": "帮我看看这几个网页"}, FakeChat(URLS))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["card"]["given"], ["urls（一组文字）：一组网址"])
        self.assertEqual(body["questions"][0]["slot"], "outputs.summary.type")
        self.assertIn("checklist", body)

    def test_spec_and_answers_round_trip(self):
        r = self.post({"idea": "看网页"}, FakeChat(URLS)).json()
        answers = [{"slot": q["slot"], "ask": q["ask"], "answer": q["options"][0]} for q in r["questions"]]
        r2 = self.post({"idea": "看网页", "answers": answers, "spec": r["spec"]}, mock.Mock(side_effect=AssertionError))
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.json()["complete"])

    def test_empty_idea(self):
        self.assertEqual(self.post({"idea": "  "}, FakeChat(VAGUE)).status_code, 400)

    def test_bad_model_answer_is_502_with_message(self):
        r = self.post({"idea": "x"}, FakeChat("null", "null"))
        self.assertEqual(r.status_code, 502)
        self.assertIn("直接生成", r.json()["detail"])

    def test_model_down_is_503(self):
        r = self.post({"idea": "x"}, mock.Mock(side_effect=ConnectionRefusedError("拒绝连接")))
        self.assertEqual(r.status_code, 503)


class Answers(unittest.TestCase):
    def test_merge_answers_keeps_slot(self):
        qs = [{"slot": "outputs.summary.type", "ask": "一条还是一段？", "options": ["a", "b"]}]
        prev = [{"slot": "outputs.summary.type", "ask": "旧的问法", "answer": "a"}, {"ask": "无栏目", "answer": "x"}]
        self.assertEqual(merge_answers(prev, qs, {0: "b"}),
                         [{"ask": "无栏目", "answer": "x"}, {"slot": "outputs.summary.type", "ask": "一条还是一段？", "answer": "b"}])


if __name__ == "__main__":
    unittest.main()
