"""织灵桌宠效果原型。

无边框、透明背景、置顶、可拖动的小精灵：
- 待机：呼吸起伏、随机眨眼、眼睛跟着鼠标看、头顶的线和小珠子轻轻摆动；
- 左键点一下：压扁再弹跳、眯眼笑、冒小星星，左上角弹出气泡显示（模拟的）工作进度；
- 按住拖动：跟着鼠标走，按拖动方向歪身子；
- 右键：菜单「设置」（占位）和「关闭」；
- 气泡开着时，点电脑任何别的地方（或右键桌宠）气泡就收起；
- 工作流报错：头顶小灯变红闪烁报警、旁边冒“!”、身子一阵阵发抖、眼睛往下流眼泪；
  这时点它 = 打开织灵并跳到出错的地方（Pet.open_problem，主程序那边还没做，现在只弹气泡说明）。
头顶小珠子的颜色就是工作状态：橙色在运行，绿色已完成，红色出错。
灰色表示现在没在运行。双击桌宠打开主窗口。

接进织灵：主窗口关掉时如果还有工作流在运行，窗口只是隐藏，桌宠出现（见 native/pet/hook.py），
数据来自 source.ControllerRun。单独运行（python -m native.pet）时用模拟数据 FakeRun：
第 1 轮在「AI 总结」出错，点一下后重跑，第 2 轮成功，如此交替。
数据源要提供这几样：
  state        "running" / "done" / "error" / "idle"
  headline()   气泡第一行，如「正在运行：抓取网页 3/10」「运行出错：AI 总结 失败」
  detail()     气泡第二行，如「每日新闻摘要 · 第 2 步，共 5 步」
  progress()   0~1，气泡里的进度条
  problem()    出错时返回 (要定位的节点 id 列表, 出错节点名)，交给 on_open 去打开主窗口并定位
  tick()       每 0.7 秒调一次（模拟数据靠它往前走）
"""
import ctypes
import math
import os
import random
import sys
import time

from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt, QTimer, QVariantAnimation, QEasingCurve
from PySide6.QtGui import (QAction, QBrush, QColor, QCursor, QFont, QFontMetrics, QGuiApplication, QPainter,
                           QPainterPath, QPen, QRadialGradient)
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QWidget

INK = QColor("#2f2f38")
BODY_HI = QColor("#ffffff")
BODY_LO = QColor("#e6e9f2")
CHEEK = QColor(255, 140, 160, 110)
RUN = QColor("#f0a94a")
DONE = QColor("#3fb57a")
ERR = QColor("#e5484d")
IDLE = QColor("#9aa4ae")
TEAR = QColor(120, 190, 255)
FONT = "Microsoft YaHei UI"


def state_color(state):
    return {"running": RUN, "done": DONE, "error": ERR}.get(state, IDLE)


def _mouse_down(vk):
    try:
        return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)
    except Exception:
        return False


def _ease_out_back(t):
    c = 1.70158
    t -= 1
    return 1 + (c + 1) * t * t * t + c * t * t


class FakeRun:
    """模拟一个工作流在后台跑：一步步推进，跑完歇一会儿再从头来；单数轮在「AI 总结」第 2 项出错。"""
    NAME = "每日新闻摘要"
    STEPS = [("读取配置", 1), ("抓取网页", 10), ("提取正文", 10), ("AI 总结", 3), ("写入报告", 1)]
    FAIL_AT = (3, 2)
    ERROR = "接口超时，重试 3 次仍失败"

    def __init__(self):
        self.round = 0
        self.restart()

    def restart(self):
        self.round += 1
        self.step, self.done_n, self.started, self.finished_at = 0, 0, time.time(), None
        self.failed = False

    def tick(self):
        if self.failed:
            return              # 出错后停在那，等人来看
        if self.round % 2 == 1 and (self.step, self.done_n) == self.FAIL_AT:
            self.failed = True
            return
        if self.finished_at:
            if time.time() - self.finished_at > 8:
                self.restart()
            return
        self.done_n += 1
        if self.done_n >= self.STEPS[self.step][1]:
            if self.step + 1 < len(self.STEPS):
                self.step, self.done_n = self.step + 1, 0
            else:
                self.done_n = self.STEPS[self.step][1]
                self.finished_at = time.time()

    @property
    def state(self):
        return "error" if self.failed else "running" if self.finished_at is None else "done"

    def problem(self):
        return [f"node-{self.step + 1}"], self.STEPS[self.step][0]

    def progress(self):
        total = sum(n for _, n in self.STEPS)
        done = sum(n for _, n in self.STEPS[:self.step]) + self.done_n
        return done / total

    def headline(self):
        name, n = self.STEPS[self.step]
        if self.state == "error":
            return f"运行出错：{name} 失败"
        if self.state == "running":
            return f"正在运行：{name} {self.done_n}/{n}" if n > 1 else f"正在运行：{name}"
        return f"运行完成，用时 {int(self.finished_at - self.started)} 秒"

    def detail(self):
        if self.state == "error":
            return f"{self.ERROR} · 点我去看看"
        if self.state == "running":
            return f"{self.NAME} · 第 {self.step + 1} 步，共 {len(self.STEPS)} 步"
        return f"{self.NAME} · 报告已写好"


