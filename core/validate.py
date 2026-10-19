"""工作流图与模块描述校验（接口规范第 7 节）。

返回中文错误列表；空列表表示通过。错误信息会原样反馈给生成图的 AI，因此要写清位置和原因。
"""
import json
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from core import types
from core.paths import ref_within
from core.registry import ROOT, ModuleSpec

SCHEMA_DIR = ROOT / "schemas"
_schemas = {n: json.loads((SCHEMA_DIR / n).read_text(encoding="utf-8"))
            for n in ("workflow.schema.json", "module.schema.json")}
_ref_registry = Registry().with_resources(
    (n, Resource.from_contents(s)) for n, s in _schemas.items())
_wf_validator = Draft202012Validator(_schemas["workflow.schema.json"], registry=_ref_registry)
_mod_validator = Draft202012Validator(_schemas["module.schema.json"], registry=_ref_registry)


def _schema_errors(validator, doc) -> list[str]:
    out = []
    for e in sorted(validator.iter_errors(doc), key=lambda e: list(e.absolute_path)):
        loc = "/".join(str(p) for p in e.absolute_path) or "(根)"
        out.append(f"结构错误 {loc}：{e.message}")
    return out


def value_matches(t: str, v) -> bool:
    """值与端口类型是否相符。null 只有 any 能接受：“可选”指可以不提供，而不是可以给 null。"""
    if t == "any":
        return True
    if v is None or not types.is_valid(t):
        return False
    item = types.list_item(t)
    if item is not None:
        return isinstance(v, list) and all(value_matches(item, x) for x in v)
    return {
        "string": lambda: isinstance(v, str),
        "file": lambda: isinstance(v, str),
        "boolean": lambda: isinstance(v, bool),
        "integer": lambda: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda: isinstance(v, (int, float)) and not isinstance(v, bool),
        "object": lambda: isinstance(v, dict),
    }[t]()


class RefSnapshot:
    """一次运行内的子流程文件快照：同一个文件只读一次，校验和执行用的是同一份内容。
    下一次运行新建快照，所以能看到文件的更新；不做跨运行的全局缓存。"""

    def __init__(self):
        self._docs: dict[Path, object] = {}
        self.reads = 0  # 实际读盘次数（测试用）

    def load(self, path: Path):
        path = path.resolve()
        if path not in self._docs:
            self.reads += 1
            self._docs[path] = json.loads(path.read_text(encoding="utf-8"))  # 读不到或不是 JSON 时抛出，不缓存
        return self._docs[path]


