"""画布上浮着的控件和视野计算：左下缩放条 ZoomBar、右下小地图 Overview、右上运行按钮 RunBar 和它下面的分步清单 BuildGuide、画不出来时的说明 CanvasError，
以及“看全图”、初始视野的计算（移植自 web/src/canvas.jsx、viewport.js 和 React Flow 的 fitView）。
这些控件是画布（QGraphicsView）本身的子控件、盖在 viewport 上面：平移时不跟着滚动，画布改大小时由画布重新摆放。"""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..logic.viewport import FIT, initial_viewport, whole_graph_visible
from ..theme import C, FS, R, qc

MIN_ZOOM, MAX_ZOOM = 0.15, 2.0
MARGIN = 15  # React Flow 的 Panel 离边 15px


def clamp_zoom(z):
    return max(MIN_ZOOM, min(MAX_ZOOM, z))


def fit_viewport(bounds: QRectF, width, height, padding=FIT["padding"], max_zoom=FIT["maxZoom"], min_zoom=MIN_ZOOM):
    """React Flow getViewportForBounds：把 bounds 放进 width×height、四周留 padding（比例），返回 (zoom, x, y)，屏幕 = 场景 × zoom + (x, y)"""
    bw, bh = max(bounds.width(), 1e-6), max(bounds.height(), 1e-6)
    z = min(width / (bw * (1 + padding)), height / (bh * (1 + padding)))
    z = max(min_zoom, min(max_zoom, z))
    c = bounds.center()
    return z, width / 2 - c.x() * z, height / 2 - c.y() * z


def initial_view(bounds: QRectF, width, height):
    """打开工作流时的视野：放得下就看全图（留白 0.08、最大 1.1 倍），放不下就用看得清字的 0.75 倍从左边（输入）开始"""
    v = initial_viewport({"x": bounds.x(), "y": bounds.y(), "width": bounds.width(), "height": bounds.height()}, width, height)
    if v is None:
        return fit_viewport(bounds, width, height)
    return v["zoom"], v["x"], v["y"]


def _rect_dict(r: QRectF):
    return {"x": r.x(), "y": r.y(), "width": r.width(), "height": r.height()}


# ---------- 缩放条 ----------
_ZOOM_QSS = f"""
QFrame#zoombar {{ background: {C['surface']}; border: 1px solid {C['line']}; border-radius: {R['md']}px; }}
QFrame#zoombar QPushButton {{ min-width: 30px; min-height: 30px; max-height: 30px; padding: 0 8px; border: 0; border-radius: {R['sm']}px;
  background: transparent; font-size: {FS['md']}px; color: {C['ink']}; }}
QFrame#zoombar QPushButton:hover {{ background: {C['hover']}; }}
QFrame#zoombar QPushButton#pct {{ min-width: 52px; color: {C['ink_2']}; }}
QFrame#zoombar QFrame#sep {{ background: {C['line']}; min-width: 1px; max-width: 1px; min-height: 18px; max-height: 18px; border: 0; }}
"""


class ZoomBar(QFrame):
    """左下角：缩小、当前百分比（点了回到 100%）、放大、看全图"""

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.setObjectName("zoombar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_ZOOM_QSS)
        self.setAccessibleName("缩放")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)

        def btn(text, tip, fn, name=None):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setAccessibleName(tip)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            if name:
                b.setObjectName(name)
            b.clicked.connect(fn)
            lay.addWidget(b)
            return b
        btn("−", "缩小", lambda: view.zoom_by(1 / 1.2, 160))
        self.pct = btn("100%", "恢复到 100%", lambda: view.zoom_to(1.0, 160), "pct")
        btn("+", "放大", lambda: view.zoom_by(1.2, 160))
        sep = QFrame()
        sep.setObjectName("sep")
        lay.addWidget(sep)
        btn("看全图", "缩放到能看到整张图", lambda: view.fit_items(None, FIT["padding"], FIT["maxZoom"], True, 220))
        view.viewport_changed.connect(self.sync)
        self.sync()

    def sync(self):
        t = f"{round(self.view.zoom() * 100)}%"
        if self.pct.text() != t:
            self.pct.setText(t)