class Bubble(QWidget):
    """宠物左上角的对话气泡，尾巴朝右下指向宠物。"""
    PAD, TAIL, SHADOW = 14, 10, 10

    def __init__(self, run):
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.run = run
        self.f_head = QFont(FONT, 10)
        self.f_head.setWeight(QFont.DemiBold)
        self.f_small = QFont(FONT, 8)
        self.anchor = QPoint()      # 尾巴尖要指到的屏幕位置
        self.rise = 0.0             # 出现时从下往上浮一点
        self.closing = False
        self.note = None            # 临时顶替进度的一段话 (第一行, 第二行, 颜色)
        self.anim = QVariantAnimation(self, duration=220, easingCurve=QEasingCurve.OutCubic)
        self.anim.valueChanged.connect(self._on_anim)
        self.hide_timer = QTimer(self, singleShot=True, timeout=self.fade_out)

    def _lines(self):
        if self.note:
            return self.note
        return self.run.headline(), self.run.detail(), state_color(self.run.state)

    def _layout_size(self):
        head, detail, _ = self._lines()
        w = max(QFontMetrics(self.f_head).horizontalAdvance(head) + 14,
                QFontMetrics(self.f_small).horizontalAdvance(detail), 150)
        return w + self.PAD * 2 + self.SHADOW * 2, 74 + self.TAIL + self.SHADOW * 2

    def refresh(self):
        w, h = self._layout_size()
        if (w, h) != (self.width(), self.height()):
            self.resize(w, h)
        # 尾巴尖在气泡右下，距右边 18px；桌宠被拖到屏幕边上时，气泡挪回屏幕里，尾巴跟着挪到仍指向桌宠的位置
        x = self.anchor.x() - (w - self.SHADOW - 18)
        y = self.anchor.y() - (h - self.SHADOW) + int(self.rise)
        scr = QGuiApplication.screenAt(self.anchor) or QGuiApplication.primaryScreen()
        area = scr.availableGeometry()
        x = max(area.left() - self.SHADOW, min(x, area.right() + 1 + self.SHADOW - w))
        y = max(area.top() - self.SHADOW, y)
        self.tail_x = max(self.SHADOW + 30.0, min(float(self.anchor.x() - x), w - self.SHADOW - 18.0))
        self.move(x, y)
        self.update()

    def point_to(self, anchor):
        self.anchor = anchor
        if self.isVisible():
            self.refresh()

    @property
    def showing(self):
        return self.isVisible() and not self.closing

    def pop(self, seconds=4.0, note=None):
        self.note = note
        self.hide_timer.start(int(seconds * 1000))
        if self.showing and self.anim.state() != QVariantAnimation.Running:
            self.refresh()
            return
        self.closing = False
        self.anim.stop()
        self.anim.setStartValue(self.windowOpacity() if self.isVisible() else 0.0)
        self.anim.setEndValue(1.0)
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
        self.anim.start()

    def fade_out(self):
        if not self.showing:
            return
        self.hide_timer.stop()
        self.closing = True
        self.anim.stop()
        self.anim.setStartValue(self.windowOpacity())
        self.anim.setEndValue(0.0)
        self.anim.start()

    def _on_anim(self, v):
        self.setWindowOpacity(v)
        self.rise = (1 - v) * 8
        self.refresh()
        if v == 0.0 and self.closing:
            self.hide()
            self.closing = False
            self.note = None

    def mousePressEvent(self, e):
        self.fade_out()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s, t = self.SHADOW, self.TAIL
        card = QRectF(s, s, self.width() - 2 * s, self.height() - 2 * s - t)
        tip = QPointF(getattr(self, "tail_x", card.right() - 18) + 0.5, card.bottom() + t)
        path = QPainterPath()
        path.addRoundedRect(card, 14, 14)
        tail = QPainterPath()
        tail.moveTo(tip.x() - 18, card.bottom() - 1)
        tail.quadTo(tip.x() - 6, card.bottom() + 2, tip.x(), tip.y())
        tail.quadTo(tip.x() - 2, card.bottom() + 2, tip.x() + 4, card.bottom() - 1)
        tail.closeSubpath()
        path = path.united(tail)
        for i in range(s, 0, -2):     # 柔和阴影
            p.setPen(QPen(QColor(0, 0, 0, int(26 * (1 - i / s) + 2)), i))
            p.drawPath(path.translated(0, 2))
        p.setPen(QPen(QColor(0, 0, 0, 30), 1))
        p.setBrush(QColor("#ffffff"))
        p.drawPath(path)

        run = self.run
        head, detail, color = self._lines()
        x, y = card.left() + self.PAD, card.top() + 12
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawEllipse(QPointF(x + 4, y + 9), 4, 4)
        p.setFont(self.f_head)
        p.setPen(QColor("#1f1f24"))
        p.drawText(QRectF(x + 14, y, card.width(), 20), Qt.AlignLeft | Qt.AlignVCenter, head)
        bar = QRectF(x, y + 28, card.width() - self.PAD * 2, 5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#eceef3"))
        p.drawRoundedRect(bar, 2.5, 2.5)
        p.setBrush(color)
        p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(5.0, bar.width() * run.progress()), 5), 2.5, 2.5)
        p.setFont(self.f_small)
        p.setPen(QColor(ERR) if run.state == "error" and not self.note else QColor("#8a8a94"))
        p.drawText(QRectF(x, y + 38, card.width(), 18), Qt.AlignLeft | Qt.AlignVCenter, detail)


