"""开始页（网页版 start.jsx）：没打开工作流时显示。两条路——理清需求再生成（重点）、从空白开始；示例只列在左侧栏（不在这里重复）。
“帮我理清需求”把想法交给 /api/refine，它回一张标准需求单（显示为需求卡片和“还缺什么”）、针对缺项的问题和做不到的部分；
每个问题有几个选项，最后一个“其他”可以自己写。回答问题或改卡片后再整理，问到没有缺项为止，任何时候都可以按当前需求生成。换了项目（包括回到首页）时这一页的状态全部重来（网页版 key={project || "home"}）。"""
import copy
import json

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QScrollArea, QSizePolicy, QToolButton, QVBoxLayout, QWidget)

from ..logic.flow import CARD_PARTS, card_to_requirement, merge_answers

OTHER = "其他"
CHECK_MARK = {"ok": "✓", "default": "按常规", "missing": "缺"}
from ..theme import C, FS, R
from .widgets import FlowLayout, LayoutToggles, set_prop

QSS = f"""
QWidget#startBody {{ background: {C['canvas']}; }}
QFrame[card="true"] {{ background: {C['surface']}; border: 1px solid {C['line']}; border-radius: {R['xl']}px; }}
QFrame[card="warn"] {{ background: {C['warn_bg']}; border: 1px solid rgba(240,169,74,0.4); border-radius: {R['xl']}px; }}
QLabel#h2 {{ font-size: 22px; font-weight: 650; background: transparent; }}
QLabel#h3 {{ font-size: 15px; font-weight: 650; background: transparent; }}
QLabel#h3warn {{ font-size: 15px; font-weight: 650; color: {C['warn_ink']}; background: transparent; }}
QLabel#h4 {{ font-size: {FS['md']}px; font-weight: 600; color: {C['ink_2']}; background: transparent; }}
QLabel#legend {{ font-weight: 600; background: transparent; }}
QLabel[role="muted"] {{ background: transparent; }}
QLabel#working {{ color: {C['warn_ink']}; background: transparent; }}
QLabel#errorText {{ color: {C['bad_ink']}; background: transparent; }}
QLabel#finalText {{ background: {C['rail']}; border-radius: {R['lg']}px; padding: 12px 14px; }}
QLabel#bullet {{ background: transparent; }}
QLabel#why {{ font-size: {FS['sm']}px; color: {C['muted']}; background: transparent; }}
QLabel[slot="ok"] {{ padding: 3px 10px; border-radius: 11px; background: {C['rail']}; color: {C['ink_2']}; }}
QLabel[slot="default"] {{ padding: 3px 10px; border-radius: 11px; background: {C['rail']}; color: {C['muted']}; }}
QLabel[slot="missing"] {{ padding: 3px 10px; border-radius: 11px; background: {C['warn_bg']}; color: {C['warn_ink']}; }}
QPlainTextEdit#idea {{ padding: 12px 14px; font-size: {FS['lg']}px; border-radius: {R['lg']}px; }}
QPlainTextEdit#cardItem {{ padding: 4px 8px; border: 1px solid {C['line']}; border-radius: {R['md']}px; }}
QPlainTextEdit#cardItem:focus {{ border-color: {C['focus']}; }}
QPushButton#chip {{ padding: 6px 14px; border: 1.5px solid {C['line']}; border-radius: 15px; background: {C['surface']}; }}
QPushButton#chip:hover {{ border-color: {C['line_strong']}; }}
QPushButton#chip[on="true"] {{ border-color: {C['ink']}; background: {C['ink']}; color: {C['on_ink']}; }}
QLineEdit#chipInput {{ padding: 6px 12px; border: 1.5px dashed {C['line']}; border-radius: 15px; background: {C['rail']}; }}
QLineEdit#chipInput:focus {{ border: 1.5px solid {C['focus']}; background: {C['surface']}; }}
QPushButton#link {{ border: 0; background: transparent; padding: 2px 0; color: {C['focus']}; font-size: {FS['sm']}px; text-align: left; }}
QPushButton#link:hover {{ text-decoration: underline; }}
QToolButton#del {{ font-size: 16px; color: {C['muted']}; border: 0; border-radius: {R['sm']}px; }}
QToolButton#del:hover {{ background: {C['hover']}; color: {C['ink']}; }}
QPushButton:disabled {{ color: {C['muted']}; }}
"""


