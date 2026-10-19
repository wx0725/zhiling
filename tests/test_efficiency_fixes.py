"""效率与严谨性审查报告（2026-10-17）Q03–Q06 的回归测试。
按准备次数、读盘次数和处理量断言，不绑定某台机器的毫秒数。"""
import asyncio
import json
import random
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import server.app as appmod
from core import worker
from core.engine import Engine, WorkflowInvalid
from core.registry import load_registry
from core.validate import validate_workflow

REG = load_registry()
SLOW = "tests.fixtures.slow_module.module"


def wf(inputs, outputs, nodes, edges, wid="t"):
    return {"spec": "0.2", "id": wid, "name": "测试", "intent": "", "inputs": inputs, "outputs": outputs,
            "nodes": nodes, "edges": edges}


IDENTITY_CHILD = wf({"x": {"type": "integer"}}, {"y": {"type": "integer"}}, [], [{"from": "$in.x", "to": "$out.y"}], "child")


def loop_over_ref(n_items: int):
    body = {"inputs": {"item": {"type": "integer"}}, "outputs": {"result": {"type": "integer"}},
            "nodes": [{"id": "s", "kind": "subflow", "ref": "child.workflow.json"}],
            "edges": [{"from": "$in.item", "to": "s.x"}, {"from": "s.y", "to": "$out.result"}]}
    return wf({"xs": {"type": "list<integer>"}}, {"ys": {"type": "list<integer>"}},
              [{"id": "each", "kind": "foreach", "max_items": n_items, "body": body}],
              [{"from": "$in.xs", "to": "each.items"}, {"from": "each.results", "to": "$out.ys"}], "parent")


