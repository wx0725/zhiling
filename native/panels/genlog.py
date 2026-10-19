"""生成过程页（移植自 web/src/panels.jsx 的 GenerationLog、Round、Step、Issues、doneText）：每一轮的起草、结构校验、实跑检查。
问题能在画布上找到对应节点时给出“在图上看”。事件一多就整页重画（很便宜），展开过的“用到的示例输入”记在 open_samples 里。"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QWidget

from ..logic._js import js_str, truthy
from ..logic.flow import is_refusal, issue_text
from ..theme import C
from .common import Details, esc, Led, TextBox, button, clear, hbox, label, pretty, vbox

STEP_STATE = {"passed": "completed", "failed": "failed", "inconclusive": "skipped"}


def done_text(e, drawn_round):
    if e.get("ok"):
        return "" if is_refusal(e.get("workflow")) else f"用了 {js_str(e.get('rounds'))} 轮，已显示在画布上，可以运行或保存。"  # 拒绝的原因已在上面显示
    if e.get("stopped"):
        return "已停止生成。"
    shown = f"画布上是第 {js_str(drawn_round)} 轮那一版，可以运行看看，但不能保存。" if truthy(drawn_round) else "画布上没有可用的版本。"
    where = "最后一版没有通过实跑检查，" if e.get("valid") else "最后一版没有通过结构校验，"
    if e.get("inconclusive"):
        return f"运行检查没有得出结论（{e.get('reason') or '原因不明'}）。{shown}"
    return f"{js_str(e.get('rounds'))} 轮之后仍有问题：{where}{shown}"


class GenerationLog(QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.lay = vbox(self)
        self.open_samples = set()
        self._key = None

    def sync(self):
        c = self.ctrl
        key = (id(c.gen_events), len(c.gen_events), c.generating, (c.drawn or {}).get("round"), id(c.nodes))
        if key == self._key:
            return False
        self._key = key
        clear(self.lay)
        self._build()
        return True

    def _build(self):
        c = self.ctrl
        events = c.gen_events
        add = self.lay.addWidget
        if not events:
            add(label("在上面输入一句话需求，点“生成工作流”，这里会显示每一轮的起草、校验和运行检查。", cls="muted"))
            return
        start = next((e for e in events if e.get("type") == "start"), None)
        rounds, tail = {}, []
        for e in events:
            if truthy(e.get("round")):
                rounds.setdefault(e["round"], []).append(e)
            elif e.get("type") != "start":
                tail.append(e)
        done = next((e for e in tail if e.get("type") == "done"), None)
        errors = [e for e in tail if e.get("type") == "error"]
        drawn_round = (c.drawn or {}).get("round")

        def banner(text, tone):
            add(label(text, cls="banner", tone=tone, select=True))
            self.lay.addSpacing(12)
        if done:
            banner(done_text(done, drawn_round) or "生成结束。", "ok" if done.get("ok") else "bad")
        for e in errors:
            banner(js_str(e.get("message")), "bad")
        if not done and not errors and c.generating:
            banner("正在生成…", "busy")
        if start:
            m = start.get("models") or {}
            add(label(f"起草：{js_str(m.get('generator_label') or m.get('generator'))}　审查：{js_str(m.get('judge_label') or m.get('judge'))}", cls="models"))
        for e in tail:
            if e.get("type") == "feasibility":
                self.lay.addSpacing(4)
                add(label("现有模块能做到这件事。", cls="feasible") if e.get("ok") else label(f"做不到：{js_str(e.get('reason'))}", cls="error-text"))
        nums = sorted(rounds)
        if nums:
            self.lay.addSpacing(12)
        for i, r in enumerate(nums):
            if i:
                self.lay.addSpacing(8)
            add(self._round(r, rounds[r], i == len(nums) - 1))

    # 一轮：起草 → 结构校验 → 实跑检查；没通过的交回模型重写
    def _round(self, n, events, last):
        find = lambda t: next((e for e in events if e.get("type") == t), None)  # noqa: E731
        validated, checking, checked = find("validated"), find("checking"), find("checked")
        errs = (validated or {}).get("errors") or []
        problems = (checked or {}).get("problems") or []
        rejected = len(errs) > 0 or (checked or {}).get("status") == "failed"
        box = QFrame()
        box.setProperty("cls", "box")
        lay = vbox(box, spacing=8, margins=(12, 12, 12, 12))
        lay.addWidget(label(f"第 {js_str(n)} 轮", cls="round-n"))
        lay.addWidget(Step("completed" if validated else "running", "起草",
                           f"{js_str(validated.get('seconds'))} 秒" if validated else "模型正在写图…"))
        if validated:
            s = Step("failed" if errs else "completed", "结构校验", f"发现 {len(errs)} 个问题" if errs else "通过")
            s.add(Issues(errs))
            lay.addWidget(s)
        if checking:
            st = checked.get("status") if checked else None
            if not checked:
                note = "用示例输入实际运行，再由审查模型检查…"
            elif st == "passed":
                secs = checked.get("seconds")
                note = f"用 {len(checked.get('samples') or [])} 组示例输入运行，没有发现问题{f'（{js_str(secs)} 秒）' if truthy(secs) else ''}"
            elif st == "failed":
                note = f"发现 {len(problems)} 个问题"
            else:
                note = f"没有得出结论：{checked.get('reason') or '原因不明'}"
            s = Step("running" if not checked else STEP_STATE.get(st, "skipped"), "实跑检查", note)
            if st == "failed":
                s.add(Issues(problems, lambda x: self.ctrl.targets_for(x, n), self.ctrl.focus_node))
            samples = (checked or {}).get("samples") or []
            if samples:
                d = Details("用到的示例输入", open=n in self.open_samples, pad=(0, 4, 0, 0),
                            on_toggle=lambda on: self.open_samples.add(n) if on else self.open_samples.discard(n))
                d.body_lay.setSpacing(4)
                for x in samples:
                    d.body_lay.addWidget(TextBox(pretty(x), mono=True))
                d.setContentsMargins(0, 6, 0, 0)
                s.add(d)
            lay.addWidget(s)
        if rejected and not last:
            lay.addWidget(label("把这些问题和改法交回模型，重写一版 ↓", cls="round-next"))
        return box


class Step(QWidget):
    def __init__(self, state, title, note):
        super().__init__()
        row = hbox(self, spacing=10)
        self.state = state
        row.addWidget(Led(state, size=10, top=6), 0, Qt.AlignmentFlag.AlignTop)
        body = QWidget()
        self.body = vbox(body)
        head = label(f'<span style="color:{C["ink"]}; font-weight:600; font-size:14px;">{esc(title)}</span>&nbsp;&nbsp;{esc(note)}', cls="step-note", tone=state if state in ("failed", "skipped") else None, rich=True)
        head.setObjectName("stepHead")
        self.body.addWidget(head)
        row.addWidget(body, 1)

    def add(self, w):
        if w is not None:
            self.body.addWidget(w)


def Issues(items, targets=None, on_focus=None):
    """问题列表：能在图上找到的，给出“在图上看”"""
    if not items:
        return None
    w = QWidget()
    lay = vbox(w, spacing=4, margins=(0, 6, 0, 0))
    for x in items:
        row = hbox(spacing=4)
        row.addWidget(label("•", cls="issue", wrap=False), 0, Qt.AlignmentFlag.AlignTop)
        col = vbox()
        col.addWidget(label(issue_text(x), cls="issue", tip=js_str(x), select=True))
        for nid in (targets(x) if on_focus and targets else []):
            b = button(f"在图上看 {nid.split('/')[-1]}", cls="link", on=lambda nid=nid: on_focus(nid))
            col.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)
        row.addLayout(col, 1)
        lay.addLayout(row)
    return w
