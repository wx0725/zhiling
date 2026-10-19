"""右侧面板各页共用的小部件和样式：状态灯、类型圆点、可展开的一段（对应 <details>）、长文本值、输入框的“离开时提交”。
样式对应 web/src/styles.css 里 .panel、.detail、.field、.value 等；控件用动态属性 cls 挑样式。"""
import json
import re

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QTextOption
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit, QPushButton,
                               QSizePolicy, QTextEdit, QVBoxLayout, QWidget)

from ..logic._js import UNSET, _norm, js_str
from ..theme import FS, MONO, R, C, mono_font, qc, type_color, ui_font

LONG = 400  # 超过这么多字先收起
MONO_CSS = ", ".join(f'"{f}"' for f in MONO)
MONO_HTML = ", ".join(f"'{f}'" for f in MONO)  # 写在 style="..." 里，用单引号


def pretty(v):
    """JSON.stringify(v, null, 2)：整数值的小数写成整数，中文原样"""
    return json.dumps(_norm(v), ensure_ascii=False, indent=2)


def js_len(text):
    """JS 的字符串长度（按 UTF-16 计）：显示“展开全文（N 字）”时与网页版一致"""
    return len(text.encode("utf-16-le")) // 2


def esc(t):
    return js_str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def code_html(t, size=FS["sm"]):
    return f'<span style="font-family:{MONO_HTML}; font-size:{size}px;">{esc(t)}</span>'


