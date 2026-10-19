"""画布：深炭灰底加灰点阵，上面是节点和连线（对应网页版 App.jsx 里的 ReactFlow 和 canvas.jsx）。
状态都在 Controller 里：这里按 ctrl.nodes / ctrl.edges 画图，鼠标操作转成 ctrl 的方法调用，位置变化由 ctrl 发回来再更新图元。
  滚轮以鼠标为中心缩放（0.15～2 倍），拖空白处或按住中键平移，双击空白处放大；
  点节点选中、点空白取消选中、悬停高亮它的连线；能编辑时拖节点（多选的一组一起拖）、从插孔拖线连接。
视野：屏幕坐标 = 场景坐标 × zoom + origin，用一张很大的场景加隐藏的滚动条实现平移。"""
import math
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QVariantAnimation, QEasingCurve, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QFrame, QGraphicsProxyWidget, QGraphicsScene, QGraphicsView

from ..theme import qc
from .base import NodeItem
from .items import BoxFallbackItem, DraftEdge, EdgeItem, ModuleItem, TerminalItem, bezier
from .overlays import MARGIN, BuildGuide, CanvasError, Overview, RunBar, ZoomBar, clamp_zoom, fit_viewport, initial_view

try:  # 循环框由 loop.py 画；它还没写好或载入出错时用简单的虚线框代替
    from .loop import LoopItem
except Exception:  # noqa: BLE001
    LoopItem = BoxFallbackItem

GAP, DOT = 48, 3          # 点阵：间距 48、点直径 3（网页版 <Background gap={48} size={3}>）
JACK_HIT = 10             # 按在插孔附近多远算按住了插孔（场景单位）
CONNECT_RADIUS = 24       # 拖线时离插孔多近就吸上去（React Flow connectionRadius）
DRAG_START = 3            # 按下后移动多少像素才算拖动（之前算点击）
WORLD = 1e5               # 场景范围：足够大，平移不会碰到边


