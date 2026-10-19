import copy
import json
import unittest

from core import types
from core.registry import ROOT, load_registry
from core.validate import validate_module, validate_workflow

REG = load_registry()


def example(name):
    return json.loads((ROOT / "examples" / f"{name}.workflow.json").read_text(encoding="utf-8"))


def errors_of(wf):
    return validate_workflow(wf, REG)


class TypeRules(unittest.TestCase):
    def test_compatible(self):
        self.assertTrue(types.compatible("integer", "number"))
        self.assertFalse(types.compatible("number", "integer"))
        self.assertTrue(types.compatible("list<integer>", "list<number>"))
        self.assertTrue(types.compatible("string", "any"))
        self.assertFalse(types.compatible("integer", "string"))
        self.assertFalse(types.compatible("list<string>", "string"))

    def test_valid_names(self):
        self.assertTrue(types.is_valid("list<list<string>>"))
        self.assertFalse(types.is_valid("str"))


class ModulesAndExamples(unittest.TestCase):
    def test_all_modules_valid(self):
        self.assertGreaterEqual(len(REG), 3)
        for spec in REG.values():
            self.assertEqual(validate_module(spec), [], spec.id)

    def test_examples_valid(self):
        for f in sorted((ROOT / "examples").rglob("*.workflow.json")):
            wf = json.loads(f.read_text(encoding="utf-8"))
            self.assertEqual(validate_workflow(wf, REG, f.parent), [], f.name)


class Rejections(unittest.TestCase):
    """每条都对应接口规范第 7 节的一条规则，错误信息要能指出位置。"""

    def setUp(self):
        self.wf = example("summarize_page")

    def assertRejected(self, wf, *fragments):
        errs = errors_of(wf)
        self.assertTrue(errs, "应当被拒绝")
        joined = "\n".join(errs)
        for frag in fragments:
            self.assertIn(frag, joined)

    def test_schema_missing_field(self):
        del self.wf["intent"]
        self.assertRejected(self.wf, "结构错误", "intent")

    def test_unknown_module(self):
        self.wf["nodes"][0]["module"] = "web.crawl@0"
        self.assertRejected(self.wf, "不存在的模块 web.crawl@0", "web.fetch@0")

    def test_wrong_major_version(self):
        self.wf["nodes"][0]["module"] = "web.fetch@1"
        self.assertRejected(self.wf, "不存在的模块 web.fetch@1")

    def test_unknown_port(self):
        self.wf["edges"][1]["from"] = "fetch.body"
        self.assertRejected(self.wf, "没有输出端口 body", "text")

    def test_type_mismatch(self):
        self.wf["edges"][1]["from"] = "fetch.status"  # integer -> string
        self.assertRejected(self.wf, "类型不兼容", "integer", "string")

    def test_missing_required_input(self):
        del self.wf["nodes"][1]["config"]
        self.assertRejected(self.wf, "必需输入 instruction")

    def test_two_sources_one_input(self):
        self.wf["edges"].append({"from": "fetch.url", "to": "think.text"})
        self.assertRejected(self.wf, "已经有来源")

    def test_config_and_edge_conflict(self):
        self.wf["nodes"][1]["config"]["text"] = "固定文本"
        self.assertRejected(self.wf, "既有连线又有 config")

    def test_config_wrong_type(self):
        self.wf["nodes"][1]["config"]["temperature"] = "高"
        self.assertRejected(self.wf, "config.temperature", "number")

    def test_config_unknown_key(self):
        self.wf["nodes"][1]["config"]["prompt"] = "x"
        self.assertRejected(self.wf, "config 中的 prompt 不是输入端口")

    def test_output_without_source(self):
        self.wf["edges"] = [e for e in self.wf["edges"] if not e["to"].startswith("$out")]
        self.assertRejected(self.wf, "输出 summary 没有来源")

    def test_cycle(self):
        wf = self.wf
        wf["nodes"].append({"id": "think2", "kind": "module", "module": "llm.think@0",
                            "config": {"instruction": "再想想"}})
        wf["nodes"][1]["config"].pop("instruction")
        wf["edges"] += [{"from": "think.answer", "to": "think2.text"},
                        {"from": "think2.answer", "to": "think.instruction"}]
        self.assertRejected(wf, "连线形成环")

    def test_duplicate_node_id(self):
        self.wf["nodes"].append(copy.deepcopy(self.wf["nodes"][0]))
        self.assertRejected(self.wf, "节点 id 重复：fetch")