class _Graph:
    """检查一层图（工作流本身、foreach.body 或 subflow.body）。"""

    def __init__(self, g: dict, where: str, reg: dict[str, ModuleSpec], base_dir: Path, errors: list, depth: int,
                 refs: tuple = (), snap: RefSnapshot | None = None):
        self.g, self.where, self.reg, self.base_dir, self.errors, self.depth = g, where, reg, base_dir, errors, depth
        self.refs = refs  # 正在校验的子流程文件链，用来发现循环引用
        self.snap = snap or RefSnapshot()
        self.nodes: dict[str, dict] = {}
        self.ins: dict[str, dict[str, dict]] = {}     # 节点 -> 输入端口 -> {type, required}
        self.outs: dict[str, dict[str, str | None]] = {}  # 节点 -> 输出端口 -> 类型（None 表示由输入推断）
        self.incoming: dict[tuple[str, str], str] = {}    # (节点, 输入端口) -> 来源端点
        self._types: dict[str, str | None] = {}           # 端点 -> 推断出的类型（source_type 的缓存）
        self.infer_steps = 0                               # 类型推断实际走了多少步（测试用）

    def err(self, msg: str):
        self.errors.append(f"{self.where}：{msg}")

    # ---------- 端口 ----------
    def _ports_of(self, n: dict):
        nid, kind = n["id"], n["kind"]
        if kind == "module":
            spec = self.reg.get(n["module"])
            if spec is None:
                known = "、".join(sorted(self.reg)) or "（无）"
                self.err(f"节点 {nid} 引用了不存在的模块 {n['module']}。可用模块：{known}")
                return None, None
            ins = {k: {"type": p["type"], "required": p.get("required", True) and "default" not in p}
                   for k, p in spec.inputs.items()}
            return ins, {k: p["type"] for k, p in spec.outputs.items()}
        if "config" in n:
            self.err(f"节点 {nid}：只有模块节点可以使用 config")
        if kind == "if":
            # v0.2：value 可选；不带 value 时 then/else 只能接控制线
            return ({"condition": {"type": "boolean", "required": True}, "value": {"type": "any", "required": False}},
                    {"then": None, "else": None})
        if kind == "merge":
            names = n.get("ports", ["a", "b"])
            return {p: {"type": "any", "required": False} for p in names}, {"value": None}
        if kind == "foreach":
            body = n["body"]
            if set(body["inputs"]) != {"item"} or set(body["outputs"]) != {"result"}:
                self.err(f"节点 {nid}：foreach 的 body 必须恰好有输入 item 和输出 result")
            item_t = body["inputs"].get("item", {}).get("type", "any")
            res_t = body["outputs"].get("result", {}).get("type", "any")
            self._sub(body, f"{self.where} > {nid}.body")
            return {"items": {"type": f"list<{item_t}>", "required": True}}, {"results": f"list<{res_t}>"}
        if kind == "subflow":
            if "ref" in n:
                path = (self.base_dir / n["ref"]).resolve()
                if not ref_within(path, self.base_dir):  # 口径与引擎 / api/ports 一致
                    self.err(f"节点 {nid}：子流程引用越出了允许的目录（只能指向项目或当前目录内的文件）：{n['ref']}")
                    return None, None
                if path in self.refs:
                    chain = " → ".join(p.name for p in self.refs + (path,))
                    self.err(f"节点 {nid}：子流程循环引用（{chain}）")
                    return None, None
                if self.depth >= 8:
                    self.err(f"节点 {nid}：嵌套超过 8 层")
                    return None, None
                try:
                    body = self.snap.load(path)
                except (OSError, json.JSONDecodeError) as e:
                    self.err(f"节点 {nid}：无法读取子图 {n['ref']}（{e}）")
                    return None, None
                sub_errors = validate_workflow(body, self.reg, path.parent, self.depth + 1, _refs=self.refs + (path,),
                                               snap=self.snap)
                self.errors.extend(f"{self.where} > {nid}（{n['ref']}）：{m}" for m in sub_errors)
                if sub_errors:  # 子图本身无效，端口无从推导；错误已记录，不再读取它的字段
                    return None, None
            else:
                body = n["body"]
                self._sub(body, f"{self.where} > {nid}.body")
            ins = {k: {"type": p["type"], "required": p.get("required", True) and "default" not in p}
                   for k, p in body["inputs"].items()}
            return ins, {k: p["type"] for k, p in body["outputs"].items()}
        return None, None

    def _sub(self, body: dict, where: str):
        if self.depth >= 8:
            self.err("嵌套超过 8 层")
            return
        _Graph(body, where, self.reg, self.base_dir, self.errors, self.depth + 1, self.refs, self.snap).check()

    # ---------- 类型推断 ----------
    def source_type(self, src: str) -> str | None:
        """沿 if / merge 往上游找实际类型。用循环而不是递归，长链不会超出 Python 递归深度；
        结果按端点缓存（只在连线全部登记后调用），长链上每个端点只推断一次，总体是线性的。"""
        if src in self._types:
            return self._types[src]
        chain, seen, cur = [], set(), src  # chain：途中经过的、类型要靠上游推断的端点
        while True:
            self.infer_steps += 1
            if cur in self._types:
                t = self._types[cur]
                break
            nid, port = cur.split(".", 1)
            if nid == "$in":
                p = self.g["inputs"].get(port)
                t = self._types[cur] = p["type"] if p else None
                break
            if nid not in self.outs or port not in self.outs[nid]:
                t = self._types[cur] = None
                break
            t = self.outs[nid][port]
            if t is not None:
                self._types[cur] = t
                break
            if nid in seen:  # 环，交给环检测报告
                t = "any"
                break
            seen.add(nid)
            chain.append(cur)
            feeders = ["value"] if self.nodes[nid]["kind"] == "if" else list(self.ins[nid])
            nxt = next((self.incoming[(nid, f)] for f in feeders if (nid, f) in self.incoming), None)
            if nxt is None:
                t = "any"
                break
            cur = nxt
        inferred = t if t is not None else "any"  # 推断途中断了线按 any 处理
        for c in chain:
            self._types[c] = inferred
        return self._types.get(src, t)

    # ---------- 主检查 ----------
    def _check_ports(self):
        """这一层图声明的输入输出：类型必须合法，默认值必须符合类型。"""
        for side in ("inputs", "outputs"):
            for name, p in self.g[side].items():
                if not types.is_valid(p["type"]):
                    self.err(f"{side}.{name} 的类型 {p['type']} 不合法，可用：string number integer boolean object file any 及 list<类型>")
                elif "default" in p and not value_matches(p["type"], p["default"]):
                    self.err(f"{side}.{name} 的默认值 {json.dumps(p['default'], ensure_ascii=False)} 不符合类型 {p['type']}")

    def check(self):
        g = self.g
        self._check_ports()
        for n in g["nodes"]:
            if n["id"] in self.nodes:
                self.err(f"节点 id 重复：{n['id']}")
                continue
            self.nodes[n["id"]] = n
        for nid, n in self.nodes.items():
            ins, outs = self._ports_of(n)
            if ins is not None:
                self.ins[nid], self.outs[nid] = ins, outs

        # 连线端点
        valid_edges = []
        self.controls: list[tuple[str, str]] = []  # (if 节点.then/else, 被控制的节点)
        for i, e in enumerate(g["edges"]):
            src, dst = e["from"], e["to"]
            sn, sp = src.split(".", 1)
            if "." not in dst:  # 控制线：只决定节点是否运行，不传数据
                self._check_control(i, src, sn, sp, dst)
                continue
            dn, dp = dst.split(".", 1)
            ok = True
            if sn == "$in":
                if sp not in g["inputs"]:
                    self.err(f"连线 {i}（{src} → {dst}）：工作流没有输入 {sp}"); ok = False
            elif sn not in self.nodes:
                self.err(f"连线 {i}（{src} → {dst}）：节点 {sn} 不存在"); ok = False
            elif sn in self.outs and sp not in self.outs[sn]:
                self.err(f"连线 {i}（{src} → {dst}）：节点 {sn} 没有输出端口 {sp}，可用：{'、'.join(self.outs[sn])}"); ok = False
            if dn == "$out":
                if dp not in g["outputs"]:
                    self.err(f"连线 {i}（{src} → {dst}）：工作流没有输出 {dp}"); ok = False
            elif dn not in self.nodes:
                self.err(f"连线 {i}（{src} → {dst}）：节点 {dn} 不存在"); ok = False
            elif dn in self.ins and dp not in self.ins[dn]:
                self.err(f"连线 {i}（{src} → {dst}）：节点 {dn} 没有输入端口 {dp}，可用：{'、'.join(self.ins[dn])}"); ok = False
            if not ok or sn in self.nodes and sn not in self.outs or dn in self.nodes and dn not in self.ins:
                continue
            key = (dn, dp)
            if key in self.incoming:
                self.err(f"连线 {i}：{dst} 已经有来源 {self.incoming[key]}，一个输入只能接一条线")
                continue
            self.incoming[key] = src
            valid_edges.append((i, src, dst))

        # config
        for nid, n in self.nodes.items():
            if n["kind"] != "module" or nid not in self.ins or not isinstance(n.get("config", {}), dict):
                continue
            for k, v in n.get("config", {}).items():
                if k not in self.ins[nid]:
                    self.err(f"节点 {nid}：config 中的 {k} 不是输入端口，可用：{'、'.join(self.ins[nid])}")
                elif (nid, k) in self.incoming:
                    self.err(f"节点 {nid}：输入 {k} 既有连线又有 config，只能保留一个")
                elif not value_matches(self.ins[nid][k]["type"], v):
                    self.err(f"节点 {nid}：config.{k} 的值 {json.dumps(v, ensure_ascii=False)} 不符合类型 {self.ins[nid][k]['type']}")

        # 必需输入
        for nid, ports in self.ins.items():
            node = self.nodes[nid]
            cfg = node.get("config") if node["kind"] == "module" and isinstance(node.get("config"), dict) else {}
            for p, info in ports.items():
                if info["required"] and (nid, p) not in self.incoming and p not in cfg:
                    self.err(f"节点 {nid}：必需输入 {p}（{info['type']}）没有来源")
            if self.nodes[nid]["kind"] == "merge" and not any((nid, p) in self.incoming for p in ports):
                self.err(f"节点 {nid}：merge 至少要接一条输入")

        # 类型兼容
        for i, src, dst in valid_edges:
            sn, sp = src.split(".", 1)
            if sn in self.nodes and self.nodes[sn]["kind"] == "if" and (sn, "value") not in self.incoming:
                self.err(f"连线 {i}（{src} → {dst}）：if 节点 {sn} 没有接 value，{sp} 没有数据可传，只能接控制线"
                         f"（终点只写节点 id，例如 \"to\": \"{dst.split('.', 1)[0]}\"）")
                continue
            st = self.source_type(src)
            dn, dp = dst.split(".", 1)
            dt = self.g["outputs"][dp]["type"] if dn == "$out" else self.ins[dn][dp]["type"]
            if st and not types.compatible(st, dt):
                self.err(f"连线 {i}（{src} → {dst}）：类型不兼容，{st} 不能接到 {dt}，需要先经过转换模块")
        for nid, n in self.nodes.items():
            if n["kind"] == "merge" and nid in self.ins:
                ts = {self.source_type(self.incoming[(nid, p)]) for p in self.ins[nid] if (nid, p) in self.incoming}
                ts.discard("any")
                if len(ts) > 1:
                    self.err(f"节点 {nid}：merge 的各输入类型不一致（{'、'.join(sorted(ts))}）")

        # 输出都有来源
        for p in g["outputs"]:
            if ("$out", p) not in self.incoming:
                self.err(f"工作流输出 {p} 没有来源")

        self._check_cycles(valid_edges)

    def _check_control(self, i: int, src: str, sn: str, sp: str, dst: str):
        where = f"控制线 {i}（{src} → {dst}）"
        if sn not in self.nodes or self.nodes[sn]["kind"] != "if" or sp not in ("then", "else"):
            self.err(f"{where}：控制线的起点只能是 if 节点的 then 或 else；要传数据请写成“节点id.端口”")
        elif dst not in self.nodes:
            self.err(f"{where}：节点 {dst} 不存在")
        elif dst == sn:
            self.err(f"{where}：if 节点不能控制自己")
        else:
            self.controls.append((src, dst))

    def _check_cycles(self, edges):
        adj: dict[str, set[str]] = {n: set() for n in self.nodes}
        for _, src, dst in list(edges) + [(None, s, d) for s, d in self.controls]:
            s, d = src.split(".")[0], dst.split(".")[0]
            if s in adj and d in adj:
                adj[s].add(d)
        # 显式栈的深度优先搜索：长链不会超出 Python 递归深度
        state: dict[str, int] = {}  # 1 = 在当前路径上，2 = 已查完
        for start in adj:
            if start in state:
                continue
            path, its = [start], [iter(sorted(adj[start]))]
            state[start] = 1
            while path:
                v = next(its[-1], None)
                if v is None:
                    state[path.pop()] = 2
                    its.pop()
                elif state.get(v) == 1:
                    self.err(f"连线形成环：{' → '.join(path[path.index(v):] + [v])}。重复执行只能用 foreach")
                    return
                elif v not in state:
                    state[v] = 1
                    path.append(v)
                    its.append(iter(sorted(adj[v])))