# ---------- 小地图 ----------
MINI = {"running": QColor("#f0a94a"), "completed": QColor("#3fb57a"), "failed": QColor("#e45c5c")}
MINI_TERMINAL = QColor("#ececf0")
MINI_NODE = QColor("#6b6b74")
MINI_MASK = QColor(20, 20, 22, 140)   # rgba(20,20,22,0.55)
MINI_BG = QColor(38, 38, 42, 235)     # rgba(38,38,42,0.92)


class Overview(QWidget):
    """右下角小地图：节点按运行状态着色，视野范围以外盖一层暗色。按下、拖动移动视野，滚轮缩放。整张图都在视野里时不显示"""
    W, H = 168, 104

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.setFixedSize(self.W, self.H)
        self.setAccessibleName("整张图的缩略图")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._drag = None
        self._map = None  # (场景 → 小地图) 的 (缩放, 偏移 x, 偏移 y)
        self.hide()

    def _geometry(self):
        """React Flow MiniMap 的取景：节点范围和视野范围合起来，按小地图的比例放大一点、四周留 5 个单位"""
        nb = self.view.graph_bounds()
        vb = self.view.visible_scene_rect()
        bb = nb.united(vb) if nb is not None else vb
        scale = max(bb.width() / self.W, bb.height() / self.H, 1e-6)
        vw, vh = scale * self.W, scale * self.H
        off = 5 * scale
        box = QRectF(bb.x() - (vw - bb.width()) / 2 - off, bb.y() - (vh - bb.height()) / 2 - off, vw + 2 * off, vh + 2 * off)
        s = min(self.W / box.width(), self.H / box.height())
        ox = (self.W - box.width() * s) / 2 - box.x() * s
        oy = (self.H - box.height() * s) / 2 - box.y() * s
        return s, ox, oy, vb

    def sync(self):
        nb = self.view.graph_bounds()
        vp = self.view.viewport()
        all_ = nb is None or whole_graph_visible(_rect_dict(nb), (self.view.origin().x(), self.view.origin().y(), self.view.zoom()), vp.width(), vp.height())
        want = not all_ and not self.view.ctrl.canvas_error
        if want != self.isVisible():
            self.setVisible(want)
        if want:
            self.update()

    def paintEvent(self, e):
        s, ox, oy, vb = self._geometry()
        self._map = (s, ox, oy)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        frame = QRectF(0.5, 0.5, self.W - 1, self.H - 1)
        clip = QPainterPath()
        clip.addRoundedRect(frame, R["lg"], R["lg"])
        p.setClipPath(clip)
        p.fillRect(self.rect(), MINI_BG)
        statuses = self.view.ctrl.statuses()

        def to_mini(r: QRectF):
            return QRectF(r.x() * s + ox, r.y() * s + oy, r.width() * s, r.height() * s)
        p.setPen(Qt.PenStyle.NoPen)
        for it in self.view.ordered_items():
            n = it.node
            if n.get("type") == "terminal":
                col = MINI_TERMINAL
            else:
                col = MINI.get((statuses.get(n["id"]) or {}).get("state"), MINI_NODE)
            p.setBrush(col)
            p.drawRoundedRect(to_mini(it.mapRectToScene(it.frame_rect())), 3, 3)
        # 视野以外盖暗色
        mask = QPainterPath()
        mask.setFillRule(Qt.FillRule.OddEvenFill)
        mask.addRect(QRectF(self.rect()))
        mask.addRect(to_mini(vb))
        p.setBrush(MINI_MASK)
        p.drawPath(mask)
        p.setClipping(False)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(QPen(qc("line"), 1))
        p.drawRoundedRect(frame, R["lg"], R["lg"])

    def _scene_at(self, pt: QPointF):
        # 拖动中用按下时定好的取景（重画会更新 _map，拿它算会越拖越跑）
        s, ox, oy = getattr(self, "_drag_map", None) or self._map or self._geometry()[:3]
        return QPointF((pt.x() - ox) / s, (pt.y() - oy) / s)

    def mousePressEvent(self, e):
        if e.button() != Qt.MouseButton.LeftButton:
            return
        self._drag_map = self._geometry()[:3]  # 拖动中取景不变，免得越拖越跑
        sp = self._scene_at(e.position())
        vb = self.view.visible_scene_rect()
        if not vb.contains(sp):  # 点在视野框外：先把视野移到这里
            self.view.center_on_scene(sp)
            vb = self.view.visible_scene_rect()
        self._drag = (sp, vb.center())
        e.accept()

    def mouseMoveEvent(self, e):
        if not self._drag:
            return
        start, center = self._drag
        sp = self._scene_at(e.position())
        self.view.center_on_scene(center + (sp - start))

    def mouseReleaseEvent(self, e):
        self._drag = None
        self._drag_map = None

    def wheelEvent(self, e):
        d = e.angleDelta().y()
        if d:
            self.view.zoom_by(2 ** (d / 120 * 0.2))
        e.accept()


