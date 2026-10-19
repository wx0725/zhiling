"""运行页（移植自 web/src/panels.jsx 的 RunPanel、OutputsPreview、RunResult）：按输入类型生成表单，运行，显示结果。
另有“bug 检查”：运行前把工作流完整检查一遍，用通俗中文列出问题，点“定位”移到出问题的节点；运行出错时同样说明哪一步、为什么。
填好的参数只在输入声明变了时重置；换工作流时整个运行页由 RightPanel 重新建一个。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QLineEdit, QWidget

from ..logic import bugcheck
from ..logic._js import js_str, json_stringify
from ..logic.form import collect_inputs, initial_values
from ..logic.labels import type_label
from .common import CommitText, Dot, Value, button, clear, combo, esc, hbox, label, sep_frame, set_combo, vbox
from .editors import FieldBox


BUSY_TEXT = {"running": "运行中…", "pausing": "正在暂停…", "paused": "已暂停", "stopping": "正在终止…"}


def is_multiline(t):
    return t.startswith("list<") or t in ("object", "any")


class RunPanel(QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.lay = vbox(self)
        self.values = {}
        self.errors = {}
        self.fields = {}
        self._inputs_key = None
        self._bottom_key = None
        self.empty = label("先打开或生成一个工作流。", cls="muted")
        self.lay.addWidget(self.empty)
        self.form = QWidget()
        self.form_lay = vbox(self.form)
        self.lay.addWidget(self.form)
        self.inputs_host = vbox()
        self.form_lay.addLayout(self.inputs_host)
        self.run_btn = button("运行", kind="primary", wide=True, on=self.submit)
        self.form_lay.addWidget(self.run_btn)
        self.bug_host = vbox()
        self.form_lay.addLayout(self.bug_host)
        self._bug_key = None
        self.bottom = vbox()
        self.form_lay.addLayout(self.bottom)
        self.form.hide()

    def wf(self):
        c = self.ctrl.current
        return c["wf"] if c else None

    def sync(self):
        wf = self.wf()
        self.empty.setVisible(wf is None)
        self.form.setVisible(wf is not None)
        if wf is None:
            return
        inputs = wf.get("inputs") or {}
        key = json_stringify(inputs)
        if key != self._inputs_key:  # 只在输入声明变了时重置；在画布上改节点不会清掉已填的参数
            self._inputs_key = key
            self.values = initial_values(inputs)
            self.errors = {}
            self._build_inputs(inputs)
        running = bool(self.ctrl.running)
        self.run_btn.setText(BUSY_TEXT.get(self.ctrl.run_state(), "运行中…") if running else "运行")
        self.run_btn.setEnabled(not running)
        bugs = getattr(self.ctrl, "bugs", None)
        raw = self.ctrl.current.get("raw")
        bug_key = (id(bugs), bugs is not None and bugs.get("raw") is raw)
        if bug_key != self._bug_key:
            self._bug_key = bug_key
            clear(self.bug_host)
            self.bug_host.addWidget(BugCheck(self.ctrl, bugs if bugs and bugs.get("raw") is raw else None))
        result = self.ctrl.result
        bkey = (id(result), json_stringify(wf.get("outputs") or {}))
        if bkey != self._bottom_key:
            self._bottom_key = bkey
            clear(self.bottom)
            w = RunResult(result, wf.get("outputs") or {}, self.ctrl) if result else OutputsPreview(wf.get("outputs") or {})
            if w is not None:
                self.bottom.addWidget(w)

    def _build_inputs(self, inputs):
        clear(self.inputs_host)
        self.fields = {}
        if not inputs:
            self.inputs_host.addWidget(label("这个工作流不需要输入。", cls="muted"))
            self.inputs_host.addSpacing(14)
        for k, p in inputs.items():
            t = p.get("type") or "any"
            optional = p.get("required") is False or "default" in p
            f = FieldBox(esc(p.get("description") or k), type_label(t) + ("，可不填" if optional else ""), t)
            f.title_row.insertWidget(0, Dot(t), 0, Qt.AlignmentFlag.AlignVCenter)
            v = self.values.get(k)
            if t == "boolean" and p.get("required") is False and "default" not in p:
                ctl = combo([("", "不填"), (True, "是"), (False, "否")], "" if v is None else bool(v),
                            lambda x, k=k: self._set(k, None if x == "" else x))
            elif t == "boolean":
                ctl = QCheckBox()
                ctl.setChecked(bool(v))
                ctl.toggled.connect(lambda on, k=k: self._set(k, on))
            elif is_multiline(t):
                ctl = CommitText(js_str(v if v is not None else ""), rows=3)
                ctl.textChanged.connect(lambda k=k, c=ctl: self._set(k, c.text()))
            else:
                ctl = QLineEdit(js_str(v if v is not None else ""))
                ctl.textChanged.connect(lambda text, k=k: self._set(k, text))
                ctl.returnPressed.connect(self.submit)  # 表单里按 Enter 就是提交
            ctl.setObjectName(f"in-{k}")
            f.body.addWidget(ctl, 0, Qt.AlignmentFlag.AlignLeft if isinstance(ctl, QCheckBox) else Qt.AlignmentFlag(0))
            self.fields[k] = (f, ctl)
            self.inputs_host.addWidget(f)

    def _set(self, k, v):
        self.values[k] = v

    def set_value(self, k, v):
        """测试和外部用：像使用者那样填一个输入"""
        f, ctl = self.fields[k]
        if isinstance(ctl, QCheckBox):
            ctl.setChecked(bool(v))
        elif hasattr(ctl, "itemData"):
            set_combo(ctl, "" if v is None else v)
            self._set(k, v)
        else:
            ctl.setText(v)

    def submit(self):
        """按填好的输入运行；输入有误时标出错处、返回 False"""
        wf = self.wf()
        if wf is None or self.ctrl.running:
            return False
        r = collect_inputs(wf.get("inputs") or {}, self.values)
        self.errors = r["errors"]
        for k, (f, _) in self.fields.items():
            f.set_error(self.errors.get(k, ""))
        if self.errors:
            return False
        self.ctrl.run(r["values"])
        return True


def OutputsPreview(outputs):
    """还没运行时，先告诉使用者这个工作流会得到什么"""
    items = list((outputs or {}).items())
    if not items:
        return None
    f, lay = sep_frame()
    lay.addWidget(label("运行后会得到", cls="h3r", tone="soft"))
    lay.addSpacing(8)
    for k, p in items:
        t = p.get("type") or "any"
        row = hbox(spacing=7, margins=(0, 4, 0, 4))
        row.addWidget(Dot(t), 0, Qt.AlignmentFlag.AlignVCenter)
        row.addWidget(label(p.get("description") or k), 1)
        row.addWidget(label(type_label(t), cls="type", wrap=False, tip=t), 0, Qt.AlignmentFlag.AlignVCenter)
        lay.addLayout(row)
    return f


def _locate(ctrl, targets):
    """“定位”小按钮：画布移到出问题的节点"""
    return button("定位", small=True, on=lambda: ctrl.focus_targets(targets))


def _issue_row(ctrl, text, targets, tip=None):
    row = hbox(spacing=8, margins=(0, 4, 0, 4))
    row.addWidget(label(text, cls="error-text", select=True, tip=tip), 1)
    if targets and ctrl is not None:
        row.addWidget(_locate(ctrl, targets), 0, Qt.AlignmentFlag.AlignTop)
    return row


def BugCheck(ctrl, bugs):
    """bug 检查：按钮 + 结果（没检查过、检查中、没有问题、问题列表）"""
    f, lay = sep_frame()
    f.setAccessibleName("运行前检查")
    head = hbox(spacing=8)
    head.addWidget(label("运行前检查", cls="h3r", tone="soft"), 1)
    checking = bool(bugs and bugs.get("checking"))
    b = button("检查中…" if checking else "检查一遍", small=True, on=ctrl.bug_check)
    b.setEnabled(not checking)
    head.addWidget(b, 0, Qt.AlignmentFlag.AlignVCenter)
    lay.addLayout(head)
    lay.addSpacing(6)
    if bugs is None:
        lay.addWidget(label("运行前先检查：有没有没接线的必填输入、接不上的类型、绕成圈的连线、填错的固定值、没接上的输出。", cls="muted"))
    elif checking:
        lay.addWidget(label("正在检查……", cls="muted"))
    elif bugs.get("error"):
        lay.addWidget(label(bugs["error"], cls="error-text", select=True))
    elif not bugs.get("items"):
        lay.addWidget(label("没有发现问题，可以运行。", cls="banner", tone="ok"))
    else:
        items = bugs["items"]
        lay.addWidget(label(f"发现 {len(items)} 个问题", cls="banner", tone="bad"))
        for it in items:
            lay.addLayout(_issue_row(ctrl, f"· {it['text']}", it.get("targets"), tip=it.get("raw")))
    return f


def RunResult(result, outputs, ctrl=None):
    ok = result.get("status") == "completed"
    stopped = result.get("status") == "stopped"
    f, lay = sep_frame()
    f.setAccessibleName("运行结果")
    title = f"完成，用时 {js_str(result.get('seconds'))} 秒" if ok else "已终止" if stopped else "没有完成"
    lay.addWidget(label(title, cls="h3r", tone="ok" if ok else "soft" if stopped else "bad"))
    lay.addSpacing(8)
    err = result.get("error")
    if stopped:
        lay.addWidget(label("运行被你中途终止，没有得到输出。", cls="muted"))
    elif err:
        c = ctrl.current if ctrl is not None else None
        wf = c["raw"] if c else {}
        why = bugcheck.explain_error(err, wf, ctrl.node_names() if ctrl is not None else {})
        raw = js_str(err.get("message") if err.get("message") is not None else "")
        row = hbox(spacing=8)
        row.addWidget(label(why["title"], cls="flabel"), 1)
        if why["targets"] and ctrl is not None:
            row.addWidget(_locate(ctrl, why["targets"]), 0, Qt.AlignmentFlag.AlignTop)
        lay.addLayout(row)
        if why["cause"]:
            lay.addWidget(label(f"原因：{why['cause']}", select=True))
        if why["detail"]:
            lay.addWidget(label(why["detail"], cls="error-text", select=True, tip=raw))
    for w in result.get("warnings") or []:
        lay.addWidget(label(js_str(w), cls="muted"))
    state = {}
    for k, v in (result.get("outputs") or {}).items():
        box = QWidget()
        bl = vbox(box, spacing=3, margins=(0, 10, 0, 0))
        bl.addWidget(label((outputs.get(k) or {}).get("description") or k, cls="out-name", tip=k))
        bl.addWidget(Value(v, state, k))
        lay.addWidget(box)
    return f