class CanvasView(QGraphicsView):
    viewport_changed = Signal()  # 平移、缩放、改大小后：缩放条、小地图跟着更新

    def __init__(self, ctrl, parent=None):
        super().__init__(parent)
        self.ctrl = ctrl
        self.issue_map = {}
        self.led_phase = 1.0
        self.dash_offset = 0.0
        self.hot_handle = None
        self.node_items = {}   # 节点 id → 图元
        self.edge_items = {}   # 连线 id → EdgeItem
        self._need_initial = False
        self._press = None
        # 切走窗口时（例如 Alt+Tab）正在进行的拖动就地结束，回来后不会只动鼠标就带着节点走
        from PySide6.QtWidgets import QApplication
        QApplication.instance().applicationStateChanged.connect(
            lambda st: st != Qt.ApplicationState.ApplicationActive and self.cancel_press())
        self._focus = None
        self._cursor = None
        self._anim = None
        self._tiles = {}

        sc = QGraphicsScene(self)
        sc.setSceneRect(-WORLD, -WORLD, 2 * WORLD, 2 * WORLD)
        sc.setItemIndexMethod(QGraphicsScene.ItemIndexMethod.NoIndex)  # 拖动时位置一直在变，不维护索引反而快
        self.setScene(sc)
        self._draft = DraftEdge()
        self._draft.setVisible(False)
        sc.addItem(self._draft)

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing | QPainter.RenderHint.SmoothPixmapTransform)
        # 不用 SmartViewportUpdate：快速拖动循环框时它漏掉框原来的位置，旧框和连线的残影留在画布上，松开后才消失
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
        self.setCacheMode(QGraphicsView.CacheModeFlag.CacheBackground)
        self.setOptimizationFlag(QGraphicsView.OptimizationFlag.DontAdjustForAntialiasing, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.setBackgroundBrush(qc("board"))

        self.zoombar = ZoomBar(self)
        self.overview = Overview(self)
        self.runbar = RunBar(self)
        self.guide = BuildGuide(self)
        self.error = CanvasError(self)
        self.guide.changed.connect(self._place_overlays)
        self.runbar.changed.connect(self._place_overlays)
        self.centerOn(0, 0)

        # 运行中：状态灯闪烁、信号走过的线虚线流动
        self._tick = QTimer(self, interval=40, timeout=self._animate)

        c = ctrl
        c.flow_changed.connect(self.rebuild)
        c.nodes_moved.connect(self._on_moved)
        c.status_changed.connect(self._on_status)
        c.checks_changed.connect(self._on_issues)
        c.gen_changed.connect(self._on_issues)
        c.selection_changed.connect(self._on_selection)
        c.hover_changed.connect(self._on_hover)
        c.view_reset.connect(self._on_view_reset)
        c.focus_request.connect(lambda nid, pad, mz: self.fit_items([nid], pad, mz, True, 300))
        c.current_changed.connect(self._sync_error)
        self.horizontalScrollBar().valueChanged.connect(self._changed)
        self.verticalScrollBar().valueChanged.connect(self._changed)
        self.rebuild()

    # ================= 视野 =================
    def zoom(self):
        return self.transform().m11()

    def origin(self):
        vt = self.viewportTransform()
        return QPointF(vt.dx(), vt.dy())

    def to_scene(self, pos: QPointF) -> QPointF:
        z, o = self.zoom(), self.origin()
        return QPointF((pos.x() - o.x()) / z, (pos.y() - o.y()) / z)

    def set_viewport(self, z, x, y):
        """屏幕 = 场景 × z + (x, y)"""
        z = clamp_zoom(z)
        if abs(self.zoom() - z) > 1e-9:
            self.setTransform(QTransform.fromScale(z, z))
        o = self.origin()
        h, v = self.horizontalScrollBar(), self.verticalScrollBar()
        h.setValue(h.value() + round(o.x() - x))
        v.setValue(v.value() + round(o.y() - y))
        self._changed()

    def get_viewport(self):
        o = self.origin()
        return self.zoom(), o.x(), o.y()

    def animate_to(self, z, x, y, duration=0):
        self._stop_anim()
        z = clamp_zoom(z)
        if duration <= 0 or not self.isVisible():
            return self.set_viewport(z, x, y)
        z0, x0, y0 = self.get_viewport()
        # 以场景坐标插值视野中心，缩放按对数插值：移动、缩放看起来匀速
        w, h = self.viewport().width(), self.viewport().height()
        c0 = QPointF((w / 2 - x0) / z0, (h / 2 - y0) / z0)
        c1 = QPointF((w / 2 - x) / z, (h / 2 - y) / z)
        a = QVariantAnimation(self, startValue=0.0, endValue=1.0, duration=int(duration))
        a.setEasingCurve(QEasingCurve.Type.InOutCubic)

        def step(t):
            t = float(t)
            zz = z0 * (z / z0) ** t
            c = c0 + (c1 - c0) * t
            self.set_viewport(zz, w / 2 - c.x() * zz, h / 2 - c.y() * zz)
        a.valueChanged.connect(step)
        a.finished.connect(lambda: self.set_viewport(z, x, y))
        self._anim = a
        a.start()

    def _stop_anim(self):
        if self._anim is not None:
            self._anim.stop()
            self._anim = None

    def zoom_at(self, factor, pos: QPointF, duration=0):
        z, o = self.zoom(), self.origin()
        nz = clamp_zoom(z * factor)
        sp = self.to_scene(pos)
        self.animate_to(nz, pos.x() - sp.x() * nz, pos.y() - sp.y() * nz, duration)

    def zoom_by(self, factor, duration=0):
        """以画布中心缩放（缩放条、小地图滚轮）"""
        vp = self.viewport()
        self.zoom_at(factor, QPointF(vp.width() / 2, vp.height() / 2), duration)

    def zoom_to(self, z, duration=0):
        self.zoom_by(z / self.zoom(), duration)

    def center_on_scene(self, sp: QPointF):
        vp = self.viewport()
        z = self.zoom()
        self.set_viewport(z, vp.width() / 2 - sp.x() * z, vp.height() / 2 - sp.y() * z)

    def visible_scene_rect(self) -> QRectF:
        vp = self.viewport()
        return QRectF(self.to_scene(QPointF(0, 0)), self.to_scene(QPointF(vp.width(), vp.height())))

    def view_center_scene(self):
        """画布当前视野中间在画布坐标里的位置（ctrl.add_at 用）"""
        c = self.visible_scene_rect().center()
        return {"x": c.x(), "y": c.y()}

    def graph_bounds(self, ids=None):
        """节点（含框里的节点）在场景里的范围；ids 为 None 时是整张图。没有节点时为 None"""
        r = None
        for nid, it in self.node_items.items():
            if ids is not None and nid not in ids:
                continue
            b = it.mapRectToScene(it.frame_rect())
            r = b if r is None else r.united(b)
        return r

    def fit_items(self, ids=None, padding=0.08, max_zoom=1.1, animated=False, duration=300):
        """把这些节点（None：整张图）放进视野正中，四周留 padding（比例）"""
        b = self.graph_bounds(set(ids) if ids else None)
        vp = self.viewport()
        if b is None or vp.width() <= 0:
            return
        z, x, y = fit_viewport(b, vp.width(), vp.height(), padding, max_zoom)
        self.animate_to(z, x, y, duration if animated else 0)

    def _on_view_reset(self):
        self._need_initial = True
        QTimer.singleShot(0, self._try_initial)

    def _try_initial(self):
        """每打开一张图只设置一次初始视野：等节点画好、画布有了真实大小再设"""
        if not self._need_initial or not self.node_items or not self.isVisible():
            return
        vp = self.viewport()
        if vp.width() < 50 or vp.height() < 50:
            return
        self._need_initial = False
        b = self.graph_bounds()
        self._stop_anim()
        self.set_viewport(*initial_view(b, vp.width(), vp.height()))

    def _changed(self, *_):
        self.viewport_changed.emit()
        self.overview.sync()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self._changed()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._place_overlays()
        if self._need_initial:
            QTimer.singleShot(0, self._try_initial)
        self._changed()

    def showEvent(self, e):
        super().showEvent(e)
        self._place_overlays()
        if self._need_initial:
            QTimer.singleShot(0, self._try_initial)

    def _place_overlays(self):
        vp = self.viewport().geometry()
        zb = self.zoombar
        zb.adjustSize()
        zb.move(vp.left() + MARGIN, vp.bottom() - MARGIN - zb.height() + 1)
        ov = self.overview
        ov.move(vp.right() - MARGIN - ov.width() + 1, vp.bottom() - MARGIN - ov.height() + 1)
        rb = self.runbar
        rb.adjustSize()
        rb.move(vp.right() - MARGIN - rb.width() + 1, vp.top() + MARGIN)
        g = self.guide
        g.move(vp.right() - MARGIN - g.width() + 1, vp.top() + MARGIN + (rb.height() + 10 if rb.isVisible() else 0))
        self.error.setGeometry(vp.adjusted(40, 40, -40, -40))
        for w in (zb, ov, rb, g):
            w.raise_()

    # ================= 背景 =================
    def _tile(self, z, dpr):
        """一格点阵的贴图（每个缩放比例一张，场景坐标里正好一格 48）"""
        key = (round(z, 4), dpr)
        t = self._tiles.get(key)
        if t is None:
            if len(self._tiles) > 8:
                self._tiles.clear()
            n = max(2, round(GAP * z * dpr))
            pm = QPixmap(n, n)
            pm.fill(Qt.GlobalColor.transparent)
            pm.setDevicePixelRatio(dpr)
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            p.setPen(Qt.PenStyle.NoPen)
            col = QColor(qc("board_dot"))
            r = DOT * z / 2
            if r < 0.5:  # 太小时用淡一点的半像素点代替，免得闪
                col.setAlphaF(max(0.15, r * 2))
                r = 0.5
            p.setBrush(col)
            L = n / dpr
            p.drawEllipse(QPointF(L / 2, L / 2), r, r)
            p.end()
            t = (pm, L)
            self._tiles[key] = t
        return t

    def drawBackground(self, p: QPainter, rect: QRectF):
        p.fillRect(rect, qc("board"))
        z = self.zoom()
        pm, L = self._tile(z, self.devicePixelRatioF())
        br = QBrush(pm)
        s = GAP / L
        br.setTransform(QTransform().translate(-GAP / 2, -GAP / 2).scale(s, s))
        p.fillRect(rect, br)

    # ================= 画图 =================
    def _cls_for(self, n):
        t = n.get("type")
        return LoopItem if t == "loop" else TerminalItem if t == "terminal" else ModuleItem

    def ordered_items(self):
        """按 ctrl.nodes 的顺序（父节点在前）"""
        return [self.node_items[n["id"]] for n in self.ctrl.nodes if n["id"] in self.node_items]

    def rebuild(self):
        """flow_changed：按 ctrl.nodes / ctrl.edges 重画。同一个节点（类型、所在的框都没变）沿用原来的图元，只换数据"""
        ctrl, sc = self.ctrl, self.scene()
        self.issue_map = ctrl.issue_map()
        self.hot_handle = None
        for it in self.edge_items.values():
            if it.scene() is sc:
                sc.removeItem(it)
        self.edge_items = {}
        old = self.node_items
        new = {}
        for n in ([] if ctrl.canvas_error else ctrl.nodes):
            pid = n.get("parentId")
            parent = new.get(pid) if pid else None
            if pid and parent is None:
                continue  # 父节点没画出来（坏数据）：子节点也不画
            it = old.get(n["id"])
            cls = self._cls_for(n)
            if it is not None and type(it) is cls and it.parentItem() is parent and it.scene() is sc:
                it.set_node(n)
            else:
                try:
                    it = cls(self, n, parent)
                except Exception:  # noqa: BLE001  循环框图元出错时退回简单的框，其余照常画
                    if cls is ModuleItem or cls is TerminalItem:
                        raise
                    it = BoxFallbackItem(self, n, parent)
                if parent is None:
                    sc.addItem(it)
            it.setZValue(0 if n.get("type") == "loop" else 2)
            new[n["id"]] = it
        for nid, it in old.items():
            if new.get(nid) is not it and it.scene() is sc:
                sc.removeItem(it)
        self.node_items = new
        for it in new.values():
            it.refresh()
        states = ctrl.edge_states(ctrl.edges) if not ctrl.canvas_error else {}
        for e in ([] if ctrl.canvas_error else ctrl.edges):
            if e["source"] not in new or e["target"] not in new:
                continue
            scope = (e.get("data") or {}).get("scope") or []
            parent = new.get("/".join(scope)) if scope else None
            ei = EdgeItem(self, e, parent)
            if parent is None:
                sc.addItem(ei)
            ei.update_path()
            ei.restyle(states.get(e["id"], {}), self._edge_selected(e))
            self.edge_items[e["id"]] = ei
        self._focus = ctrl.hovered or ctrl.selected
        self._sync_error()
        self._sync_ticker()
        self.overview.sync()
        if self._need_initial:
            QTimer.singleShot(0, self._try_initial)

    def _edge_selected(self, e):
        se = self.ctrl.selected_edge
        return bool(se and se.get("id") == e["id"])

    def _sync_error(self):
        err = self.ctrl.canvas_error if self.ctrl.current else ""
        self.error.show_error(err)
        self.zoombar.setVisible(not err)
        self.overview.sync()
        self._place_overlays()

    def _on_moved(self, ids):
        """拖动中：只更新这些节点（位置、框的大小和 data.visual）和连到它们（及框里节点）的线"""
        by_id = {n["id"]: n for n in self.ctrl.nodes}
        moved = set(ids)
        for i in ids:
            it = self.node_items.get(i)
            if it is not None and i in by_id:
                it.set_node(by_id[i])

        def under(nid):
            while nid:
                if nid in moved:
                    return True
                nid = (by_id.get(nid) or {}).get("parentId")
            return False
        for ei in self.edge_items.values():
            e = ei.edge
            if under(e["source"]) or under(e["target"]):
                ei.update_path()
        self.overview.sync()

    def _restyle_edges(self, only=None):
        es = [ei.edge for ei in self.edge_items.values()
              if only is None or ei.edge["source"] in only or ei.edge["target"] in only]
        states = self.ctrl.edge_states(es)
        for e in es:
            self.edge_items[e["id"]].restyle(states[e["id"]], self._edge_selected(e))

    def _refresh_items(self, ids=None):
        for nid, it in self.node_items.items():
            if ids is None or nid in ids:
                it.refresh()

    def _on_status(self):
        self._refresh_items()
        self._restyle_edges()
        self._sync_ticker()
        self.overview.sync()

    def _on_issues(self):
        self.issue_map = self.ctrl.issue_map()
        self._refresh_items()

    def _on_selection(self):
        self._refresh_items()
        self._focus = self.ctrl.hovered or self.ctrl.selected
        self._restyle_edges()

    def _on_hover(self):
        old, self._focus = self._focus, self.ctrl.hovered or self.ctrl.selected
        touched = {x for x in (old, self._focus) if x}
        # 悬停的阴影只重画前后两个节点
        prev = getattr(self, "_hovered", None)
        self._hovered = self.ctrl.hovered
        self._refresh_items({x for x in (prev, self._hovered) if x})
        if touched or old != self._focus:
            self._restyle_edges(touched)

    # ---- 运行中的动画 ----
    def _sync_ticker(self):
        # 暂停时灯不闪、线不流动：停在那里等继续
        running = not self.ctrl.paused and (self.ctrl.running or any(
            (s or {}).get("state") == "running" for s in self.ctrl.node_status.values()))
        if running and not self._tick.isActive():
            self._tick.start()
        elif not running and self._tick.isActive():
            self._tick.stop()
            self.led_phase = 1.0
            self.dash_offset = 0.0
            self._animate(redraw_only=True)

    def _animate(self, redraw_only=False):
        if not redraw_only:
            t = time.monotonic()
            self.led_phase = 0.5 + 0.5 * math.cos(2 * math.pi * t)  # 1 秒一次，缓进缓出
            self.dash_offset = 10 * (1 - (t / 0.5) % 1.0)
        st = self.ctrl.node_status
        for nid, it in self.node_items.items():
            if (st.get(nid) or {}).get("state") == "running":
                led = getattr(it, "led_rect", None)  # 只重画灯那一小块：图元有缓存，整块重画很贵
                it.update(led() if led else it.boundingRect())
        for ei in self.edge_items.values():
            if ei.state.get("animated"):
                ei.update()

    # ================= 鼠标 =================
    def _topmost(self, sp):
        for it in self.scene().items(sp):
            if it is self._draft or not it.isVisible():
                continue
            return it
        return None

    def _node_of(self, it):
        while it is not None and not isinstance(it, NodeItem):
            it = it.parentItem()
        return it

    def _jacks_near(self, sp, radius, want=None, exclude=None):
        """离 sp 不超过 radius 的插孔，近的在前：[(距离, 节点图元, 插孔名, 种类)]。want：只要这几种（in / out / ctrl）"""
        out = []
        for nid, it in self.node_items.items():
            if nid == exclude:
                continue
            r = it.mapRectToScene(it.frame_rect()).adjusted(-radius - 10, -radius - 10, radius + 10, radius + 10)
            if not r.contains(sp):
                continue
            for h, pt, kind, _ in it.handles():
                if want and kind not in want:
                    continue
                d = math.hypot(*(lambda q: (q.x(), q.y()))(it.mapToScene(pt) - sp))
                if d <= radius:
                    out.append((d, it, h, kind))
        out.sort(key=lambda x: x[0])
        return out

    def _hit(self, sp):
        """按下的是什么：("other"|"jack"|"node"|"edge"|"pane", 对象)"""
        top = self._topmost(sp)
        if top is not None and (getattr(top, "is_box_control", False) or isinstance(top, QGraphicsProxyWidget)):
            return "other", top
        if self.ctrl.editable:
            js = self._jacks_near(sp, JACK_HIT)
            if js:
                _, it, h, kind = js[0]
                top_node = self._node_of(top)
                # 插孔被别的节点盖住时不算（只认最上面那个节点的插孔，或者插孔伸出边框、上面没有节点）
                if top_node is None or top_node is it or isinstance(top, EdgeItem) or top_node.isAncestorOf(it):
                    return "jack", (it, h, kind)
        if top is None:
            return "pane", None
        if isinstance(top, EdgeItem):
            return "edge", top
        node = self._node_of(top)
        if node is not None:
            if node.wants_press(sp):
                return "other", node
            return "node", node
        return "pane", None

    def _set_cursor(self, shape):
        if shape != self._cursor:
            self._cursor = shape
            self.viewport().setCursor(shape)

    def mousePressEvent(self, e):
        self._stop_anim()
        pos = e.position()
        sp = self.to_scene(pos)
        if e.button() == Qt.MouseButton.MiddleButton:
            self._press = {"kind": "pan", "last": pos, "start": pos, "moved": True}
            self._set_cursor(Qt.CursorShape.ClosedHandCursor)
            return
        if e.button() == Qt.MouseButton.RightButton and self._press is None:
            kind, obj = self._hit(sp)
            if kind == "jack":  # 右键插孔：断开连在它上面的线
                it, h, _ = obj
                QTimer.singleShot(0, lambda: self.ctrl.disconnect_port(it.nid, h))
                e.accept()
                return
        if e.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(e)
        kind, obj = self._hit(sp)
        if kind == "other":
            self._press = {"kind": "other"}
            return super().mousePressEvent(e)
        self._press = {"kind": kind, "obj": obj, "start": pos, "last": pos, "scene": sp, "moved": False}
        if kind == "jack":
            it, h, jk = obj
            self._press.update(nid=it.nid, handle=h, jack=jk, anchor=it.handle_scene_pos(h), cand=None)
            self._draft_to(sp)
            self._draft.setVisible(True)

    def mouseMoveEvent(self, e):
        pos = e.position()
        pr = self._press
        if pr is None:
            super().mouseMoveEvent(e)  # 让图元收到悬停（提示文字）
            return self._hover_at(pos)
        if pr["kind"] == "other":
            return super().mouseMoveEvent(e)
        if not (e.buttons() & (Qt.MouseButton.LeftButton | Qt.MouseButton.MiddleButton)):
            return self.cancel_press()  # 没收到松手（例如在窗口外松开）：就地结束
        sp = self.to_scene(pos)
        if not pr["moved"] and (pos - pr["start"]).manhattanLength() >= DRAG_START:
            pr["moved"] = True
            if pr["kind"] == "node" and self.ctrl.editable:
                self._drag_begin(pr)
            elif pr["kind"] in ("node", "edge", "pane"):
                pr["kind"] = "pan"
                self._set_cursor(Qt.CursorShape.ClosedHandCursor)
        if not pr["moved"]:
            return
        if pr["kind"] == "pan":
            d = pos - pr["last"]
            pr["last"] = pos
            z, o = self.zoom(), self.origin()
            self.set_viewport(z, o.x() + d.x(), o.y() + d.y())
        elif pr["kind"] == "drag":
            d = sp - pr["scene"]
            self.ctrl.drag_move({i: {"x": p["x"] + d.x(), "y": p["y"] + d.y()} for i, p in pr["starts"].items()})
        elif pr["kind"] == "jack":
            self._draft_to(sp)

    def mouseReleaseEvent(self, e):
        pr, self._press = self._press, None
        if pr is None:
            return super().mouseReleaseEvent(e)
        if pr["kind"] == "other":
            return super().mouseReleaseEvent(e)
        ctrl = self.ctrl
        if pr["kind"] == "jack":
            self._draft.setVisible(False)
            c = pr.get("cand")
            self.hot_handle = None
            if c:
                QTimer.singleShot(0, lambda: ctrl.connect(c))
            elif not pr["moved"]:
                ctrl.select_node(pr["nid"])  # 只是点了一下插孔：当作点了这个节点
        elif pr["kind"] == "drag":
            ids = list(pr["starts"])
            QTimer.singleShot(0, lambda: ctrl.drag_stop(ids))
        elif not pr["moved"]:
            if pr["kind"] == "node":
                ctrl.select_node(pr["obj"].nid)
            elif pr["kind"] == "edge":
                ctrl.select_edge(pr["obj"].edge)
            elif pr["kind"] == "pane":
                ctrl.clear_selection()
        self._cursor = None
        self._hover_at(e.position())

    def cancel_press(self):
        """拖动中途断了（窗口失去焦点、没收到松手）：拖节点就停在当前位置（照常整理、算一步改动），拉线作废，平移停下"""
        pr, self._press = self._press, None
        if pr is None or pr["kind"] == "other":
            return
        if pr["kind"] == "jack":
            self._draft.setVisible(False)
            self.hot_handle = None
        elif pr["kind"] == "drag":
            ids = list(pr["starts"])
            QTimer.singleShot(0, lambda: self.ctrl.drag_stop(ids))
        self._cursor = None
        self._set_cursor(Qt.CursorShape.ArrowCursor)
        self.viewport().update()

    def focusOutEvent(self, e):
        if e.reason() != Qt.FocusReason.PopupFocusReason:
            self.cancel_press()
        super().focusOutEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            kind, _ = self._hit(self.to_scene(e.position()))
            if kind == "pane":  # 双击空白处放大一倍（React Flow zoomOnDoubleClick）
                self.zoom_at(0.5 if e.modifiers() & Qt.KeyboardModifier.ShiftModifier else 2.0, e.position(), 250)
                return
            if kind == "other":
                return super().mouseDoubleClickEvent(e)
        e.accept()

    def wheelEvent(self, e):
        d = e.angleDelta().y() or e.angleDelta().x()
        if not d:
            return
        self._stop_anim()
        # d3-zoom：每格滚轮缩放 2^0.2 倍，按住 Ctrl 快十倍
        k = 0.2 * (10 if e.modifiers() & Qt.KeyboardModifier.ControlModifier else 1)
        self.zoom_at(2 ** (d / 120 * k), e.position())
        e.accept()

    def leaveEvent(self, e):
        if self._press is None:
            self.ctrl.set_hover(None)
            self._set_hot(None)
        super().leaveEvent(e)

    def _set_hot(self, hot):
        if hot != self.hot_handle:
            prev, self.hot_handle = self.hot_handle, hot
            for h in (prev, hot):
                if h and h[0] in self.node_items:
                    self.node_items[h[0]].update()

    def _hover_at(self, pos):
        sp = self.to_scene(pos)
        kind, obj = self._hit(sp)
        node = None
        if kind == "node":
            node = obj
        elif kind == "jack":
            node = obj[0]
        elif kind == "other":
            node = self._node_of(obj)
        self.ctrl.set_hover(node.nid if node is not None else None)
        self._set_hot((obj[0].nid, obj[1]) if kind == "jack" else None)
        if kind == "jack":
            self._set_cursor(Qt.CursorShape.CrossCursor)
        elif kind == "other":
            self._cursor = None  # 拉伸点等自己设光标
            self.viewport().unsetCursor()
        elif kind == "edge":
            self._set_cursor(Qt.CursorShape.PointingHandCursor)
        elif kind == "node":
            self._set_cursor(Qt.CursorShape.OpenHandCursor if self.ctrl.editable else Qt.CursorShape.PointingHandCursor)
        else:
            self._set_cursor(Qt.CursorShape.OpenHandCursor)

    # ---- 拖节点 ----
    def _drag_begin(self, pr):
        nid = pr["obj"].nid
        ids = sorted(self.ctrl.multi) if nid in self.ctrl.multi else [nid]
        self.begin_node_drag(ids, pr)

    def begin_node_drag(self, ids, pr=None):
        """开始拖这些节点（位置都相对各自的父节点；鼠标移动量在场景里和在父节点里一样，框不缩放）"""
        by_id = {n["id"]: n for n in self.ctrl.nodes}
        starts = {i: dict(by_id[i]["position"]) for i in ids if i in by_id}
        if not starts:
            return
        self.ctrl.drag_start()
        if pr is not None:
            pr["kind"] = "drag"
            pr["starts"] = starts
        self._set_cursor(Qt.CursorShape.ClosedHandCursor)

    # ---- 拖线 ----
    def connection_for(self, nid, handle, jack, other_nid, other_handle):
        """从 (nid, handle) 拖到 (other_nid, other_handle) 时要连的线；拖的是输入插孔时反过来"""
        if jack == "out":
            return {"source": nid, "sourceHandle": handle, "target": other_nid, "targetHandle": other_handle}
        return {"source": other_nid, "sourceHandle": other_handle, "target": nid, "targetHandle": handle}

    def candidate(self, pr, sp):
        """拖线时离鼠标最近、能连上的插孔（同一层、不是自己）：(连接, 插孔场景位置) 或 None"""
        want = ("in", "ctrl") if pr["jack"] == "out" else ("out",)
        from ..controller import Controller
        for _, it, h, _k in self._jacks_near(sp, CONNECT_RADIUS, want, exclude=pr["nid"]):
            c = self.connection_for(pr["nid"], pr["handle"], pr["jack"], it.nid, h)
            if Controller.can_connect(c):
                return c, it.handle_scene_pos(h), (it.nid, h)
        return None

    def _draft_to(self, sp):
        pr = self._press
        got = self.candidate(pr, sp)
        end = got[1] if got else sp
        pr["cand"] = got[0] if got else None
        self._set_hot(got[2] if got else (pr["nid"], pr["handle"]))
        a = pr["anchor"]
        if pr["jack"] == "out":
            path = bezier(a, end, target_top=bool(got and got[0]["targetHandle"] == "ctrl"))
        else:
            path = bezier(end, a, target_top=pr["jack"] == "ctrl")
        self._draft.set_path(path)