# ---------- 运行按钮 ----------
_RUN_QSS = f"""
QFrame#runbar {{ background: {C['surface']}; border: 1px solid {C['line']}; border-radius: {R['md']}px; }}
QFrame#runbar QPushButton {{ min-height: 30px; max-height: 30px; padding: 0 10px; border: 0; border-radius: {R['sm']}px;
  background: transparent; font-size: {FS['md']}px; color: {C['ink']}; }}
QFrame#runbar QPushButton:hover {{ background: {C['hover']}; }}
QFrame#runbar QPushButton:disabled {{ color: {C['led_off']}; background: transparent; }}
QFrame#runbar QPushButton#runStart {{ color: {C['led_ok']}; }}
QFrame#runbar QPushButton#runStop {{ color: {C['bad_ink']}; }}
QFrame#runbar QPushButton#runStart:disabled, QFrame#runbar QPushButton#runStop:disabled {{ color: {C['led_off']}; }}
QFrame#runbar QLabel#runState {{ background: transparent; padding: 0 6px 0 8px; font-size: {FS['sm']}px; color: {C['ink_2']}; }}
QFrame#runbar QFrame#sep {{ background: {C['line']}; min-width: 1px; max-width: 1px; min-height: 18px; max-height: 18px; border: 0; }}
"""
# 运行状态 → (右上角显示的文字, 灯的颜色键)；idle 不显示
RUN_STATE_TEXT = {
    "running": ("运行中", "led_run"), "pausing": ("等当前节点跑完再暂停", "led_run"), "paused": ("已暂停", "led_run"),
    "stopping": ("正在终止…", "led_bad"), "done": ("完成", "led_ok"), "error": ("出错", "led_bad"), "stopped": ("已终止", "led_off"),
}


