"""画图、自动排版、写模块检查（移植自 web/tests 里 review_fixes、recheck_fixes、audit3_fixes、efficiency_fixes、library 的画图部分）"""
import unittest

from native.logic import layered
from native.logic.collide import overlaps, rect_of
from native.logic.graph import (HEAD, NODE_W, PAD, TOP, node_height, path_to_id, port_top, ports_of, to_flow,
                                unconfirmed_module, write_modules)

MODULES = {
    "text.format@0": {"inputs": {"template": {"type": "string"}, "value": {"type": "any", "required": False}}, "outputs": {"text": {"type": "string"}}},
}


def find(nodes, id):
    return next(n for n in nodes if n["id"] == id)


class DrawTest(unittest.TestCase):
    def test_f01_bad_drafts_do_not_raise(self):
        # 缺 edges 或连到不存在节点的草稿不会让画图抛错
        no_edges = {"id": "x", "inputs": {}, "outputs": {}, "nodes": [{"id": "f", "kind": "module", "module": "text.format@0"}]}
        to_flow(no_edges, MODULES)
        dangling = {**no_edges, "edges": [{"from": "ghost.text", "to": "f.value"}, {"from": "f.text"}, None]}
        self.assertEqual(len(to_flow(dangling, MODULES)["edges"]), 0)
        to_flow({"id": "y", "nodes": "坏的", "edges": {}}, MODULES)
        self.assertEqual(to_flow(None, MODULES), {"nodes": [], "edges": []})

    def test_f16_ref_subflow_ports(self):
        wf = {"id": "o", "inputs": {"x": {"type": "string"}}, "outputs": {"y": {"type": "string"}},
              "nodes": [{"id": "sub", "kind": "subflow", "ref": "hello.workflow.json"}],
              "edges": [{"from": "$in.x", "to": "sub.x"}, {"from": "sub.y", "to": "$out.y"}]}
        ports = {"hello.workflow.json": {"inputs": {"x": {"type": "string"}}, "outputs": {"y": {"type": "string"}}}}
        sub = find(to_flow(wf, MODULES, ports)["nodes"], "sub")
        self.assertEqual([p["name"] for p in sub["data"]["ins"]], ["x"])
        self.assertEqual([p["name"] for p in sub["data"]["outs"]], ["y"])

    def test_partial_layout_terminals_do_not_overlap(self):
        # 示例只保存了中间节点的位置、没存输入输出端点：首次打开时端点不能压在节点上（“抓取网页并总结”的输入被抓取网页盖住）
        wf = {"id": "p", "inputs": {"url": {"type": "string"}}, "outputs": {"summary": {"type": "string"}},
              "nodes": [{"id": "fetch", "kind": "module", "module": "text.format@0"},
                        {"id": "think", "kind": "module", "module": "text.format@0"}],
              "edges": [{"from": "$in.url", "to": "fetch.template"}, {"from": "fetch.text", "to": "think.template"},
                        {"from": "think.text", "to": "$out.summary"}],
              "layout": {"fetch": {"x": 0, "y": 0}, "think": {"x": 260, "y": 0}}}
        ns = to_flow(wf, MODULES)["nodes"]
        self.assertEqual(find(ns, "fetch")["position"], {"x": 0, "y": 0})
        self.assertEqual(find(ns, "think")["position"], {"x": 260, "y": 0})
        for i, a in enumerate(ns):
            for b in ns[i + 1:]:
                self.assertFalse(overlaps(rect_of(a), rect_of(b), 0), (a["id"], b["id"]))
        r_in, r_fetch, r_think, r_out = (rect_of(find(ns, k)) for k in ("$in", "fetch", "think", "$out"))
        self.assertLess(r_in["x"] + r_in["w"], r_fetch["x"])  # 输入在左边、输出在右边，按连线方向排
        self.assertGreater(r_out["x"], r_think["x"] + r_think["w"])

    def test_examples_open_without_overlap(self):
        # 每个示例首次打开（照它文件里保存的 layout 画）时，同一层节点互不重叠
        import glob
        import json
        import os
        from core.registry import load_registry
        mods = {k: {"key": k, **s.data} for k, s in load_registry().items()}
        root = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "examples")
        files = glob.glob(os.path.join(root, "**", "*.workflow.json"), recursive=True)
        self.assertTrue(files)
        for f in files:
            with open(f, encoding="utf-8") as fh:
                ns = to_flow(json.load(fh), mods)["nodes"]
            for i, a in enumerate(ns):
                for b in ns[i + 1:]:
                    if a.get("parentId") == b.get("parentId"):
                        self.assertFalse(overlaps(rect_of(a), rect_of(b), 0), (os.path.basename(f), a["id"], b["id"]))

    def test_a04_saved_layout_wins(self):
        wf = {"id": "l", "inputs": {}, "outputs": {}, "layout": {"f": {"x": 1000, "y": 2000}},
              "nodes": [{"id": "f", "kind": "module", "module": "text.format@0", "config": {"template": "hi"}}], "edges": []}
        self.assertEqual(find(to_flow(wf, MODULES)["nodes"], "f")["position"], {"x": 1000, "y": 2000})

    def test_r01_prototype_names(self):
        for id in ("constructor", "toString", "__proto__", "hasOwnProperty"):
            wf = {"id": "c", "inputs": {"x": {"type": "string"}}, "outputs": {"y": {"type": "string"}},
                  "nodes": [{"id": id, "kind": "module", "module": "text.format@0", "config": {"template": "hello {value}"}}],
                  "edges": [{"from": "$in.x", "to": f"{id}.value"}, {"from": f"{id}.text", "to": "$out.y"}]}
            res = to_flow(wf, MODULES)
            self.assertTrue(any(n["id"] == id for n in res["nodes"]), id)
            self.assertEqual(len(res["edges"]), 2, id)

    def test_r02_broken_ref_ports(self):
        wf = {"id": "o", "inputs": {}, "outputs": {}, "nodes": [{"id": "s", "kind": "subflow", "ref": "child.workflow.json"}], "edges": []}
        for bad in ({"inputs": {"x": None}, "outputs": {}}, {"inputs": [], "outputs": "x"}, None):
            to_flow(wf, MODULES, {"child.workflow.json": bad})
        to_flow({**wf, "inputs": {"a": None}, "outputs": {"b": 3}}, MODULES)

    def test_n08_identity_workflow(self):
        identity = {"id": "id", "name": "直通", "inputs": {"x": {"type": "integer"}}, "outputs": {"y": {"type": "integer"}},
                    "nodes": [], "edges": [{"from": "$in.x", "to": "$out.y"}]}
        self.assertEqual(len(to_flow(identity, {})["edges"]), 1)

    def test_q01_bad_merge_ports(self):
        for ports in (1, "ab", [1, 2], None, {}):
            wf = {"id": "m", "inputs": {"x": {"type": "any"}}, "outputs": {"y": {"type": "any"}},
                  "nodes": [{"id": "m", "kind": "merge", "ports": ports}], "edges": [{"from": "$in.x", "to": "m.a"}, {"from": "m.value", "to": "$out.y"}]}
            m = find(to_flow(wf, {})["nodes"], "m")
            self.assertEqual([p["name"] for p in m["data"]["ins"]], ["a", "b"], repr(ports))

    def test_shapes(self):
        wf = {"id": "w", "inputs": {"x": {"type": "string"}}, "outputs": {"y": {"type": "string"}},
              "nodes": [{"id": "f", "kind": "module", "module": "text.format@0", "config": {"template": "一段很长很长的模板文字，超过十四个字"}},
                        {"id": "each", "kind": "foreach", "max_items": 3, "body": {"inputs": {"item": {"type": "string"}}, "outputs": {"result": {"type": "string"}},
                         "nodes": [], "edges": [{"from": "$in.item", "to": "$out.result"}]}}],
              "edges": [{"from": "$in.x", "to": "f.value"}, {"from": "f.text", "to": "$out.y"}]}
        res = to_flow(wf, MODULES)
        nodes, edges = res["nodes"], res["edges"]
        f = find(nodes, "f")
        self.assertEqual(f["type"], "module")
        self.assertIs(f["data"]["node"], wf["nodes"][0])  # 画布节点带着程序里那个节点对象本身
        self.assertIs(f["data"]["module"], MODULES["text.format@0"])
        self.assertIsNone(f["parentId"])
        self.assertNotIn("style", f)
        tpl = next(p for p in f["data"]["ins"] if p["name"] == "template")
        self.assertEqual(tpl["fixed"], "一段很长很长的模板文字，超…")
        self.assertEqual(tpl["full"], wf["nodes"][0]["config"]["template"])
        self.assertEqual(f["data"]["height"], node_height(2, 1))
        loop = find(nodes, "each")
        self.assertEqual(loop["type"], "loop")
        self.assertEqual(loop["style"], {"width": loop["data"]["width"], "height": loop["data"]["height"]})
        self.assertEqual(loop["data"]["ins"][0]["type"], "list<string>")
        self.assertEqual(find(nodes, "$in")["data"]["terminal"], "terminal_in")
        self.assertEqual(find(nodes, "each/$in")["data"]["title"], "每一项")
        self.assertEqual(find(nodes, "each/$in")["parentId"], "each")
        # 父节点排在子节点前
        order = [n["id"] for n in nodes]
        self.assertLess(order.index("each"), order.index("each/$in"))
        e = next(e for e in edges if e["target"] == "f")
        self.assertEqual(e, {"id": "e0", "source": "$in", "sourceHandle": "out:x", "target": "f", "targetHandle": "in:value",
                             "type": "default", "data": {"control": False, "from": "$in.x", "to": "f.value", "scope": []}, "className": "edge-data"})
        inner = next(e for e in edges if e["source"] == "each/$in")
        self.assertEqual(inner["id"], "each/e0")
        self.assertEqual(inner["data"]["scope"], ["each"])
        self.assertEqual(port_top(0), HEAD + 11)

    def test_auto_layout_left_to_right_without_overlap(self):
        # 代替 dagre 的排版：沿连线从左到右，同一层不重叠
        wf = {"id": "w", "inputs": {"x": {"type": "string"}}, "outputs": {"y": {"type": "string"}, "z": {"type": "string"}},
              "nodes": [{"id": n, "kind": "module", "module": "text.format@0"} for n in ("a", "b", "c", "d")],
              "edges": [{"from": "$in.x", "to": "a.value"}, {"from": "a.text", "to": "b.value"}, {"from": "a.text", "to": "c.value"},
                        {"from": "b.text", "to": "d.template"}, {"from": "c.text", "to": "d.value"}, {"from": "d.text", "to": "$out.y"},
                        {"from": "c.text", "to": "$out.z"}]}
        nodes = to_flow(wf, MODULES)["nodes"]
        pos = {n["id"]: n for n in nodes}

        def right(a, b):  # a 整个在 b 左边
            return rect_of(pos[a])["x"] + rect_of(pos[a])["w"] <= rect_of(pos[b])["x"]
        for a, b in (("$in", "a"), ("a", "b"), ("a", "c"), ("b", "d"), ("c", "d"), ("d", "$out")):
            self.assertTrue(right(a, b), (a, b))
        for p in nodes:
            for q in nodes:
                if p is not q:
                    self.assertFalse(overlaps(rect_of(p), rect_of(q), 0), (p["id"], q["id"]))
        self.assertEqual(min(rect_of(n)["x"] for n in nodes), 0)
        self.assertEqual(min(rect_of(n)["y"] for n in nodes), 0)

    def test_layered_handles_cycles_and_gaps(self):
        res = layered.layout([("a", 100, 50), ("b", 100, 50), ("c", 80, 30)], [("a", "b"), ("b", "a"), ("a", "a"), ("b", "c"), ("x", "a")])
        self.assertEqual(set(res), {"a", "b", "c"})
        self.assertLess(res["a"][0], res["b"][0])
        self.assertLess(res["b"][0], res["c"][0])
        # 每列相隔 ranksep（48）
        self.assertEqual(res["b"][0] - 50 - (res["a"][0] + 50), 48)
        self.assertEqual(layered.layout([], []), {})
        # 同一列的节点上下相隔 nodesep（26）
        two = layered.layout([("s", 50, 40), ("p", 60, 40), ("q", 60, 40)], [("s", "p"), ("s", "q")])
        ys = sorted([two["p"][1], two["q"][1]])
        self.assertEqual(ys[1] - ys[0], 40 + 26)
        self.assertEqual(two["s"][1], sum(ys) / 2)  # 各列上下居中

    def test_ports_of_module_filters_optional(self):
        mods = {"m@0": {"inputs": {"req": {"type": "string"}, "temp": {"type": "number", "default": 0.7}, "text": {"type": "string", "default": ""},
                                    "v": {"type": "any", "required": False}, "n": {"type": "any", "default": None}}, "outputs": {}}}
        names = [p["name"] for p in ports_of({"kind": "module", "module": "m@0"}, mods)["ins"]]
        self.assertEqual(names, ["req", "text", "n"])
        names = [p["name"] for p in ports_of({"kind": "module", "module": "m@0", "config": {"temp": 1}}, mods, None, {"v"})["ins"]]
        self.assertEqual(names, ["req", "temp", "text", "v", "n"])
        self.assertEqual(ports_of({"kind": "module", "module": "nope"}, mods), {"ins": [], "outs": []})

    def test_manual_box_with_negative_children(self):
        wf = {"nodes": [{"id": "each", "kind": "foreach", "max_items": 5, "body": {"inputs": {}, "outputs": {},
              "nodes": [{"id": "a", "kind": "module", "module": "x"}], "edges": [], "layout": {"a": {"x": -50, "y": -20}}}}],
              "edges": [], "layout": {"each": {"x": 0, "y": 0, "w": 300, "h": 200}}}
        nodes = to_flow(wf, {})["nodes"]
        loop = find(nodes, "each")
        a = find(nodes, "each/a")
        self.assertEqual(loop["data"]["shift"], {"x": 50, "y": 20})
        self.assertEqual(a["position"], {"x": PAD, "y": TOP})
        self.assertGreaterEqual(loop["data"]["width"], NODE_W)


