"""“节点”页（移植自 web/src/panels.jsx 的 NodeDetail、NodeDetailBody、Field，以及 App.jsx 里这一页的分流）：
选中连线 → 连线详情；选中“输入”“输出”且可编辑 → 增删工作流的输入输出；什么都没选且可编辑 → 名称和说明；其余 → 节点详情。
节点详情按节点 id 建一个（换节点就从第一页看起）；显示部分在数据变了时重画，编辑器一直保留、只更新值，正在打的字不丢。"""
import json
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget

from ..logic._js import js_str
from .common import Details, Field, Led, button, clear, flush_focused, hbox, label, sep_frame, set_tone, vbox
from .editors import EdgeDetail, LabelEditor, MaxItemsEditor, MetaEditor, ParamEditor, PortsEditor

PAGE = 20  # 循环节点的记录一次显示多少条
STATE_TEXT = {"idle": "还没运行", "running": "运行中", "completed": "完成", "skipped": "跳过", "failed": "出错"}
KIND_TEXT = {"if": "条件分支", "merge": "合并", "foreach": "逐项循环", "subflow": "子流程"}
_ITEM = re.compile(r"\[(\d+)\]")


def _sig(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True, default=str)


class NodeTab(QWidget):
    """按选中的东西挑一种详情；种类（或节点）变了才换一个新的"""

    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.lay = vbox(self)
        self.mode = None
        self.child = None

    def _mode(self):
        c = self.ctrl
        if c.selected_edge:
            return ("edge", _sig(c.selected_edge), c.editable)
        side = c.port_side()
        if side:
            return ("ports", side)
        if not c.selected and c.editable:
            return ("meta",)
        return ("node", c.selected or "")

    def sync(self):
        c = self.ctrl
        mode = self._mode()
        if mode != self.mode:
            if self.child is not None:
                flush_focused(self.child, self.ctrl)
            clear(self.lay)
            self.mode = mode
            self.child = self._make(mode)
            self.lay.addWidget(self.child)
            return
        kind = mode[0]
        if kind == "ports":
            self.child.editor.sync(c.current["raw"].get(mode[1]), c.port_ops())
        elif kind == "meta":
            self.child.sync(c.current["raw"].get("name"), c.current["raw"].get("intent"), c.set_meta)
        elif kind == "node":
            self._sync_node(self.child)

    def _make(self, mode):
        c = self.ctrl
        kind = mode[0]
        if kind == "edge":
            return EdgeDetail(c.selected_edge, c.delete_selected if c.editable else None)
        if kind == "ports":
            side = mode[1]
            w = QWidget()
            lay = vbox(w)
            lay.addWidget(label(f"工作流的{'输入' if side == 'inputs' else '输出'}", cls="h3"))
            lay.addSpacing(12)
            lay.addWidget(label("运行时要使用者提供的值。改名字时，从它连出去的线会跟着改。" if side == "inputs"
                                else "运行结束后交给使用者的结果。每个输出都要有一条线接进来。", cls="desc"))
            lay.addSpacing(12)
            w.editor = PortsEditor(side, c.current["raw"].get(side), c.port_ops())
            lay.addWidget(w.editor)
            return w
        if kind == "meta":
            return MetaEditor(c.current["raw"].get("name"), c.current["raw"].get("intent"), c.set_meta)
        body = NodeDetailBody()
        self._sync_node(body)
        return body

    def _sync_node(self, body):
        c = self.ctrl
        sel = c.selected
        body.sync(c.selected_node(), c.statuses().get(sel) if sel else None,
                  c.records_by_id().get(sel) if sel else None, c.node_edits())