def qss():
    c = C
    return f"""
QWidget#rightPanel {{ background: {c['surface']}; border-left: 1px solid {c['line']}; }}
QFrame[cls="tabs"] {{ background: {c['rail']}; border: 1px solid {c['line']}; border-radius: 10px; }}
QPushButton[cls="tab"] {{ background: transparent; border: 0; border-radius: 7px; padding: 6px 8px; color: {c['muted']}; font-size: {FS['md']}px; }}
QPushButton[cls="tab"]:hover {{ color: {c['ink']}; }}
QPushButton[cls="tab"][on="true"] {{ background: {c['surface']}; color: {c['ink']}; font-weight: 600; }}

QLabel {{ background: transparent; }}
QLabel[cls="muted"] {{ color: {c['muted']}; }}
QLabel[cls="hint"] {{ color: {c['muted']}; font-size: {FS['sm']}px; }}
QLabel[cls="h3"] {{ font-size: {FS['lg']}px; font-weight: 600; }}
QLabel[cls="h3r"] {{ font-size: 15px; font-weight: 600; }}
QLabel[cls="h3r"][tone="ok"] {{ color: {c['led_ok']}; }}
QLabel[cls="h3r"][tone="bad"] {{ color: {c['led_bad']}; }}
QLabel[cls="h3r"][tone="soft"] {{ color: {c['ink_2']}; }}
QLabel[cls="h4"] {{ font-size: {FS['md']}px; font-weight: 700; }}
QLabel[cls="h5"] {{ font-size: {FS['sm']}px; font-weight: 600; color: {c['muted']}; }}
QLabel[cls="desc"] {{ color: {c['ink_2']}; }}
QLabel[cls="sub"] {{ color: {c['muted']}; font-size: {FS['sm']}px; }}
QLabel[cls="chip"] {{ font-family: {MONO_CSS}; font-size: {FS['xs']}px; background: {c['rail']}; color: {c['ink_2']}; padding: 1px 6px; border-radius: {R['xs']}px; }}
QLabel[cls="state"] {{ background: {c['rail']}; border-radius: {R['md']}px; padding: 6px 10px; font-size: {FS['md']}px; color: {c['ink_2']}; }}
QLabel[cls="state"][tone="completed"] {{ color: {c['led_ok']}; }}
QLabel[cls="state"][tone="failed"] {{ color: {c['bad_ink']}; }}
QLabel[cls="flabel"] {{ font-weight: 600; }}
QLabel[cls="type"] {{ color: {c['muted']}; font-size: {FS['sm']}px; }}
QLabel[cls="field-error"] {{ color: {c['bad_ink']}; font-size: {FS['md']}px; }}
QLabel[cls="error-text"] {{ color: {c['bad_ink']}; }}
QLabel[cls="out-name"] {{ color: {c['muted']}; font-size: {FS['sm']}px; }}
QLabel[cls="param-desc"] {{ color: {c['muted']}; font-size: {FS['sm']}px; }}
QLabel[cls="wired"] {{ background: {c['rail']}; border-radius: {R['md']}px; padding: 6px 10px; font-size: {FS['md']}px; color: {c['ink_2']}; }}
QLabel[cls="banner"] {{ border-radius: {R['md']}px; padding: 8px 12px; font-weight: 600; }}
QLabel[cls="banner"][tone="ok"] {{ background: rgba(63,181,122,0.14); color: #7fd6a8; }}
QLabel[cls="banner"][tone="bad"] {{ background: rgba(228,92,92,0.14); color: {c['bad_ink']}; }}
QLabel[cls="banner"][tone="busy"] {{ background: {c['warn_bg']}; color: {c['warn_ink']}; }}
QLabel[cls="models"] {{ color: {c['muted']}; font-size: {FS['sm']}px; }}
QLabel[cls="feasible"] {{ color: {c['ink_2']}; }}
QLabel[cls="round-n"] {{ font-weight: 700; }}
QLabel[cls="step-title"] {{ font-weight: 600; }}
QLabel[cls="step-note"] {{ color: {c['ink_2']}; font-size: {FS['md']}px; }}
QLabel[cls="step-note"][tone="failed"] {{ color: {c['bad_ink']}; }}
QLabel[cls="step-note"][tone="skipped"] {{ color: {c['warn_ink']}; }}
QLabel[cls="round-next"] {{ color: {c['muted']}; font-size: {FS['sm']}px; }}
QLabel[cls="issue"] {{ color: {c['issue_ink']}; font-size: {FS['md']}px; }}
QLabel[cls="summary"] {{ color: {c['muted']}; font-size: {FS['sm']}px; }}
QLabel[cls="rec-summary"] {{ font-weight: 600; font-size: {FS['md']}px; }}
QLabel[cls="rec-summary"][tone="skipped"] {{ color: {c['muted']}; font-weight: 400; }}
QLabel[cls="check-row"] {{ font-size: {FS['md']}px; }}

QFrame[cls="sep"] {{ background: transparent; border: 0; border-top: 1px solid {c['line']}; }}
QFrame[cls="box"] {{ background: transparent; border: 1px solid {c['line']}; border-radius: {R['md']}px; }}

QPushButton[cls="link"] {{ background: transparent; border: 0; padding: 2px 0; color: {c['focus']}; font-size: {FS['sm']}px; text-align: left; }}
QPushButton[cls="link"]:hover {{ color: #8fb8f5; }}

QTextEdit[cls="value"] {{ background: {c['rail']}; border: 0; border-radius: {R['md']}px; padding: 0; font-size: {FS['md']}px; }}
QTextEdit[cls="value"]:focus {{ background: {c['rail']}; }}
QTextEdit[cls="value"][mono="true"] {{ font-family: {MONO_CSS}; font-size: {FS['sm']}px; }}
QPlainTextEdit[cls="code"] {{ background: {c['rail']}; border: 0; border-radius: {R['md']}px; padding: 8px; font-family: {MONO_CSS}; font-size: {FS['sm']}px; }}
QPlainTextEdit[cls="code"]:focus {{ background: {c['rail']}; }}
QPlainTextEdit[cls="area"] {{ font-family: {MONO_CSS}; font-size: 12.5px; }}
"""


def label(text="", cls=None, tone=None, wrap=True, rich=False, tip=None, select=False):
    w = QLabel()
    w.setTextFormat(Qt.TextFormat.RichText if rich else Qt.TextFormat.PlainText)
    w.setText(text)
    w.setWordWrap(wrap)
    if cls:
        w.setProperty("cls", cls)
    if tone:
        w.setProperty("tone", tone)
    if tip:
        w.setToolTip(tip)
    if select:
        w.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return w


