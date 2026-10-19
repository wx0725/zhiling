"""画布编辑的纯函数：改完的 JSON 就是新的程序（移植自 web/tests/edit.test.mjs）"""
import json
import unittest

from native.logic.edit import (UNSET, add_node, reparent, add_port, arrange_body, connect, freeze_layout, move_node, remove_edge,
                               remove_node, remove_port, rename_port, set_config, set_label, set_max_items, set_meta,
                               set_port_fields, set_port_type, split_id, unique_id)
from native.logic._js import js_round
from native.logic.graph import to_flow

MODULES = {
    "text.format@0": {"name": "转成文字", "category": "处理", "inputs": {"template": {"type": "string"}, "value": {"type": "any", "required": False}},
                      "outputs": {"text": {"type": "string"}}},
    "logic.compare@0": {"name": "比较", "category": "逻辑", "inputs": {"a": {"type": "any"}, "op": {"type": "string"}, "b": {"type": "any", "required": False}},
                        "outputs": {"result": {"type": "boolean"}}},
}


def base():
    return {
        "spec": "0.2", "id": "w", "name": "测试", "intent": "",
        "inputs": {"x": {"type": "integer"}}, "outputs": {"y": {"type": "string"}},
        "nodes": [
            {"id": "f", "kind": "module", "module": "text.format@0", "config": {"template": "数是 {value}"}},
            {"id": "each", "kind": "foreach", "max_items": 5, "body": {
                "inputs": {"item": {"type": "any"}}, "outputs": {"result": {"type": "any"}},
                "nodes": [{"id": "g", "kind": "module", "module": "text.format@0", "config": {"template": "{value}"}}],
                "edges": [{"from": "$in.item", "to": "g.value"}, {"from": "g.text", "to": "$out.result"}]}},
        ],
        "edges": [{"from": "$in.x", "to": "f.value"}, {"from": "f.text", "to": "$out.y"}],
    }


def c(source, sh, target, th):
    return {"source": source, "sourceHandle": sh, "target": target, "targetHandle": th}