def validate_workflow(wf: dict, reg: dict[str, ModuleSpec], base_dir: Path = ROOT, depth: int = 0,
                      _refs: tuple = (), snap: RefSnapshot | None = None) -> list[str]:
    """snap：传入时，子流程文件从这份快照读（执行引擎用它保证校验和执行是同一份内容）。"""
    if not isinstance(wf, dict):
        return ["结构错误 (根)：工作流必须是一个 JSON 对象"]
    errors = _schema_errors(_wf_validator, wf)
    if errors:
        return errors  # 结构不对时语义检查没有意义
    _Graph(wf, wf["id"], reg, base_dir, errors, depth, _refs, snap).check()
    return errors


def validate_module(spec: ModuleSpec) -> list[str]:
    d = spec.data
    errors = [f"{spec.id}：{m}" for m in _schema_errors(_mod_validator, d)]
    if errors:
        return errors
    for side in ("inputs", "outputs"):
        for k, p in d[side].items():
            if not types.is_valid(p["type"]):
                errors.append(f"{spec.id}：{side}.{k} 的类型 {p['type']} 不合法")
    file_name = d["runtime"]["entry"].split(":")[0]
    if not (spec.path / file_name).is_file():
        errors.append(f"{spec.id}：入口文件 {file_name} 不存在")
    if "dry_run" in d["runtime"]:
        if d["side_effects"] != "write":
            errors.append(f"{spec.id}：只有 side_effects 为 write 的模块需要 runtime.dry_run")
        elif not (spec.path / d["runtime"]["dry_run"].split(":")[0]).is_file():
            errors.append(f"{spec.id}：演练入口文件 {d['runtime']['dry_run'].split(':')[0]} 不存在")
    for ex in d["examples"]:
        bad_in = set(ex["input"]) - set(d["inputs"])
        bad_out = set(ex["output"]) - set(d["outputs"])
        missing = [k for k, p in d["inputs"].items() if p.get("required", True) and "default" not in p and k not in ex["input"]]
        if bad_in or bad_out or missing:
            errors.append(f"{spec.id}：示例“{ex['title']}”端口不符（多余输入 {sorted(bad_in)}，多余输出 {sorted(bad_out)}，缺少必需输入 {missing}）")
    return errors