def set_tone(w, tone):
    if w.property("tone") != tone:
        w.setProperty("tone", tone)
        w.style().unpolish(w)
        w.style().polish(w)


def button(text, kind=None, small=False, wide=False, cls=None, on=None):
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if small:
        b.setProperty("small", True)
    if cls:
        b.setProperty("cls", cls)
    if wide:
        b.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    else:
        b.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if on:
        b.clicked.connect(lambda *_: on())
    return b


def vbox(w=None, spacing=0, margins=(0, 0, 0, 0)):
    lay = QVBoxLayout(w) if w is not None else QVBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


def hbox(w=None, spacing=0, margins=(0, 0, 0, 0)):
    lay = QHBoxLayout(w) if w is not None else QHBoxLayout()
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    return lay


def clear(lay):
    """清空一个布局（连同里面的部件）"""
    while lay.count():
        it = lay.takeAt(0)
        w = it.widget()
        if w is not None:
            w.hide()
            w.setParent(None)
            w.deleteLater()
        elif it.layout() is not None:
            clear(it.layout())


def sep_frame(top=18, pad=14, spacing=0):
    """上方一条分隔线的一块（.preview、.result、.detail-section）：top 线上方的间距，pad 线下方的间距"""
    f = QWidget()
    outer = vbox(f, margins=(0, top, 0, 0))
    line = QFrame()
    line.setFixedHeight(1)
    line.setStyleSheet(f"background: {C['line']}; border: 0;")
    outer.addWidget(line)
    outer.addSpacing(pad)
    lay = vbox(spacing=spacing)
    outer.addLayout(lay)
    return f, lay