def _label(text, name=None, wrap=True, role=None):
    w = QLabel(text)
    if name:
        w.setObjectName(name)
    if role:
        w.setProperty("role", role)
    w.setWordWrap(wrap)
    w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return w


def _btn(text, kind=None):
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def _card(kind="true"):
    f = QFrame()
    f.setProperty("card", kind)
    col = QVBoxLayout(f)
    col.setContentsMargins(20, 20, 20, 20)
    col.setSpacing(0)
    return f, col


class AutoText(QPlainTextEdit):
    """随内容增高的文本框（网页版 field-sizing: content）"""

    def __init__(self, text="", min_h=34):
        super().__init__(text)
        self._min = min_h
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.textChanged.connect(self._fit)
        self._fit()

    def _fit(self):
        doc = self.document()
        doc.setTextWidth(self.viewport().width())
        lines = 0
        b = doc.begin()
        while b.isValid():
            lines += max(1, b.layout().lineCount())
            b = b.next()
        h = int(lines * self.fontMetrics().lineSpacing() * 1.08) + 18
        self.setFixedHeight(max(self._min, h))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        QTimer.singleShot(0, self._fit)


class Question(QWidget):
    """一个要确定的问题（像 Codex、Claude Code 那样）：几个选项（再点一下取消），最后一个“其他”点了可以自己写"""

    def __init__(self, q, value, on_change):
        super().__init__()
        self.q = q
        self.options = [o for o in (q.get("options") or []) if isinstance(o, str) and o != OTHER]
        self.on_change = on_change
        self.other = False  # 选了“其他”
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 16)
        col.setSpacing(6)
        col.addWidget(_label(q.get("ask") or "", "legend"))
        if isinstance(q.get("why"), str) and q["why"]:
            col.addWidget(_label(q["why"], "why"))
        chips = QWidget()
        self.flow = FlowLayout(chips, spacing=8)
        self.chips = []
        for o in self.options:
            b = QPushButton(o, objectName="chip")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.clicked.connect(lambda _=False, o=o: self._pick(o))
            self.flow.addWidget(b)
            self.chips.append((o, b))
        self.other_btn = QPushButton(OTHER, objectName="chip")
        self.other_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.other_btn.clicked.connect(self._pick_other)
        self.flow.addWidget(self.other_btn)
        col.addWidget(chips)
        self.input = QLineEdit(objectName="chipInput")
        self.input.setPlaceholderText("写下你的回答")
        self.input.setAccessibleName(f"{q.get('ask') or ''}（其他）")
        self.input.textEdited.connect(self._typed)
        col.addWidget(self.input)
        self.value = ""
        self.show_value(value)

    def _pick(self, o):
        self.other = False
        self._set("" if self.value == o else o)

    def _pick_other(self):
        self.other = not self.other
        self._set(self.input.text().strip() if self.other else "")
        if self.other:
            QTimer.singleShot(0, self.input.setFocus)

    def _typed(self, v):
        self.other = True
        self._set(v)

    def _set(self, v):
        self.show_value(v)
        self.on_change(v)

    def show_value(self, v):
        self.value = v or ""
        self.other = self.other or (bool(self.value) and self.value not in self.options)
        for o, b in self.chips:
            on = not self.other and self.value == o
            set_prop(b, "on", on)
            b.setAccessibleDescription("已选" if on else "")
        set_prop(self.other_btn, "on", self.other)
        self.input.setVisible(self.other)
        want = self.value if self.other else self.input.text()
        if self.input.text() != want:
            self.input.setText(want)