class PathAndModulesTest(unittest.TestCase):
    def test_path_to_id(self):
        self.assertEqual(path_to_id("summarize_many.each[1].fetch", "summarize_many"), {"id": "each/fetch", "items": [1]})
        self.assertEqual(path_to_id("w.a[2].b[10].c", "w"), {"id": "a/b/c", "items": [2, 10]})
        self.assertEqual(path_to_id("other.x", "w"), {"id": "other/x", "items": []})

    MODS = {"file.write@0": {"side_effects": "write"}, "file.read@0": {"side_effects": "read"}, "text.join@0": {"side_effects": "none"}}

    def test_write_modules(self):
        wf = {"nodes": [{"id": "r", "kind": "module", "module": "file.read@0"},
                        {"id": "each", "kind": "foreach", "body": {"nodes": [{"id": "w", "kind": "module", "module": "file.write@0"}]}},
                        {"id": "w2", "kind": "module", "module": "file.write@0"}]}
        self.assertEqual(write_modules(wf, self.MODS), ["file.write@0"])

    def test_write_modules_ignores_bad(self):
        self.assertEqual(write_modules({"nodes": [{"kind": "module", "module": "text.join@0"}]}, self.MODS), [])
        self.assertEqual(write_modules({"nodes": [None, {"kind": "module", "module": "constructor"}, {"kind": "foreach", "body": 3}]}, self.MODS), [])
        self.assertEqual(write_modules({}, self.MODS), [])

    def test_unconfirmed_module(self):
        self.assertEqual(unconfirmed_module("模块 file.write@0 会写文件或产生外部影响，未获得确认"), "file.write@0")
        self.assertIsNone(unconfirmed_module("别的错误"))
        self.assertIsNone(unconfirmed_module(None))


if __name__ == "__main__":
    unittest.main()
