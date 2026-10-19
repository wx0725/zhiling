"""执行引擎（接口规范第 6 节）。

run_workflow(wf, inputs) 先校验，再按数据线 + 控制线的拓扑顺序逐个运行节点：
- 跳过传播：任一已连接输入来自被跳过的节点，或任一控制线起点没被选中 → 本节点跳过（merge 例外）
- 出错即停：返回失败状态、出错节点和已有的运行记录
- 每一层图（工作流、循环体、子流程）的入口和出口都按声明的类型检查实际的值
- 模块在独立进程里运行，超时或停止时真正结束它（见 core/worker.py）
- 每个节点留下运行记录（实际收到的参数、输出、耗时、状态），供前端在图上显示，也用于语义检查
v0 按顺序执行；规范允许的并行留待以后。
"""
import json
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core import worker
from core.errors import ModuleError
from core.paths import ref_within
from core.registry import ROOT, ModuleSpec, load_registry
from core.validate import RefSnapshot, validate_workflow, value_matches


class _Skipped:
    def __repr__(self):
        return "SKIPPED"


SKIPPED = _Skipped()  # 被跳过支路上的“值”
FIRED = True          # 不带 value 的 if：被选中的一侧只带一个“已选中”信号


class WorkflowInvalid(Exception):
    def __init__(self, errors: list[str]):
        super().__init__("工作流没有通过校验：\n" + "\n".join(errors))
        self.errors = errors


class _Stop(Exception):
    """某个节点出错（或用户停止），整个运行停止。"""

    def __init__(self, path: str, code: str, message: str):
        super().__init__(f"{path}：{code}：{message}")
        self.path, self.code, self.message = path, code, message


@dataclass
class NodeRecord:
    path: str
    kind: str
    status: str                      # completed / skipped / failed
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)
    seconds: float = 0.0
    reason: str = ""                 # 跳过或出错的原因


@dataclass
class RunResult:
    status: str                      # completed / failed
    outputs: dict
    records: list[NodeRecord]
    error: dict | None = None
    warnings: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def to_json(self) -> dict:
        def clean(v, limit=None):
            """limit 只用于节点记录的预览；最终输出必须原样完整返回。"""
            if v is SKIPPED:
                return "<跳过>"
            if isinstance(v, dict):
                return {k: clean(x, limit) for k, x in v.items()}
            if isinstance(v, list):
                return [clean(x, limit) for x in v]
            if limit and isinstance(v, str) and len(v) > limit:  # 记录里截断长文本（如整页 HTML）
                return v[:limit] + f"…（共 {len(v)} 字）"
            return v
        preview = lambda v: clean(v, 2000)  # noqa: E731
        return {"status": self.status, "seconds": round(self.seconds, 2), "outputs": clean(self.outputs),
                "error": self.error, "warnings": self.warnings,
                "records": [{"path": r.path, "kind": r.kind, "status": r.status, "seconds": round(r.seconds, 2),
                             "reason": r.reason, "inputs": preview(r.inputs), "outputs": preview(r.outputs)} for r in self.records]}


def _dump(v) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)[:100]


def _target_path(args: dict) -> str | None:
    """这次调用涉及的文件路径（供确认界面显示）：取参数里第一个像路径的字符串。"""
    for k in ("path", "source", "folder", "to_folder"):
        v = args.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return None