MENU_QSS = """
QMenu { background: #ffffff; border: 1px solid rgba(0,0,0,0.12); border-radius: 12px; padding: 6px; }
QMenu::item { padding: 8px 34px 8px 16px; border-radius: 8px; color: #24242a; font-family: 'Microsoft YaHei UI';
              font-size: 13px; }
QMenu::item:selected { background: #f0f1f5; }
QMenu::separator { height: 1px; background: #ececf0; margin: 4px 8px; }
"""


class Pet(QWidget):
    W, H = 150, 180

    def __init__(self, run=None, on_open=None, on_quit=None):
        """run：数据源（不给就用模拟数据）；on_open(targets)：打开主窗口，targets 非空时定位到这些节点；
        on_quit()：菜单里点“关闭”（不给就直接退出）"""
        super().__init__(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setFixedSize(self.W, self.H)
        self.setCursor(Qt.PointingHandCursor)
        self.run = run or FakeRun()
        self.on_open, self.on_quit = on_open, on_quit
        self.bubble = Bubble(self.run)

        self.t0 = time.time()
        self.next_blink = self.t0 + 1.5
        self.blink_at = -1.0
        self.hop_at = -10.0          # 点击弹跳开始的时间
        self.hop_happy = True        # 这次弹跳要不要笑（出错时点它只蹦不笑）
        self.sparkles = []           # (出发时间, 角度, 距离, 大小)
        self.look = QPointF()        # 眼睛看的方向（-1..1）
        self.tilt = 0.0
        self.press = None            # 按下时 (全局坐标, 窗口位置)
        self.dragging = False
        self.last_x = 0
        self.buttons_down = {}       # 上一帧各鼠标键是否按着，用来发现“在别处点了一下”

        self.frame = QTimer(self, interval=16, timeout=self._tick)      # 显示时才开（showEvent）
        self.work = QTimer(self, interval=700, timeout=self._work_tick)

        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.right() - self.W - 60, geo.bottom() - self.H - 30)

    def showEvent(self, e):
        self.frame.start()
        self.work.start()

    def hideEvent(self, e):
        # 接进主程序后桌宠大部分时间是收着的：收着时不画、不查鼠标
        self.frame.stop()
        self.work.stop()
        self.press, self.dragging = None, False

    # ---------- 状态 ----------
    def now(self):
        return time.time() - self.t0

    def _work_tick(self):
        self.run.tick()
        if self.bubble.isVisible():
            self.bubble.refresh()

    def _tick(self):
        t = time.time()
        n = self.now()
        if t >= self.next_blink:
            self.blink_at = n
            # 偶尔连眨两下
            self.next_blink = t + (0.25 if random.random() < 0.2 else random.uniform(2.2, 5.0))
        # 眼睛跟着鼠标
        c = self.mapToGlobal(QPoint(self.W // 2, self.H - 50))
        d = QCursor.pos() - c
        dist = math.hypot(d.x(), d.y()) or 1
        k = min(1.0, dist / 260)
        target = QPointF(d.x() / dist * k, d.y() / dist * k)
        self.look += (target - self.look) * 0.15
        if not self.dragging:
            self.tilt *= 0.85
        self._watch_outside_clicks()
        self.update()

    def _watch_outside_clicks(self):
        """气泡开着时，鼠标在气泡和桌宠以外任何地方按下（或在桌宠上按右键/中键）就收起气泡。"""
        for vk in (0x01, 0x02, 0x04):      # 左、右、中键
            down = _mouse_down(vk)
            if down and not self.buttons_down.get(vk) and self.bubble.showing:
                pos = QCursor.pos()
                on_pet = self.frameGeometry().contains(pos)
                on_bubble = self.bubble.frameGeometry().contains(pos)
                if not on_bubble and not (on_pet and vk == 0x01):
                    self.bubble.fade_out()
            self.buttons_down[vk] = down

    def _bubble_anchor(self):
        return self.mapToGlobal(QPoint(26, self.H - 84))

    def moveEvent(self, e):
        self.bubble.point_to(self._bubble_anchor())

    # ---------- 交互 ----------
    def poke(self):
        if self.run.state == "error":
            self.open_problem(*self.run.problem())
            return
        n = self.now()
        self.hop_at, self.hop_happy = n, True
        self.sparkles = [(n + random.uniform(0, 0.08), a + random.uniform(-0.25, 0.25),
                          random.uniform(34, 52), random.uniform(3.5, 6))
                         for a in (-2.6, -2.0, -1.57, -1.1, -0.5)]
        self.bubble.point_to(self._bubble_anchor())
        self.bubble.pop()

    def open_problem(self, targets, node_name):
        """出错时点桌宠：打开织灵主界面并跳到出错节点（单独运行时没有主界面：弹气泡说明，再重跑一轮）"""
        self.hop_at, self.hop_happy = self.now(), False
        self.sparkles = []
        if self.on_open:
            self.bubble.fade_out()
            self.on_open(targets)
            return
        self.bubble.point_to(self._bubble_anchor())
        self.bubble.pop(3.5, ("正在打开织灵…", f"将定位到出错的「{node_name}」（单独运行时没有主界面）", ERR))
        QTimer.singleShot(3600, self.run.restart)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and self.on_open:
            self.bubble.fade_out()
            self.on_open([])

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.press = (e.globalPosition().toPoint(), self.pos())
            self.dragging = False
            self.last_x = e.globalPosition().x()

    def mouseMoveEvent(self, e):
        if not self.press:
            return
        g = e.globalPosition().toPoint()
        if not self.dragging and (g - self.press[0]).manhattanLength() > 4:
            self.dragging = True
            self.setCursor(Qt.ClosedHandCursor)
        if self.dragging:
            self.move(self.press[1] + g - self.press[0])
            vx = e.globalPosition().x() - self.last_x
            self.last_x = e.globalPosition().x()
            self.tilt += (max(-18.0, min(18.0, -vx * 1.6)) - self.tilt) * 0.3

    def mouseReleaseEvent(self, e):
        if e.button() != Qt.LeftButton or not self.press:
            return
        if not self.dragging:
            self.poke()
        self.press, self.dragging = None, False
        self.setCursor(Qt.PointingHandCursor)

    def closeEvent(self, e):
        """Alt+F4 等直接关桌宠：和菜单“关闭”走同一条路（接进主程序时先确认、存好再退出，取消就留着）"""
        if self.on_quit:
            e.ignore()
            self.on_quit()
        else:
            super().closeEvent(e)
            QApplication.quit()

    def contextMenuEvent(self, e):
        self.bubble.fade_out()
        self.make_menu().exec(e.globalPos())

    def make_menu(self):
        m = QMenu(self)
        m.setWindowFlags(m.windowFlags() | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        m.setAttribute(Qt.WA_TranslucentBackground)
        m.setStyleSheet(MENU_QSS)
        m.addAction(QAction("设置", m, triggered=self.open_settings))
        m.addSeparator()
        m.addAction(QAction("关闭", m, triggered=self.on_quit or QApplication.quit))
        return m

    def open_settings(self):
        fn = getattr(self, "on_settings", None)  # 接进主程序时由 hook 指向主程序的设置面板
        if fn:
            fn()
        else:
            QMessageBox.information(self, "织灵 · 设置", "单独运行的演示里没有设置；在织灵里右键桌宠会打开设置面板。")

    # ---------- 画 ----------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        n = self.now()
        cx, ground = self.W / 2, self.H - 14.0
        err = self.run.state == "error"

        # 呼吸 + 点击弹跳（压扁 -> 起跳拉长 -> 落地再压一下）
        breath = math.sin(n * 2.2)
        sx, sy, lift = 1 + 0.018 * breath, 1 - 0.022 * breath, 0.0
        h = n - self.hop_at
        happy = h < 0.9 and self.hop_happy and not err
        if 0 <= h < 0.12:
            q = h / 0.12
            sx, sy = 1 + 0.14 * q, 1 - 0.16 * q
        elif 0.12 <= h < 0.62:
            q = (h - 0.12) / 0.5
            lift = 22 * math.sin(q * math.pi)
            st = 0.12 * math.cos(q * math.pi)
            sx, sy = 1 - st * 0.6, 1 + st
        elif 0.62 <= h < 0.9:
            q = (h - 0.62) / 0.28
            sq = 0.1 * math.sin(q * math.pi) * (1 - q)
            sx, sy = 1 + sq, 1 - sq
        if self.dragging:
            sx, sy, lift = 0.96, 1.07, 4

        # 地上的影子，跳起时变小变淡
        sh = max(0.45, 1 - lift / 40)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(0, 0, 0, int(55 * sh)))
        p.drawEllipse(QPointF(cx, ground + 3), 34 * sh, 6 * sh)

        shake = 0.0
        if err and n % 2.2 < 0.45:          # 出错时一阵阵发抖
            shake = math.sin(n * 55) * 1.8
        p.save()
        p.translate(cx + shake, ground - lift)
        p.rotate(self.tilt)
        p.scale(sx, sy)
        self._draw_body(p, n, happy)
        p.restore()

        self._draw_sparkles(p, n, cx, ground - 60)

    def _draw_body(self, p, n, happy):
        W, H = 88.0, 76.0
        state = self.run.state
        # 身体：圆滚滚、底部略平的团子
        body = QPainterPath()
        body.moveTo(-W / 2, -H * 0.42)
        body.cubicTo(-W / 2, -H * 1.02, W / 2, -H * 1.02, W / 2, -H * 0.42)
        body.cubicTo(W / 2, -H * 0.05, W * 0.36, 0, 0, 0)
        body.cubicTo(-W * 0.36, 0, -W / 2, -H * 0.05, -W / 2, -H * 0.42)

        # 头顶的线：从头顶冒出来弯一下，末端一颗发光的状态珠
        sway = math.sin(n * 1.7) * 5 + self.tilt * -0.4
        top = QPointF(2, -H * 0.86)
        bead = QPointF(16 + sway, -H - 22)
        thread = QPainterPath(top)
        thread.cubicTo(QPointF(-10, -H - 6), QPointF(4 + sway, -H - 26), bead)
        p.setPen(QPen(INK, 2.4, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawPath(thread)
        color = state_color(state)
        pulse = 0.5 + 0.5 * math.sin(n * {"running": 5, "done": 2, "error": 14}.get(state, 1.2))
        gr = 14 + (9 * pulse if state == "error" else 0)
        glow = QRadialGradient(bead, gr)
        c0 = QColor(color)
        c0.setAlpha(int(90 + 70 * pulse) if state != "error" else int(120 + 110 * pulse))
        c1 = QColor(color)
        c1.setAlpha(0)
        glow.setColorAt(0, c0)
        glow.setColorAt(1, c1)
        p.setPen(Qt.NoPen)
        p.setBrush(glow)
        p.drawEllipse(bead, gr, gr)
        if state == "error":                # 报警：灯旁边冒个一跳一跳的“!”
            bang = bead + QPointF(17, -8 - 3 * abs(math.sin(n * 7)))
            p.setPen(QPen(ERR, 3.2, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(bang, bang + QPointF(-1.2, 9))
            p.setPen(Qt.NoPen)
            p.setBrush(ERR)
            p.drawEllipse(bang + QPointF(-1.8, 14), 1.9, 1.9)
        p.setPen(QPen(INK, 2))
        p.setBrush(color)
        p.drawEllipse(bead, 5.5, 5.5)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(255, 255, 255, 200))
        p.drawEllipse(bead + QPointF(-1.8, -1.8), 1.6, 1.6)

        g = QRadialGradient(QPointF(-W * 0.18, -H * 0.72), W * 0.9)
        g.setColorAt(0, BODY_HI)
        g.setColorAt(1, BODY_LO)
        p.setPen(QPen(INK, 2.6))
        p.setBrush(QBrush(g))
        p.drawPath(body)

        # 肚子上一道缝线（“织”）
        pen = QPen(QColor("#b9bdcb"), 1.8, Qt.CustomDashLine, Qt.RoundCap)
        pen.setDashPattern([2.2, 2.4])
        p.setPen(pen)
        seam = QPainterPath(QPointF(-W * 0.28, -H * 0.16))
        seam.quadTo(QPointF(0, -H * 0.04), QPointF(W * 0.28, -H * 0.16))
        p.drawPath(seam)

        # 腮红
        p.setPen(Qt.NoPen)
        p.setBrush(CHEEK)
        p.drawEllipse(QPointF(-25, -H * 0.42), 7.5, 4.5)
        p.drawEllipse(QPointF(25, -H * 0.42), 7.5, 4.5)

        # 眼睛
        ey = -H * 0.56
        lx, ly = self.look.x() * 4, self.look.y() * 3
        if state == "error":
            self._draw_sad_eyes(p, n, ey)
        elif happy:
            p.setPen(QPen(INK, 2.8, Qt.SolidLine, Qt.RoundCap))
            p.setBrush(Qt.NoBrush)
            for ex in (-14, 14):
                arc = QPainterPath(QPointF(ex - 6, ey + 2))
                arc.quadTo(QPointF(ex, ey - 7), QPointF(ex + 6, ey + 2))
                p.drawPath(arc)
        elif self.dragging:
            p.setPen(QPen(INK, 2.6, Qt.SolidLine, Qt.RoundCap))
            for ex, d in ((-14, 1), (14, -1)):
                p.drawLine(QPointF(ex - 5 * d, ey - 5), QPointF(ex + 4 * d, ey))
                p.drawLine(QPointF(ex + 4 * d, ey), QPointF(ex - 5 * d, ey + 5))
        else:
            b = n - self.blink_at
            open_k = 1.0 if b > 0.14 or b < 0 else abs(b - 0.07) / 0.07
            open_k = max(0.12, open_k)
            for ex in (-14, 14):
                c = QPointF(ex + lx, ey + ly)
                p.setPen(Qt.NoPen)
                p.setBrush(INK)
                p.drawEllipse(c, 5.2, 7.2 * open_k)
                if open_k > 0.6:
                    p.setBrush(QColor(255, 255, 255, 230))
                    p.drawEllipse(c + QPointF(-1.6, -2.6), 1.9, 1.9)

        # 嘴：平时小小的 ω，开心时张开，出错时撇下来还发抖
        p.setPen(QPen(INK, 2.2, Qt.SolidLine, Qt.RoundCap))
        my = -H * 0.38
        if state == "error":
            p.setBrush(Qt.NoBrush)
            wob = math.sin(n * 9) * 0.8
            frown = QPainterPath(QPointF(-6, my + 3))
            frown.quadTo(QPointF(0, my - 4 + wob), QPointF(6, my + 3))
            p.drawPath(frown)
        elif happy:
            p.setBrush(QColor("#ff8a8a"))
            mouth = QPainterPath(QPointF(-5, my - 1))
            mouth.quadTo(QPointF(0, my + 9), QPointF(5, my - 1))
            mouth.closeSubpath()
            p.drawPath(mouth)
        else:
            p.setBrush(Qt.NoBrush)
            w = QPainterPath(QPointF(-5, my - 1))
            w.quadTo(QPointF(-2.5, my + 3), QPointF(0, my))
            w.quadTo(QPointF(2.5, my + 3), QPointF(5, my - 1))
            p.drawPath(w)

    def _draw_sad_eyes(self, p, n, ey):
        """难过：八字眉、水汪汪的眼睛，两条眼泪直直流下来（不动）。"""
        p.setPen(QPen(INK, 2.4, Qt.SolidLine, Qt.RoundCap))
        for ex, d in ((-14, -1), (14, 1)):
            p.drawLine(QPointF(ex - 6 * d, ey - 13), QPointF(ex + 5 * d, ey - 9))
        streak = QColor(TEAR)
        streak.setAlpha(190)
        for ex in (-14, 14):
            c = QPointF(ex, ey + 1)
            p.setPen(Qt.NoPen)
            p.setBrush(INK)
            p.drawEllipse(c, 5.0, 6.0)
            p.setBrush(QColor(255, 255, 255, 230))
            p.drawEllipse(c + QPointF(-1.5, -2.2), 1.8, 1.8)
            p.drawEllipse(c + QPointF(1.6, 2.0), 1.1, 1.1)
            # 眼泪：眼睛下面一条笔直流下去的线
            p.setPen(QPen(streak, 3.6, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(ex, ey + 8), QPointF(ex, ey + 34))

    def _draw_sparkles(self, p, n, cx, cy):
        for start, ang, dist, size in self.sparkles:
            q = (n - start) / 0.75
            if not 0 <= q <= 1:
                continue
            r = dist * _ease_out_back(min(1.0, q * 1.3))
            pos = QPointF(cx + math.cos(ang) * r, cy + math.sin(ang) * r)
            s = size * (1 - q * 0.6)
            col = QColor(state_color(self.run.state))
            col.setAlpha(int(255 * (1 - q) ** 0.7))
            star = QPainterPath(QPointF(pos.x(), pos.y() - s * 1.6))
            for dx, dy in ((0.35, -0.35), (1.6, 0), (0.35, 0.35), (0, 1.6), (-0.35, 0.35), (-1.6, 0), (-0.35, -0.35),
                           (0, -1.6)):
                star.lineTo(pos.x() + dx * s, pos.y() + dy * s)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawPath(star)


def _demo_shots(app, pet, out):
    """PET_DEMO_SHOTS=目录：自动点一下、开右键菜单，把屏幕上宠物周围截下来，用来自查效果。"""
    os.makedirs(out, exist_ok=True)
    screen = QGuiApplication.primaryScreen()

    def grab(name):
        r = QRect(pet.x() - 260, pet.y() - 140, pet.width() + 300, pet.height() + 170)
        screen.grabWindow(0, r.x(), r.y(), r.width(), r.height()).save(os.path.join(out, name + ".png"))

    seq = [(900, lambda: grab("1_idle")), (1000, pet.poke)]
    for i, dt in enumerate((40, 120, 260, 380, 560, 900)):
        seq.append((1000 + dt, lambda i=i: grab(f"2_click_{i}")))
    seq.append((4200, pet.poke))
    seq.append((5600, lambda: grab("3_bubble_later")))
    menu = {}

    def open_menu():
        menu["m"] = pet.make_menu()
        menu["m"].popup(pet.mapToGlobal(QPoint(pet.width() // 2 + 10, pet.height() // 2)))

    seq.append((10500, open_menu))
    seq.append((11000, lambda: grab("4_menu")))
    seq.append((11200, lambda: menu["m"].close()))

    def force_error():
        pet.run.round, pet.run.step, pet.run.done_n, pet.run.failed = 1, 3, 2, True

    seq.append((11400, force_error))
    seq.append((12600, lambda: grab("5_error_a")))
    seq.append((13000, lambda: grab("5_error_b")))
    seq.append((13200, pet.poke))
    seq.append((13700, lambda: grab("6_error_click")))
    seq.append((13800, app.quit))

    # 一步接一步地排：启动慢时也不会把几步挤到同一刻
    def run_from(i, prev_ms):
        if i < len(seq):
            ms, fn = seq[i]
            QTimer.singleShot(ms - prev_ms, lambda: (fn(), run_from(i + 1, ms)))

    QTimer.singleShot(1500, lambda: run_from(0, 0))


def main():
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    pet = Pet()
    pet.show()
    if os.environ.get("PET_DEMO_SHOTS"):
        _demo_shots(app, pet, os.environ["PET_DEMO_SHOTS"])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