class ControlFlow(unittest.TestCase):
    def setUp(self):
        self.wf = example("summarize_many")
        self.body = self.wf["nodes"][0]["body"]

    def test_foreach_requires_max_items(self):
        del self.wf["nodes"][0]["max_items"]
        self.assertTrue(any("max_items" in e for e in errors_of(self.wf)))

    def test_foreach_body_ports(self):
        self.body["outputs"] = {"summary": {"type": "string"}}
        errs = errors_of(self.wf)
        self.assertTrue(any("恰好有输入 item 和输出 result" in e for e in errs), errs)

    def test_foreach_item_type_checked(self):
        self.wf["inputs"]["urls"]["type"] = "list<integer>"
        errs = errors_of(self.wf)
        self.assertTrue(any("list<integer> 不能接到 list<string>" in e for e in errs), errs)

    def test_if_condition_must_be_boolean(self):
        for e in self.body["edges"]:
            if e["to"] == "branch.condition":
                e["from"] = "fetch.status"
        errs = errors_of(self.wf)
        self.assertTrue(any("integer 不能接到 boolean" in e for e in errs), errs)

    def test_if_infers_value_type(self):
        # branch.value 接 fetch.status（integer），then 再接到需要 string 的 text 上应当报错
        for e in self.body["edges"]:
            if e["to"] == "branch.value":
                e["from"] = "fetch.status"
        errs = errors_of(self.wf)
        self.assertTrue(any("branch.then" in e and "integer 不能接到 string" in e for e in errs), errs)

    def test_merge_needs_input(self):
        self.body["edges"] = [e for e in self.body["edges"] if not e["to"].startswith("join.")]
        errs = errors_of(self.wf)
        self.assertTrue(any("merge 至少要接一条输入" in e for e in errs), errs)

    def test_error_location_names_body(self):
        self.body["nodes"][0]["module"] = "web.nope@0"
        errs = errors_of(self.wf)
        self.assertTrue(any(e.startswith("summarize_many > each.body") for e in errs), errs)

    def test_subflow_ref(self):
        sub = ROOT / "tests" / "_tmp_sub.workflow.json"
        sub.write_text(json.dumps(example("summarize_page"), ensure_ascii=False), encoding="utf-8")
        try:
            wf = {"spec": "0.1", "id": "outer", "name": "外层", "intent": "",
                  "inputs": {"u": {"type": "string"}}, "outputs": {"s": {"type": "string"}},
                  "nodes": [{"id": "sub", "kind": "subflow", "ref": "_tmp_sub.workflow.json"}],
                  "edges": [{"from": "$in.u", "to": "sub.url"}, {"from": "sub.summary", "to": "$out.s"}]}
            self.assertEqual(validate_workflow(wf, REG, ROOT / "tests"), [])
            wf["edges"][0]["to"] = "sub.link"
            self.assertTrue(any("没有输入端口 link" in e for e in validate_workflow(wf, REG, ROOT / "tests")))
        finally:
            sub.unlink()

    def test_subflow_ref_outside_dir_rejected(self):
        """ref 指向项目根和当前目录之外的文件：拒绝（口径同引擎 / api/ports）。"""
        wf = {"spec": "0.1", "id": "outer", "name": "外层", "intent": "",
              "inputs": {"u": {"type": "string"}}, "outputs": {"s": {"type": "string"}},
              "nodes": [{"id": "sub", "kind": "subflow", "ref": "../../secret.workflow.json"}],
              "edges": [{"from": "$in.u", "to": "sub.url"}, {"from": "sub.summary", "to": "$out.s"}]}
        errs = validate_workflow(wf, REG, ROOT / "tests")
        self.assertTrue(any("越出" in e for e in errs), errs)


class V02ControlLines(unittest.TestCase):
    """v0.2：if 不带 value 只做路由；控制线只决定节点是否运行。"""

    def setUp(self):
        self.wf = example("score_message")

    def edge(self, src, dst):
        for e in self.wf["edges"]:
            if e["from"] == src and e["to"] == dst:
                return e
        raise AssertionError(f"{src} -> {dst} 不存在")

    def test_example_valid(self):
        self.assertEqual(errors_of(self.wf), [])
        self.assertEqual(errors_of(example("overall_summary")), [])

    def test_data_edge_from_valueless_if_rejected_with_hint(self):
        self.edge("branch.then", "congratulate")["to"] = "congratulate.text"
        self.wf["edges"] = [e for e in self.wf["edges"] if not (e["from"] == "score_text.text" and e["to"] == "congratulate.text")]
        errs = errors_of(self.wf)
        self.assertTrue(any("没有接 value" in e and "只能接控制线" in e for e in errs), errs)

    def test_control_from_non_if_rejected(self):
        self.wf["edges"].append({"from": "passed.result", "to": "encourage"})
        self.assertTrue(any("控制线的起点只能是 if" in e for e in errors_of(self.wf)))

    def test_control_to_missing_node(self):
        self.edge("branch.else", "encourage")["to"] = "comfort"
        self.assertTrue(any("节点 comfort 不存在" in e for e in errors_of(self.wf)))

    def test_cycle_through_control_line(self):
        # encourage 的回答再去决定 branch 的条件：数据线 + 控制线成环
        self.wf["edges"] = [e for e in self.wf["edges"] if e["to"] != "branch.condition"]
        self.wf["nodes"].append({"id": "is_long", "kind": "module", "module": "logic.compare@0", "config": {"op": "empty"}})
        self.wf["edges"] += [{"from": "encourage.answer", "to": "is_long.a"}, {"from": "is_long.result", "to": "branch.condition"}]
        self.assertTrue(any("连线形成环" in e for e in errors_of(self.wf)))

    def test_if_with_value_still_works(self):
        self.assertEqual(errors_of(example("summarize_many")), [])

    def test_v01_spec_still_accepted(self):
        wf = example("summarize_page")
        self.assertEqual(wf["spec"], "0.1")
        self.assertEqual(errors_of(wf), [])




class BodyLayout(unittest.TestCase):
    """画布编辑会把循环体里节点的位置写进 body.layout：只影响显示，校验要接受；格式不对仍然报错。"""

    def test_body_layout_allowed(self):
        wf = example("summarize_many")
        body = next(n for n in wf["nodes"] if n["kind"] == "foreach")["body"]
        body["layout"] = {n["id"]: {"x": 10, "y": 20} for n in body["nodes"]}
        self.assertEqual(errors_of(wf), [])
        body["layout"]["fetch"] = {"x": "左边"}
        self.assertTrue(errors_of(wf))


if __name__ == "__main__":
    unittest.main()
