"""整个界面共享的状态和操作（对应网页版 App.jsx 里的逻辑部分）。画布、左侧栏、右侧面板都只读这里的状态、调这里的方法，
状态变了由信号通知它们重画。所有方法、信号都在界面线程里调用。

主要状态（直接读属性）：
  modules        模块 key → 模块描述
  items          工作流列表（[{file, source, id, name, intent}]），saved_items()/example_items() 分组
  current        正打开的工作流：{wf：整理后供界面用, raw：原文（运行、保存时发送）, problems, source, file, dirty, frozen, project, inst}；首页为 None
  nodes, edges   画布上的节点和连线（与网页版 React Flow 的形状相同，见 native/logic/graph.py）
  selected       选中的节点 id；selected_edge：选中的连线（edge.data 加 id）；multi：“选中框里全部”选中的一组
  hovered        鼠标悬停的节点
  tab            右侧面板当前页签：run / node / generate
  side_open, panel_open, guide_open   左侧列表、右侧面板、分步清单是否展开（记在本机设置里）
  run_state()    这次运行处在哪一步（见 run_state 的说明）；暂停、继续、终止：pause_run / resume_run / stop_run
  result, running, gen_events, generating, gen_project, requirement, check, notice, check_errors, canvas_error
  projects, project   左侧项目栏里的项目；project 为正打开的项目编号（None：首页）

信号：见下面 Controller 类里的定义，每个都不带参数（除了注明的），收到后按需读状态重画。"""
import copy
import json
import time
import uuid

from PySide6.QtCore import QObject, QSettings, QTimer, Signal

from . import dialog
from .api import Aborted, ApiError
from .logic import bugcheck, collide, edit as ed, flow, graph
from .logic.run_status import RunStatus
from .logic.viewport import free_spot

BLANK = {
    "spec": "0.2", "id": "my_workflow", "name": "新工作流", "intent": "",
    "inputs": {"input": {"type": "string", "description": "运行时要提供的内容"}},
    "outputs": {"output": {"type": "string", "description": "最后得到的结果"}},
    "nodes": [], "edges": [],
    "layout": {"$in": {"x": 0, "y": 0}, "$out": {"x": 620, "y": 0}},  # 左右摆开，中间留出放节点的地方
}

RUN_STATE = {"node_start": "running", "node_end": "completed", "node_skipped": "skipped", "node_failed": "failed"}


class Seq:
    """只认最新一次请求：切换工作流或重新运行后，旧请求晚到的结果被丢弃"""

    def __init__(self):
        self.n = 0

    def next(self):
        self.n += 1
        return self.n

    def is_current(self, t):
        return t == self.n