class Q03_OnePreparationPerRun(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.child = self.dir / "child.workflow.json"
        self.child.write_text(json.dumps(IDENTITY_CHILD), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_ref_read_once_and_graphs_prepared_once(self):
        eng = Engine(REG)
        res = eng.run(loop_over_ref(300), {"xs": list(range(300))}, self.dir)
        self.assertEqual(res.status, "completed")
        self.assertEqual(res.outputs["ys"], list(range(300)))
        self.assertEqual(eng._snap.reads, 1)    # 校验和 300 次执行共用一次读取
        self.assertEqual(eng.plans_built, 3)    # 外层、循环体、子流程各准备一次

    def test_run_uses_validated_content_even_if_file_changes(self):
        broken = {**IDENTITY_CHILD, "nodes": [{"id": "m", "kind": "module", "module": "missing.module@0"}]}

        def on_event(ev):  # 校验之后、执行途中，别的请求改写了子流程文件
            if ev["type"] == "node_start" and ev["path"] == "parent.each":
                self.child.write_text(json.dumps(broken), encoding="utf-8")

        eng = Engine(REG, on_event=on_event)
        res = eng.run(loop_over_ref(3), {"xs": [1, 2, 3]}, self.dir)
        self.assertEqual((res.status, res.outputs), ("completed", {"ys": [1, 2, 3]}))
        with self.assertRaises(WorkflowInvalid):  # 下一次运行看得到更新
            eng.run(loop_over_ref(3), {"xs": [1]}, self.dir)


def merge_chain(n: int):
    nodes = [{"id": f"m{i}", "kind": "merge"} for i in range(n)]
    edges = [{"from": "$in.x", "to": "m0.a"}] + [{"from": f"m{i - 1}.value", "to": f"m{i}.a"} for i in range(1, n)]
    edges.append({"from": f"m{n - 1}.value", "to": "$out.y"})
    return wf({"x": {"type": "integer"}}, {"y": {"type": "integer"}}, nodes, edges)


def old_topo(nodes, data_in, control_in):
    """修改前的逐轮扫描实现，用来对照新实现的顺序完全一致。"""
    deps = {nid: {s.split(".", 1)[0] for s in list(data_in[nid].values()) + control_in[nid]} - {"$in"} for nid in nodes}
    order, done, pending = [], set(), list(nodes)
    while pending:
        ready = [n for n in pending if deps[n] <= done]
        for n in ready:
            order.append(n)
            done.add(n)
        pending = [n for n in pending if n not in done]
    return order


class Q04_LongChains(unittest.TestCase):
    def test_long_chain_validates_and_runs(self):
        g = merge_chain(3000)  # 修改前 1,100 节点就 RecursionError
        self.assertEqual(validate_workflow(g, REG), [])
        res = Engine(REG).run(g, {"x": 5})
        self.assertEqual((res.status, res.outputs), ("completed", {"y": 5}))

    def test_type_inference_is_linear_on_long_chain(self):
        """长链上每个端点只推断一次（修改前每条连线都沿整条链往上游走，4,000 节点约 800 万步）。"""
        from core.validate import _Graph
        for n in (1000, 4000):
            g = _Graph(merge_chain(n), "t", REG, Path("."), [], 0)
            g.check()
            self.assertEqual(g.errors, [])
            self.assertLessEqual(g.infer_steps, 3 * n, n)

    def test_long_cycle_reported_not_crash(self):
        g = merge_chain(3000)
        g["nodes"][0]["ports"] = ["a", "b"]
        g["edges"].append({"from": "m2999.value", "to": "m0.b"})
        errors = validate_workflow(g, REG)
        self.assertTrue(any("连线形成环" in e for e in errors), errors[:3])

    def test_long_chain_validate_route(self):
        from fastapi.testclient import TestClient
        r = TestClient(appmod.app).post("/api/validate", json={"workflow": merge_chain(2000)})
        self.assertEqual((r.status_code, r.json()), (200, {"errors": []}))

    def test_order_identical_to_previous_algorithm(self):
        rnd = random.Random(7)
        for _ in range(300):
            n = rnd.randint(1, 30)
            ids = [f"n{i}" for i in rnd.sample(range(100), n)]
            data_in = {nid: {} for nid in ids}
            control_in = {nid: [] for nid in ids}
            for j, nid in enumerate(ids):  # 只连向书写顺序更早的节点 → 无环；书写顺序再打乱
                for k in rnd.sample(range(j), min(j, rnd.randint(0, 3))):
                    if rnd.random() < 0.3:
                        control_in[nid].append(f"{ids[k]}.then")
                    else:
                        data_in[nid][f"p{k}"] = f"{ids[k]}.out"
            shuffled = ids[:]
            rnd.shuffle(shuffled)
            nodes = {nid: {} for nid in shuffled}
            self.assertEqual(Engine._topo_order(nodes, data_in, control_in), old_topo(nodes, data_in, control_in))


class Q05_StopInsideEmptyLoopBody(unittest.TestCase):
    def test_zero_node_body_stops(self):
        body = {"inputs": {"item": {"type": "integer"}}, "outputs": {"result": {"type": "integer"}},
                "nodes": [], "edges": [{"from": "$in.item", "to": "$out.result"}]}
        g = wf({"xs": {"type": "list<integer>"}}, {"ys": {"type": "list<integer>"}},
               [{"id": "each", "kind": "foreach", "max_items": 10000, "body": body}],
               [{"from": "$in.xs", "to": "each.items"}, {"from": "each.results", "to": "$out.ys"}])
        flag = {"stop": False, "calls": 0}

        def should_stop():
            flag["calls"] += 1
            return flag["stop"]

        def on_event(ev):
            if ev["type"] == "node_start" and ev["path"] == "t.each":
                flag["stop"] = True

        res = Engine(REG, on_event=on_event, should_stop=should_stop).run(g, {"xs": list(range(10000))})
        self.assertEqual((res.status, res.error["code"]), ("failed", "cancelled"))
        self.assertLess(flag["calls"], 10)  # 修改前跑完全部 10,000 项


class Q06_WorkerBounds(unittest.TestCase):
    def setUp(self):
        worker.shutdown()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        worker.shutdown()
        self.tmp.cleanup()

    def args(self, i, seconds=0.3):
        return {"seconds": seconds, "marker": str(Path(self.tmp.name) / f"m{i}")}

    def run_parallel(self, n, seconds=0.3):
        ts = [threading.Thread(target=worker.call, args=(SLOW, "run", self.args(i, seconds), 30)) for i in range(n)]
        t0 = time.monotonic()
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        return time.monotonic() - t0

    def test_idle_pool_capped(self):
        self.run_parallel(6, 0.1)
        self.assertLessEqual(worker.idle_count(), worker.MAX_IDLE)  # 修改前 6 个都留着

    def test_idle_workers_reaped(self):
        worker.call(SLOW, "run", self.args(0, 0), 30)
        self.assertEqual(worker.idle_count(), 1)
        worker._reap(time.monotonic() + worker.IDLE_SECONDS + 1)
        self.assertEqual(worker.idle_count(), 0)

    def test_concurrency_capped(self):
        with mock.patch.object(worker, "_SLOTS", threading.BoundedSemaphore(2)):
            worker.call(SLOW, "run", self.args(99, 0), 30)  # 先热起一个进程，减少计时里的启动时间
            elapsed = self.run_parallel(4, 0.6)
        self.assertGreaterEqual(elapsed, 1.2)  # 4 个调用、同时最多 2 个 → 至少两批

    def test_waiting_for_slot_honours_stop(self):
        with mock.patch.object(worker, "_SLOTS", threading.BoundedSemaphore(1)):
            worker._SLOTS.acquire()
            with self.assertRaises(worker.WorkerStopped):
                worker.call(SLOW, "run", self.args(0), 30, should_stop=lambda: True)


class Q06_BoundedStream(unittest.TestCase):
    def test_slow_consumer_disconnect_does_not_block_worker(self):
        finished = threading.Event()

        def work(emit, should_stop):
            for i in range(200):
                emit({"type": "tick", "i": i})
            finished.set()

        class Gone:
            n = 0

            async def is_disconnected(self):
                Gone.n += 1
                return Gone.n > 2

        async def consume():
            it = appmod._stream(Gone(), work).body_iterator
            await it.__anext__()  # 只读一条，然后断开
            await asyncio.sleep(0.5)
            async for _ in it:
                pass

        with mock.patch.object(appmod, "STREAM_QUEUE", 5):
            asyncio.run(consume())
        self.assertTrue(finished.wait(5), "连接断开后，后台线程被满队列堵住了")


if __name__ == "__main__":
    unittest.main()