class Led(QWidget):
    """状态灯（.led）：未运行灰、运行中橙、完成绿、出错红、跳过空心圈"""
    COLORS = {"idle": "led_off", "running": "led_run", "completed": "led_ok", "failed": "led_bad"}

    def __init__(self, state="idle", size=12, top=3):
        super().__init__()
        self.state = state
        self._d = size
        self.setFixedSize(size + 6, size + top + 3)
        self._top = top

    def set_state(self, s):
        if s != self.state:
            self.state = s
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        x, y, d = 3, self._top, self._d
        if self.state == "skipped":
            p.setPen(QPen(qc("led_off"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(x + 1, y + 1, d - 2, d - 2)
            return
        col = qc(self.COLORS.get(self.state, "led_off"))
        if self.state in ("running", "completed", "failed"):
            glow = QColor(col)
            glow.setAlpha(70)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(x - 2, y - 2, d + 4, d + 4)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(col)
        p.drawEllipse(x, y, d, d)


class Dot(QWidget):
    """端口类型的小圆点（.dot）"""

    def __init__(self, type_):
        super().__init__()
        self.color = qc(type_color(type_))
        self.setFixedSize(9, 9)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(self.color)
        p.drawEllipse(0, 0, 9, 9)


class _Header(QWidget):
    clicked = Signal()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()


class Details(QWidget):
    """可展开的一段（对应 <details><summary>）。extra：摘要行里放在文字前的部件（如状态灯）。
    on_toggle(open)：使用者点开或收起时调用，用来记住状态（重画时不丢）"""

    def __init__(self, summary, open=False, cls="summary", tone=None, extra=None, on_toggle=None, pad=(0, 0, 0, 0)):
        super().__init__()
        lay = vbox(self)
        self.head = _Header()
        self.head.setCursor(Qt.CursorShape.PointingHandCursor)
        hl = hbox(self.head, spacing=6, margins=(0, 2, 0, 2))
        self.arrow = label("", cls=cls, tone=tone, wrap=False)
        self.arrow.setFixedWidth(10)
        hl.addWidget(self.arrow, 0, Qt.AlignmentFlag.AlignVCenter)
        for w in extra or []:
            hl.addWidget(w, 0, Qt.AlignmentFlag.AlignVCenter)
        self.text = label(summary, cls=cls, tone=tone)
        hl.addWidget(self.text, 1)
        lay.addWidget(self.head)
        self.body = QWidget()
        self.body_lay = vbox(self.body, margins=pad)
        lay.addWidget(self.body)
        self._on_toggle = on_toggle
        self.head.clicked.connect(self.toggle)
        self.set_open(open)

    def set_open(self, on):
        self.open = bool(on)
        self.arrow.setText("▾" if self.open else "▸")
        self.body.setVisible(self.open)

    def toggle(self):
        self.set_open(not self.open)
        if self._on_toggle:
            self._on_toggle(self.open)


class TextBox(QTextEdit):
    """只读的一段文字（.value）：自动换行（长网址也能断开），高度随内容，超过 cap 时：clip 为真就截掉，否则出滚动条"""

    def __init__(self, text, cap=240, clip=False, mono=False, muted=False):
        super().__init__()
        self.setProperty("cls", "value")
        if mono:
            self.setProperty("mono", "true")
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff if clip else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.document().setDocumentMargin(8)
        self.setFont(mono_font(FS["sm"]) if mono else ui_font(FS["md"]))
        if muted:
            self.setStyleSheet(f"color: {C['muted']};")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._cap = cap
        self.setPlainText(text)
        self._fit()

    def text(self):
        return self.toPlainText()

    def sizeHint(self):
        return QSize(100, self.height())

    def minimumSizeHint(self):
        return QSize(40, 20)

    def _fit(self):
        doc = self.document()
        doc.setTextWidth(max(40, self.viewport().width()))
        h = int(doc.size().height()) + 2 * self.frameWidth() + 1
        self.setFixedHeight(max(24, min(h, self._cap)))

    def resizeEvent(self, e):
        super().resizeEvent(e)
        if e.oldSize().width() != e.size().width():
            self._fit()


class Value(QWidget):
    """一个值：文字原样、其余写成 JSON；超过 400 字先收起，可展开。state/key：展开状态存在外面，重画时不丢"""

    def __init__(self, v, state=None, key=None, mono=False):
        super().__init__()
        self._state = state if state is not None else {}
        self._key = key
        lay = vbox(self, spacing=2)
        self._lay = lay
        if v is UNSET:
            lay.addWidget(TextBox("（没有值）", muted=True))
            return
        self.full = v if isinstance(v, str) else pretty(v)
        self._mono = mono
        self._render()

    def _render(self):
        clear(self._lay)
        n = js_len(self.full)
        long = n > LONG
        opened = bool(self._state.get(self._key))
        if long and not opened:
            box = TextBox(self.full[:LONG] + "…", cap=int(9 * FS["md"] * 1.0) + 2, clip=True, mono=self._mono)
        else:
            box = TextBox(self.full, mono=self._mono)
        self._lay.addWidget(box)
        if long:
            b = button("收起" if opened else f"展开全文（{n} 字）", cls="link")
            b.clicked.connect(self._flip)
            self._lay.addWidget(b, 0, Qt.AlignmentFlag.AlignLeft)

    def _flip(self):
        self._state[self._key] = not self._state.get(self._key)
        QTimer.singleShot(0, self._render)  # 不在按钮自己的点击处理里删掉它


class Field(QWidget):
    """一个端口的值（.out）：名字、含义、值"""

    def __init__(self, name, hint, v, state=None, key=None):
        super().__init__()
        lay = vbox(self, spacing=3, margins=(0, 10, 0, 0))
        head = f'<span style="font-family:{MONO_HTML}; color:{C["ink_2"]};">{esc(name)}</span>'
        if hint:
            head += f'&nbsp;&nbsp;{esc(hint)}'
        lay.addWidget(label(head, cls="out-name", rich=True))
        lay.addWidget(Value(v, state, key))


# ---------- 输入框：离开时（或按 Enter）提交 ----------
class CommitLine(QLineEdit):
    """单行输入：on_commit(文字) 在失去焦点时调用；enter=True 时按 Enter 也调用"""

    def __init__(self, text="", placeholder="", on_commit=None, enter=True):
        super().__init__(text)
        if placeholder:
            self.setPlaceholderText(placeholder)
        self.on_commit = on_commit
        if enter:
            self.returnPressed.connect(lambda: self.on_commit and self.on_commit(self.text()))

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if self.on_commit and e.reason() != Qt.FocusReason.PopupFocusReason:  # 右键菜单弹出不算离开
            self.on_commit(self.text())

    def set_text_keep(self, t):
        """程序里改文字（撤销、重做后跟着程序走）"""
        if self.text() != t:
            self.setText(t)

    def pending(self):
        return self.text()


class CommitText(QPlainTextEdit):
    """多行输入（textarea）：失去焦点时 on_commit(文字)"""

    def __init__(self, text="", placeholder="", on_commit=None, rows=3, mono=True):
        super().__init__()
        if mono:
            self.setProperty("cls", "area")
        self.setPlainText(text)
        if placeholder:
            self.setPlaceholderText(placeholder)
        self.on_commit = on_commit
        self.setTabChangesFocus(True)
        line = 19 if mono else self.fontMetrics().lineSpacing()  # 等宽 12.5px 一行约 19px
        self.setFixedHeight(line * rows + 24)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)

    def text(self):
        return self.toPlainText()

    def setText(self, t):
        self.setPlainText(t)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        if self.on_commit and e.reason() != Qt.FocusReason.PopupFocusReason:  # 右键菜单弹出不算离开
            self.on_commit(self.text())

    def set_text_keep(self, t):
        if self.text() != t:
            self.setPlainText(t)
            c = self.textCursor()
            c.movePosition(c.MoveOperation.End)
            self.setTextCursor(c)


def combo(options, current=None, on_pick=None):
    """下拉选择：options = [(值, 文字)]；on_pick(值) 只在使用者选了之后调用"""
    cb = QComboBox()
    for v, t in options:
        cb.addItem(t, v)
    set_combo(cb, current)
    if on_pick:
        cb.activated.connect(lambda i: on_pick(cb.itemData(i)))
    cb.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    return cb


def set_combo(cb, value):
    for i in range(cb.count()):
        d = cb.itemData(i)
        if d == value and type(d) is type(value):
            if cb.currentIndex() != i:
                cb.setCurrentIndex(i)
            return


def _focused_in(root):
    from PySide6.QtWidgets import QApplication
    w = QApplication.focusWidget()
    if w is None or not isinstance(w, (CommitLine, CommitText)) or not root.isAncestorOf(w) or not w.on_commit:
        return None
    return w


def flush_focused(root, ctrl=None):
    """要整块换掉之前：焦点在里面的输入框先按“离开”提交（网页里点别处时输入框会先失焦提交）。
    放到下一轮事件里做，免得在重画中途改程序；传了 ctrl 时，到那时工作流已经换了就不再提交（不能写进别的工作流）"""
    w = _focused_in(root)
    if w is None:
        return
    fn, text = w.on_commit, w.text()
    w.on_commit = None  # 删掉时不再触发一次
    inst = (ctrl.current or {}).get("inst") if ctrl is not None else None

    def later():
        if ctrl is None or (ctrl.current or {}).get("inst") == inst:
            fn(text)
    QTimer.singleShot(0, later)


def commit_focused_now(root, keep=False):
    """马上要换工作流（或关窗口）：焦点在里面的输入框立即提交，改动落在还没换掉的这一份上。
    keep=True：输入框之后还要用（关窗口被取消），不摘掉它的提交函数"""
    w = _focused_in(root)
    if w is None:
        return
    fn, text = w.on_commit, w.text()
    if not keep:
        w.on_commit = None
    fn(text)


NAME_RE = re.compile(r"[a-z][a-z0-9_]*")
