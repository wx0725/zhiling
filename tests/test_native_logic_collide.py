"""画布碰撞：节点不重叠；循环框装不下就扩大，扩大后推开邻居（移植自 web/tests/collide.test.mjs）"""
import math
import unittest

from native.logic.collide import (GAP, can_resize, fit_size, fit_while_dragging, guard_changes, nearest_free,
                                  overlaps, rect_of, settle, slide, slide_group)
from native.logic.edit import freeze_layout, move_node, set_box_size
from native.logic.graph import PAD, TOP, flow_to_layout, layout_moves, to_flow


def box(id, x, y, w=100, h=60, **extra):
    n = {"id": id, "position": {"x": x, "y": y}, "data": {"width": w, "height": h}}
    n.update(extra)
    return n


def find(nodes, id):
    return next(n for n in nodes if n["id"] == id)


# 画出来的每个节点在画布上的绝对位置
def absolute(nodes):
    by_id = {n["id"]: n for n in nodes}

    def abs_(n):
        if n.get("parentId"):
            p = abs_(by_id[n["parentId"]])
            return {"x": p["x"] + n["position"]["x"], "y": p["y"] + n["position"]["y"]}
        return n["position"]
    out = {}
    for n in nodes:
        r = rect_of(n)
        out[n["id"]] = {**abs_(n), "w": r["w"], "h": r["h"]}
    return out


# 把画布节点拖到 to（相对所在层的位置），整理后写回，再重新画
def drag_round_trip(wf, to):
    nodes = to_flow(wf, {})["nodes"]
    live = [{**n, "position": to[n["id"]]} if n["id"] in to else n for n in nodes]
    moves = {**to, **settle(live, list(to))}
    nxt = freeze_layout(wf, layout_moves(live, moves))
    return absolute(nodes), absolute(to_flow(nxt, {})["nodes"]), nxt


def loop_wf(layout, outer=None):
    return {"nodes": [{"id": "each", "kind": "foreach", "max_items": 5, "body": {
        "inputs": {}, "outputs": {}, "nodes": [{"id": i, "kind": "module", "module": "x"} for i in layout], "edges": [], "layout": layout}}],
        "edges": [], "layout": {"each": {"x": 100, "y": 100}, **(outer or {})}}


def contains(b, k):
    return (k["x"] >= b["x"] + PAD - 1 and k["y"] >= b["y"] + TOP - 1
            and k["x"] + k["w"] <= b["x"] + b["w"] - PAD + 1 and k["y"] + k["h"] <= b["y"] + b["h"] - PAD + 1)


