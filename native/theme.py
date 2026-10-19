"""界面的颜色、字号、圆角、间距（与原网页界面 web/src/styles.css 的变量一致），以及全局样式表。
组件里不写零散的颜色数值，一律从 C 取；控件用动态属性挑样式，例如 btn.setProperty("kind", "primary")。

按钮 kind：primary（浅色实底）、ghost（透明）、danger；小按钮用 setProperty("small", True)（size 是 Qt 自带属性，不能用）。
标签 role：muted（次要文字）、title、notice、warn、bad、h2（分组标题）。
面板 role：surface（卡片）、rail（侧栏）。"""
from PySide6.QtGui import QColor, QFont, QPalette

C = {
    "canvas": "#1c1c1e", "board": "#3f3e3f", "board_dot": "#6e6e6e", "surface": "#26262a", "rail": "#202023",
    "hover": "#323237", "ink": "#ececf0", "on_ink": "#18181b", "ink_2": "#c3c3cb", "muted": "#9b9ba4",
    "line": "#36363c", "line_strong": "#4f4f57", "grid": "#4f4f57", "focus": "#6ea2f0",
    "warn_bg": "rgba(240,169,74,0.12)", "warn_ink": "#f0bf6e", "bad_ink": "#f08a8a", "issue_ink": "#f0b0b0",
    # 模块类别（面板顶部色条）
    "cat_fetch": "#5b8fd0", "cat_think": "#9b80d2", "cat_logic": "#d3a23c", "cat_process": "#45b2a4",
    "cat_output": "#d77659", "cat_flow": "#8a95a1", "cat_ask": "#d2679a",
    # 端口类型（插孔颜色）
    "t_string": "#6f9be0", "t_number": "#e08a4d", "t_boolean": "#b97ed6", "t_list": "#5bb585", "t_any": "#9aa4ae",
    # 状态灯
    "led_off": "#57575f", "led_run": "#f0a94a", "led_ok": "#3fb57a", "led_bad": "#e45c5c",
}
R = {"xs": 4, "sm": 6, "md": 8, "lg": 10, "xl": 14}
S = {1: 4, 2: 8, 3: 12, 4: 16, 5: 20, 6: 24}
FS = {"xs": 11, "sm": 12, "md": 13, "base": 14, "lg": 16, "xl": 20}
FONT = ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"]
MONO = ["Cascadia Mono", "Consolas", "Microsoft YaHei UI"]

# 模块类别（中文，来自模块描述的 category）→ 颜色键
CATEGORY = {"获取": "cat_fetch", "思考": "cat_think", "逻辑": "cat_logic", "处理": "cat_process", "交互": "cat_ask",
            "输出": "cat_output"}


def qc(key: str) -> QColor:
    """颜色键 → QColor（支持 rgba(...) 写法）"""
    v = C.get(key, key)
    if v.startswith("rgba("):
        r, g, b, a = [x.strip() for x in v[5:-1].split(",")]
        return QColor(int(r), int(g), int(b), int(float(a) * 255))
    return QColor(v)


def type_color(t) -> str:
    """端口类型 → 插孔颜色键（与 nodes.jsx 的 typeClass 相同）"""
    t = t if isinstance(t, str) else "any"
    if t.startswith("list<"):
        return "t_list"
    if t in ("integer", "number"):
        return "t_number"
    if t == "boolean":
        return "t_boolean"
    if t in ("string", "file"):
        return "t_string"
    return "t_any"


def ui_font(size=FS["base"], weight=QFont.Weight.Normal) -> QFont:
    f = QFont()
    f.setFamilies(FONT)
    f.setPixelSize(size)
    f.setWeight(weight)
    return f


def mono_font(size=FS["sm"]) -> QFont:
    f = QFont()
    f.setFamilies(MONO)
    f.setPixelSize(size)
    return f


def palette() -> QPalette:
    p = QPalette()
    p.setColor(QPalette.ColorRole.Window, qc("canvas"))
    p.setColor(QPalette.ColorRole.WindowText, qc("ink"))
    p.setColor(QPalette.ColorRole.Base, qc("rail"))
    p.setColor(QPalette.ColorRole.AlternateBase, qc("surface"))
    p.setColor(QPalette.ColorRole.Text, qc("ink"))
    p.setColor(QPalette.ColorRole.Button, qc("surface"))
    p.setColor(QPalette.ColorRole.ButtonText, qc("ink"))
    p.setColor(QPalette.ColorRole.Highlight, qc("focus"))
    p.setColor(QPalette.ColorRole.HighlightedText, qc("on_ink"))
    p.setColor(QPalette.ColorRole.ToolTipBase, qc("surface"))
    p.setColor(QPalette.ColorRole.ToolTipText, qc("ink"))
    p.setColor(QPalette.ColorRole.PlaceholderText, qc("muted"))
    p.setColor(QPalette.ColorRole.Link, qc("focus"))
    return p


_ASSET = (__import__("pathlib").Path(__file__).resolve().parent / "assets").as_posix()


