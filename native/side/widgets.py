"""顶栏、左侧栏、开始页、画布标题共用的小部件：会换行的排列、布局开关、图标、弹出菜单框。"""
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLayout, QPushButton, QToolButton, QWidget

from ..theme import C, R, qc


def repolish(w):
    """改了动态属性后让样式表重新生效"""
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def set_prop(w, name, value):
    if w.property(name) != value:
        w.setProperty(name, value)
        repolish(w)


class FlowLayout(QLayout):
    """像网页 flex-wrap 一样：一行放不下就换行；align 为 right 时每行靠右"""

    def __init__(self, parent=None, spacing=8, align="left"):
        super().__init__(parent)
        self._items = []
        self._sp = spacing
        self._align = align
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, w):
        return self._do(QRect(0, 0, w, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):
        """理想大小：全部排成一行"""
        shown = [it.sizeHint() for it in self._items if not it.isEmpty()]
        m = self.contentsMargins()
        if not shown:
            return QSize(m.left() + m.right(), m.top() + m.bottom())
        w = sum(s.width() for s in shown) + self._sp * (len(shown) - 1)
        h = max(s.height() for s in shown)
        return QSize(w + m.left() + m.right(), h + m.top() + m.bottom())

    def minimumSize(self):
        s = QSize()
        for it in self._items:
            if not it.isEmpty():
                s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do(self, rect, test):
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        rows, row, row_w = [], [], 0
        for it in self._items:
            if it.isEmpty():
                continue
            w = it.sizeHint().width()
            if row and row_w + self._sp + w > r.width():
                rows.append(row)
                row, row_w = [], 0
            row_w += (self._sp if row else 0) + w
            row.append(it)
        if row:
            rows.append(row)
        y = r.y()
        for row in rows:
            h = max(it.sizeHint().height() for it in row)
            total = sum(it.sizeHint().width() for it in row) + self._sp * (len(row) - 1)
            x = r.x() + (r.width() - total if self._align == "right" else 0)
            for it in row:
                sh = it.sizeHint()
                if not test:
                    it.setGeometry(QRect(QPoint(x, y + (h - sh.height()) // 2), sh))
                x += sh.width() + self._sp
            y += h + self._sp
        return (y - self._sp - r.y() if rows else 0) + m.top() + m.bottom()

    def clear(self):
        while self._items:
            it = self._items.pop()
            w = it.widget()
            if w:
                w.deleteLater()


# ---------- 图标（与网页版里的 SVG 相同，用 QPainter 画） ----------
def _pix(size, draw):
    """在 20×20 的坐标里作画，按屏幕缩放画清楚"""
    ratio = 2.0
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 20, size / 20)
    draw(p)
    p.end()
    return pm


def plus_icon(color, size=16):
    def draw(p):
        p.setPen(QPen(QColor(color), 1.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(QPointF(10, 4.5), QPointF(10, 15.5))
        p.drawLine(QPointF(4.5, 10), QPointF(15.5, 10))
    return QIcon(_pix(size, draw))


def more_icon(color, size=16):
    def draw(p):
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        for x in (5, 10, 15):
            p.drawEllipse(QPointF(x, 10), 1.4, 1.4)
    return QIcon(_pix(size, draw))


def layout_icon(which, on, color, size=18):
    """收起/显示左侧列表、右侧面板的图标：一个框，左边或右边一条（展开时实心）"""
    def draw(p):
        c = QColor(color)
        p.setPen(QPen(c, 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(2.5, 3.5, 15, 13), 2.5, 2.5)
        c2 = QColor(c)
        c2.setAlphaF(0.9 if on else 0.5)
        p.setPen(QPen(c2, 1.5))
        if on:
            p.setBrush(c2)
        x = 2.5 if which == "side" else 12.5
        rr = 1 if on else 0
        p.drawRoundedRect(QRectF(x, 3.5, 5, 13), rr, rr)
    return QIcon(_pix(size, draw))


def brand_pixmap(size=28):
    """品牌标记：两个模块用跳线相连，右下一盏状态灯（与桌面程序图标一致，原图 64×64）"""
    ratio = 2.0
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.scale(size / 64, size / 64)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(qc("board"))
    p.drawRoundedRect(QRectF(2, 2, 60, 60), 14, 14)
    p.setBrush(QColor("#e9edf0"))
    p.drawRoundedRect(QRectF(9, 30, 18, 20), 3.5, 3.5)
    p.drawRoundedRect(QRectF(37, 14, 18, 20), 3.5, 3.5)
    path = QPainterPath(QPointF(27, 40))
    path.lineTo(32, 40)
    path.lineTo(32, 24)
    path.lineTo(37, 24)
    p.setPen(QPen(QColor("#9aa6b1"), 3.8, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap, Qt.PenJoinStyle.RoundJoin))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(qc("t_string"))
    p.drawEllipse(QPointF(27, 40), 3, 3)
    p.drawEllipse(QPointF(37, 24), 3, 3)
    p.setBrush(qc("led_ok"))
    p.drawEllipse(QPointF(49, 46), 5, 5)
    p.end()
    return pm


ICON_BTN_QSS = f"""
QToolButton[icon_btn="true"] {{ background: transparent; border: 0; border-radius: {R['sm']}px; padding: 0; }}
QToolButton[icon_btn="true"]:hover {{ background: {C['hover']}; }}
"""


class IconButton(QToolButton):
    """.icon-btn：透明的方形图标按钮，悬停时变亮；图标按“悬停/常态/未按下”换颜色"""

    def __init__(self, make_icon, size=32, tip="", parent=None):
        super().__init__(parent)
        self._make = make_icon  # make(color) → QIcon
        self._pressed = None    # None：不是开关；True/False：aria-pressed
        self._hover = False
        self.setProperty("icon_btn", True)
        self.setStyleSheet(ICON_BTN_QSS)
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tip)
        self.setAccessibleName(tip)
        self._refresh()

    def set_maker(self, make_icon):
        self._make = make_icon
        self._refresh()

    def set_pressed(self, on):
        self._pressed = on
        self._refresh()

    def _color(self):
        if self._hover:
            return C["ink"]
        return C["muted"] if self._pressed is False else C["ink_2"]

    def _refresh(self):
        icon = self._make(self._color())
        self.setIcon(icon)
        sz = icon.availableSizes()
        if sz:
            self.setIconSize(sz[0])

    def enterEvent(self, e):
        self._hover = True
        self._refresh()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hover = False
        self._refresh()
        super().leaveEvent(e)


class LayoutToggles(QWidget):
    """收起/显示左侧列表和右侧面板：两个图标按钮，文字放在悬停提示里。
    on_side / on_panel 不传时就是来回切换"""

    def __init__(self, ctrl, on_side=None, on_panel=None, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        self.side_btn = IconButton(lambda c: layout_icon("side", True, c))
        self.panel_btn = IconButton(lambda c: layout_icon("panel", True, c))
        self.side_btn.clicked.connect(on_side or (lambda: ctrl.set_side_open(not ctrl.side_open)))
        self.panel_btn.clicked.connect(on_panel or (lambda: ctrl.set_panel_open(not ctrl.panel_open)))
        row.addWidget(self.side_btn)
        row.addWidget(self.panel_btn)
        ctrl.layout_changed.connect(self.sync)
        self.sync()

    def sync(self):
        side, panel = self.ctrl.side_open, self.ctrl.panel_open
        for btn, which, on, text in ((self.side_btn, "side", side, "收起列表" if side else "显示列表"),
                                     (self.panel_btn, "panel", panel, "收起面板" if panel else "显示面板")):
            btn.set_maker(lambda c, which=which, on=on: layout_icon(which, on, c))
            btn.set_pressed(on)
            btn.setToolTip(text)
            btn.setAccessibleName(text)


class Popup(QFrame):
    """点按钮弹出的菜单框（.menu）：点框外或按 Esc 自动关掉"""
    closed = Signal()

    def __init__(self, parent, width=300):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint)
        self.setObjectName("menu")
        self.setFixedWidth(width)
        self.setStyleSheet(f"""
QFrame#menu {{ background: {C['surface']}; border: 1px solid {C['line']}; border-radius: {R['md']}px; }}
QPushButton[menu_item="true"] {{ text-align: left; background: transparent; border: 0; border-radius: {R['sm']}px; padding: 6px 8px; }}
QPushButton[menu_item="true"]:hover, QPushButton[menu_item="true"]:focus {{ background: {C['hover']}; }}
QPushButton[danger="true"] {{ color: {C['bad_ink']}; }}
""")

    def show_under(self, anchor, align="right", gap=6):
        """显示在 anchor 下方：align=right 右边对齐（.menu 的 right: 0）"""
        self.adjustSize()
        g = anchor.mapToGlobal(QPoint(0, anchor.height() + gap))
        x = g.x() + anchor.width() - self.width() if align == "right" else g.x()
        self.move(x, g.y())
        self.show()

    def hideEvent(self, e):
        super().hideEvent(e)
        self.closed.emit()
        self.deleteLater()  # 每次打开都新建一个：关掉的这个删掉，不在窗口下越攒越多


def menu_button(text, danger=False):
    b = QPushButton(text)
    b.setProperty("menu_item", True)
    if danger:
        b.setProperty("danger", True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


class ClickRow(QFrame):
    """整块可点的行（工作流列表、示例）：悬停变亮；带 on 属性时高亮。双击另发信号"""
    clicked = Signal()
    double_clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
        else:
            super().keyPressEvent(e)