class NodeDetailBody(QWidget):
    def __init__(self):
        super().__init__()
        self.lay = vbox(self)
        self.top = vbox()
        self.edit_host = vbox()
        self.bottom = vbox()
        for x in (self.top, self.edit_host, self.bottom):
            self.lay.addLayout(x)
        self.shown = PAGE
        self.open_records = {}   # 记录路径 → 是否展开（重画时保留）
        self.open_values = {}    # 长文本是否展开
        self._top_key = self._bottom_key = self._edit_key = None
        self.editing = None

    def sync(self, node, status, records, edits):
        self.node, self.status, self.records, self.edits = node, status, records, edits
        if not node:
            if self._top_key != "none":
                self._top_key = "none"
                clear(self.top)
                clear(self.bottom)
                self._bottom_key = None
                self.top.addWidget(label("点画布上的节点，查看它做什么、收到了什么、产生了什么。", cls="muted"))
            self._sync_edits(None)
            return
        d = node.get("data") or {}
        m = d.get("module")
        rec_key = tuple(id(r) for r in records) if records else None
        top_key = _sig([node.get("id"), d.get("node"), d.get("title"), (m or {}).get("key"), (m or {}).get("name"),
                        (m or {}).get("description"), status, rec_key])
        if top_key != self._top_key:
            self._top_key = top_key
            clear(self.top)
            self._build_top(node, status, records)
        self._sync_edits(edits)
        bottom_key = (_sig([d.get("node"), d.get("ins"), d.get("outs"), (m or {}).get("key"), bool(edits)]), rec_key, self.shown)
        if bottom_key != self._bottom_key:
            self._bottom_key = bottom_key
            clear(self.bottom)
            self._build_bottom(node, records, edits)

    # ---- 标题、说明、状态 ----
    def _build_top(self, node, status, records):
        d = node.get("data") or {}
        n, m = d.get("node") or {}, d.get("module")
        state = (status or {}).get("state") or "idle"
        head = hbox(spacing=10)
        head.addWidget(Led(state, size=12, top=6), 0, Qt.AlignmentFlag.AlignTop)
        titles = vbox(spacing=2)
        titles.addWidget(label(js_str(n.get("label") or (m or {}).get("name") or d.get("title") or node["id"]), cls="h3", select=True))
        sub = hbox(spacing=8)
        sub.addWidget(label(f"{m['name']}模块" if m else KIND_TEXT.get(n.get("kind")) or "工作流的端点", cls="sub", wrap=False))
        if n.get("module"):
            sub.addWidget(label(n["module"], cls="chip", wrap=False, tip="模块写法", select=True))
        sub.addStretch(1)
        titles.addLayout(sub)
        head.addLayout(titles, 1)
        self.top.addLayout(head)
        if m:
            self.top.addSpacing(12)
            self.top.addWidget(label(js_str(m.get("description") or ""), cls="desc"))
        counts = (status or {}).get("counts")
        text = STATE_TEXT.get(state, state)
        if counts:
            text += f"：共 {js_str(counts.get('total'))} 项，完成 {js_str(counts.get('completed'))}，跳过 {js_str(counts.get('skipped'))}，出错 {js_str(counts.get('failed'))}"
        elif records and len(records) == 1 and records[0].get("status") == "completed":
            text += f"，用时 {js_str(records[0].get('seconds'))} 秒"
        self.top.addSpacing(12)
        st = label(text, cls="state")
        set_tone(st, state)
        self.top.addWidget(st)
        if (status or {}).get("reason"):
            self.top.addSpacing(6)
            self.top.addWidget(label(js_str(status["reason"]), cls="muted", select=True))

    # ---- 修改（可编辑时）：编辑器保留，只更新值 ----
    def _sync_edits(self, edits):
        node = self.node
        d = (node or {}).get("data") or {}
        m = d.get("module")
        key = None
        if edits and node:
            key = (node["id"], edits["node"].get("kind"), (m or {}).get("key"), tuple((m or {}).get("inputs") or {}))
        if key != self._edit_key:
            if self.editing is not None:
                flush_focused(self.editing)
            clear(self.edit_host)
            self.editing = None
            self._edit_key = key
            if key is not None:
                self._build_edits(edits, m, d, node)
                return
        if self.editing is not None:
            en = edits["node"]
            fallback = (m or {}).get("name") or d.get("title") or node["id"]
            self.label_ed.sync(en.get("label"), fallback, edits["set_label"])
            if self.max_ed:
                self.max_ed.sync(en.get("max_items"), edits["set_max_items"])
            if self.param_ed:
                self.param_ed.sync(en.get("config") or {}, edits["incoming"], edits["set_config"])
            self.edits_now = edits

    def _build_edits(self, edits, m, d, node):
        f, lay = sep_frame(top=16, pad=12)
        self.editing = f
        self.edits_now = edits
        en = edits["node"]
        lay.addWidget(label("修改", cls="h4"))
        lay.addSpacing(4)
        self.label_ed = LabelEditor(en.get("label"), (m or {}).get("name") or d.get("title") or node["id"], edits["set_label"])
        lay.addWidget(self.label_ed)
        self.max_ed = None
        if en.get("kind") == "foreach":
            self.max_ed = MaxItemsEditor(en.get("max_items"), edits["set_max_items"])
            lay.addWidget(self.max_ed)
        self.param_ed = None
        if m:
            lay.addWidget(label("参数", cls="h5"))
            lay.addSpacing(6)
            self.param_ed = ParamEditor(m, en.get("config") or {}, edits["incoming"], edits["set_config"])
            lay.addWidget(self.param_ed)
        lay.addSpacing(12)
        if edits.get("move_out"):
            lay.addWidget(button("移出循环框", wide=True, on=lambda: self.edits_now["move_out"]()))
            lay.addSpacing(4)
            lay.addWidget(label("挪到框外面，以后不再对每一项都做一遍；连着它的线会断开。要放回去，把它拖进框里。", cls="hint"))
            lay.addSpacing(8)
        lay.addWidget(button("删除这个节点", kind="danger", wide=True, on=lambda: self.edits_now["remove"]()))
        lay.addSpacing(8)
        lay.addWidget(label("连着它的线会一起删除。可以用“撤销”恢复。", cls="hint"))
        self.edit_host.addWidget(f)

    # ---- 固定参数、运行记录 ----
    def _build_bottom(self, node, records, edits):
        d = node.get("data") or {}
        n, m = d.get("node") or {}, d.get("module")
        add = self.bottom.addWidget

        # 端口名 → 含义：显示时用含义作说明，端口名本身仍保留（它和图里的连线对应）
        def meaning(side, k):
            desc = (((m or {}).get(side) or {}).get(k) or {}).get("description") if m else None
            if desc:
                return desc
            p = next((p for p in [*(d.get("ins") or []), *(d.get("outs") or [])] if p.get("name") == k), None)
            return (p or {}).get("desc") or ""
        # 收到的参数：接了线或写了固定值的（画布上显示的那些）在前；其余是模块默认值，收起
        shown_ports = {p.get("name") for p in d.get("ins") or []}
        main = lambda r: [(k, v) for k, v in (r.get("inputs") or {}).items() if k in shown_ports or not m]  # noqa: E731
        rest = lambda r: [(k, v) for k, v in (r.get("inputs") or {}).items() if k not in shown_ports] if m else []  # noqa: E731

        def section(title):
            f, lay = sep_frame(top=16, pad=12)
            lay.addWidget(label(title, cls="h4"))
            return f, lay

        cfg = n.get("config")
        if not edits and isinstance(cfg, dict) and cfg:
            f, lay = section("固定参数")
            for k, v in cfg.items():
                lay.addWidget(Field(k, meaning("inputs", k), v, self.open_values, ("cfg", k)))
            add(f)

        loop = any("[" in js_str(r.get("path")) for r in records or [])

        def body(lay, r):
            p = r.get("path")
            if r.get("reason"):
                lay.addWidget(label(js_str(r["reason"]), cls="muted", select=True))
            if main(r):
                lay.addSpacing(12)
                lay.addWidget(label("收到", cls="h5"))
            for k, v in main(r):
                lay.addWidget(Field(k, meaning("inputs", k), v, self.open_values, (p, "i", k)))
            if rest(r):
                dd = Details(f"另有 {len(rest(r))} 个参数用的是默认值", open=bool(self.open_records.get((p, "defaults"))),
                             on_toggle=lambda on, p=p: self.open_records.__setitem__((p, "defaults"), on))
                dd.setContentsMargins(0, 8, 0, 0)
                for k, v in rest(r):
                    dd.body_lay.addWidget(Field(k, meaning("inputs", k), v, self.open_values, (p, "d", k)))
                lay.addWidget(dd)
            if r.get("outputs"):
                lay.addSpacing(12)
                lay.addWidget(label("产生", cls="h5"))
            for k, v in (r.get("outputs") or {}).items():
                lay.addWidget(Field(k, meaning("outputs", k), v, self.open_values, (p, "o", k)))

        for i, r in enumerate((records or [])[:self.shown]):
            p = r.get("path")
            if not loop:
                f, lay = section("这次运行")
                body(lay, r)
                add(f)
                continue
            mm = _ITEM.search(js_str(p))
            item = int(mm.group(1)) + 1 if mm else i + 1
            st = r.get("status")
            text = f"第 {item} 项：{STATE_TEXT.get(st, js_str(st))}" + (f"，{js_str(r.get('seconds'))} 秒" if st == "completed" else "")
            rec = Details(text, open=self.open_records.get(p, i == 0), cls="rec-summary",
                          tone="skipped" if st == "skipped" else None, extra=[Led(st, size=10, top=0)],
                          on_toggle=lambda on, p=p: self.open_records.__setitem__(p, on), pad=(0, 0, 0, 12))
            frame = _boxed(rec)
            frame.setContentsMargins(0, 16 if i == 0 else 8, 0, 0)
            body(rec.body_lay, r)
            add(frame)
        if records and len(records) > self.shown:
            self.bottom.addSpacing(12)
            add(button(f"再显示 {min(PAGE, len(records) - self.shown)} 条（共 {len(records)} 条）", kind="ghost", wide=True,
                       on=self._more))
        if not records:
            self.bottom.addSpacing(12)
            add(label("运行之后，这里会显示它每次收到的输入和产生的输出。", cls="muted"))

    def _more(self):
        self.shown += PAGE
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, lambda: self.sync(self.node, self.status, self.records, self.edits))


def _boxed(inner):
    """给一条循环记录套上边框（.record）"""
    from PySide6.QtWidgets import QFrame
    outer = QWidget()
    ol = vbox(outer)
    f = QFrame()
    f.setProperty("cls", "box")
    fl = vbox(f, margins=(12, 0, 12, 0))
    fl.addWidget(inner)
    ol.addWidget(f)
    return outer
