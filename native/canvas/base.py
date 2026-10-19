"""画布上节点图元的公共约定（画布 view.py、普通节点 items.py、循环框 loop.py 都按这里来）。

坐标：图元用 Qt 的父子关系对应工作流的嵌套——循环框里的节点的父图元就是那个循环框，
setPos(node["position"]) 即是相对父图元的位置，与网页版 React Flow 的 parentId 语义相同。
节点 dict 的形状见 native/logic/graph.py（id、type、data、parentId、position）；data.width / data.height 是大小。

每种图元都是 NodeItem 的子类：
  set_node(node)          换成新的节点数据（位置、大小、端口、data.visual 等），重画
  handles()               [(handle, QPointF 图元坐标, "in"|"out"|"ctrl", 端口类型)]：连线端点、拖线的插孔
  handle_scene_pos(h)     某个插孔在场景里的位置（连线用）
  refresh()               运行状态、问题标记、选中、悬停变了：只重画
  frame_rect()            画出来的本体范围（图元坐标）：整图范围、小地图、看全某个节点都按它算
  wants_press(scene_pos)  这次按下由图元自己处理（返回 True 时画布不点选、不拖动，交给 Qt 默认分发）
图元的子图元要自己接鼠标（拉伸点等）时设类属性 is_box_control = True，画布同样交给 Qt 默认分发。
画布提供给图元读的：view.issue_map（id → 问题列表）、view.led_phase（0..1，运行中的灯闪烁）、
view.hot_handle（(节点 id, 插孔名)：鼠标正指着、可以拖线的插孔，画高亮圈）。
画布负责：点选、拖动（包括多选一起拖）、悬停、从插孔拖线、连线绘制；图元自己只负责画。
循环框额外有拉伸点、框顶菜单，见 loop.py。"""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsObject

from ..theme import qc, ui_font

STATE_LABEL = {"idle": "未运行", "running": "运行中", "completed": "完成", "skipped": "跳过", "failed": "出错"}
LED_COLOR = {"idle": "led_off", "running": "led_run", "completed": "led_ok", "failed": "led_bad"}


class NodeItem(QGraphicsObject):
    def __init__(self, view, node, parent=None):
        super().__init__(parent)
        self.view = view          # CanvasView
        self.ctrl = view.ctrl     # Controller
        self.node = node
        self.setAcceptHoverEvents(True)
        self.setPos(node["position"]["x"], node["position"]["y"])

    # ---- 子类可以覆盖 ----
    def set_node(self, node):
        self.prepareGeometryChange()
        self.node = node
        self.setPos(node["position"]["x"], node["position"]["y"])
        self.update()

    def handles(self):
        return []

    def refresh(self):
        self.update()

    def frame_rect(self):
        w, h = self.size()
        return QRectF(0, 0, w, h)

    def wants_press(self, scene_pos):
        return False

    # ---- 公共 ----
    @property
    def nid(self):
        return self.node["id"]

    def size(self):
        d = self.node.get("data") or {}
        return float(d.get("width") or 196), float(d.get("height") or 60)

    def boundingRect(self):
        w, h = self.size()
        return QRectF(-12, -12, w + 24, h + 24)  # 留出插孔和问题标记伸出边框的部分

    def handle_scene_pos(self, handle):
        for h, p, _, _ in self.handles():
            if h == handle:
                return self.mapToScene(p)
        w, h = self.size()
        return self.mapToScene(QPointF(w / 2, h / 2))

    def status(self):
        return self.ctrl.status_of(self.nid)

    def issues(self):
        return self.view.issue_map.get(self.nid)

    def is_selected(self):
        return self.ctrl.is_selected(self.nid)


# 灯的光晕（与网页版 .led-* 的 box-shadow 一致）：(颜色, 光晕半径)
_GLOW = {"completed": ((47, 158, 99), 8), "failed": ((214, 69, 69), 9)}


def _glow(p, center, r, rgb, spread, alpha):
    """一圈由内向外变淡的光晕，代替 CSS 的模糊阴影"""
    from PySide6.QtGui import QRadialGradient
    g = QRadialGradient(center, r + spread)
    c0 = QColor(*rgb, int(255 * alpha))
    c1 = QColor(*rgb, 0)
    g.setColorAt(r / (r + spread), c0)
    g.setColorAt(1.0, c1)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QBrush(g))
    p.drawEllipse(center, r + spread, r + spread)


def draw_led(p: QPainter, center: QPointF, state: str, r=5.0, phase=1.0):
    """状态灯：绿＝完成，空心灰圈＝跳过，红＝出错，琥珀＝运行中（phase 0..1 用来闪烁：1 最亮）"""
    p.save()
    if state in _GLOW:
        rgb, spread = _GLOW[state]
        _glow(p, center, r, rgb, spread * r / 6, 0.7)
    elif state == "running":
        # 网页版 pulse：亮时 4px 光环 + 10px 光晕，暗时 7px 很淡的光环 + 4px 光晕
        ring = (4 + 3 * (1 - phase)) * r / 6
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(232, 163, 61, int(255 * (0.08 + 0.17 * phase))))
        p.drawEllipse(center, r + ring, r + ring)
        _glow(p, center, r, (232, 163, 61), (4 + 6 * phase) * r / 6, 0.5 + 0.3 * phase)
    if state == "skipped":  # 网页版：透明底，内侧 2px 灰圈
        p.setPen(QPen(qc("led_off"), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(center, r - 1, r - 1)
        p.restore()
        return
    else:
        col = QColor(qc(LED_COLOR.get(state, "led_off")))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(col))
    p.drawEllipse(center, r, r)
    p.restore()


def draw_issue_badge(p: QPainter, at: QPointF):
    """右上角的红色感叹号：检查发现问题"""
    p.save()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(qc("led_bad"))
    p.drawEllipse(at, 10, 10)  # 网页版 .issue-badge：20px 圆
    p.setPen(QColor("#ffffff"))
    f = ui_font(12)
    f.setBold(True)
    p.setFont(f)
    p.drawText(QRectF(at.x() - 10, at.y() - 10, 20, 20), Qt.AlignmentFlag.AlignCenter, "!")
    p.restore()