class CollideTest(unittest.TestCase):
    def no_overlap(self, nodes, moves):
        placed = [{**n, "position": moves.get(n["id"], n["position"])} for n in nodes]
        for a in placed:
            for b in placed:
                if a is not b and a.get("parentId") == b.get("parentId"):
                    self.assertFalse(overlaps(rect_of(a), rect_of(b), 0), f"{a['id']} 与 {b['id']} 重叠")
        return placed

    def test_slide_along_edge(self):
        # 拖动中：前方有节点就贴着边滑，空地照常走
        size = {"w": 100, "h": 60}
        wall = [{"x": 200, "y": 0, "w": 100, "h": 60}]
        self.assertEqual(slide({"x": 0, "y": 0}, {"x": 50, "y": 0}, size, wall), {"x": 50, "y": 0})
        self.assertEqual(slide({"x": 80, "y": 0}, {"x": 120, "y": 30}, size, wall), {"x": 80, "y": 30})  # 横向被挡，只走纵向
        self.assertEqual(slide({"x": 80, "y": 0}, {"x": 120, "y": 0}, size, wall), {"x": 80, "y": 0})  # 正面撞上：停住

    def test_slide_out_of_old_overlap(self):
        # 原来就压着别的节点（旧数据），可以把它拖出来
        wall = [{"x": 0, "y": 0, "w": 100, "h": 60}]
        self.assertEqual(slide({"x": 20, "y": 0}, {"x": 30, "y": 0}, {"w": 100, "h": 60}, wall), {"x": 30, "y": 0})

    def test_nearest_free(self):
        others = [{"x": 0, "y": 0, "w": 100, "h": 60}]
        self.assertEqual(nearest_free({"x": 300, "y": 0, "w": 100, "h": 60}, others), {"x": 300, "y": 0})
        self.assertEqual(nearest_free({"x": 70, "y": 10, "w": 100, "h": 60}, others), {"x": 100 + GAP, "y": 10})

    def test_dropped_node_moves_away(self):
        nodes = [box("a", 0, 0), box("b", 40, 20)]
        moves = settle(nodes, ["b"])
        self.assertEqual(list(moves), ["b"])
        self.no_overlap(nodes, moves)

    def test_box_grows_right_and_pushes(self):
        loop = box("each", 0, 0, 300, 200, type="loop")
        child = box("each/f", 400, TOP, 100, 60, parentId="each")  # 拖出右边框
        right = box("next", 320, 0)
        stay = box("each/s", PAD, TOP, 100, 60, parentId="each")
        nodes = [loop, child, right, stay]
        moves = settle(nodes, ["each/f"])
        self.assertNotIn("each/f", moves)
        self.assertGreaterEqual(moves["next"]["x"], 400 + 100 + PAD + GAP)
        self.no_overlap(nodes, moves)

    def test_taller_node_pushes_below(self):
        nodes = [box("a", 0, 0, 100, 160), box("b", 0, 100)]
        moves = settle(nodes, [])
        self.assertTrue(moves["b"]["y"] >= 160 + GAP or moves["b"]["x"] >= 100 + GAP)
        self.no_overlap(nodes, moves)

    def test_no_overlap_no_change(self):
        self.assertEqual(list(settle([box("a", 0, 0), box("b", 300, 0)], ["a"])), [])

    def test_manual_box_grows_while_dragging(self):
        loop = box("each", 0, 0, 300, 200, type="loop", style={"width": 300, "height": 200},
                   data={"width": 300, "height": 200, "manual": {"w": 300, "h": 200}})
        child = box("each/f", 260, 180, 100, 60, parentId="each")
        grown = fit_while_dragging([loop, child])[0]
        self.assertEqual([grown["data"]["width"], grown["data"]["height"]], [260 + 100 + PAD, 180 + 60 + PAD])
        self.assertEqual(grown["position"], loop["position"])  # 拖动中不挪框的位置
        still = [loop, box("each/f", 10, TOP, 100, 60, parentId="each")]
        self.assertIs(fit_while_dragging(still), still)  # 没变就返回原列表，不触发重画

    def test_auto_box_fits_while_dragging(self):
        loop = box("each", 100, 50, 600, 400, type="loop", data={"width": 600, "height": 400})
        a = box("each/a", 200, 150, 100, 60, parentId="each")
        b = box("each/b", 350, 220, 100, 60, parentId="each")
        l, a2, b2 = fit_while_dragging([loop, a, b])
        self.assertEqual(l["position"], loop["position"])
        self.assertIs(a2, a)
        self.assertIs(b2, b)
        self.assertEqual(l["data"]["visual"], {"x": 200 - PAD, "y": 150 - TOP})
        self.assertEqual([l["data"]["width"], l["data"]["height"]], [max(196, 450 - 200 + PAD * 2), 280 - 150 + PAD + TOP])

    def test_nested_visual(self):
        outer = box("o", 0, 0, 800, 600, type="loop", data={"width": 800, "height": 600})
        inner = box("o/i", PAD, TOP, 300, 200, type="loop", parentId="o", data={"width": 300, "height": 200})
        x = box("o/i/x", PAD - 100, TOP, 100, 60, parentId="o/i")  # 拖出里层框左边
        out = fit_while_dragging([outer, inner, x])
        self.assertEqual(find(out, "o/i")["data"]["visual"]["x"], -100)
        self.assertEqual(find(out, "o")["data"]["visual"]["x"], PAD + -100 - PAD)

    def test_negative_coords_moved_into_box(self):
        wf = {"nodes": [{"id": "each", "kind": "foreach", "max_items": 5, "body": {
            "inputs": {"item": {"type": "string"}}, "outputs": {"result": {"type": "string"}},
            "nodes": [{"id": "f", "kind": "module", "module": "x"}], "edges": [],
            "layout": {"f": {"x": -80, "y": -30}, "$in": {"x": 100, "y": 0}, "$out": {"x": 300, "y": 0}}}}], "edges": []}
        nodes = to_flow(wf, {})["nodes"]
        kids = [n for n in nodes if n.get("parentId") == "each"]
        self.assertTrue(all("extent" not in n for n in kids))
        for k in kids:
            self.assertTrue(k["position"]["x"] >= PAD and k["position"]["y"] >= TOP, f"{k['id']} 在框内")
        loop = find(nodes, "each")
        for k in kids:
            self.assertLessEqual(k["position"]["x"] + rect_of(k)["w"], loop["data"]["width"])

    def test_group_moves_together(self):
        wall = [{"x": 200, "y": 0, "w": 100, "h": 60}]
        size = {"w": 100, "h": 60}
        out = slide_group([
            {"id": "a", "prev": {"x": 80, "y": 0}, "next": {"x": 120, "y": 30}, "size": size, "obstacles": wall},
            {"id": "b", "prev": {"x": 80, "y": 300}, "next": {"x": 120, "y": 330}, "size": size, "obstacles": wall},
        ])
        self.assertEqual(out, {"a": {"x": 80, "y": 30}, "b": {"x": 80, "y": 330}})  # 都只走纵向

    def test_rect_prefers_layout_size(self):
        self.assertEqual(rect_of({"position": {"x": 1, "y": 2}, "data": {"width": 120, "height": 80}, "measured": {"width": 120, "height": 70}}),
                         {"x": 1, "y": 2, "w": 120, "h": 80})
        self.assertEqual(rect_of({"position": {"x": 0, "y": 0}, "data": {}, "measured": {"width": 50, "height": 40}}),
                         {"x": 0, "y": 0, "w": 50, "h": 40})

    # ---------- GPT 审查第 1 轮的回归测试 ----------

    def test_release_uses_corrected_position(self):
        nodes = [box("a", 0, 0, 196, 60), box("b", 206, 0, 196, 60)]
        memo = {}
        during = guard_changes([{"type": "position", "id": "a", "dragging": True, "position": {"x": 30, "y": 0}}], nodes, memo)
        self.assertEqual(during[0]["position"], {"x": 0, "y": 0})  # 拖动中被挡住
        end = guard_changes([{"type": "position", "id": "a", "dragging": False, "position": {"x": 30, "y": 0}}], nodes, memo)
        self.assertEqual(end[0]["position"], {"x": 0, "y": 0})  # 松手的原始位置也被换掉
        other = guard_changes([{"type": "select", "id": "a", "selected": True}], nodes, memo)
        self.assertEqual(other, [{"type": "select", "id": "a", "selected": True}])

    def test_box_grows_left_pushes_left_neighbor(self):
        loop = box("each", 200, 0, 400, 200, type="loop")
        left_nb = box("nb", -40, 0, 196, 60)
        a = box("each/a", -50, TOP, 100, 60, parentId="each")
        b = box("each/b", 250, TOP, 100, 60, parentId="each")
        nodes = [loop, left_nb, a, b]
        moves = settle(nodes, ["each/a"])
        abs_b = moves.get("each", loop["position"])["x"] + moves.get("each/b", b["position"])["x"]
        self.assertEqual(abs_b, 200 + 250, "框里没拖动的节点在屏幕上不动")
        self.assertTrue("nb" in moves and moves["nb"]["x"] < -40, "左边的邻居被往左推")
        self.no_overlap(nodes, moves)

    def test_diagonal_nearest(self):
        p = nearest_free({"x": 150, "y": 100, "w": 100, "h": 60}, [{"x": 100, "y": 0, "w": 100, "h": 300}, {"x": 210, "y": 100, "w": 300, "h": 60}])
        d = math.hypot(p["x"] - 150, p["y"] - 100)
        self.assertLess(d, 120, f"挪了 {d:.0f}：{p}")

    def test_flow_to_layout_inverse(self):
        wf = {"nodes": [{"id": "each", "kind": "foreach", "max_items": 5, "body": {
            "inputs": {}, "outputs": {}, "nodes": [{"id": "a", "kind": "module", "module": "x"}, {"id": "b", "kind": "module", "module": "x"}],
            "edges": [], "layout": {"a": {"x": -80, "y": 0}, "b": {"x": 200, "y": 40}}}}], "edges": []}
        nodes = to_flow(wf, {})["nodes"]
        conv = flow_to_layout(nodes)
        for i in ("a", "b"):
            self.assertEqual(conv(find(nodes, f"each/{i}")), wf["nodes"][0]["body"]["layout"][i])
        b = find(nodes, "each/b")
        self.assertEqual(conv(b, {"x": b["position"]["x"] + 30, "y": b["position"]["y"]}), {"x": 230, "y": 40})

    # ---------- GPT 审查第 2 轮的回归测试 ----------

    def test_constructor_id(self):
        nodes = [box("constructor", 0, 0), box("a", 300, 0)]
        memo = {}
        guard_changes([{"type": "position", "id": "a", "dragging": True, "position": {"x": 310, "y": 0}}], nodes, memo)
        end = guard_changes([{"type": "position", "id": "constructor", "dragging": False, "position": {"x": 5, "y": 5}}], nodes, memo)
        self.assertEqual(end[0]["position"], {"x": 5, "y": 5})
        self.assertNotIn("constructor", settle(nodes, ["a"]))
        self.assertNotIn("constructor", slide_group([{"id": "a", "prev": {"x": 0, "y": 0}, "next": {"x": 1, "y": 0}, "size": {"w": 10, "h": 10}, "obstacles": []}]))

    def test_base_size_tells_growth_direction(self):
        loop = box("each", 0, 0, 528, 200, type="loop")  # 拖动中已从 300 变宽到 528
        child = box("each/f", 400, TOP, 100, 60, parentId="each")
        nb = box("nb", 320, -40, 100, 60)
        stay = box("each/s", PAD, TOP, 100, 140, parentId="each")
        moves = settle([loop, child, nb, stay], ["each/f"], base={"each": {"w": 300, "h": 200}})
        self.assertNotIn("each", moves, "框不动")
        self.assertTrue("nb" in moves and moves["nb"]["x"] >= 528 + GAP, f"邻居被往右推：{moves.get('nb')}")

    # ---------- 循环框回缩与手动拉伸 ----------

    def test_manual_box_no_shrink_grows_left(self):
        loop = box("each", 100, 0, 600, 300, type="loop", data={"width": 600, "height": 300, "manual": {"w": 600, "h": 300}})
        a = box("each/a", 200, TOP, 100, 60, parentId="each")
        self.assertEqual(list(settle([loop, a], [])), [])  # 左边空着也不回缩
        out = box("each/a", -40, TOP, 100, 60, parentId="each")
        sizes = {}
        moves = settle([loop, out], ["each/a"], sizes_out=sizes)
        dx = PAD + 40
        self.assertEqual(moves["each"]["x"], 100 - dx)
        self.assertEqual(sizes["each"], {"w": 600 + dx, "h": 300})
        self.assertEqual(moves["each"]["x"] + sizes["each"]["w"], 100 + 600, "右边不动")

    def test_pinned_box_pushes_neighbor(self):
        loop = box("each", 0, 0, 500, 300, type="loop", data={"width": 500, "height": 300, "manual": {"w": 500, "h": 300}})
        a = box("each/a", PAD, TOP, 100, 60, parentId="each")
        nb = box("nb", 450, 100, 100, 60)
        moves = settle([loop, a, nb], [], pinned=["each"])
        self.assertNotIn("each", moves)
        self.assertTrue(moves["nb"]["x"] >= 500 + GAP or moves["nb"]["y"] >= 300 + GAP)

    def test_can_resize(self):
        start = {"x": 100, "y": 100, "kids": [{"x": 50, "y": 90, "w": 100, "h": 60}]}
        self.assertTrue(can_resize(start, {"x": 100, "y": 100, "width": 400, "height": 300}))
        self.assertFalse(can_resize(start, {"x": 100, "y": 100, "width": 150 + PAD - 1, "height": 300}))
        self.assertFalse(can_resize(start, {"x": 100, "y": 100, "width": 400, "height": 90 + 60 + PAD - 1}))
        self.assertTrue(can_resize(start, {"x": 100 + 50 - PAD, "y": 100, "width": 400, "height": 300}))
        self.assertFalse(can_resize(start, {"x": 100 + 50 - PAD + 1, "y": 100, "width": 400, "height": 300}))
        self.assertFalse(can_resize(start, {"x": 100, "y": 100 + 90 - TOP + 1, "width": 400, "height": 300}))
        self.assertFalse(can_resize({"x": 0, "y": 0, "kids": []}, {"x": 0, "y": 0, "width": 50, "height": 50}))

    def test_fit_size(self):
        f = fit_size([{"x": PAD, "y": TOP, "w": 300, "h": 60}, {"x": 400, "y": 200, "w": 100, "h": 60}])
        self.assertEqual(f, {"w": 500 - PAD + PAD * 2, "h": 260 - TOP + PAD + TOP})

    def test_draw_manual_box_size(self):
        def wf(w, h):
            return {"nodes": [{"id": "each", "kind": "foreach", "max_items": 5, "body": {
                "inputs": {}, "outputs": {}, "nodes": [{"id": "a", "kind": "module", "module": "x"}], "edges": [], "layout": {"a": {"x": 0, "y": 0}}}}],
                "edges": [], "layout": {"each": {"x": 0, "y": 0, "w": w, "h": h}}}
        big = find(to_flow(wf(900, 500), {})["nodes"], "each")
        self.assertEqual([big["data"]["width"], big["data"]["height"], big["data"]["manual"]], [900, 500, {"w": 900, "h": 500}])
        small = find(to_flow(wf(50, 50), {})["nodes"], "each")
        self.assertTrue(small["data"]["width"] >= 196 and small["data"]["height"] > 50)

    def test_keep_box_size_on_move(self):
        wf = {"nodes": [{"id": "each", "kind": "foreach", "max_items": 5, "body": {"inputs": {}, "outputs": {}, "nodes": [], "edges": []}}], "edges": [],
              "layout": {"each": {"x": 0, "y": 0, "w": 800, "h": 400}}}
        self.assertEqual(move_node(wf, "each", {"x": 10, "y": 20})["layout"]["each"], {"x": 10, "y": 20, "w": 800, "h": 400})
        self.assertEqual(freeze_layout(wf, {"each": {"x": 5, "y": 5}})["layout"]["each"], {"x": 5, "y": 5, "w": 800, "h": 400})
        self.assertEqual(set_box_size(wf, "each", None)["layout"]["each"], {"x": 0, "y": 0})
        self.assertEqual(set_box_size(wf, "each", {"w": 300.4, "h": 200.6})["layout"]["each"], {"x": 0, "y": 0, "w": 300, "h": 201})

    def test_manual_box_records_grown_size(self):
        loop = box("each", 0, 0, 600, 300, type="loop", data={"width": 600, "height": 300, "manual": {"w": 600, "h": 300}})
        a = box("each/a", PAD, TOP, 100, 60, parentId="each")
        far = box("each/b", 900, TOP, 100, 60, parentId="each")
        sizes = {}
        settle([loop, a, far], ["each/b"], sizes_out=sizes)
        self.assertEqual(sizes["each"], {"w": 1000 + PAD, "h": 300})

    # ---------- 没拉伸过的框始终正好包住框里节点：按“拖动 → 整理 → 写回 → 重新画图”整条路验证 ----------

    def test_drag_leftmost_right_shrinks_box(self):
        wf = loop_wf({"a": {"x": 0, "y": 0}, "b": {"x": 600, "y": 0}})
        a = find(to_flow(wf, {})["nodes"], "each/a")
        before, after, _ = drag_round_trip(wf, {"each/a": {"x": a["position"]["x"] + 200, "y": a["position"]["y"]}})
        self.assertEqual(after["each/a"]["x"], before["each/a"]["x"] + 200)
        self.assertEqual(after["each/b"]["x"], before["each/b"]["x"])
        self.assertEqual(after["each"]["x"], after["each/a"]["x"] - PAD, "框左边收到 a 的左边")
        self.assertTrue(contains(after["each"], after["each/a"]) and contains(after["each"], after["each/b"]))

    def test_drag_out_left_grows_box(self):
        wf = loop_wf({"a": {"x": 0, "y": 0}, "b": {"x": 300, "y": 0}})
        a = find(to_flow(wf, {})["nodes"], "each/a")
        before, after, _ = drag_round_trip(wf, {"each/a": {"x": a["position"]["x"] - 150, "y": a["position"]["y"]}})
        self.assertEqual(after["each/a"]["x"], before["each/a"]["x"] - 150)
        self.assertEqual(after["each/b"]["x"], before["each/b"]["x"])
        self.assertEqual(after["each"]["x"], after["each/a"]["x"] - PAD)

    def test_nested_drag_out(self):
        wf = {"nodes": [{"id": "o", "kind": "foreach", "max_items": 5, "body": {"inputs": {}, "outputs": {}, "edges": [],
              "nodes": [{"id": "i", "kind": "foreach", "max_items": 5, "body": {"inputs": {}, "outputs": {}, "edges": [],
                         "nodes": [{"id": "x", "kind": "module", "module": "x"}, {"id": "y", "kind": "module", "module": "x"}],
                         "layout": {"x": {"x": 0, "y": 0}, "y": {"x": 300, "y": 0}}}},
                        {"id": "k", "kind": "module", "module": "x"}], "layout": {"i": {"x": 0, "y": 0}, "k": {"x": 0, "y": 400}}}}],
              "edges": [], "layout": {"o": {"x": 0, "y": 0}}}
        x = find(to_flow(wf, {})["nodes"], "o/i/x")
        before, after, _ = drag_round_trip(wf, {"o/i/x": {"x": x["position"]["x"] - 120, "y": x["position"]["y"]}})
        self.assertEqual(after["o/i/x"]["x"], before["o/i/x"]["x"] - 120)
        for i in ("o/i/y", "o/k"):
            self.assertEqual([after[i]["x"], after[i]["y"]], [before[i]["x"], before[i]["y"]], i)
        self.assertTrue(contains(after["o/i"], after["o/i/x"]) and contains(after["o"], after["o/i"]))

    def test_old_negative_coords_drag_one(self):
        wf = loop_wf({"a": {"x": -80, "y": 0}, "b": {"x": 200, "y": 40}})
        b = find(to_flow(wf, {})["nodes"], "each/b")
        before, after, _ = drag_round_trip(wf, {"each/b": {"x": b["position"]["x"] + 30, "y": b["position"]["y"]}})
        self.assertEqual(after["each/b"]["x"], before["each/b"]["x"] + 30)
        self.assertEqual([after["each/a"]["x"], after["each/a"]["y"]], [before["each/a"]["x"], before["each/a"]["y"]])
        self.assertTrue(contains(after["each"], after["each/a"]))

    def test_auto_box_drawn_at_offset(self):
        a = absolute(to_flow(loop_wf({"a": {"x": 200, "y": 50}, "b": {"x": 400, "y": 150}}), {})["nodes"])
        self.assertEqual(a["each"]["x"], a["each/a"]["x"] - PAD)
        self.assertEqual(a["each"]["y"], a["each/a"]["y"] - TOP)
        self.assertEqual(a["each"]["x"] + a["each"]["w"], a["each/b"]["x"] + a["each/b"]["w"] + PAD)
        self.assertEqual(a["each"]["y"] + a["each"]["h"], a["each/b"]["y"] + a["each/b"]["h"] + PAD)

    # ---------- GPT 审查（重写后第 1 轮）的回归测试 ----------

    def test_manual_outer_auto_inner_drag_out_left(self):
        outer = box("o", 100, 0, 1000, 400, type="loop", data={"width": 1000, "height": 400, "manual": {"w": 1000, "h": 400}})
        inner = box("o/i", -72, TOP, 252, 150, type="loop", parentId="o")
        deep = box("o/i/x", PAD, TOP, 196, 60, parentId="o/i")
        sib = box("o/s", 600, TOP, 196, 60, parentId="o")
        sizes = {}
        moves = settle([outer, inner, deep, sib], ["o/i"], sizes_out=sizes)
        self.assertEqual(moves["o"]["x"], 100 - (PAD + 72))
        self.assertEqual(sizes["o"], {"w": 1000 + PAD + 72, "h": 400})
        self.assertEqual(moves["o"]["x"] + moves["o/s"]["x"], 100 + 600, "兄弟节点在屏幕上不动")

    def test_manual_outer_negative_inner_round_trip(self):
        wf = {"nodes": [{"id": "o", "kind": "foreach", "max_items": 5, "body": {"inputs": {}, "outputs": {}, "edges": [],
              "nodes": [{"id": "i", "kind": "foreach", "max_items": 5, "body": {"inputs": {}, "outputs": {}, "edges": [],
                         "nodes": [{"id": "x", "kind": "module", "module": "x"}, {"id": "y", "kind": "module", "module": "x"}],
                         "layout": {"x": {"x": -80, "y": 0}, "y": {"x": 300, "y": 0}}}},
                        {"id": "k", "kind": "module", "module": "x"}], "layout": {"i": {"x": -50, "y": 0}, "k": {"x": 0, "y": 400}}}}],
              "edges": [], "layout": {"o": {"x": 0, "y": 0, "w": 1200, "h": 800}}}
        x = find(to_flow(wf, {})["nodes"], "o/i/x")
        before, after, _ = drag_round_trip(wf, {"o/i/x": {"x": x["position"]["x"] + 30, "y": x["position"]["y"]}})
        self.assertEqual(after["o/i/x"]["x"], before["o/i/x"]["x"] + 30)
        for i in ("o/i/y", "o/k"):
            self.assertEqual([after[i]["x"], after[i]["y"]], [before[i]["x"], before[i]["y"]], i)

    def test_all_negative_box_tight(self):
        nodes = to_flow(loop_wf({"a": {"x": -500, "y": -500}}), {})["nodes"]
        loop = find(nodes, "each")
        a = find(nodes, "each/a")
        self.assertEqual(loop["data"]["width"], max(196, rect_of(a)["w"] + PAD * 2))
        self.assertEqual(loop["data"]["height"], rect_of(a)["h"] + PAD + TOP)

    def test_first_open_auto_layout_no_overlap(self):
        wf = {"inputs": {"a": {"type": "any"}}, "outputs": {"b": {"type": "any"}},
              "nodes": [{"id": "each", "kind": "foreach", "max_items": 5, "body": {"inputs": {"item": {"type": "any"}}, "outputs": {"result": {"type": "any"}},
                         "nodes": [], "edges": [{"from": "$in.item", "to": "$out.result"}], "layout": {"$in": {"x": 300, "y": 0}, "$out": {"x": 500, "y": 0}}}}],
              "edges": [{"from": "$in.a", "to": "each.items"}, {"from": "each.results", "to": "$out.b"}]}
        a = absolute(to_flow(wf, {})["nodes"])
        top = [a[i] for i in ("$in", "each", "$out")]
        for p in top:
            for q in top:
                if p is not q:
                    self.assertFalse(overlaps(p, q, 0), [p, q])


if __name__ == "__main__":
    unittest.main()
