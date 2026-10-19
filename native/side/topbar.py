"""顶栏（网页版 .bar）：左边品牌（点了回首页），右边选生成用的模型、“配置 AI”（打开引导重新配置）和“设置”（桌宠、杂项、自定义；
“生成后实跑检查”开关搬到了设置的“杂项”里）。
需求在首页“你想让它做什么？”里写（原来顶栏中间也有一个一句话输入框，和首页重复，已去掉）；生成中的“停止生成”在画布标题行。"""
from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QWidget

from ..theme import C, FS, R, qc, ui_font
from .modelpick import ModelPicker
from .widgets import brand_pixmap

QSS = f"""
QWidget#bar {{ background: {C['surface']}; }}
QPushButton#brand {{ background: transparent; border: 0; border-radius: {R['md']}px; padding: 2px 8px 2px 2px;
  font-size: 17px; font-weight: 650; letter-spacing: 0.3px; text-align: left; }}
QPushButton#brand:hover {{ background: {C['hover']}; }}
"""


class Switch(QWidget):
    """.switch：32×18 的拨动开关加一段文字，点哪儿都切换"""
    toggled = Signal(bool)

    def __init__(self, text, checked=False, parent=None):
        super().__init__(parent)
        self._text = text
        self._on = checked
        self.setFont(ui_font(FS["md"]))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName(text)

    def isChecked(self):
        return self._on

    def setChecked(self, on):
        if bool(on) != self._on:
            self._on = bool(on)
            self.update()
            self.toggled.emit(self._on)

    def sizeHint(self):
        fm = QFontMetrics(self.font())
        return QSize(32 + 8 + fm.horizontalAdvance(self._text) + 4, max(22, fm.height() + 4))

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.setChecked(not self._on)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.setChecked(not self._on)
        else:
            super().keyPressEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        y = (self.height() - 18) / 2
        track = QRectF(0, y, 32, 18)
        if self.hasFocus():
            p.setPen(qc("focus"))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(track.adjusted(-2, -2, 2, 2), 11, 11)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(qc("led_ok") if self._on else qc("line_strong"))
        p.drawRoundedRect(track, 9, 9)
        p.setBrush(QColor(0, 0, 0, 64))
        knob_x = 2 + (14 if self._on else 0)
        p.drawEllipse(QRectF(knob_x, y + 3, 14, 14))  # 轻微阴影
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QRectF(knob_x, y + 2, 14, 14))
        p.setPen(qc("ink_2"))
        p.setFont(self.font())
        p.drawText(QRectF(40, 0, self.width() - 40, self.height()), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)


class TopBar(QWidget):
    def __init__(self, ctrl):
        super().__init__()
        self.ctrl = ctrl
        self.setObjectName("bar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet(QSS)
        row = QHBoxLayout(self)
        row.setContentsMargins(20, 10, 20, 10)
        row.setSpacing(20)

        self.brand = QPushButton("织灵", objectName="brand")
        self.brand.setIcon(brand_pixmap(28))
        self.brand.setIconSize(QSize(28, 28))
        self.brand.setToolTip("回到首页")
        self.brand.setCursor(Qt.CursorShape.PointingHandCursor)
        self.brand.clicked.connect(ctrl.go_home)
        row.addWidget(self.brand)

        row.addStretch(1)

        self.model = ModelPicker(ctrl)
        row.addWidget(self.model)
        self.setup = QPushButton("配置 AI")
        self.setup.setProperty("kind", "ghost")
        self.setup.setProperty("small", True)
        self.setup.setToolTip("重新选择用云端 AI、本地模型，或检测电脑并安装本地模型")
        self.setup.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setup.clicked.connect(self._open_setup)
        row.addWidget(self.setup)

        self.settings_btn = QPushButton("设置")
        self.settings_btn.setProperty("kind", "ghost")
        self.settings_btn.setProperty("small", True)
        self.settings_btn.setToolTip("桌宠、生成后实跑检查、思考模型、自定义云端服务商")
        self.settings_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_btn.clicked.connect(self._open_settings)
        row.addWidget(self.settings_btn)

        ctrl.gen_changed.connect(self._sync)
        self._sync()

    def _sync(self):
        self.setup.setEnabled(not self.ctrl.generating)  # 生成中不能换模型

    def _open_setup(self):
        w = self.window()
        if hasattr(w, "open_setup"):
            w.open_setup("B")

    def _open_settings(self):
        w = self.window()
        if hasattr(w, "open_settings"):
            w.open_settings("pet")