class Engine:
    def __init__(self, reg: dict[str, ModuleSpec] | None = None, *,
                 overrides: dict[str, Callable[[dict], dict]] | None = None,
                 confirm: Callable[[ModuleSpec, str, str | None], bool] | None = None,
                 on_event: Callable[[dict], None] | None = None,
                 timeout_override: float | None = None,
                 should_stop: Callable[[], bool] | None = None,
                 should_pause: Callable[[], bool] | None = None,
                 rehearse: set[str] | None = None,
                 ask: Callable[[str, dict], dict | None] | None = None):
        """overrides：按“模块id@主版本”替换实现（测试用，在线程里运行，超时无法强制结束）。
        confirm：副作用为 write 的模块、以及正式运行中副作用为 read 的模块（读他人电脑上的文件也等同运行他人程序），
            首次运行前调用 confirm(spec, 节点路径, 目标路径或 None)，返回 False 则停止。默认拒绝，必须显式提供。
            目标路径取自参数里的 path / source / folder / to_folder（供界面显示“会读取/改动哪个文件”）。
            实跑检查（rehearse 非空）里 read 不确认——它本来就不改动东西。
        on_event：节点开始/结束时回调，供前端实时显示。
        should_stop：返回真时停止运行（正在运行的模块进程会被结束）。
        should_pause：每个节点开始前检查，返回真时先发 paused 事件、原地等到它变假（发 resumed）或被停止；
            正在运行的节点不会被打断，跑完后才停在下一个节点前。
        rehearse：这些写模块改为演练（调用 runtime.dry_run，同样在独立进程里、可停止），不需要确认、不改动任何东西；
            会问使用者的模块（interaction 为 ask）在演练时不问，按没有回答运行。
        ask：会问使用者的模块运行前调用 ask(路径, 要问的内容)，等使用者回答后返回回答（字典）；返回 None 表示没等到（已停止）。
            不提供时按“没人回答”运行（命令行里就是这样：模块按默认回答继续）。
        同一个 Engine 可以先后运行多次，但不能在多个线程里同时运行（每次运行的准备结果存在实例上）。"""
        self.reg = reg if reg is not None else load_registry()
        self.overrides = overrides or {}
        self.confirm = confirm
        self.on_event = on_event or (lambda e: None)
        self.timeout_override = timeout_override
        self.should_stop = should_stop or (lambda: False)
        self.should_pause = should_pause or (lambda: False)
        self.rehearse = set(rehearse or ())
        self.ask = ask
        self._confirmed: set[str] = set()
        self._snap = RefSnapshot()
        self._plans: dict[int, tuple[dict, tuple]] = {}
        self.plans_built = 0  # 图准备次数（测试用）

    # ---------- 对外入口 ----------
    def run(self, wf: dict, inputs: dict, base_dir: Path = ROOT) -> RunResult:
        # 每次运行一份子流程快照：校验读到的内容就是执行用的内容；下一次运行重新读取
        self._snap, self._plans = RefSnapshot(), {}
        errors = validate_workflow(wf, self.reg, base_dir, snap=self._snap)
        if errors:
            raise WorkflowInvalid(errors)
        records: list[NodeRecord] = []
        t0 = time.time()
        try:
            outputs, warnings = self._run_graph(wf, inputs, wf["id"], records, base_dir)
            return RunResult("completed", outputs, records, warnings=warnings, seconds=time.time() - t0)
        except _Stop as s:
            return RunResult("failed", {}, records, {"path": s.path, "code": s.code, "message": s.message},
                             seconds=time.time() - t0)

    # ---------- 一层图 ----------
    @staticmethod
    def _check_inputs(g: dict, given: dict, path: str) -> dict:
        """一层图的入口：补默认值后统一按声明类型检查；未提供的可选输入不出现在结果里。"""
        values = {}
        for name, p in g["inputs"].items():
            if name in given:
                values[name] = given[name]
            elif "default" in p:
                values[name] = p["default"]
            elif p.get("required", True):
                raise _Stop(path, "missing_input", f"缺少输入 {name}（{p['type']}）")
            else:
                continue
            if not value_matches(p["type"], values[name]):
                raise _Stop(path, "input_type", f"输入 {name} 应为 {p['type']}，收到 {_dump(values[name])}")
        return values

    def _plan(self, g: dict) -> tuple:
        """一层图的静态结构（节点、入边、控制线、输出线、执行顺序）。每次运行内每张图只准备一次，循环各项复用。"""
        hit = self._plans.get(id(g))
        if hit is not None and hit[0] is g:
            return hit[1]
        nodes = {n["id"]: n for n in g["nodes"]}
        data_in: dict[str, dict[str, str]] = {nid: {} for nid in nodes}   # 节点 -> 输入端口 -> 来源端点
        control_in: dict[str, list[str]] = {nid: [] for nid in nodes}     # 节点 -> 控制线起点
        out_edges = []
        for e in g["edges"]:
            src, dst = e["from"], e["to"]
            if "." not in dst:
                control_in[dst].append(src)
            elif dst.startswith("$out."):
                out_edges.append((src, dst[5:]))
            else:
                dn, dp = dst.split(".", 1)
                data_in[dn][dp] = src
        plan = (nodes, data_in, control_in, out_edges, self._topo_order(nodes, data_in, control_in))
        self._plans[id(g)] = (g, plan)  # 同时保存 g，保证 id 在本次运行内不被复用
        self.plans_built += 1
        return plan

    def _run_graph(self, g: dict, given: dict, path: str, records: list, base_dir: Path) -> tuple[dict, list]:
        if self.should_stop():  # 每进入一层图都检查：没有内部节点的循环体也能停下
            raise _Stop(path, "cancelled", "已停止")
        inputs = self._check_inputs(g, given, path)
        nodes, data_in, control_in, out_edges, order = self._plan(g)

        values: dict[str, Any] = {f"$in.{k}": v for k, v in inputs.items()}
        for nid in order:
            node, npath = nodes[nid], f"{path}.{nid}"
            self._hold(npath)
            if self.should_stop():
                raise _Stop(path, "cancelled", "已停止")
            srcs = data_in[nid]
            got = {p: values[s] for p, s in srcs.items() if s in values}  # 未提供的可选输入视为没接
            blocked = [s for s in control_in[nid] if values[s] is SKIPPED]
            if node["kind"] == "merge":
                skip_reason = "所有输入都被跳过" if got and all(v is SKIPPED for v in got.values()) else ""
            else:
                dead = [f"{p}（来自 {srcs[p]}）" for p, v in got.items() if v is SKIPPED]
                skip_reason = (f"输入 {'、'.join(dead)} 所在支路被跳过" if dead else "") or \
                              (f"控制线 {'、'.join(blocked)} 没被选中" if blocked else "")
            if blocked and node["kind"] == "merge":
                skip_reason = f"控制线 {'、'.join(blocked)} 没被选中"
            if skip_reason:
                for port in self._output_ports(node, base_dir):
                    values[f"{nid}.{port}"] = SKIPPED
                records.append(NodeRecord(npath, node["kind"], "skipped", reason=skip_reason))
                self.on_event({"type": "node_skipped", "path": npath, "reason": skip_reason})
                continue

            self.on_event({"type": "node_start", "path": npath})
            t = time.time()
            rec = NodeRecord(npath, node["kind"], "completed", inputs=dict(got))
            records.append(rec)
            try:
                self._require(node, got, srcs, npath)
                outs = self._run_node(node, got, npath, records, base_dir, rec)
            except _Stop as s:
                rec.status, rec.reason, rec.seconds = "failed", f"{s.code}：{s.message}", time.time() - t
                self.on_event({"type": "node_failed", "path": npath, "code": s.code, "message": s.message})
                raise
            rec.seconds, rec.outputs = time.time() - t, outs
            for port, v in outs.items():
                values[f"{nid}.{port}"] = v
            self.on_event({"type": "node_end", "path": npath, "seconds": rec.seconds})

        outputs, warnings = {}, []
        for src, name in out_edges:
            v = values.get(src, SKIPPED)
            if v is SKIPPED:
                why = "被跳过" if src in values else "没有提供"
                warnings.append(f"{path}：输出 {name} 的来源 {src} {why}，没有产生值")
                outputs[name] = None
                continue
            want = g["outputs"][name]["type"]
            if not value_matches(want, v):  # 出口检查：any 收窄到具体类型的地方在这里把关
                raise _Stop(path, "output_type", f"输出 {name} 应为 {want}，实际是 {_dump(v)}")
            outputs[name] = v
        return outputs, warnings

    def _hold(self, path: str):
        """暂停：停在 path 这个节点之前，等到继续或停止"""
        if not self.should_pause() or self.should_stop():
            return
        self.on_event({"type": "paused", "path": path})
        while self.should_pause() and not self.should_stop():
            time.sleep(0.05)
        if not self.should_stop():
            self.on_event({"type": "resumed", "path": path})

    def _require(self, node: dict, got: dict, srcs: dict, path: str):
        """必需输入的来源可能是没提供的可选输入：明确报 missing_input，而不是在取值时崩溃。"""
        kind = node["kind"]
        if kind == "module":
            spec = self.reg[node["module"]]
            need = [n for n, p in spec.inputs.items()
                    if p.get("required", True) and "default" not in p and n not in node.get("config", {})]
        elif kind == "if":
            need = ["condition"]
        elif kind == "foreach":
            need = ["items"]
        else:
            return  # merge 全是可选；subflow 由子图入口检查
        missing = [n for n in need if n not in got]
        if missing:
            detail = "、".join(f"{n}（来源 {srcs[n]} 没有提供）" if n in srcs else n for n in missing)
            raise _Stop(path, "missing_input", f"必需输入 {detail}")

    @staticmethod
    def _topo_order(nodes: dict, data_in: dict, control_in: dict) -> list[str]:
        """按层排序（入度 + 就绪队列，O(节点+连线)）：每一层是上一层完成后可以运行的节点，层内保持图里的书写顺序。
        与逐轮扫描的结果完全相同，只是长链上不再是平方复杂度。"""
        pos = {nid: i for i, nid in enumerate(nodes)}
        deps = {nid: {s.split(".", 1)[0] for s in list(data_in[nid].values()) + control_in[nid]} - {"$in"}
                for nid in nodes}
        users: dict[str, list[str]] = {nid: [] for nid in nodes}
        for nid, ds in deps.items():
            for d in ds:
                if d in users:
                    users[d].append(nid)
        left = {nid: len(ds) for nid, ds in deps.items()}
        level = [nid for nid in nodes if left[nid] == 0]
        order = []
        while level:
            order.extend(level)
            nxt = []
            for n in level:
                for u in users[n]:
                    left[u] -= 1
                    if left[u] == 0:
                        nxt.append(u)
            level = sorted(nxt, key=pos.__getitem__)
        if len(order) != len(nodes):  # 校验器已经排除了环，这里只是保险
            raise _Stop("", "cycle", "连线形成环")
        return order

    def _output_ports(self, node: dict, base_dir: Path) -> list[str]:
        kind = node["kind"]
        if kind == "module":
            return list(self.reg[node["module"]].outputs)
        if kind == "if":
            return ["then", "else"]
        if kind == "merge":
            return ["value"]
        if kind == "foreach":
            return ["results"]
        return list(self._subflow_body(node, base_dir)[0]["outputs"])

    def _subflow_body(self, node: dict, base_dir: Path) -> tuple[dict, Path]:
        if "ref" in node:  # 从本次运行的快照取：和校验时读到的是同一份，循环里也不重复读盘
            ref_path = (base_dir / node["ref"]).resolve()
            # 目录包含校验，口径与校验器 / api/ports 一致：ref 只能指向项目根或当前目录内的文件
            if not ref_within(ref_path, base_dir):
                raise _Stop(node.get("id", ""), "ref_blocked", f"子流程引用越出了允许的目录：{node['ref']}")
            return self._snap.load(ref_path), ref_path.parent
        return node["body"], base_dir

    # ---------- 各类节点 ----------
    def _run_node(self, node: dict, got: dict, path: str, records: list, base_dir: Path, rec: NodeRecord) -> dict:
        kind = node["kind"]
        if kind == "module":
            return self._run_module(node, got, path, rec)
        if kind == "if":
            cond = got["condition"]
            if not isinstance(cond, bool):
                raise _Stop(path, "type_error", f"condition 应为真或假，收到 {cond!r}")
            v = got.get("value", FIRED)
            return {"then": v if cond else SKIPPED, "else": SKIPPED if cond else v}
        if kind == "merge":
            for port in node.get("ports", ["a", "b"]):
                if port in got and got[port] is not SKIPPED:
                    return {"value": got[port]}
            raise _Stop(path, "merge_empty", "merge 没有任何到达的输入")
        if kind == "foreach":
            return self._run_foreach(node, got, path, records, base_dir)
        if kind == "subflow":
            return self._run_subflow(node, got, path, records, base_dir)
        raise _Stop(path, "unknown_kind", f"未知节点类型 {kind}")

    def _run_module(self, node: dict, got: dict, path: str, rec: NodeRecord) -> dict:
        key = node["module"]
        spec = self.reg[key]
        args = {}
        for name, p in spec.inputs.items():  # 默认值 < config < 连线
            if "default" in p:
                args[name] = p["default"]
        args.update(node.get("config", {}))
        args.update(got)
        rec.inputs = dict(args)  # 记录模块实际收到的全部参数，可据此单独重放这次调用
        for name, v in args.items():
            if name in spec.inputs and not value_matches(spec.inputs[name]["type"], v):
                raise _Stop(path, "type_error", f"输入 {name} 应为 {spec.inputs[name]['type']}，收到 {_dump(v)}")
        asks = spec.data.get("interaction") == "ask"
        rehearsing = key in self.rehearse and ("dry_run" in spec.data["runtime"] or asks)
        if asks and not rehearsing:
            # 停下来问使用者：先发事件让界面弹出提示，等回答回来再把它交给模块
            prompt = {k: v for k, v in args.items() if k != "answer"}
            self.on_event({"type": "ask", "path": path, "prompt": prompt})
            answer = self.ask(path, prompt) if self.ask else {}
            if answer is None:
                raise _Stop(path, "cancelled", "已停止")
            args["answer"] = answer if isinstance(answer, dict) else {}
            rec.inputs = dict(args)
            self.on_event({"type": "answered", "path": path, "answer": args["answer"]})
        effects = spec.data["side_effects"]
        # write 一律确认；read 在正式运行时也确认（读文件同样可能外泄），但实跑检查(rehearse)里不确认
        needs_confirm = effects == "write" or (effects == "read" and not self.rehearse)
        if needs_confirm and not rehearsing and key not in self._confirmed:
            target = _target_path(args)
            if not (self.confirm and self.confirm(spec, path, target)):
                verb = "读取你电脑上的文件" if effects == "read" else "写文件或产生外部影响"
                raise _Stop(path, "not_confirmed", f"模块 {key} 会{verb}，未获得确认")
            self._confirmed.add(key)
        timeout = self.timeout_override or spec.data["runtime"]["timeout_s"]
        try:
            if key in self.overrides:
                outs = self._call_in_thread(self.overrides[key], args, timeout, path)
            else:
                entry = "dry_run" if rehearsing and "dry_run" in spec.data["runtime"] else "entry"
                file_name, func = spec.data["runtime"][entry].split(":")
                rel = spec.path.relative_to(ROOT).parts
                outs = worker.call(".".join(rel + (Path(file_name).stem,)), func, args, timeout, self.should_stop)
        except worker.WorkerTimeout:
            raise _Stop(path, "timeout", f"超过 {timeout} 秒没有完成，已结束该模块") from None
        except worker.WorkerStopped:
            raise _Stop(path, "cancelled", "已停止") from None
        except worker.WorkerCrash as e:
            raise _Stop(path, "module_crash", str(e)) from None
        except ModuleError as e:
            raise _Stop(path, e.code, e.message) from None
        if not isinstance(outs, dict) or set(outs) != set(spec.outputs):
            raise _Stop(path, "bad_output", f"模块返回的字段 {sorted(outs) if isinstance(outs, dict) else type(outs).__name__} 与描述的输出 {sorted(spec.outputs)} 不一致")
        for name, v in outs.items():
            if not value_matches(spec.outputs[name]["type"], v):
                raise _Stop(path, "bad_output", f"输出 {name} 应为 {spec.outputs[name]['type']}，实际 {type(v).__name__}")
        return outs

    @staticmethod
    def _call_in_thread(fn, args: dict, timeout: float, path: str) -> dict:
        """测试替身在线程里运行。区分“等待期限到了”和“模块自己抛出的超时异常”。"""
        pool = ThreadPoolExecutor(max_workers=1)
        fut = pool.submit(fn, args)
        try:
            return fut.result(timeout=timeout)
        except TimeoutError:
            if not fut.done():  # 等待期限到了
                raise _Stop(path, "timeout", f"超过 {timeout} 秒没有完成") from None
            raise _Stop(path, "module_crash", f"模块抛出超时异常：{fut.exception()}") from None
        except ModuleError:
            raise
        except Exception as e:  # 模块自身的 bug 也要停下来并报告
            raise _Stop(path, "module_crash", f"{type(e).__name__}: {e}") from None
        finally:
            pool.shutdown(wait=False)

    def _run_foreach(self, node: dict, got: dict, path: str, records: list, base_dir: Path) -> dict:
        items = got["items"]
        if not isinstance(items, list):
            raise _Stop(path, "type_error", "items 应为列表")
        if len(items) > node["max_items"]:
            raise _Stop(path, "too_many_items", f"共有 {len(items)} 项，超过上限 max_items={node['max_items']}")
        results = []
        for i, item in enumerate(items):  # 每一项都经过循环体入口的类型检查（_run_graph 入口也检查停止）
            outs, warns = self._run_graph(node["body"], {"item": item}, f"{path}[{i}]", records, base_dir)
            if outs.get("result") is None and warns:
                raise _Stop(f"{path}[{i}]", "no_result", f"第 {i + 1} 项没有产生结果（{warns[0]}）")
            results.append(outs.get("result"))
        return {"results": results}

    def _run_subflow(self, node: dict, got: dict, path: str, records: list, base_dir: Path) -> dict:
        body, sub_dir = self._subflow_body(node, base_dir)
        outs, warns = self._run_graph(body, got, path, records, sub_dir)
        if warns:
            raise _Stop(path, "no_result", warns[0])
        return outs


def run_workflow(wf: dict, inputs: dict, base_dir: Path = ROOT, **engine_kwargs) -> RunResult:
    return Engine(**engine_kwargs).run(wf, inputs, base_dir)