class Controller(QObject):
    modules_changed = Signal()
    items_changed = Signal()          # 工作流列表（我保存的、示例）
    projects_changed = Signal()       # 项目列表、正打开的项目、正在生成的项目
    current_changed = Signal()        # 换了工作流或改了程序：标题、保存按钮、程序页签、面板
    flow_changed = Signal()           # 画布节点、连线整体重画
    nodes_moved = Signal(list)        # 拖动中这些节点的位置、大小（data.visual、width、height）变了，只更新它们
    selection_changed = Signal()
    hover_changed = Signal()
    status_changed = Signal()         # 运行状态、结果、是否在运行、暂停或终止
    run_requested = Signal()          # 画布上点了“开始”：运行页按已填的输入运行（输入有误就打开面板让使用者改）
    gen_changed = Signal()            # 生成过程的事件、是否在生成
    notice_changed = Signal()
    checks_changed = Signal()         # 自动校验结果、图上的问题标记
    bugs_changed = Signal()           # 运行页“bug 检查”的结果
    ask_changed = Signal()            # “提醒确认”节点在等回答（pending_ask 有值）或已经回答完
    layout_changed = Signal()         # 左侧列表、右侧面板、分步清单的展开收起
    tab_changed = Signal()
    requirement_changed = Signal()
    history_changed = Signal()        # 撤销、重做是否可用
    view_reset = Signal()             # 换了一张图：画布重新设置初始视野
    focus_request = Signal(str, float, float)  # 把画布移到某个节点：id、留白比例、最大缩放
    leaving = Signal()                # 马上要换工作流（或回到开始页）：正在输入的框先提交
    commit_pending = Signal()         # 马上要关窗口：正在输入的框先提交（输入框留着，使用者取消关闭后照常可用）

    def __init__(self, api, window=None):
        super().__init__()
        self.api = api
        self.window = window  # 确认框居中显示在它上面
        self.settings = QSettings("local.workflow", "workflow-native")
        self.modules = {}
        self.items = []
        self.current = None
        self.nodes = []
        self.edges = []
        self.selected = None
        self.selected_edge = None
        self.multi = set()
        self.hovered = None
        self.tab = "run"
        self.side_open = self._remembered("side", True)
        self.panel_open = self._remembered("panel", True)
        self.guide_open = self._remembered("guide", True)
        self.pet_background = self._remembered("pet_background", True)  # 关主窗口时有工作流在运行：转到后台并显示桌宠

        self.run_status = RunStatus("")
        self.node_status = {}
        self.result = None
        self._stop_timer = QTimer(self, singleShot=True, interval=5000, timeout=self._stop_overdue)
        self._clear_run_flags()
        self.ran_ok = False
        self._flush_timer = QTimer(self, singleShot=True, interval=60, timeout=self._flush_status)

        self.requirement = ""
        self.check = True
        self.gen_events = []
        self.generating = False
        self.gen_project = None
        self.drawn = None  # 画布上正显示生成过程中哪一轮的草稿：{round, wf}
        self._gen_handle = None
        self._run_handle = None
        self.run_seq = Seq()
        self.open_seq = Seq()
        self.ref_ports = {}
        self.notice = ""
        self.canvas_error = ""
        self.check_errors = []
        self.bugs = None        # bug 检查：{"raw": 检查的是哪一份程序, "items": [...]} 或 {"raw", "checking": True} / {"raw", "error": 文字}
        self._bug_seq = Seq()
        self._check_seq = Seq()
        self._ports_seq = Seq()
        self._check_timer = QTimer(self, singleShot=True, interval=300, timeout=self._validate)
        self._gen_last = None        # 生成中最近一次收到事件的时刻（单调时钟）
        self._gen_wait_text = ""     # 当前显示着的“还没回音”提示原文（只撤自己写的，不碰别的通知）
        self._gen_wait_timer = QTimer(self, interval=5000, timeout=self._gen_watch)

        self.past = []
        self.future = []
        self._tidy = None       # 改动后等图画好再整理重叠：{raw, focus, pinned, arranged}
        self._guarded = {}      # 拖动中经过碰撞修正的位置（松手时以它为准）
        self.drop_box = None    # 拖着一个节点经过的循环框：松手就放进去（框亮起提示）
        self._drag_base = {}    # 拖动开始时各循环框的尺寸
        self._drag_raw = None   # 拖动开始时的程序：拖动中程序变了（撤销、换了工作流）这次拖动作废
        self._resize = None     # 正在拉伸的循环框：{id, x, y, kids, nodes}

        self.projects = []
        self.project = None
        self.project_model = None  # 当前项目自己选的模型（None 表示跟着默认模型）
        self._creating = None   # 正在新建的项目：[回调...]；同时触发两次时只建一个
        self._creating_project = False  # 点了“+”、项目还在建：再点不重复建
        self._inst = 0
        self._last_stored = None
        self._unsynced = {}     # pid → {body, rev}：每个项目还没确认存好的最新内容
        self._latest_body = {}  # pid → {body, rev, inst}：本机最近一次交出去存的内容
        self._inflight = set()  # 正在发请求的项目：同一个项目同时只发一个
        self._rev = 0
        self._store_timer = QTimer(self, singleShot=True, interval=500, timeout=self.flush_store)
        self._retry_timer = QTimer(self, singleShot=True, interval=3000, timeout=self.flush_store)

    # ---------- 启动 ----------
    def load(self):
        self.api.get("/api/modules", ok=self._got_modules)
        self.refresh_items()
        self.refresh_projects()

    def _got_modules(self, ms):
        self.modules = {m["key"]: m for m in ms if isinstance(m, dict) and "key" in m}
        self.modules_changed.emit()
        self._rebuild()

    def refresh_items(self):
        def ok(data):
            self.items = data if isinstance(data, list) else []
            self.items_changed.emit()
        self.api.get("/api/workflows", ok=ok)

    def saved_items(self):
        return [i for i in self.items if i.get("source") == "saved"]

    def example_items(self):
        return [i for i in self.items if i.get("source") == "examples"]

    def refresh_projects(self):
        def ok(data):
            self.projects = data if isinstance(data, list) else []
            self.projects_changed.emit()
        self.api.get("/api/projects", ok=ok, err=lambda e: None)

    # ---------- 本机设置 ----------
    def _remembered(self, key, fallback):
        v = self.settings.value(key)
        return fallback if v is None else str(v) in ("1", "true", "True")

    def set_side_open(self, on):
        self.side_open = bool(on)
        self.settings.setValue("side", "1" if on else "0")
        self.layout_changed.emit()

    def set_panel_open(self, on):
        self.panel_open = bool(on)
        self.settings.setValue("panel", "1" if on else "0")
        self.layout_changed.emit()

    def set_pet_background(self, on):
        self.pet_background = bool(on)
        self.settings.setValue("pet_background", "1" if on else "0")

    def set_guide_open(self, on):
        self.guide_open = bool(on)
        self.settings.setValue("guide", "1" if on else "0")
        self.layout_changed.emit()

    def set_tab(self, tab):
        if tab != self.tab:
            self.tab = tab
            self.tab_changed.emit()

    def set_notice(self, text):
        if text != self.notice:
            self.notice = text
            self.notice_changed.emit()

    def set_requirement(self, text):
        if text != self.requirement:
            self.requirement = text
            self.requirement_changed.emit()

    def set_check(self, on):
        self.check = bool(on)

    def ask(self, **kw) -> bool:
        return dialog.ask(self.window, **kw)

    # ---------- 运行状态 ----------
    def _flush_status(self):
        self._flush_timer.stop()
        self.node_status = self.run_status.snapshot()
        self.status_changed.emit()

    def _reset_status(self, wf_id):
        self._flush_timer.stop()
        self.run_status = RunStatus(wf_id)
        self.node_status = {}

    def _clear_run_flags(self):
        self.running = False
        self.run_id = None
        self.paused = False        # 服务端已经停在某个节点前
        self.pause_wanted = False  # 使用者要暂停（点了暂停、还没点继续）
        self.stopping = False      # 使用者点了终止，等运行结束
        self._ctl_n = 0            # 发给服务端的暂停、继续、终止的序号
        had_ask = getattr(self, "pending_ask", None) is not None
        self.pending_ask = None    # 正在等回答的“提醒确认”：{"run_id", "path", "prompt", "sending"?}
        self._stop_timer.stop()
        if had_ask:  # 终止、换工作流、清画布……任何路径结束运行都把提示窗关掉
            self.ask_changed.emit()

    def run_state(self):
        """这次运行处在哪一步（画布右上角的运行按钮读它，以后桌宠也可以读它）：
        idle 还没运行 / running 运行中 / pausing 点了暂停、等当前节点跑完 / paused 已暂停 / stopping 正在终止 /
        done 完成 / error 出错 / stopped 被使用者终止。变化时发 status_changed"""
        if self.running:
            if self.stopping:
                return "stopping"
            if self.pause_wanted:
                return "paused" if self.paused else "pausing"
            return "running"
        if not self.result:
            return "idle"
        st = self.result.get("status")
        return "done" if st == "completed" else "stopped" if st == "stopped" else "error"

    def statuses(self):
        """画布用的状态表：节点 id → {state, reason, counts}"""
        s = dict(self.node_status)
        if self.running or self.result:
            s["$in"] = {"state": "completed"}
        if self.result:
            s["$out"] = {"state": "completed" if self.result.get("status") == "completed" else "skipped"}
        return s

    def status_of(self, node_id):
        return self.statuses().get(node_id) or {"state": "idle"}

    def records_by_id(self):
        m = {}
        if not self.result or not self.current:
            return m
        for r in self.result.get("records") or []:
            m.setdefault(graph.path_to_id(r["path"], self.current["wf"]["id"])["id"], []).append(r)
        return m

    def edge_state(self, e, _ctx=None):
        """一条连线现在怎么画：{"live", "dead", "animated", "hover"}。运行之后信号走过的连线加深，没走到的变淡；悬停或选中节点的连线高亮。
        _ctx：(状态表, 运行记录)，一次画很多条时由 edge_states 先算好传进来"""
        focus = self.hovered or self.selected
        hover = bool(focus and (e["source"] == focus or e["target"] == focus))
        if not self.running and not self.result:
            return {"live": False, "dead": False, "animated": False, "hover": hover}
        statuses, by_id = _ctx or (self.statuses(), self.records_by_id())
        src = e["data"]["from"].split(".")
        recs = by_id.get(e["source"])
        live = True if src[0] == "$in" else (statuses.get(e["source"]) or {}).get("state") == "completed"
        if recs and not src[0].startswith("$"):
            live = any(r.get("status") == "completed" and (r.get("outputs") or {}).get(src[1]) != "<跳过>" for r in recs)
        return {"live": live, "dead": bool(self.result) and not live, "animated": self.running and not self.paused and live, "hover": hover}

    # ---------- 打开、显示 ----------
    @property
    def editable(self):
        return bool(self.current) and not self.generating and not self.canvas_error

    def _show(self, wf, source, file, dirty=False):
        self.leaving.emit()
        self.drawn = None
        self.past, self.future = [], []
        self.selected_edge = None
        self.multi = set()
        self.check_errors = []
        self.run_seq.next()   # 换了工作流，旧的运行结果作废
        self.open_seq.next()  # 还没回来的“打开”请求也作废
        if self._run_handle:
            self._run_handle.abort()
        self._clear_run_flags()
        r = flow.inspect_workflow(wf)
        self._inst += 1  # 换了一份工作流（编辑不换）：晚到的“保存到我的”响应只认发起保存的那一份
        self.current = {"wf": r["wf"], "raw": wf, "problems": r["problems"], "source": source, "file": file,
                        "dirty": dirty, "frozen": False, "project": self.project, "inst": self._inst}
        self.ran_ok = False
        self._reset_status(r["wf"]["id"])
        self.result = None
        self.selected = None
        self.ref_ports = {}  # 换了工作流：旧的子流程端口不再对应
        self._current_set(new_view=True)
        self._load_ref_ports()

    def _current_set(self, new_view=False):
        """current 换了之后：重画画布、通知各处、自动保存进项目、自动校验"""
        self._rebuild()
        if new_view:
            self.view_reset.emit()
        self.current_changed.emit()
        self.status_changed.emit()
        self.selection_changed.emit()
        self.history_changed.emit()
        self.checks_changed.emit()
        self._stage_current()
        c = self.current
        if c and (c["dirty"] or c["source"] == "new"):
            self._check_timer.start()
        if not new_view:
            self._load_ref_ports()  # 改了程序或换了来源（保存到“我的”）：引用的子流程可能变了

    def _rebuild(self):
        """按 current 重新画图；改动后再整理一次重叠（并入同一步改动）"""
        c = self.current
        if not c or not self.modules:
            if not c:
                self.nodes, self.edges = [], []
                self.flow_changed.emit()
            return
        try:
            fl = graph.to_flow(c["wf"], self.modules, self.ref_ports)
            self.nodes, self.edges = fl["nodes"], fl["edges"]
            self.canvas_error = ""
        except Exception as e:  # 画图转换出错时只在画布区显示原因，侧栏和右侧面板照常可用
            self.nodes, self.edges = [], []
            self.canvas_error = str(e)
        t = self._tidy
        if t and t["raw"] is c["raw"] and not self.canvas_error:
            self._tidy = None
            sizes = {}
            moves = collide.settle(self.nodes, t.get("focus") or [], sizes_out=sizes, pinned=t.get("pinned") or [])
            # 刚整理过的框：框里这一层按自动排版画好了，把位置全部写回（程序里每个位置都要有 x、y）
            if t.get("arranged"):
                for n in self.nodes:
                    if n.get("parentId") == t["arranged"] and n["id"] not in moves:
                        moves[n["id"]] = n["position"]
            if moves or sizes:
                nxt = self._apply_moves(t["raw"], moves, self.nodes, sizes)
                r = flow.inspect_workflow(nxt)
                c.update(raw=nxt, wf=r["wf"], problems=r["problems"])
                fl = graph.to_flow(c["wf"], self.modules, self.ref_ports)
                self.nodes, self.edges = fl["nodes"], fl["edges"]
        self.flow_changed.emit()

    def _load_ref_ports(self):
        """引用文件的子流程：向服务端要它们的输入输出，才能画出插孔"""
        c = self.current
        if not c:
            return
        token = self._ports_seq.next()
        key = (c["inst"], c["source"])

        def ok(p):
            cur = self.current
            if not self._ports_seq.is_current(token) or cur is None or (cur["inst"], cur["source"]) != key:
                return
            p = p if isinstance(p, dict) else {}
            if p != self.ref_ports:
                self.ref_ports = p
                self._rebuild()
        self.api.post("/api/ports", {"workflow": c["wf"], "source": c["source"]}, ok=ok, err=lambda e: None)

    def confirm_leave(self) -> bool:
        return not (self.current and self.current["dirty"]) or self.ask(
            title="改动还没保存", message="当前工作流有改动还没保存，确定要离开吗？离开后改动会丢失。",
            ok="离开，不保存", cancel="留下", danger=True)

    def open(self, item):
        """打开“我保存的”或示例里的一个工作流（在首页时先建一个项目）"""
        at = self.current
        if not self.confirm_leave() or self.current is not at:
            return
        token = self.open_seq.next()

        def got(wf):
            if not self.open_seq.is_current(token):
                return  # 之后又打开了别的，晚到的旧响应不换图

            def entered(pid):
                if pid and self.open_seq.is_current(token):
                    self._show(wf, item["source"], item["file"])
                    self.set_tab("run")
            self._ensure_project({"workflow": wf, "source": item["source"], "file": item["file"]},
                                 lambda: self.open_seq.is_current(token), entered)

        def bad(e):
            if self.open_seq.is_current(token):
                self.set_notice(f"没能打开：{e}")
        self.api.get(f"/api/workflows/{item['source']}/{item['file']}", ok=got, err=bad)

    def new_blank(self):
        at = self.current
        if not self.confirm_leave() or self.current is not at:
            return
        blank = copy.deepcopy(BLANK)
        token = self.open_seq.next()

        def entered(pid):
            if pid and self.open_seq.is_current(token):
                self._show(blank, "new", None)
                self.set_tab("run")
        self._ensure_project({"workflow": blank, "source": "new"}, lambda: self.open_seq.is_current(token), entered)

    def _clear_canvas(self):
        self.leaving.emit()
        self.drawn = None
        self.past, self.future = [], []
        self.run_seq.next()
        self.open_seq.next()
        if self._run_handle:
            self._run_handle.abort()
        self._clear_run_flags()
        self.current = None
        self.result = None
        self.selected = None
        self.selected_edge = None
        self.multi = set()
        self.check_errors = []
        self._reset_status("")
        self.nodes, self.edges = [], []
        self.notice = ""
        self.flow_changed.emit()
        self.current_changed.emit()
        self.status_changed.emit()
        self.selection_changed.emit()
        self.history_changed.emit()
        self.checks_changed.emit()
        self.notice_changed.emit()

    # ---------- 项目：自动保存 ----------
    def _enter(self, pid, model=None):
        self.project = pid
        self.project_model = model if isinstance(model, str) and model else None
        self.projects_changed.emit()

    def generation_model(self):
        """这次生成、理清需求用哪个模型：项目自己选了就用项目的，否则 None（服务端用默认模型）"""
        return self.project_model if self.project else None

    def set_project_model(self, model):
        """顶栏换了模型：记到当前项目上（空表示跟着默认）。没打开项目时改的是默认模型，由模型下拉框自己处理"""
        if not self.project:
            return False
        self.project_model = model or None
        self.projects_changed.emit()
        # 和工作流内容一样走自动保存队列（带 rev、按项目串行）：快速切几次也只会留下最后一次
        pid = self.project
        entry = self._unsynced.get(pid)
        last = self._latest_body.get(pid)
        base = dict(entry["body"]) if entry else (dict(last["body"]) if last else {})
        self._stage(pid, {**base, "model": model or ""}, last["inst"] if last else None)
        self._store_timer.start()
        return True

    def _bump_rev(self, floor=0):
        """rev 取“当前时间”和“见过的最大编号 + 1”中较大的：电脑时间往回调了也照样递增"""
        self._rev = max(int(time.time() * 1_000_000), self._rev + 1, int(floor) + 1)
        return self._rev

    def _stage(self, pid, body, inst=None):
        rev = self._bump_rev()
        self._unsynced[pid] = {"body": body, "rev": rev}
        self._latest_body[pid] = {"body": body, "rev": rev, "inst": inst}

    def _stage_current(self):
        """项目内容自动存到电脑上：停手半秒后存一次；只在内容真的变了时存"""
        c = self.current
        if not c or not c["project"]:
            return
        key = (c["project"], id(c["raw"]), c["source"], c["file"], c["dirty"])
        if self._last_stored == key:
            return
        self._last_stored = key
        self._stage(c["project"], {"workflow": c["raw"], "source": c["source"], "file": c["file"], "dirty": c["dirty"]}, c["inst"])
        self._store_timer.start()

    def _push_project(self, pid):
        if pid in self._inflight:
            return
        entry = self._unsynced.get(pid)
        if not entry:
            return
        self._inflight.add(pid)

        def done():
            if self._unsynced.get(pid) is entry:
                del self._unsynced[pid]

        def after():
            self._inflight.discard(pid)
            now = self._unsynced.get(pid)
            if now and now is not entry:
                self._push_project(pid)  # 发的时候又有了新改动

        def ok(_):
            done()
            self.refresh_projects()
            after()

        def bad(e):
            if e.status == 404:  # 项目已删除
                done()
                return after()
            if e.status == 409:  # 服务端的编号更大：换个更大的编号重发本机这份
                def got(doc):
                    if self._unsynced.get(pid) is entry:
                        rev = self._bump_rev(doc.get("rev") if isinstance(doc.get("rev"), (int, float)) else 0)
                        self._unsynced[pid] = {"body": entry["body"], "rev": rev}
                        last = self._latest_body.get(pid)
                        if last and last["body"] is entry["body"]:
                            last["rev"] = rev
                self.api.get(f"/api/projects/{pid}", ok=got, err=lambda e: None)
            self.set_notice(f"没能存进项目，稍后自动重试：{e}")
            self._retry_timer.start()
            after()
        self.api.send("PUT", f"/api/projects/{pid}", {**entry["body"], "rev": entry["rev"]}, ok=ok, err=bad)

    def flush_store(self):
        self._store_timer.stop()
        for pid in list(self._unsynced):
            self._push_project(pid)

    def flush_store_sync(self):
        """关窗口前：把每个项目还没存好的内容同步发出去（rev 保证不会被旧的覆盖）。
        服务端编号更大（409）时读出它的编号、换个更大的再发一次。返回没能存好的项目：[(项目名, 原因)]"""
        self._store_timer.stop()
        failed = []
        for pid, entry in list(self._unsynced.items()):
            body = {**entry["body"], "rev": entry["rev"]}
            for attempt in range(2):
                try:
                    self.api.request_sync("PUT", f"/api/projects/{pid}", body)
                    self._unsynced.pop(pid, None)
                    break
                except ApiError as e:
                    if e.status == 404:  # 项目已删除
                        self._unsynced.pop(pid, None)
                        break
                    if e.status == 409 and attempt == 0:
                        try:
                            doc = self.api.request_sync("GET", f"/api/projects/{pid}")
                            rev = doc.get("rev") if isinstance(doc.get("rev"), (int, float)) else 0
                            body = {**body, "rev": self._bump_rev(rev)}
                            continue
                        except Exception as e2:
                            e = e2
                    failed.append((self._project_name(pid), str(e)))
                    break
                except Exception as e:
                    failed.append((self._project_name(pid), str(e)))
                    break
        return failed

    def _project_name(self, pid):
        return next((p.get("name") for p in self.projects if p.get("id") == pid), None) or pid

    def _ensure_project(self, body, is_current, then):
        """没有打开项目时（首页）先建一个，再 then(编号)。建好时这次动作已经过时就 then(None)"""
        if self.project:
            return then(self.project)
        if self._creating is not None:
            self._creating.append((is_current, then))
            return
        self._creating = [(is_current, then)]

        def ok(p):
            waiters, self._creating = self._creating, None
            self.refresh_projects()
            for cur, cb in waiters:
                if self.project:
                    cb(self.project if self.project == p["id"] else None)
                elif not cur():
                    cb(None)
                else:
                    self._enter(p["id"])
                    cb(p["id"])

        def bad(e):
            waiters, self._creating = self._creating, None
            self.set_notice(f"没能新建项目：{e}")
            for _, cb in waiters:
                cb(None)
        self.api.post("/api/projects", body, ok=ok, err=bad)

    def go_home(self):
        self.flush_store()
        self._enter(None)
        self._clear_canvas()
        self.set_requirement("")

    def project_is_empty(self):
        """当前项目还什么都没有：没打开工作流，也没写需求"""
        return bool(self.project) and self.current is None and not self.requirement.strip()

    def new_project(self):
        """新建项目。当前项目还是空的（刚建、什么都没写）就留在它这里，不再建一个；正在建的时候再点也不会多建。
        这样连续点“+”只会有一个“未命名项目”"""
        if self._creating_project:
            return
        if self.project_is_empty():
            return  # 已经在一个空项目的首页上：留在这里写需求就行
        self.flush_store()
        token = self.open_seq.next()
        self._creating_project = True

        def ok(p):
            self._creating_project = False
            self.refresh_projects()
            if not self.open_seq.is_current(token):
                return
            self._enter(p["id"])
            self._clear_canvas()
            self.set_requirement("")

        def bad(e):
            self._creating_project = False
            self.set_notice(f"没能新建项目：{e}")
        self.api.post("/api/projects", {}, ok=ok, err=bad)

    def open_project(self, pid):
        if pid == self.project:
            return
        self.flush_store()
        token = self.open_seq.next()

        def got(doc):
            if not self.open_seq.is_current(token):
                return  # 之后又点了别的项目
            self._enter(pid, doc.get("model"))
            self.set_requirement(doc.get("requirement") if isinstance(doc.get("requirement"), str) else "")
            # 本机最近交出去的这份比服务端读到的新（还没存好）就用本机的
            server_rev = doc.get("rev") if isinstance(doc.get("rev"), (int, float)) and not isinstance(doc.get("rev"), bool) else 0
            self._bump_rev(server_rev)
            mine = self._latest_body.get(pid)
            local = mine["body"] if mine and mine["rev"] >= server_rev else None
            src = local if local else doc
            if local and "model" in local:  # 本机刚选的模型还没存好：以本机的为准
                self.project_model = local["model"] or None
            wf = src.get("workflow")
            if isinstance(wf, dict):
                source = src.get("source") or "new"
                file = src.get("file") or None
                dirty = bool(src.get("dirty"))
                self._last_stored = (pid, id(wf), source, file, dirty)  # 不是新改动，不用再存一遍
                self._show(wf, source, file, dirty=dirty)
                if local:
                    mine["inst"] = self._inst
                self.set_tab("run")
            else:
                self._clear_canvas()

        def bad(e):
            if self.open_seq.is_current(token):
                self.set_notice("没能打开这个项目，它可能已经被删除了")
            self.refresh_projects()
        self.api.get(f"/api/projects/{pid}", ok=got, err=bad)

    def rename_project(self, pid, name):
        for p in self.projects:
            if p.get("id") == pid:
                p["name"] = name
        self.projects_changed.emit()

        def bad(e):
            self.set_notice(f"没能改名：{e}")
            self.refresh_projects()
        self.api.send("PUT", f"/api/projects/{pid}", {"name": name}, ok=lambda _: self.refresh_projects(), err=bad)

    def delete_project(self, p):
        ok = self.ask(title=f"删除项目“{p.get('name', '')}”？",
                      message="项目里的工作流和还没保存的改动都会删掉，不能恢复。已经存进“我保存的”的工作流不受影响。",
                      ok="删除", cancel="留下", danger=True)
        if not ok:
            return
        pid = p["id"]
        self._unsynced.pop(pid, None)
        self._latest_body.pop(pid, None)
        if self.gen_project == pid and self._gen_handle:
            self._gen_handle.abort()  # 正在给它生成：停下
        if self.project == pid:
            self.go_home()

        def bad(e):
            if e.status != 404:
                self.set_notice(f"没能删除：{e}")
            self.refresh_projects()
        self.api.send("DELETE", f"/api/projects/{pid}", ok=lambda _: self.refresh_projects(), err=bad)

    # ---------- 运行 ----------
    def _ask_write(self, keys):
        """运行前确认会动电脑的模块：写文件的（会改动）和读文件的（会读取，内容可能被送去别处）分开说清"""
        def side(k):
            return (self.modules.get(k) or {}).get("side_effects")
        writes = [k for k in keys if side(k) == "write"]
        reads = [k for k in keys if side(k) != "write"]
        lines = []
        if writes:
            lines.append("会改动你电脑上的文件：")
            lines += [f"· {(self.modules.get(k) or {}).get('name') or k}" for k in writes]
        if reads:
            lines.append(("\n" if lines else "") + "会读取你电脑上的文件（读到的内容可能被交给模型或写到别处）：")
            lines += [f"· {(self.modules.get(k) or {}).get('name') or k}" for k in reads]
        title = "这个工作流会改动你电脑上的文件" if writes else "这个工作流会读取你电脑上的文件"
        return self.ask(title=title, message="\n".join(lines) + "\n\n确定运行吗？", ok="确定运行")

    def run(self, inputs, approved=None):
        """运行当前工作流。approved：已经确认过的写模块（重跑时传入）；不传则先问服务端要确认哪些写模块并询问"""
        c = self.current
        if not c:
            return
        if approved is None:
            local = graph.write_modules(c["raw"], self.modules)

            def got(keys):
                writes = sorted(set(keys) | set(local)) if isinstance(keys, list) else local
                self._run_after_confirm(c, inputs, writes)
            # 问不到时退回只看本图
            self.api.post("/api/writes", {"workflow": c["raw"], "source": c["source"]}, ok=got,
                          err=lambda e: self._run_after_confirm(c, inputs, local))
        else:
            self._start_run(c, inputs, approved)

    def _run_after_confirm(self, c, inputs, writes):
        if self.current is not c:
            return
        if writes and (not self._ask_write(writes) or self.current is not c):
            return
        self._start_run(c, inputs, writes)

    def _start_run(self, c, inputs, writes):
        token = self.run_seq.next()
        mine = lambda: self.run_seq.is_current(token)  # noqa: E731
        if self._run_handle:
            self._run_handle.abort()
        self._reset_status(c["wf"]["id"])
        self.result = None
        self._clear_run_flags()
        self.running = True
        self.run_id = run_id = uuid.uuid4().hex
        self.set_notice("")
        self.status_changed.emit()
        final = {}

        def on_event(ev):
            if not mine():
                return
            t = ev.get("type")
            if t == "ask":  # “提醒确认”节点停下来等回答：界面弹出提示窗
                self.run_status.apply(ev["path"], "running", "等你在提示窗里回答")
                self._flush_status()
                self.pending_ask = {"run_id": run_id, "path": ev["path"], "prompt": ev.get("prompt") or {}}
                self.ask_changed.emit()
                return
            if t == "answered":
                self.pending_ask = None
                self.ask_changed.emit()
                return
            if t in ("paused", "resumed"):
                self._flush_status()
                # 服务端真正停下或接着跑了：以它为准（请求失败回滚、几个请求先后颠倒后界面也能回到实际状态）
                self.paused = self.pause_wanted = t == "paused"
                self.status_changed.emit()
                return
            state = RUN_STATE.get(t)
            if state:
                reason = ev.get("reason") or (ev.get("code") and f"{ev['code']}：{ev.get('message')}")
                if state == "failed" and ev.get("code") == "cancelled":
                    state, reason = "skipped", "已终止"  # 被终止打断的节点不算出错
                self.run_status.apply(ev["path"], state, reason)
                if not self._flush_timer.isActive():
                    self._flush_timer.start()
                return
            self._flush_status()  # 结果到达前先把最后一批状态发布出去
            if t == "result":
                final["ev"] = ev
                self.result = ev
            elif t == "invalid":
                self.result = {"status": "failed", "error": {"message": "\n".join(ev.get("errors") or []), "code": "invalid"}, "outputs": {}}
            elif t == "error":
                self.result = {"status": "failed", "error": {"message": ev.get("message"), "code": "server"}, "outputs": {}}
            if self.result and self.result.get("status") == "completed":
                self.ran_ok = True
            self.status_changed.emit()

        def on_done(error):
            if not mine():
                return
            if isinstance(error, ApiError):
                self.result = {"status": "failed", "error": {"message": str(error), "code": "backend_unreachable"}, "outputs": {}}
            if self.stopping:
                self._mark_stopped()
            self._flush_status()
            self._clear_run_flags()
            self.status_changed.emit()
            self.current_changed.emit()  # 分步清单“运行一次”
            # 保险：运行前没查到的写模块被服务端拦下时，再问一次，同意就带上它重跑
            fev = final.get("ev") or {}
            err = fev.get("error") or {}
            missing = graph.unconfirmed_module(err.get("message")) if err.get("code") == "not_confirmed" else None
            if missing and missing not in writes and self._ask_write([missing]) and mine() and self.current is c:
                self.run(inputs, [*writes, missing])
        self._run_handle = self.api.stream("/api/run", {"workflow": c["raw"], "inputs": inputs, "source": c["source"],
                                                        "allow_write": writes, "run_id": run_id}, on_event, on_done)

    def answer_ask(self, answer):
        """使用者在提示窗里回答了：交给正在等的“提醒确认”节点（answer：{choice, done, note}）。
        送到之前提示窗留着、按钮变灰；没送到就恢复可点，可以再按一次"""
        p = self.pending_ask
        if not p or p.get("sending") or not self.running or p["run_id"] != self.run_id:
            return
        p["sending"] = True
        self.ask_changed.emit()

        def ok(_):
            if self.pending_ask is p:
                self.pending_ask = None
                self.ask_changed.emit()

        def bad(e):
            if self.pending_ask is p:
                p["sending"] = False
                self.ask_changed.emit()
            self.set_notice(f"没能把回答交给工作流，请再点一次：{e}")
        self.api.post(f"/api/run/{p['run_id']}/answer", {"path": p["path"], "answer": answer}, ok=ok, err=bad)

    def request_run(self):
        """画布上的“开始”：由运行页按已填的输入运行"""
        if self.current and not self.running and not self.generating:
            self.set_tab("run")
            self.run_requested.emit()

    def _control_run(self, action, failed):
        """让服务端暂停、继续或终止这次运行；failed(错误)：请求没成功、而且之后没有发过别的操作时才调用。
        每个操作带递增的序号：几个请求同时在路上时，服务端只认比已处理的更新的（快速点暂停、继续不会颠倒）"""
        rid = self.run_id
        self._ctl_n += 1
        n = self._ctl_n

        def bad(e):
            if self.running and self.run_id == rid and self._ctl_n == n:
                failed(e)
        self.api.post(f"/api/run/{rid}/{action}", {"seq": n}, err=bad)

    def pause_run(self):
        """暂停：正在运行的节点跑完后停在下一个节点前"""
        if not self.running or self.stopping or self.pause_wanted or not self.run_id:
            return

        def failed(e):
            self.pause_wanted = self.paused  # 回到服务端最后确认的状态
            self.set_notice(f"没能暂停：{e}")
            self.status_changed.emit()
        self.pause_wanted = True
        self.status_changed.emit()
        self._control_run("pause", failed)

    def resume_run(self):
        if not self.running or self.stopping or not self.pause_wanted:
            return

        def failed(e):
            self.pause_wanted = self.paused
            self.set_notice(f"没能继续：{e}")
            self.status_changed.emit()
        self.pause_wanted = False
        self.status_changed.emit()
        self._control_run("resume", failed)

    def stop_run(self):
        """终止：正在运行的模块进程会被结束。服务端没响应时直接断开连接（服务端同样会停下）"""
        if not self.running or self.stopping:
            return
        rid = self.run_id

        def cut(*_):
            if self.running and self.run_id == rid:
                self._finish_stop()
        self.stopping = True
        self.status_changed.emit()
        self._control_run("stop", cut)
        self._stop_timer.start()  # 保险：5 秒还没结束就断开（运行结束时 _clear_run_flags 停掉它）

    def _stop_overdue(self):
        if self.running and self.stopping:
            self._finish_stop()

    def _finish_stop(self):
        """终止请求没成功或迟迟没结束：断开连接，界面上直接当作已终止（不等流线程的回调：连接可能还卡在等服务端响应）"""
        self.run_seq.next()  # 这次运行晚到的事件、结束回调都不再认
        if self._run_handle:
            self._run_handle.abort()
        self._mark_stopped()
        self._flush_status()
        self._clear_run_flags()
        self.status_changed.emit()
        self.current_changed.emit()

    def _mark_stopped(self):
        self.run_status.settle_running("skipped", "已终止")  # 不会再有结束事件了：还亮着的灯熄掉
        if not self.result:
            self.result = {"status": "stopped", "error": {"code": "cancelled", "message": "已终止"}, "outputs": {}}

    # ---------- 生成 ----------
    def generate(self, text=None, spec=None):
        """spec：理清需求得到的标准需求单（卡片改过或直接生成时为 None）"""
        text = self.requirement if not isinstance(text, str) else text
        if not text.strip() or self.generating:
            return
        at = self.current
        if not self.confirm_leave() or self.current is not at:
            return
        token = self.open_seq.next()

        def entered(pid):
            if not pid or not self.open_seq.is_current(token) or self.gen_project:
                return  # 切走了，或者已经在生成
            self.api.send("PUT", f"/api/projects/{pid}", {"requirement": text}, err=lambda e: None)
            self.gen_project = pid
            self.generating = True
            self.gen_events = []
            self._gen_last = time.monotonic()
            self._gen_wait_text = ""
            self._gen_wait_timer.start()
            self.set_tab("generate")
            self.set_panel_open(True)  # 生成过程显示在右侧面板里
            self.set_notice("")
            self.set_requirement(text)
            self.projects_changed.emit()
            self.gen_changed.emit()
            self.current_changed.emit()

            def on_event(ev):
                self.gen_events.append(ev)
                self._gen_tick()
                # 只画通过结构校验的版本；没通过运行检查的只算草稿，不能保存
                if flow.drawable(ev):
                    source = "generated" if ev.get("type") == "done" and ev.get("ok") else "draft"
                    if self.project == pid:
                        self._show(ev["workflow"], source, None)
                        self.drawn = {"round": ev.get("round") if ev.get("round") is not None else ev.get("rounds"), "wf": ev["workflow"]}
                        self.checks_changed.emit()
                    else:  # 使用者切到了别的项目：草稿直接存进它自己的项目
                        self._stage(pid, {"workflow": ev["workflow"], "source": source, "file": None, "dirty": False})
                        self._push_project(pid)
                self.gen_changed.emit()

            def on_done(error):
                if isinstance(error, Aborted):
                    self.gen_events.append({"type": "error", "message": "已停止生成"})
                elif error is not None:
                    self.gen_events.append({"type": "error", "message": str(error)})
                self._gen_wait_timer.stop()
                self._clear_gen_wait()  # 先撤“还没回音”，再写结束原因，失败原因不会被盖掉
                why = flow.gen_outcome(self.gen_events)  # 生成过程页签撤下了：没成功时在提示条里说明原因
                if why and not isinstance(error, Aborted):
                    self.set_notice(why)
                self._gen_handle = None
                self.gen_project = None
                self.generating = False
                self.projects_changed.emit()
                self.gen_changed.emit()
                self.current_changed.emit()
                self.flow_changed.emit()  # 生成结束后可以编辑了
            self._gen_handle = self.api.stream("/api/generate", {"requirement": text, "check": self.check, "model": self.generation_model(),
                                                               **({"spec": spec} if isinstance(spec, dict) else {})},
                                            on_event, on_done)
        self._ensure_project({"requirement": text}, lambda: self.open_seq.is_current(token), entered)

    # 生成中模型迟迟没回音：隔一会儿提醒一句，并指向“停止生成”（本地模型卡死时不至于干等几分钟）
    GEN_WAIT_AFTER = 45  # 秒

    def _gen_watch(self):
        if not self.generating or self._gen_last is None:
            self._gen_wait_timer.stop()
            return
        waited = int(time.monotonic() - self._gen_last)
        if waited < self.GEN_WAIT_AFTER:
            return
        # 提示条正显示着别的通知（比如改名失败）时不抢：只在空着或显示的是上一条“还没回音”时写
        if self.notice and self.notice != self._gen_wait_text:
            return
        self._gen_wait_text = f"模型还没回音（已等 {waited} 秒）。本地模型第一次加载可能要一两分钟；一直没动静可以点“停止生成”"
        self.set_notice(self._gen_wait_text)

    def _clear_gen_wait(self):
        """撤掉自己写的“还没回音”提示；提示条上已经换成别的通知就不动"""
        if self._gen_wait_text and self.notice == self._gen_wait_text:
            self.set_notice("")
        self._gen_wait_text = ""

    def _gen_tick(self):
        """生成过程来了新事件：重新计时；之前显示的“还没回音”提示撤掉"""
        self._gen_last = time.monotonic()
        self._clear_gen_wait()

    def stop_generate(self):
        if self._gen_handle:
            self._gen_handle.abort()

    # ---------- 保存 ----------
    def save(self):
        c = self.current
        if not c:
            return
        pid, inst, raw = c["project"], c["inst"], c["raw"]
        was_saved = c["source"] == "saved"

        def ok(r):
            self.refresh_items()

            def patch(wf):
                return {**wf, "id": r["id"]} if r.get("renamed") else wf
            cur = self.current
            if cur and cur["project"] == pid:
                if cur["inst"] != inst:
                    return
                cur.update(source=r["source"], file=r["file"], raw=patch(cur["raw"]), wf=patch(cur["wf"]),
                           dirty=False if cur["raw"] is raw else cur["dirty"])
                self.set_notice(f"已保存为 {r['id']}：已有同名的另一个工作流，没有覆盖它" if r.get("renamed")
                                else "已保存" if was_saved else "已保存到“我保存的”")
                self._current_set()
            elif pid and (self._latest_body.get(pid) or {}).get("inst") == inst:
                last = self._latest_body[pid]["body"]
                self._stage(pid, {**last, "workflow": patch(last["workflow"]), "source": r["source"], "file": r["file"],
                                  "dirty": False if last["workflow"] is raw else last["dirty"]}, inst)
                self._push_project(pid)

        def bad(e):
            if self.current and self.current["project"] == pid:
                self.set_notice(f"没能保存：{e}")
        self.api.post("/api/workflows", {"workflow": raw, "overwrite": was_saved}, ok=ok, err=bad)

    # ---------- 编辑 ----------
    def _current_positions(self):
        conv = graph.flow_to_layout(self.nodes)
        return {n["id"]: conv(n) for n in self.nodes}

    def _apply_moves(self, wf, moves, flow_nodes, sizes=None):
        """画布坐标的新位置一次写回程序的 layout；sizes：关掉自动收缩的框要改记的大小"""
        out = ed.freeze_layout(wf, graph.layout_moves(flow_nodes, moves))
        for nid, size in (sizes or {}).items():
            out = ed.set_box_size(out, nid, size)
        return out

    def edit(self, fn, focus=None, **tidy) -> bool:
        """一次改动：fn(旧 JSON) → 新 JSON 或 {"wf": ...} 或 {"error": ...}。第一次改动前先把当前位置固定下来，之后重新画图时节点不会跳。
        focus：这次改动直接涉及的节点（拖动的、新加的），重新画图后整理重叠时优先保持它们的位置。
        tidy：整理时的其他参数：pinned（不许动的框）、arranged（刚整理过的框：框里这一层位置全部写回）"""
        c = self.current
        if not self.editable or not c:
            return False
        before = c["raw"]
        start = before if c["frozen"] else ed.freeze_layout(before, self._current_positions())
        try:
            nxt = fn(start)
        except Exception as e:
            self.set_notice(f"没能修改：{e}")
            return False
        if isinstance(nxt, dict) and "error" in nxt and "nodes" not in nxt:
            self.set_notice(nxt["error"])
            return False
        if isinstance(nxt, dict) and "wf" in nxt and "nodes" not in nxt:
            nxt = nxt["wf"]
        if not nxt or json.dumps(nxt, sort_keys=True) == json.dumps(before, sort_keys=True):
            return False
        self.past.append(before)
        if len(self.past) > 100:
            self.past.pop(0)
        self.future = []
        r = flow.inspect_workflow(nxt)
        self._tidy = {"raw": nxt, "focus": list(focus or []), **tidy}
        self.current = {**c, "raw": nxt, "wf": r["wf"], "problems": r["problems"], "dirty": True, "frozen": True}
        self.notice = ""
        self.notice_changed.emit()
        self._drop_run()
        self.result = None  # 程序改了，上一次的运行结果不再对应
        self._reset_status(r["wf"]["id"])
        self._current_set()
        return True

    def _drop_run(self):
        """程序改了：正在跑的那次运行作废（停下它，晚到的事件也不再认）"""
        self.run_seq.next()
        if self._run_handle:
            self._run_handle.abort()
        self._clear_run_flags()

    def can_undo(self):
        return bool(self.past) and self.editable

    def can_redo(self):
        return bool(self.future) and self.editable

    def travel(self, direction):
        frm, to = (self.past, self.future) if direction == "undo" else (self.future, self.past)
        c = self.current
        if not frm or not c or not self.editable:
            return
        target = frm.pop()
        to.append(c["raw"])
        r = flow.inspect_workflow(target)
        self.current = {**c, "raw": target, "wf": r["wf"], "problems": r["problems"], "dirty": True, "frozen": True}
        self._drop_run()
        self.result = None
        self._reset_status(r["wf"]["id"])
        self._current_set()

    def _validate(self):
        c = self.current
        if not c or not (c["dirty"] or c["source"] == "new"):
            return
        token = self._check_seq.next()
        raw = c["raw"]

        def ok(r):
            if self._check_seq.is_current(token) and self.current and self.current["raw"] is raw:
                self.check_errors = r.get("errors") or []
                self.checks_changed.emit()
                self.current_changed.emit()
        self.api.post("/api/validate", {"workflow": raw, "source": c["source"]}, ok=ok, err=lambda e: None)

    def node_names(self):
        return bugcheck.node_names(self.nodes)

    def bug_check(self):
        """运行页的“bug 检查”：把当前程序交给后端完整校验一遍，结果改写成通俗中文、能定位到节点"""
        c = self.current
        if not c:
            return
        token = self._bug_seq.next()
        raw = c["raw"]
        pending = self.bugs = {"raw": raw, "checking": True}
        self.bugs_changed.emit()

        def settle(result):
            if not self._bug_seq.is_current(token):
                return
            if self.current and self.current["raw"] is raw:
                self.bugs = {"raw": raw, **result()}
            elif self.bugs is pending:
                self.bugs = None  # 检查期间程序变了：结果作废，也不留下“检查中”（撤销回原样时按钮还能再点）
            self.bugs_changed.emit()

        def ok(r):
            settle(lambda: {"items": bugcheck.check_items(r.get("errors") or [], self.current.get("problems"), raw, self.node_names())})

        def bad(e):
            settle(lambda: {"error": f"没能检查：{e}"})
        self.api.post("/api/validate", {"workflow": raw, "source": c["source"]}, ok=ok, err=bad)

    def focus_targets(self, targets):
        """点了问题旁的“定位”：选中并移到那个节点"""
        on_canvas = {n["id"] for n in self.nodes}
        for t in targets or []:
            if t in on_canvas:
                return self.focus_node(t)
        self.set_notice("画布上找不到这个问题对应的节点")

    def issue_map(self):
        """图上要标问题的节点：id → 问题文字列表（编辑后校验出的、生成过程中实跑检查指出的）"""
        m = {}
        c = self.current
        if not c:
            return m
        on_canvas = {n["id"] for n in self.nodes}
        if c["dirty"] or c["source"] == "new":
            for err in self.check_errors:
                for nid in flow.issue_targets(err, c["raw"]):
                    if nid in on_canvas:
                        m.setdefault(nid, []).append(flow.issue_text(err))
        if not self.drawn:
            return m
        checked = next((e for e in self.gen_events if e.get("type") == "checked" and e.get("round") == self.drawn["round"]), None)
        for p in (checked.get("problems") or []) if checked and checked.get("status") == "failed" else []:
            for nid in self.targets_for(p, self.drawn["round"]):
                m.setdefault(nid, []).append(p)
        return m

    def targets_for(self, issue, rnd):
        """生成过程里某一轮的一条问题说的是画布上哪些节点（只有画布上正显示那一轮时才有）"""
        if not self.drawn or self.drawn["round"] != rnd:
            return []
        on_canvas = {n["id"] for n in self.nodes}
        return [i for i in flow.issue_targets(issue, self.drawn["wf"]) if i in on_canvas]

    def guide(self):
        """从空白开始的分步清单：新建的工作流，或者还没有节点的图"""
        c = self.current
        if not (self.editable and c and (c["source"] == "new" or not (c["raw"].get("nodes") or [])) and c["source"] != "draft"):
            return None
        return flow.guide_steps(c["raw"], self.check_errors, self.ran_ok)

    # ---------- 选中 ----------
    def select_node(self, nid):
        self.multi = set()
        self.selected = nid
        self.selected_edge = None
        self.selection_changed.emit()

    def select_edge(self, e):
        self.multi = set()
        self.selected_edge = {"id": e["id"], **e["data"]}
        self.selected = None
        self.selection_changed.emit()

    def clear_selection(self):
        self.multi = set()
        self.selected = None
        self.selected_edge = None
        self.selection_changed.emit()

    def is_selected(self, nid):
        return nid == self.selected or nid in self.multi

    def set_hover(self, nid):
        if nid != self.hovered:
            self.hovered = nid
            self.hover_changed.emit()

    def focus_node(self, nid):
        self.multi = set()
        self.selected = nid
        self.selection_changed.emit()
        self.focus_request.emit(nid, 0.6, 1.1)

    def selected_node(self):
        return next((n for n in self.nodes if n["id"] == self.selected), None)

    def delete_selected(self):
        if self.selected_edge:
            if self.edit(lambda wf: ed.remove_edge(wf, self.selected_edge)):
                self.selected_edge = None
                self.selection_changed.emit()
        elif self.selected and not ed.split_id(self.selected)["local"].startswith("$"):
            sel = self.selected
            if self.edit(lambda wf: ed.remove_node(wf, sel)):
                self.selected = None
                self.selection_changed.emit()

    def loop_scope(self):
        """选中的是循环（或循环里的节点）时，新节点加进那个循环里"""
        if not self.selected:
            return None
        n = self.selected_node()
        if n and n["type"] == "loop":
            return self.selected
        return (n or {}).get("parentId")

    def add_at(self, spec, view_center=None):
        """添加节点：循环里放在已有节点下方，否则放在画布当前视野中间（view_center：画布坐标）附近的空地"""
        ls = self.loop_scope()
        scope = ls.split("/") if ls else []
        box = {}

        def fn(wf):
            if scope:
                g = ed.graph_at(wf, scope)
                ys = [p["y"] for p in (g.get("layout") or {}).values() if isinstance(p, dict) and "y" in p]
                pos = {"x": 140, "y": max(ys) + 110 if ys else 0}
            else:
                want = {"x": view_center["x"] - 98, "y": view_center["y"] - 40} if view_center else {"x": 0, "y": 0}
                pos = free_spot(want, [n for n in self.nodes if not n.get("parentId")])
            r = ed.add_node(wf, scope, spec, pos)
            box["id"] = r["id"]
            box["focus"].append(r["id"])
            return r["wf"]
        box["focus"] = []
        if self.edit(fn, box["focus"]) and box.get("id"):
            self.select_node(box["id"])

    def connect(self, c):
        """c：{source, sourceHandle, target, targetHandle}"""
        return self.edit(lambda wf: ed.connect(wf, c))

    def edges_at(self, nid, handle):
        """画布上连在 (节点, 插孔) 上的全部连线"""
        return [e for e in self.edges
                if (e["source"] == nid and e["sourceHandle"] == handle) or (e["target"] == nid and e["targetHandle"] == handle)]

    def disconnect_port(self, nid, handle):
        """右键插孔：断开与它相连的所有连线。返回断开了几条"""
        edges = self.edges_at(nid, handle)
        if not edges:
            self.set_notice("这个插孔上没有连线")
            return 0
        gone = [e["data"] for e in edges]
        if not self.edit(lambda wf: ed.remove_edges(wf, gone)):
            return 0
        if self.selected_edge and any(self.selected_edge.get("from") == g["from"] and self.selected_edge.get("to") == g["to"]
                                      and self.selected_edge.get("scope") == g["scope"] for g in gone):
            self.selected_edge = None
            self.selection_changed.emit()
        return len(gone)

    @staticmethod
    def can_connect(c):
        """只能连同一层的节点"""
        if c["source"] == c["target"]:
            return False
        return ed.split_id(c["source"])["scope"] == ed.split_id(c["target"])["scope"]

    # ---------- 选中节点的编辑器 ----------
    def node_edits(self):
        """选中节点在程序里的参数与来源，交给节点详情里的编辑器；不能编辑时为 None"""
        sel, c = self.selected, self.current
        if not sel or not c or not self.editable:
            return None
        sp = ed.split_id(sel)
        if sp["local"].startswith("$"):
            return None
        try:
            g = ed.graph_at(c["raw"], sp["scope"])
        except Exception:
            return None
        n = next((x for x in g.get("nodes") or [] if x.get("id") == sp["local"]), None)
        if not n:
            return None
        local = sp["local"]
        incoming = {e["to"][len(local) + 1:]: e["from"] for e in g.get("edges") or [] if str(e.get("to", "")).startswith(f"{local}.")}

        def remove():
            if self.edit(lambda wf: ed.remove_node(wf, sel)):
                self.selected = None
                self.selection_changed.emit()
        return {
            "node": n, "incoming": incoming,
            "set_config": lambda port, v: self.edit(lambda wf: ed.set_config(wf, sel, port, v)),
            "set_label": lambda v: self.edit(lambda wf: ed.set_label(wf, sel, v)),
            "set_max_items": lambda v: self.edit(lambda wf: ed.set_max_items(wf, sel, v)),
            "remove": remove,
            "move_out": (lambda: self.move_out_of_loop(sel)) if sp["scope"] else None,
        }

    def port_side(self):
        """选中“输入”或“输出”端点时：inputs / outputs"""
        if not self.editable:
            return None
        return "inputs" if self.selected == "$in" else "outputs" if self.selected == "$out" else None

    def port_ops(self):
        side = self.port_side()
        if not side:
            return None
        return {
            "add": lambda: self.edit(lambda wf: ed.add_port(wf, side)),
            "rename": lambda a, b: self.edit(lambda wf: ed.rename_port(wf, side, a, b)),
            "type": lambda name, t: self.edit(lambda wf: ed.set_port_type(wf, side, name, t)),
            "fields": lambda name, f: self.edit(lambda wf: ed.set_port_fields(wf, side, name, f)),
            "remove": lambda name: self.edit(lambda wf: ed.remove_port(wf, side, name)),
        }

    def set_meta(self, meta):
        return self.edit(lambda wf: ed.set_meta(wf, **meta))

    # ---------- 拖动节点 ----------
    def _loop_sizes(self):
        return {n["id"]: {"w": n["data"]["width"], "h": n["data"]["height"]} for n in self.nodes if n["type"] == "loop"}

    def drag_start(self):
        self._drag_base = self._loop_sizes()
        self._drag_raw = self.current["raw"] if self.current else None
        self._guarded = {}
        self.drop_box = None

    def _drop_target(self, nodes, ids):
        """正在拖的单个节点（模块、条件分支、合并）中心落在同一层的哪个循环框里：返回框的 id，没有就是 None"""
        if len(ids) != 1:
            return None
        nid = next(iter(ids))
        n = next((x for x in nodes if x["id"] == nid), None)
        if not n or n.get("type") != "module" or n.get("parentId"):
            return None
        r = collide.rect_of(n)
        cx, cy = r["x"] + r["w"] / 2, r["y"] + r["h"] / 2
        for b in nodes:
            if b.get("type") == "loop" and not b.get("parentId") and b["id"] != nid:
                br = collide.rect_of(b)
                if br["x"] <= cx <= br["x"] + br["w"] and br["y"] <= cy <= br["y"] + br["h"]:
                    return b["id"]
        return None

    def drag_move(self, positions):
        """拖动中：positions = {节点 id: 想去的位置}。撞上同层节点就贴着边滑，不重叠；框里的节点碰到框边时框跟着变大。
        改好后发 nodes_moved（位置变了的节点和变了样子的框）"""
        changes = [{"type": "position", "id": k, "position": p, "dragging": True} for k, p in positions.items()]
        # 单个模块节点可以从外面拖进循环框：框不挡它（松手时中心在框里就放进去，不在就照常挪到空位）
        enter = len(positions) == 1 and any(n["id"] in positions and n.get("type") == "module" and not n.get("parentId")
                                            for n in self.nodes)
        guarded = collide.guard_changes(changes, self.nodes, self._guarded, passable_loops=enter)
        new_pos = {ch["id"]: ch["position"] for ch in guarded if ch.get("type") == "position" and ch.get("position")}
        before = {n["id"]: n for n in self.nodes}
        nodes = [{**n, "position": new_pos[n["id"]]} if n["id"] in new_pos else n for n in self.nodes]
        nodes = collide.fit_while_dragging(nodes)
        changed = [n["id"] for n in nodes if before.get(n["id"]) is not n]
        self.nodes = nodes
        box = self._drop_target(nodes, positions) if enter else None
        if box != self.drop_box:  # 进出循环框：两个框都重画（亮起“松手放进来”）
            changed += [b for b in (self.drop_box, box) if b and b not in changed]
            self.drop_box = box
        if changed:
            self.nodes_moved.emit(changed)

    def drag_stop(self, ids):
        """放下后：压着别的节点就挪到最近的空位；框装不下就扩大，扩大后挤到的邻居被推开。整个结果算一步，可以撤销"""
        box, self.drop_box = self.drop_box, None
        if not ids:
            return
        if not self.current or self.current["raw"] is not self._drag_raw:
            self._guarded = {}
            return self._rebuild()  # 拖动中程序变了：这次拖动作废，按当前程序重画
        if box and len(ids) == 1:
            nid = next(iter(ids))
            pos = self._guarded.get(nid) or next((n["position"] for n in self.nodes if n["id"] == nid), None)
            self._guarded = {}
            if pos and self._drop_into(nid, box, pos):
                return
        live = [{**n, "position": self._guarded[n["id"]]} if n["id"] in self._guarded else n for n in self.nodes]
        self._guarded = {}
        sizes = {}
        moves = {i: next(n["position"] for n in live if n["id"] == i) for i in ids if any(n["id"] == i for n in live)}
        moves.update(collide.settle(live, list(ids), base=self._drag_base, sizes_out=sizes))
        # 程序没变（例如拖出去又拖回原处）：按当前程序重画，位置和拖动中临时撑大的框都回到程序里的样子
        if not self.edit(lambda wf: self._apply_moves(wf, moves, live, sizes), list(ids)):
            self._rebuild()

    def _drop_into(self, nid, box, pos):
        """把拖到框里的节点放进这个循环：pos 是节点在外面那一层的画布位置。连着它的线断开（不同层不能连线），提示一下"""
        b = next((n for n in self.nodes if n["id"] == box), None)
        if not b:
            return False
        rel = {"x": pos["x"] - b["position"]["x"], "y": pos["y"] - b["position"]["y"]}
        at = graph.flow_to_layout(self.nodes)({"id": f"{box}/_", "parentId": box, "data": {}, "position": rel})
        at = {"x": max(0, at["x"]), "y": max(0, at["y"])}
        res = {}

        def fn(wf):
            r = ed.reparent(wf, nid, ed.split_id(box)["scope"] + [ed.split_id(box)["local"]], at)
            res.update(r)
            return r
        if not self.edit(fn, [f"{box}/{nid}"]):
            self._rebuild()
            return True
        self.select_node(res.get("id") or f"{box}/{nid}")
        n = res.get("dropped") or 0
        self.set_notice(f"已放进循环框{f'，原来连着它的 {n} 条线断开了，请在框里重新连' if n else ''}（可以撤销）")
        return True

    def move_out_of_loop(self, cid):
        """“移出循环框”：把循环里的节点挪到框外面（框的正下方），连着它的线断开"""
        sp = ed.split_id(cid)
        if not sp["scope"]:
            return
        box_id = "/".join(sp["scope"])
        b = next((n for n in self.nodes if n["id"] == box_id), None)
        if not b:
            return
        r = collide.rect_of(b)
        at = graph.flow_to_layout(self.nodes)(b, {"x": r["x"], "y": r["y"] + r["h"] + 40})
        res = {}

        def fn(wf):
            out = ed.reparent(wf, cid, sp["scope"][:-1], at)
            res.update(out)
            return out
        if self.edit(fn):
            self.select_node(res["id"])
            n = res.get("dropped") or 0
            self.set_notice(f"已移出循环框{f'，原来连着它的 {n} 条线断开了' if n else ''}（可以撤销）")

    # ---------- 循环框 ----------
    def box_resize_start(self, bid):
        box = next((n for n in self.nodes if n["id"] == bid), None)
        if not box:
            self._resize = None
            return
        kids = [collide.rect_of(n) for n in self.nodes if n.get("parentId") == bid]
        self._resize = {"id": bid, "x": box["position"]["x"], "y": box["position"]["y"], "kids": kids,
                        "nodes": self.nodes, "raw": self.current["raw"] if self.current else None}

    def _resize_live(self, bid):
        """正在拉伸的框；拉伸中程序变了（撤销、换了工作流）就作废这次拉伸，按当前程序重画"""
        st = self._resize
        if not st or st["id"] != bid:
            return None
        if not self.current or self.current["raw"] is not st["raw"]:
            self._resize = None
            self._rebuild()
            return None
        return st

    def box_resize_allow(self, bid, rect):
        """rect：{x, y, width, height}（框在它那一层的新位置和大小）。压到框里的节点时不允许"""
        st = self._resize
        return not st or st["id"] != bid or collide.can_resize(st, rect)

    def box_resize_move(self, bid, rect):
        """拉伸中：框改成 rect，框里的节点在画布上不动（拉左边、上边时它们相对框的位置相应变化）"""
        st = self._resize_live(bid)
        if not st or not self.box_resize_allow(bid, rect):
            return False
        dx, dy = rect["x"] - st["x"], rect["y"] - st["y"]
        out = []
        for n in st["nodes"]:
            if n["id"] == bid:
                n = {**n, "position": {"x": rect["x"], "y": rect["y"]}, "data": {**n["data"], "width": rect["width"], "height": rect["height"]}}
            elif n.get("parentId") == bid:
                n = {**n, "position": {"x": n["position"]["x"] - dx, "y": n["position"]["y"] - dy}}
            out.append(n)
        self.nodes = out
        self.nodes_moved.emit([bid] + [n["id"] for n in out if n.get("parentId") == bid])
        return True

    def box_resize_end(self, bid, rect):
        st = self._resize_live(bid)
        self._resize = None
        if st:
            self._commit_box(bid, rect)

    def _commit_box(self, bid, rect, kid_shift=None):
        """把框改成 rect 并记下大小、关掉自动收缩，算一步改动。kid_shift：框里节点相对框要挪的量"""
        kx, ky = (kid_shift or {}).get("x", 0), (kid_shift or {}).get("y", 0)
        ns = [{**n, "position": {"x": n["position"]["x"] + kx, "y": n["position"]["y"] + ky}}
              if n.get("parentId") == bid and (kx or ky) else n for n in self.nodes]
        moves = {bid: {"x": rect["x"], "y": rect["y"]}}
        for n in ns:
            if n.get("parentId") == bid:
                moves[n["id"]] = n["position"]  # 拉左边、上边后框里节点的相对位置变了
        own = {"w": rect["width"], "h": rect["height"]}
        # 按“画在哪就写哪”换算：不再带自动伸缩时的 offset、shift
        live = [{**n, "position": {"x": rect["x"], "y": rect["y"]},
                 "data": {**n["data"], "width": rect["width"], "height": rect["height"], "manual": own, "offset": None, "shift": {"x": 0, "y": 0}}}
                if n["id"] == bid else n for n in ns]
        # 写回之前先整理一遍：嵌套的框拉出外层框的左边、上边时，外层框先扩大、坐标先补偿好；拉大压到的邻居也在这里推开
        sizes = {}
        moves.update(collide.settle(live, [], pinned=[bid], sizes_out=sizes))
        final = sizes.pop(bid, own)
        ok = self.edit(lambda wf: ed.set_box_size(self._apply_moves(wf, moves, live, sizes), bid, final), [], pinned=[bid])
        if not ok:
            self._rebuild()  # 没变：拉伸中改过的框按程序画回去
        return ok

    def box_set_auto(self, bid, on):
        """自动收缩：打开时去掉记下的大小，框回到正好包住框里节点；关掉时按框现在画出来的样子记下大小"""
        box = next((n for n in self.nodes if n["id"] == bid), None)
        if not box:
            return
        if on:
            self.edit(lambda wf: ed.set_box_size(wf, bid, None), [])
        else:
            self._commit_box(bid, {"x": box["position"]["x"], "y": box["position"]["y"], "width": box["data"]["width"], "height": box["data"]["height"]})

    def box_fit(self, bid):
        """收紧到节点：框收到正好包住框里节点（自动收缩仍关着，之后保持这个大小）"""
        box = next((n for n in self.nodes if n["id"] == bid), None)
        kids = [collide.rect_of(n) for n in self.nodes if n.get("parentId") == bid]
        if not box or not kids:
            return
        dx = min(k["x"] for k in kids) - graph.PAD
        dy = min(k["y"] for k in kids) - graph.TOP
        size = collide.fit_size(kids)
        self._commit_box(bid, {"x": box["position"]["x"] + dx, "y": box["position"]["y"] + dy, "width": size["w"], "height": size["h"]},
                         {"x": -dx, "y": -dy})

    def box_arrange(self, bid):
        """整理框里节点：按连线从左到右重新排整齐"""
        self.edit(lambda wf: ed.arrange_body(wf, bid), [], arranged=bid)

    def box_select_kids(self, bid):
        """选中框里的全部节点（不含框本身），可以一起拖动"""
        self.multi = {n["id"] for n in self.nodes if n.get("parentId") == bid}
        self.selected = None
        self.selected_edge = None
        self.selection_changed.emit()

    def box_zoom(self, bid):
        self.focus_request.emit(bid, 0.15, 1.2)

    # ---------- 画布用（canvas/view.py 加的） ----------
    def edge_states(self, edges):
        """一次算出多条连线的画法：edge id → edge_state。运行中状态表、运行记录只整理一次，连线多时不卡"""
        ctx = (self.statuses(), self.records_by_id()) if (self.running or self.result) else None
        return {e["id"]: self.edge_state(e, ctx) for e in edges}

    # ---------- 关闭 ----------
    def shutdown(self, keep_running=False) -> bool:
        """关窗口前：存好所有项目。有没存好的就问使用者，返回 False 表示先不关。
        keep_running：窗口只是转到后台（桌宠接着显示进度），正在进行的运行、生成不停"""
        self.commit_pending.emit()  # 焦点还在输入框里的改动先进程序，再一起存
        failed = self.flush_store_sync()
        if failed and not self.ask(title="有项目没能存好",
                                   message="\n".join(f"· {n}：{why}" for n, why in failed) + "\n\n现在关闭，这些项目最近的改动会丢失。",
                                   ok="仍然关闭", cancel="先不关", danger=True):
            return False
        if keep_running:
            return True
        for h in (self._gen_handle, self._run_handle):
            if h:
                h.abort()
        return True