class EditTest(unittest.TestCase):
    def test_reparent_into_and_out_of_loop(self):
        """拖进循环框：节点挪进 body，参数带过去，连着它的线断开；移出来同理；重名改 id；不能嵌套循环"""
        wf = base()
        r = reparent(wf, "f", ["each"], {"x": 10.4, "y": 20})
        self.assertEqual((r["id"], r["dropped"]), ("each/f", 2))
        self.assertNotIn("f", [n["id"] for n in r["wf"]["nodes"]])
        self.assertEqual(r["wf"]["edges"], [])
        body = r["wf"]["nodes"][0]["body"]
        moved = next(n for n in body["nodes"] if n["id"] == "f")
        self.assertEqual(moved["config"], {"template": "数是 {value}"})
        self.assertEqual(body["layout"]["f"], {"x": 10, "y": 20})
        self.assertEqual(len(wf["nodes"]), 2)  # 不改传入的对象
        back = reparent(r["wf"], "each/g", [], {"x": 0, "y": 300})
        self.assertEqual((back["id"], back["dropped"]), ("g", 2))
        again = reparent(back["wf"], "g", ["each"])
        self.assertEqual(again["id"], "each/g")
        clash = reparent(base(), "each/g", [])
        self.assertEqual(clash["id"], "g")
        wf2 = base()
        wf2["nodes"].append({"id": "g", "kind": "module", "module": "text.format@0", "config": {"template": "x"}})
        self.assertEqual(reparent(wf2, "g", ["each"])["id"], "each/g_2")
        wf3 = base()
        wf3["nodes"].append({"id": "each2", "kind": "foreach", "body": {"inputs": {}, "outputs": {}, "nodes": [], "edges": []}})
        self.assertIn("error", reparent(wf3, "each2", ["each"]))
        self.assertIn("error", reparent(base(), "$in", ["each"]))
        self.assertIn("error", reparent(base(), "f", []))

    def test_does_not_mutate_input(self):
        wf = base()
        snapshot = json.dumps(wf)
        set_config(wf, "f", "template", "新")
        remove_node(wf, "f")
        connect(wf, c("$in", "out:x", "f", "in:template"))
        self.assertEqual(json.dumps(wf), snapshot)

    def test_set_config(self):
        wf = set_config(base(), "f", "template", "值：{value}")
        self.assertEqual(wf["nodes"][0]["config"]["template"], "值：{value}")
        wf = set_config(wf, "f", "template", UNSET)
        self.assertNotIn("config", wf["nodes"][0])
        wf = set_config(base(), "each/g", "template", "第 {value} 项")
        self.assertEqual(wf["nodes"][1]["body"]["nodes"][0]["config"]["template"], "第 {value} 项")
        self.assertIsNone(set_config(base(), "f", "template", None)["nodes"][0]["config"]["template"])  # None 是 JSON 的 null

    def test_connect_replaces_source_and_fixed_value(self):
        wf = connect(base(), c("$in", "out:x", "f", "in:template"))["wf"]
        self.assertTrue(any(e["from"] == "$in.x" and e["to"] == "f.template" for e in wf["edges"]))
        self.assertNotIn("config", wf["nodes"][0])
        r = connect(wf, c("f", "out:text", "$out", "in:y"))
        self.assertEqual(len([e for e in r["wf"]["edges"] if e["to"] == "$out.y"]), 1)
        self.assertIs(r["wf"], wf)  # 已有的线：原样返回

    def test_connect_rules(self):
        ok = connect(base(), c("each/$in", "out:item", "each/g", "in:template"))
        self.assertTrue(any(e["from"] == "$in.item" and e["to"] == "g.template" for e in ok["wf"]["nodes"][1]["body"]["edges"]))
        self.assertRegex(connect(base(), c("f", "out:text", "each/g", "in:value"))["error"], "同一层")
        self.assertRegex(connect(base(), c("f", "out:text", "f", "in:value"))["error"], "自己")
        self.assertRegex(connect(base(), c("f", "out:text", "each", "ctrl"))["error"], "控制线")
        self.assertRegex(connect(base(), c("f", None, "each", "in:items"))["error"], "输出插孔")
        self.assertRegex(connect(base(), c("f", "out:text", "each", None))["error"], "输入插孔")

    def test_control_edge_from_then(self):
        r1 = add_node(base(), [], {"kind": "if"}, {"x": 10, "y": 10})
        r = connect(r1["wf"], c(r1["id"], "out:then", "f", "ctrl"))
        self.assertTrue(any(e["from"] == f"{r1['id']}.then" and e["to"] == "f" for e in r["wf"]["edges"]))
        self.assertIs(connect(r["wf"], c(r1["id"], "out:then", "f", "ctrl"))["wf"], r["wf"])

    def test_remove_edge_and_node(self):
        wf = remove_edge(base(), {"scope": [], "from": "$in.x", "to": "f.value"})
        self.assertEqual(len(wf["edges"]), 1)
        wf = remove_node(base(), "f")
        self.assertEqual([n["id"] for n in wf["nodes"]], ["each"])
        self.assertEqual(wf["edges"], [])
        wf = remove_node(base(), "each/g")
        self.assertEqual(wf["nodes"][1]["body"]["edges"], [])
        self.assertEqual(len(remove_node(base(), "$in")["nodes"]), 2)

    def test_add_node(self):
        r = add_node(base(), [], {"kind": "module", "module": "text.format@0"}, {"x": 100.4, "y": 50})
        self.assertEqual(r["id"], "format")
        r = add_node(r["wf"], [], {"kind": "module", "module": "text.format@0"}, {"x": 0, "y": 0})
        self.assertEqual(r["id"], "format_2")
        self.assertEqual(r["wf"]["layout"]["format"], {"x": 100, "y": 50})
        loop = add_node(r["wf"], [], {"kind": "foreach"}, {"x": 0, "y": 0})
        body = next(n for n in loop["wf"]["nodes"] if n["id"] == "each_2")["body"]
        self.assertEqual(list(body["inputs"]), ["item"])
        inner = add_node(base(), ["each"], {"kind": "merge"}, {"x": 0, "y": 200})
        self.assertEqual(inner["id"], "each/merge")
        to_flow(loop["wf"], MODULES)
        self.assertTrue(any(n["id"] == "each/merge" for n in to_flow(inner["wf"], MODULES)["nodes"]))

    def test_freeze_and_move(self):
        nodes = to_flow(base(), MODULES)["nodes"]
        positions = {n["id"]: n["position"] for n in nodes if not n.get("parentId")}
        frozen = freeze_layout(base(), positions)
        again = [n for n in to_flow(frozen, MODULES)["nodes"] if not n.get("parentId")]
        for n in again:
            self.assertEqual(n["position"], {"x": js_round(positions[n["id"]]["x"]), "y": js_round(positions[n["id"]]["y"])})
        moved = move_node(frozen, "f", {"x": 999, "y": 7})
        self.assertEqual(next(n for n in to_flow(moved, MODULES)["nodes"] if n["id"] == "f")["position"], {"x": 999, "y": 7})

    def test_label_and_max_items(self):
        self.assertEqual(set_label(base(), "f", " 写一句 ")["nodes"][0]["label"], "写一句")
        self.assertNotIn("label", set_label(set_label(base(), "f", "x"), "f", "")["nodes"][0])
        self.assertEqual(set_max_items(base(), "each", 50)["nodes"][1]["max_items"], 50)

    def test_ports(self):
        r = add_port(base(), "inputs")
        self.assertEqual(r["name"], "input")
        self.assertEqual(add_port(r["wf"], "inputs")["name"], "input_2")
        r = rename_port(base(), "inputs", "x", "num")
        self.assertEqual(list(r["wf"]["inputs"]), ["num"])
        self.assertTrue(any(e["from"] == "$in.num" and e["to"] == "f.value" for e in r["wf"]["edges"]))
        r = rename_port(base(), "outputs", "y", "sentence")
        self.assertTrue(any(e["to"] == "$out.sentence" for e in r["wf"]["edges"]))
        self.assertRegex(rename_port(base(), "inputs", "x", "Bad Name")["error"], "小写")
        self.assertRegex(rename_port(base(), "inputs", "x", "ok\n")["error"], "小写")  # 末尾换行也不算合法名字
        self.assertRegex(rename_port(add_port(base(), "inputs")["wf"], "inputs", "input", "x")["error"], "已经有")
        wf = set_port_fields(base(), "inputs", "x", {"description": "分数", "default": 60})
        self.assertEqual(wf["inputs"]["x"], {"type": "integer", "description": "分数", "default": 60})
        wf = set_port_type(wf, "inputs", "x", "string")
        self.assertEqual(wf["inputs"]["x"], {"type": "string", "description": "分数"})
        wf = set_port_fields(wf, "inputs", "x", {"description": UNSET})
        self.assertEqual(wf["inputs"]["x"], {"type": "string"})
        wf = remove_port(base(), "inputs", "x")
        self.assertEqual(wf["inputs"], {})
        self.assertFalse(any(e["from"] == "$in.x" for e in wf["edges"]))
        wf = remove_port(base(), "outputs", "y")
        self.assertFalse(any(e["to"] == "$out.y" for e in wf["edges"]))

    def test_meta(self):
        wf = set_meta(base(), name="新名字", intent="做点什么")
        self.assertEqual(wf["name"], "新名字")
        self.assertEqual(wf["intent"], "做点什么")
        self.assertEqual(set_meta(base(), intent="只改说明")["name"], "测试")

    def test_arrange_body(self):
        wf = base()
        wf["nodes"][1]["body"]["layout"] = {"g": {"x": 300, "y": 40}, "$in": {"x": 0, "y": 0}}
        out = arrange_body(wf, "each")
        self.assertNotIn("layout", out["nodes"][1]["body"])
        self.assertEqual(wf["nodes"][1]["body"]["layout"]["g"], {"x": 300, "y": 40}, "不改原来的")
        wf["nodes"][1]["body"]["nodes"].append({"id": "box2", "kind": "foreach", "max_items": 3, "body": {"inputs": {}, "outputs": {}, "nodes": [], "edges": []}})
        wf["nodes"][1]["body"]["layout"] = {"box2": {"x": 1, "y": 2, "w": 300, "h": 150}, "gone": {"x": 10, "y": 20, "w": 400, "h": 200}}
        self.assertEqual(arrange_body(wf, "each")["nodes"][1]["body"]["layout"], {"box2": {"w": 300, "h": 150}})
        wf["nodes"][1]["body"]["nodes"].pop()
        wf["nodes"][1]["body"]["layout"] = {"g": {"x": 300, "y": 40}}
        self.assertNotIn("layout", arrange_body(wf, "each")["nodes"][1]["body"])
        nodes = to_flow(arrange_body(wf, "each"), MODULES)["nodes"]
        self.assertTrue(any(n["id"] == "each/g" for n in nodes), "整理后照样画得出来")

    def test_split_and_unique_id_and_rounding(self):
        self.assertEqual(split_id("a/b/c"), {"scope": ["a", "b"], "local": "c"})
        self.assertEqual(unique_id({"nodes": []}, "3d-x"), "n3d_x")
        self.assertEqual(unique_id({"nodes": [{"id": "node"}]}, ""), "node_2")
        # Math.round：.5 往上进（不是银行家舍入）
        self.assertEqual(move_node(base(), "f", {"x": 0.5, "y": 2.5})["layout"]["f"], {"x": 1, "y": 3})
        self.assertEqual(move_node(base(), "f", {"x": -0.5, "y": -2.5})["layout"]["f"], {"x": 0, "y": -2})
        # 找不到所在那一层的位置跳过
        self.assertNotIn("layout", freeze_layout(base(), {"ghost/x": {"x": 1, "y": 1}}))


if __name__ == "__main__":
    unittest.main()


class RemoveEdgesTest(unittest.TestCase):
    def test_remove_several(self):
        from native.logic.edit import remove_edges
        wf = {"nodes": [], "edges": [{"from": "$in.a", "to": "x.a"}, {"from": "x.o", "to": "$out.o"}, {"from": "x.o", "to": "y.i"}]}
        out = remove_edges(wf, [{"scope": [], "from": "x.o", "to": "$out.o"}, {"scope": [], "from": "x.o", "to": "y.i"}])
        self.assertEqual(out["edges"], [{"from": "$in.a", "to": "x.a"}])
        self.assertEqual(len(wf["edges"]), 3)  # 不改动传入的对象