class CardPart(QWidget):
    """需求卡片的一栏：每条可改、可删，下面“加一条”"""

    def __init__(self, label, items, on_change):
        super().__init__()
        self.label = label
        self.items = list(items)
        self.on_change = on_change
        col = QVBoxLayout(self)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(6)
        col.addWidget(_label(label, "h4"))
        self.list = QVBoxLayout()
        self.list.setSpacing(4)
        col.addLayout(self.list)
        add = QPushButton("加一条", objectName="link")
        add.setCursor(Qt.CursorShape.PointingHandCursor)
        add.clicked.connect(self._add)
        row = QHBoxLayout()
        row.addWidget(add)
        row.addStretch(1)
        col.addLayout(row)
        self._render()

    def _render(self, focus=None):
        while self.list.count():
            it = self.list.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for i, x in enumerate(self.items):
            li = QWidget()
            row = QHBoxLayout(li)
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(4)
            t = AutoText(x)
            t.setObjectName("cardItem")
            t.setAccessibleName(f"{self.label} 第 {i + 1} 条")
            t.textChanged.connect(lambda i=i, t=t: self._edit(i, t.toPlainText()))
            row.addWidget(t, 1)
            d = QToolButton(objectName="del")
            d.setText("×")
            d.setFixedSize(26, 26)
            d.setToolTip("删掉这一条")
            d.setAccessibleName(f"删掉：{x}")
            d.setCursor(Qt.CursorShape.PointingHandCursor)
            d.clicked.connect(lambda _=False, i=i: self._remove(i))
            row.addWidget(d, 0, Qt.AlignmentFlag.AlignTop)
            self.list.addWidget(li)
            if focus == i:
                QTimer.singleShot(0, t.setFocus)

    def _edit(self, i, text):
        self.items = [text if j == i else y for j, y in enumerate(self.items)]
        self.on_change(self.items)

    def _remove(self, i):
        self.items = [y for j, y in enumerate(self.items) if j != i]
        self.on_change(self.items)
        self._render()

    def _add(self):
        self.items = [*self.items, ""]
        self.on_change(self.items)
        self._render(focus=len(self.items) - 1)