def qss() -> str:
    c = C
    return f"""
* {{ font-family: "Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI"; font-size: {FS['base']}px; color: {c['ink']}; }}
QMainWindow, QWidget#root {{ background: {c['canvas']}; }}
QToolTip {{ background: {c['surface']}; color: {c['ink']}; border: 1px solid {c['line_strong']}; padding: 6px 8px; border-radius: {R['sm']}px; }}

QWidget[role="surface"] {{ background: {c['surface']}; }}
QWidget[role="rail"] {{ background: {c['rail']}; }}
QFrame[role="card"] {{ background: {c['surface']}; border: 1px solid {c['line']}; border-radius: {R['lg']}px; }}
QFrame[role="hline"] {{ background: {c['line']}; max-height: 1px; min-height: 1px; border: 0; }}

QLabel[role="muted"] {{ color: {c['muted']}; }}
QLabel[role="h1"] {{ font-size: {FS['xl']}px; font-weight: 650; }}
QLabel[role="h2"] {{ color: {c['muted']}; font-size: {FS['sm']}px; font-weight: 600; letter-spacing: 1px; }}
QLabel[role="h3"] {{ font-size: {FS['lg']}px; font-weight: 600; }}
QLabel[role="notice"] {{ color: {c['ink_2']}; font-size: {FS['md']}px; }}
QLabel[role="warn"] {{ color: {c['warn_ink']}; background: {c['warn_bg']}; border-radius: {R['sm']}px; padding: 4px 8px; font-size: {FS['md']}px; }}
QLabel[role="bad"] {{ color: {c['bad_ink']}; }}
QLabel[role="mono"] {{ font-family: "Cascadia Mono", Consolas; font-size: {FS['sm']}px; }}

QPushButton {{ background: {c['surface']}; border: 1.5px solid {c['line']}; border-radius: {R['md']}px; padding: 8px 16px; }}
QPushButton:hover {{ border-color: {c['line_strong']}; }}
QPushButton:disabled {{ color: {c['muted']}; border-color: {c['line']}; }}
QPushButton:focus {{ border-color: {c['focus']}; }}
QPushButton[kind="primary"] {{ background: {c['ink']}; border-color: {c['ink']}; color: {c['on_ink']}; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: #ffffff; border-color: #ffffff; }}
QPushButton[kind="primary"]:disabled {{ background: {c['line_strong']}; border-color: {c['line_strong']}; color: {c['muted']}; }}
QPushButton[kind="danger"] {{ background: {c['led_bad']}; border-color: {c['led_bad']}; color: #ffffff; font-weight: 600; }}
QPushButton[kind="ghost"] {{ background: transparent; }}
QPushButton[kind="ghost"]:hover {{ background: {c['hover']}; border-color: {c['line']}; }}
QPushButton[small="true"] {{ padding: 4px 10px; font-size: {FS['md']}px; border-radius: {R['sm']}px; }}
QPushButton[kind="item"] {{ text-align: left; background: transparent; border: 0; border-radius: {R['md']}px; padding: 6px 10px; }}
QPushButton[kind="item"]:hover {{ background: {c['hover']}; }}
QPushButton[kind="item"][on="true"] {{ background: {c['hover']}; }}
QPushButton[kind="tab"] {{ background: transparent; border: 0; border-bottom: 2px solid transparent; border-radius: 0; padding: 10px 12px; color: {c['muted']}; }}
QPushButton[kind="tab"]:hover {{ color: {c['ink']}; }}
QPushButton[kind="tab"][on="true"] {{ color: {c['ink']}; border-bottom-color: {c['ink']}; font-weight: 600; }}
QToolButton {{ background: transparent; border: 1.5px solid transparent; border-radius: {R['sm']}px; padding: 4px; }}
QToolButton:hover {{ background: {c['hover']}; }}
QToolButton:checked {{ background: {c['hover']}; }}

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{
  background: {c['rail']}; border: 1.5px solid {c['line']}; border-radius: {R['md']}px; padding: 6px 10px;
  selection-background-color: {c['focus']}; selection-color: {c['on_ink']};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{
  border-color: {c['focus']}; background: {c['surface']};
}}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled {{ color: {c['muted']}; }}
QComboBox::drop-down {{ border: 0; width: 22px; }}
QComboBox::down-arrow {{ image: url({_ASSET}/chevron-down.svg); width: 12px; height: 12px; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['line_strong']}; selection-background-color: {c['hover']}; selection-color: {c['ink']}; outline: 0; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 30px; height: 16px; border-radius: 8px; background: {c['line_strong']}; }}
QCheckBox::indicator:checked {{ background: {c['led_ok']}; }}

QMenu {{ background: {c['surface']}; border: 1px solid {c['line_strong']}; border-radius: {R['md']}px; padding: 4px; }}
QMenu::item {{ padding: 6px 18px; border-radius: {R['sm']}px; }}
QMenu::item:selected {{ background: {c['hover']}; }}
QMenu::separator {{ height: 1px; background: {c['line']}; margin: 4px 6px; }}

QScrollArea {{ border: 0; background: transparent; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {c['line_strong']}; border-radius: 3px; min-height: 30px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: {c['line_strong']}; border-radius: 3px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: {c['line']}; }}

QDialog#confirm {{ background: {c['surface']}; border: 1px solid {c['line_strong']}; border-radius: {R['xl']}px; }}
"""
