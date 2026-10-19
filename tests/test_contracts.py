"""按契约的贯穿测试（效率审查第五节 B、E）：
B 输入：缺省、可选、默认值、null/false/0/空串，经网页接口 → 引擎；示例生成的检查规则与引擎一致。
E 引用：同一次运行内容稳定、下一次运行看得到更新、不同目录同名文件、嵌套引用按各自文件夹解析、深度上限。"""
import itertools
import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import server.app as appmod
from core.engine import Engine, WorkflowInvalid
from core.registry import load_registry
from core.validate import validate_workflow
from tools.semantic import sample_ok

REG = load_registry()
client = TestClient(appmod.app)

DECL = {
    "r": {"type": "integer"},                                   # 必填
    "i": {"type": "integer", "default": 5},                     # 有默认值
    "b": {"type": "boolean", "required": False},                # 可选，无默认值
    "s": {"type": "string", "default": "默认"},
    "a": {"type": "any", "required": False},
}


def passthrough(decl):
    """每个输入原样接到同名输出，方便看到引擎实际收到的值。"""
    return {"spec": "0.2", "id": "pt", "name": "直通", "intent": "", "inputs": decl,
            "outputs": {k: {"type": p["type"]} for k, p in decl.items()}, "nodes": [],
            "edges": [{"from": f"$in.{k}", "to": f"$out.{k}"} for k in decl]}


def run_api(inputs):
    evs = [json.loads(x) for x in client.post("/api/run", json={"workflow": passthrough(DECL), "inputs": inputs}).text.splitlines() if x]
    return evs[-1]


class B_Inputs(unittest.TestCase):
    def test_omitted_inputs_use_defaults_or_stay_absent(self):
        res = run_api({"r": 1})
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["outputs"], {"r": 1, "i": 5, "b": None, "s": "默认", "a": None})
        self.assertEqual(len(res["warnings"]), 2)  # b、a 没有提供，各有一条说明

    def test_falsy_values_are_kept_not_treated_as_missing(self):
        res = run_api({"r": 0, "i": 0, "b": False, "s": "", "a": None})
        self.assertEqual(res["status"], "completed")
        self.assertEqual(res["outputs"], {"r": 0, "i": 0, "b": False, "s": "", "a": None})

    def test_missing_required_and_wrong_types(self):
        cases = [({}, "missing_input"), ({"r": None}, "input_type"), ({"r": "1"}, "input_type"),
                 ({"r": True}, "input_type"), ({"r": 1, "b": 0}, "input_type"), ({"r": 1, "s": None}, "input_type")]
        for inputs, code in cases:
            res = run_api(inputs)
            self.assertEqual((res["status"], res["error"]["code"]), ("failed", code), inputs)

    def test_sample_rule_matches_engine(self):
        """示例生成的检查（sample_ok）与引擎实际是否接受完全一致（未声明字段除外：引擎忽略，示例要拒绝）。"""
        values = {"r": [1, 0, "1", None], "i": [2, None, "x"], "b": [True, False, 0], "s": ["", "x", 1], "a": [None, 0, "z"]}
        wf = passthrough(DECL)
        eng = Engine(REG)
        for keys in itertools.chain.from_iterable(itertools.combinations(DECL, n) for n in range(len(DECL) + 1)):
            for combo in itertools.product(*(values[k][:2] if len(keys) > 3 else values[k] for k in keys)):
                s = dict(zip(keys, combo))
                accepted = eng.run(wf, s).status == "completed"
                self.assertEqual(sample_ok(DECL, s), accepted, s)
        self.assertFalse(sample_ok(DECL, {"r": 1, "unknown": 1}))


def child(wid, template):
    return {"spec": "0.2", "id": wid, "name": wid, "intent": "", "inputs": {"x": {"type": "string"}},
            "outputs": {"y": {"type": "string"}},
            "nodes": [{"id": "f", "kind": "module", "module": "text.format@0", "config": {"template": template}}],
            "edges": [{"from": "$in.x", "to": "f.value"}, {"from": "f.text", "to": "$out.y"}]}


def ref_node(nid, ref):
    return {"id": nid, "kind": "subflow", "ref": ref}


def outer(refs):
    """依次经过若干个引用子流程：x → s0 → s1 → … → y"""
    nodes = [ref_node(f"s{i}", r) for i, r in enumerate(refs)]
    edges = [{"from": "$in.x", "to": "s0.x"}] + [{"from": f"s{i - 1}.y", "to": f"s{i}.x"} for i in range(1, len(refs))]
    edges.append({"from": f"s{len(refs) - 1}.y", "to": "$out.y"})
    return {"spec": "0.2", "id": "outer", "name": "外层", "intent": "", "inputs": {"x": {"type": "string"}},
            "outputs": {"y": {"type": "string"}}, "nodes": nodes, "edges": edges}


class E_Refs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, doc):
        p = self.d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    def test_same_name_in_different_folders(self):
        self.write("c.workflow.json", child("top", "上层[{value}]"))
        self.write("sub/c.workflow.json", child("low", "下层[{value}]"))
        res = Engine(REG).run(outer(["c.workflow.json", "sub/c.workflow.json"]), {"x": "v"}, self.d)
        self.assertEqual(res.outputs, {"y": "下层[上层[v]]"})

    def test_nested_ref_resolves_from_its_own_folder(self):
        # sub/mid 引用 "c.workflow.json"：应找 sub/c，而不是最外层的 c
        self.write("c.workflow.json", child("top", "错的[{value}]"))
        self.write("sub/c.workflow.json", child("low", "对的[{value}]"))
        mid = outer(["c.workflow.json"])
        mid["id"] = "mid"
        self.write("sub/mid.workflow.json", mid)
        res = Engine(REG).run(outer(["sub/mid.workflow.json"]), {"x": "v"}, self.d)
        self.assertEqual(res.outputs, {"y": "对的[v]"})

    def test_same_file_twice_read_once_and_updates_seen_next_run(self):
        self.write("c.workflow.json", child("c", "一[{value}]"))
        eng = Engine(REG)
        self.assertEqual(eng.run(outer(["c.workflow.json"] * 3), {"x": "v"}, self.d).outputs, {"y": "一[一[一[v]]]"})
        self.assertEqual(eng._snap.reads, 1)
        self.write("c.workflow.json", child("c", "二[{value}]"))
        self.assertEqual(eng.run(outer(["c.workflow.json"]), {"x": "v"}, self.d).outputs, {"y": "二[v]"})

    def test_depth_limit_reported(self):
        for i in range(10):  # l0 → l1 → … → l9，每层都是一个引用
            nxt = outer([f"l{i + 1}.workflow.json"]) if i < 9 else child("leaf", "{value}")
            nxt["id"] = f"l{i}"
            self.write(f"l{i}.workflow.json", nxt)
        errors = validate_workflow(outer(["l0.workflow.json"]), REG, self.d)
        self.assertTrue(any("嵌套超过 8 层" in e for e in errors), errors)
        with self.assertRaises(WorkflowInvalid):
            Engine(REG).run(outer(["l0.workflow.json"]), {"x": "v"}, self.d)


if __name__ == "__main__":
    unittest.main()