class StartPage(QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.setStyleSheet(QSS)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setObjectName("startBody")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        holder = QWidget(objectName="startBody")
        self.scroll.setWidget(holder)
        hl = QHBoxLayout(holder)
        hl.setContentsMargins(0, 0, 0, 0)
        self.page = QWidget()
        self.page.setMaximumWidth(760)
        hl.addStretch(1)
        hl.addWidget(self.page, 100)
        hl.addStretch(1)
        self.col = QVBoxLayout(self.page)
        self.col.setContentsMargins(24, 40, 24, 64)
        self.col.setSpacing(20)

        # 状态（与网页版相同）
        self.result = None    # {spec, card, questions, checklist, unsupported, requirement, complete}
        self.card = None      # 使用者可改的卡片
        self.answers = []     # 已经回答过的 [{ask, answer}]
        self.chosen = {}      # 本轮：问题序号 → 答案
        self.busy = False
        self.error = ""
        self._token = 0       # 只认最新一次整理：停止、重新开始、换项目后晚到的结果丢掉
        self._project = ctrl.project
        self._syncing = False

        self._build_ask()
        self.refine = QWidget()
        self.refine_col = QVBoxLayout(self.refine)
        self.refine_col.setContentsMargins(0, 0, 0, 0)
        self.refine_col.setSpacing(12)
        self.col.addWidget(self.refine)
        self._build_other()
        self.col.addStretch(1)

        # 左侧列表收起时，右上角放布局开关（点了都是“显示”）
        self.toggles = LayoutToggles(ctrl, on_side=lambda: ctrl.set_side_open(True),
                                     on_panel=lambda: ctrl.set_panel_open(True), parent=self)
        ctrl.layout_changed.connect(self._place_toggles)
        ctrl.requirement_changed.connect(self._sync_idea)
        ctrl.gen_changed.connect(self._sync)
        ctrl.projects_changed.connect(self._project_check)
        self._render_refine()
        self._sync()
        self._place_toggles()

    # ---------- 构建 ----------
    def _build_ask(self):
        box, col = _card()
        col.addWidget(_label("你想让它做什么？", "h2"))
        col.addSpacing(6)
        col.addWidget(_label("说说你会给它什么、想得到什么。说不清也没关系，点“帮我理清需求”，它会问你几个关键问题。", role="muted"))
        col.addSpacing(12)
        self.idea = QPlainTextEdit(objectName="idea")
        self.idea.setAccessibleName("想法")
        self.idea.setPlaceholderText("例如：帮我看看这几个网页都讲了什么")
        self.idea.setPlainText(self.ctrl.requirement)
        self.idea.setFixedHeight(3 * 25 + 30)
        self.idea.textChanged.connect(self._idea_edited)
        col.addWidget(self.idea)
        acts = QHBoxLayout()
        acts.setContentsMargins(0, 12, 0, 0)
        acts.setSpacing(8)
        self.ask_btn = _btn("帮我理清需求", "primary")
        self.ask_btn.clicked.connect(lambda: self.ask([]))
        self.stop_btn = _btn("停止整理", "ghost")
        self.stop_btn.clicked.connect(self.stop)
        self.direct_btn = _btn("直接生成")
        self.direct_btn.clicked.connect(lambda: self.ctrl.generate(self.ctrl.requirement))
        self.working = _label("正在整理需求，通常要十几秒…", "working", wrap=False)
        for w in (self.ask_btn, self.stop_btn, self.direct_btn, self.working):
            acts.addWidget(w)
        acts.addStretch(1)
        col.addLayout(acts)
        self.error_label = _label("", "errorText")
        self.error_label.setContentsMargins(0, 12, 0, 0)
        col.addWidget(self.error_label)
        self.col.addWidget(box)

    def _build_other(self):
        self.other = QWidget()
        self.other_grid = QGridLayout(self.other)
        self.other_grid.setContentsMargins(0, 0, 0, 0)
        self.other_grid.setSpacing(12)
        self.blank_box, bl = _card()
        bl.addWidget(_label("从空白开始", "h3"))
        bl.addSpacing(8)
        bl.addWidget(_label("自己在画布上搭：画布右上角会有一份清单，一步步告诉你接下来做什么。"
                            "想先看看现成的工作流怎么搭，点左侧“示例”里的任意一个。", role="muted"))
        bl.addSpacing(12)
        b = _btn("新建空白工作流")
        b.clicked.connect(self.ctrl.new_blank)
        row = QHBoxLayout()
        row.addWidget(b)
        row.addStretch(1)
        bl.addLayout(row)
        bl.addStretch(1)
        self._wide = None
        self._arrange()
        self.col.addWidget(self.other)

    def _arrange(self):
        """宽屏时需求卡片两列，窄于 1100 时一列（网页版的 @media (max-width: 1100px)）"""
        win = self.window()
        wide = (win.width() if win is not self else self.width()) > 1100
        if wide == self._wide:
            return
        self._wide = wide
        if self.other_grid.indexOf(self.blank_box) < 0:
            self.other_grid.addWidget(self.blank_box, 0, 0)
        m = (24, 40, 24, 64) if wide else (16, 20, 16, 40)
        self.col.setContentsMargins(*m)
        if self.result:
            self._render_refine()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._arrange()
        self._place_toggles()

    def _place_toggles(self):
        self.toggles.setVisible(not self.ctrl.side_open)
        self.toggles.adjustSize()
        self.toggles.move(self.width() - 20 - self.toggles.width(), 12)
        self.toggles.raise_()

    # ---------- 想法输入框与需求同步 ----------
    def _idea_edited(self):
        if self._syncing:
            return
        self.ctrl.set_requirement(self.idea.toPlainText())

    def _sync_idea(self):
        if self.idea.toPlainText() != self.ctrl.requirement:
            self._syncing = True
            self.idea.setPlainText(self.ctrl.requirement)
            self._syncing = False
        self._sync()

    def _project_check(self):
        if self.ctrl.project != self._project:
            self._project = self.ctrl.project
            self.restart()

    # ---------- 理清需求 ----------
    def card_edited(self):
        return bool(self.result) and json.dumps(self.card, ensure_ascii=False) != json.dumps(self.result.get("card"), ensure_ascii=False)

    def final_text(self):
        if not self.result:
            return self.ctrl.requirement
        return card_to_requirement(self.card) if self.card_edited() else (self.result.get("requirement") or "")

    def ask(self, with_answers):
        idea = self.ctrl.requirement
        if not idea.strip() or self.busy:
            return
        self._token += 1
        token = self._token
        self.busy = True
        self.error = ""
        # 只在使用者改过卡片时把卡片交回去；否则模型会把上一轮自己整理的卡片当成定论，忽略新的回答
        # 上一轮的需求单原样送回：只点了选项时由代码直接改单子，不必再调用模型；改过卡片时之前的决定也照样保留。
        # 想法改过时后端不再用它（单子里记着对应的想法）
        body = {"idea": idea, "answers": with_answers, "card": self.card if self.card_edited() else None,
                "spec": self.result.get("spec") if self.result else None, "model": self.ctrl.generation_model()}

        def ok(r):
            if token != self._token:
                return
            r = r if isinstance(r, dict) else {}
            self.result = {"card": r.get("card") if isinstance(r.get("card"), dict) else {},
                           "questions": [q for q in (r.get("questions") or []) if isinstance(q, dict)],
                           "unsupported": [x for x in (r.get("unsupported") or []) if isinstance(x, str)],
                           "requirement": r.get("requirement") if isinstance(r.get("requirement"), str) else "",
                           "spec": r.get("spec") if isinstance(r.get("spec"), dict) else None,
                           "checklist": [c for c in (r.get("checklist") or []) if isinstance(c, dict)],
                           "complete": r.get("complete") is True}
            self.card = copy.deepcopy(self.result["card"])
            self.answers = with_answers
            self.chosen = {}
            self.busy = False
            self._render_refine()
            self._sync()

        def bad(e):
            if token != self._token:
                return
            self.error = f"没能整理：{e}"
            self.busy = False
            self._sync()
        self.ctrl.api.post("/api/refine", body, ok=ok, err=bad)
        self._sync()

    def stop(self):
        self._token += 1  # 晚到的结果不再理会
        self.busy = False
        self._sync()

    def restart(self):
        self.stop()
        self.result = None
        self.card = None
        self.answers = []
        self.chosen = {}
        self.error = ""
        self._render_refine()
        self._sync()

    def _update_answers(self):
        self.ask(merge_answers(self.answers, self.result["questions"], self.chosen))

    def _render_refine(self):
        col = self.refine_col
        while col.count():
            it = col.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self.update_btn = self.gen_btn = self.card_title = self.final_label = None
        r = self.result
        self.refine.setVisible(bool(r))
        if not r:
            return
        if r["checklist"]:
            box, c = _card()
            c.addWidget(_label("需求单已完整，可以生成" if r["complete"] else "需求单还缺什么", "h3"))
            c.addSpacing(10)
            chips = QWidget()
            flow = FlowLayout(chips, spacing=8)
            for item in r["checklist"]:
                st = item.get("state") if item.get("state") in CHECK_MARK else "missing"
                w = QLabel(f"{item.get('label') or ''}  {CHECK_MARK[st]}")
                w.setProperty("slot", st)
                w.setToolTip({"ok": "已经清楚", "default": "问过了，按你的回答或常规处理", "missing": "还没说清"}[st])
                flow.addWidget(w)
            c.addWidget(chips)
            col.addWidget(box)

        if r["questions"]:
            box, c = _card()
            c.addWidget(_label("还需要你确定", "h3"))
            c.addSpacing(8)
            for i, q in enumerate(r["questions"]):
                c.addWidget(Question(q, self.chosen.get(i, ""), lambda v, i=i: self._choose(i, v)))
            self.update_btn = _btn("根据回答更新")
            self.update_btn.clicked.connect(self._update_answers)
            row = QHBoxLayout()
            row.addWidget(self.update_btn)
            row.addStretch(1)
            c.addLayout(row)
            col.addWidget(box)

        box, c = _card()
        self.card_title = _label("", "h3")
        c.addWidget(self.card_title)
        c.addSpacing(16)
        grid = QGridLayout()
        grid.setHorizontalSpacing(20)
        grid.setVerticalSpacing(16)
        cols = 2 if self._wide else 1
        for n, (k, label) in enumerate(CARD_PARTS):
            items = self.card.get(k) if isinstance(self.card.get(k), list) else []
            part = CardPart(label, [x if isinstance(x, str) else str(x) for x in items], lambda v, k=k: self._card_change(k, v))
            grid.addWidget(part, n // cols, n % cols, Qt.AlignmentFlag.AlignTop)
        for i in range(cols):
            grid.setColumnStretch(i, 1)
        c.addLayout(grid)
        col.addWidget(box)

        if r["unsupported"]:
            box, c = _card("warn")
            c.addWidget(_label("现有模块做不到", "h3warn"))
            c.addSpacing(8)
            for x in r["unsupported"]:
                c.addWidget(_label(f"•  {x}", "bullet"))
            c.addSpacing(8)
            c.addWidget(_label("这些部分不会出现在生成的工作流里。", role="muted"))
            col.addWidget(box)

        box, c = _card()
        c.addWidget(_label("将按这段需求生成", "h3"))
        c.addSpacing(8)
        self.final_label = _label("", "finalText")
        c.addWidget(self.final_label)
        acts = QHBoxLayout()
        acts.setContentsMargins(0, 12, 0, 0)
        acts.setSpacing(8)
        self.gen_btn = _btn("按这个需求生成", "primary")
        self.gen_btn.clicked.connect(self._generate)
        again = _btn("重新开始", "ghost")
        again.clicked.connect(self.restart)
        acts.addWidget(self.gen_btn)
        acts.addWidget(again)
        acts.addStretch(1)
        c.addLayout(acts)
        col.addWidget(box)
        self._sync_card()

    def _generate(self):
        """卡片没改过时把需求单一起交给生成：代码据此直接判断能不能做、检查生成的输入输出"""
        spec = self.result.get("spec") if self.result and not self.card_edited() else None
        self.ctrl.generate(self.final_text(), spec=spec)

    def _choose(self, i, v):
        self.chosen = {**self.chosen, i: v}
        self._sync()

    def _card_change(self, k, items):
        self.card = {**self.card, k: items}
        self._sync_card()

    def _sync_card(self):
        if self.card_title is not None:
            self.card_title.setText("需求卡片（改过）" if self.card_edited() else "需求卡片")
        if self.final_label is not None:
            self.final_label.setText(self.final_text())
        self._sync()

    def _sync(self):
        idea = self.ctrl.requirement.strip()
        gen = self.ctrl.generating
        busy = self.busy
        self.idea.setEnabled(not busy)
        self.ask_btn.setVisible(not busy)
        self.ask_btn.setText("重新整理" if self.result else "帮我理清需求")
        self.ask_btn.setEnabled(bool(idea) and not gen)
        self.stop_btn.setVisible(busy)
        self.direct_btn.setEnabled(bool(idea) and not busy and not gen)
        self.working.setVisible(busy)
        self.error_label.setText(self.error)
        self.error_label.setVisible(bool(self.error))
        if self.update_btn is not None:
            self.update_btn.setEnabled(not busy and any(str(v).strip() for v in self.chosen.values()))
        if self.gen_btn is not None:
            self.gen_btn.setEnabled(not busy and not gen and bool(self.final_text().strip()))

    def sizeHint(self):
        return QSize(700, 600)