class RunBar(QFrame):
    """右上角：开始、暂停（暂停后变成继续）、终止，按 ctrl.run_state() 启用或禁用；旁边显示这次运行到了哪一步"""
    changed = Signal()

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.ctrl = c = view.ctrl
        self.setObjectName("runbar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_RUN_QSS)
        self.setAccessibleName("运行")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self.state = QLabel(objectName="runState")
        self.state.setTextFormat(Qt.TextFormat.RichText)
        lay.addWidget(self.state)
        self.sep = QFrame(objectName="sep")
        lay.addWidget(self.sep)

        def btn(text, tip, fn, name):
            b = QPushButton(text, objectName=name)
            b.setToolTip(tip)
            b.setAccessibleName(text)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            b.clicked.connect(fn)
            lay.addWidget(b)
            return b
        self.start = btn("▶ 开始", "按右侧“运行”页填好的输入运行这个工作流", c.request_run, "runStart")
        self.pause = btn("Ⅱ 暂停", "", self._pause_or_resume, "runPause")
        self.stop = btn("■ 终止", "立即终止：正在运行的节点会被结束，后面的节点不再运行", c.stop_run, "runStop")
        for sig in (c.current_changed, c.status_changed, c.gen_changed):
            sig.connect(self.sync)
        self._shown = None
        self.sync()

    def _pause_or_resume(self):
        if self.ctrl.pause_wanted:
            self.ctrl.resume_run()
        else:
            self.ctrl.pause_run()

    def sync(self):
        c = self.ctrl
        show = bool(c.current) and not c.canvas_error
        st = c.run_state()
        busy = c.running
        self.start.setEnabled(show and not busy and not c.generating)
        self.stop.setEnabled(busy and not c.stopping)
        resume = busy and c.pause_wanted
        self.pause.setText("▶ 继续" if resume else "Ⅱ 暂停")
        self.pause.setAccessibleName("继续" if resume else "暂停")
        self.pause.setToolTip("从停下的地方接着运行" if resume else "正在运行的节点跑完后暂停，之后可以继续")
        self.pause.setEnabled(busy and not c.stopping)
        text, led = RUN_STATE_TEXT.get(st, ("", None))
        self.state.setVisible(bool(text))
        self.sep.setVisible(bool(text))
        if text:
            self.state.setText(f'<span style="color:{C[led]}">●</span>&nbsp; {text}')
        key = (show, text)
        if key != self._shown:  # 显示与否、宽度变了：画布重新摆放右上角
            self._shown = key
            self.setVisible(show)
            self.adjustSize()
            self.changed.emit()


# ---------- 分步清单 ----------
_GUIDE_QSS = f"""
QFrame#guide {{ background: {C['surface']}; border: 1px solid {C['line']}; border-radius: 12px; }}
QFrame#guide QLabel {{ background: transparent; }}
QLabel#guideTitle {{ font-weight: 650; }}
QLabel#guideCount {{ font-size: {FS['sm']}px; color: {C['muted']}; }}
QPushButton#guideToggle {{ background: transparent; border: 0; padding: 0; color: {C['focus']}; font-size: {FS['base']}px; }}
QPushButton#guideToggle:hover {{ text-decoration: underline; }}
QLabel[guide="step"] {{ font-weight: 600; color: {C['muted']}; }}
QLabel[guide="step"][st="now"] {{ color: {C['ink']}; }}
QLabel[guide="step"][st="done"] {{ color: {C['ink_2']}; }}
QLabel[guide="how"] {{ font-size: {FS['md']}px; color: {C['ink_2']}; }}
QLabel[guide="mark"] {{ min-width: 17px; max-width: 17px; min-height: 17px; max-height: 17px; border-radius: 10px;
  border: 1.5px solid {C['line_strong']}; font-size: {FS['sm']}px; color: {C['muted']}; }}
QLabel[guide="mark"][st="now"] {{ border-color: {C['led_run']}; background: {C['warn_bg']}; color: {C['warn_ink']}; font-weight: 650; }}
QLabel[guide="mark"][st="done"] {{ border-color: {C['led_ok']}; background: {C['led_ok']}; color: #ffffff; }}
"""


class BuildGuide(QFrame):
    """从空白开始：右上角的分步清单（步骤状态由 ctrl.guide() 算出，这里只负责显示和收起）"""
    changed = Signal()

    def __init__(self, view):
        super().__init__(view)
        self.view = view
        self.ctrl = view.ctrl
        self.setObjectName("guide")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(_GUIDE_QSS)
        self._key = None
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(16, 12, 16, 12)
        self._lay.setSpacing(8)
        for sig in (self.ctrl.current_changed, self.ctrl.checks_changed, self.ctrl.layout_changed, self.ctrl.flow_changed,
                    self.ctrl.gen_changed, self.ctrl.status_changed):
            sig.connect(self.sync)
        self.hide()

    def sync(self):
        g = self.ctrl.guide() if not self.ctrl.canvas_error else None
        if g is None:
            self._key = None
            if self.isVisible():
                self.hide()
                self.changed.emit()
            return
        open_ = bool(self.ctrl.guide_open)
        key = (open_, g["current"], tuple(s["done"] for s in g["steps"]))
        if key != self._key:
            self._key = key
            self._build(g, open_)
        if not self.isVisible():
            self.show()
            self.raise_()
        self.changed.emit()

    def _clear(self):
        while self._lay.count():
            it = self._lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.deleteLater()
            elif it.layout() is not None:
                lay = it.layout()
                while lay.count():
                    x = lay.takeAt(0)
                    if x.widget():
                        x.widget().deleteLater()

    def _build(self, g, open_):
        self._clear()
        steps = g["steps"]
        head = QHBoxLayout()
        head.setSpacing(8)
        t = QLabel("从空白搭一个工作流", objectName="guideTitle")
        f = t.font()
        f.setBold(True)
        t.setFont(f)
        head.addWidget(t)
        head.addWidget(QLabel(f"{sum(1 for s in steps if s['done'])}/{len(steps)}", objectName="guideCount"))
        head.addStretch(1)
        tog = QPushButton("收起" if open_ else "展开", objectName="guideToggle")
        tog.setCursor(Qt.CursorShape.PointingHandCursor)
        tog.clicked.connect(lambda: self.ctrl.set_guide_open(not self.ctrl.guide_open))
        head.addWidget(tog)
        w = QWidget()
        w.setLayout(head)
        head.setContentsMargins(0, 0, 0, 0)
        self._lay.addWidget(w)
        if open_:
            self._lay.addSpacing(4)
            for i, s in enumerate(steps):
                st = "done" if s["done"] else "now" if i == g["current"] else ""
                row = QWidget()
                rl = QHBoxLayout(row)
                rl.setContentsMargins(0, 0, 0, 0)
                rl.setSpacing(10)
                mark = QLabel("✓" if s["done"] else str(i + 1))
                mark.setProperty("guide", "mark")
                mark.setProperty("st", st)
                mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
                rl.addWidget(mark, 0, Qt.AlignmentFlag.AlignTop)
                col = QVBoxLayout()
                col.setSpacing(2)
                title = QLabel(s["title"])
                title.setProperty("guide", "step")
                title.setProperty("st", st)
                title.setAccessibleName(s["title"] + ("（已完成）" if s["done"] else "（下一步）" if i == g["current"] else ""))
                col.addWidget(title)
                if i == g["current"]:
                    how = QLabel(s["how"])
                    how.setProperty("guide", "how")
                    how.setWordWrap(True)
                    col.addWidget(how)
                rl.addLayout(col, 1)
                self._lay.addWidget(row)
            if g["current"] == -1:
                done = QLabel("都完成了。点右上角“保存到我的”把它存下来。")
                done.setProperty("guide", "how")
                done.setWordWrap(True)
                self._lay.addWidget(done)
        # 展开时固定 300 宽、高度随换行的说明文字；收起时按标题行的大小
        for i in range(self._lay.count()):  # 加到已显示的控件里的新子控件要稍后才显示，先显示出来才能量出大小
            w = self._lay.itemAt(i).widget()
            if w is not None:
                w.show()
        self._lay.activate()
        if open_:
            h = self._lay.heightForWidth(300) if self._lay.hasHeightForWidth() else self._lay.sizeHint().height()
            self.setFixedSize(300, max(h, self._lay.minimumSize().height()))
        else:
            self.setFixedSize(self._lay.sizeHint())


# ---------- 画不出来 ----------
class CanvasError(QLabel):
    """这个工作流没法画出来：显示在画布中间，代替整张图"""

    def __init__(self, view):
        super().__init__(view)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setStyleSheet(f"color: {C['muted']}; font-size: 15px; background: transparent;")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.hide()

    def show_error(self, err):
        if err:
            self.setText(f"这个工作流没法画出来：{err}")
            self.show()
            self.raise_()
        else:
            self.hide()
