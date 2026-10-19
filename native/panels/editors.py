"""编辑用的面板部件（移植自 web/src/editor.jsx）：改参数、节点名称、循环最多项数、看/删连线、工作流自己的输入输出、名称说明，以及程序（JSON）视图。

和网页版一样：输入框里的文字先只记在框里，离开输入框（或按 Enter）时才提交改动；
控制器每次变化都会让面板重画，重画时只有程序里的值真的变了（撤销、重做、提交成功）才把框里的文字换成程序里的，
所以正在打的字不会被冲掉。每个部件的 sync(...) 对应 React 组件拿到新 props。"""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QCheckBox, QFrame, QWidget

from ..logic._js import UNSET, is_finite, js_number, js_str, json_stringify
from ..logic.form import parse_value, to_raw
from ..logic.labels import type_label
from ..theme import C
from .common import NAME_RE, CommitLine, CommitText, button, clear, code_html, combo, esc, hbox, label, set_combo, vbox


class FieldBox(QWidget):
    """一个输入项（.field）：标题行（可带右侧的类型说明）、输入控件、出错说明"""

    def __init__(self, title, type_text=None, type_tip=None, gap=14, label_gap=6):
        super().__init__()
        self.lay = vbox(self, spacing=0, margins=(0, 0, 0, gap))
        row = hbox(spacing=7, margins=(0, 0, 0, label_gap))
        self.title_row = row
        self.title = label(title, cls="flabel", rich=True)
        row.addWidget(self.title, 1)
        if type_text is not None:
            self.type = label(type_text, cls="type", wrap=False, tip=type_tip)
            row.addWidget(self.type, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.lay.addLayout(row)
        self.body = vbox(spacing=0)
        self.lay.addLayout(self.body)
        self.error = label("", cls="field-error")
        self.error.setContentsMargins(0, 4, 0, 0)
        self.error.hide()
        self.lay.addWidget(self.error)

    def set_error(self, text):
        self.error.setText(text or "")
        self.error.setVisible(bool(text))


# ---------- 模块节点的参数 ----------
class ParamEditor(QWidget):
    """接了线的显示来源；没接线的可以填固定值，留空表示用默认值。on_set(端口, 值)，值为 UNSET 表示删掉固定值"""

    def __init__(self, module, config, incoming, on_set):
        super().__init__()
        self.lay = vbox(self)
        self.params = {}
        self.module = module
        for k, p in (module.get("inputs") or {}).items():
            w = Param(k, p)
            self.params[k] = w
            self.lay.addWidget(w)
        self.sync(config, incoming, on_set)

    def sync(self, config, incoming, on_set):
        config = config or {}
        for k, w in self.params.items():
            w.sync(config.get(k), k in config, incoming.get(k), lambda v, k=k: on_set(k, v))


class Param(FieldBox):
    def __init__(self, name, decl):
        t = decl.get("type") or "any"
        # 标题用模块说明里的中文短名（和画布上的插孔一样），程序里的名字小字跟在后面
        title = (f'<b>{esc(decl["label"])}</b>&nbsp;&nbsp;<span style="color:{C["muted"]};">{code_html(name, 12)}</span>'
                 if isinstance(decl.get("label"), str) and decl["label"] else code_html(name, 14))
        super().__init__(title, type_label(t), t, gap=12, label_gap=2)
        self.name, self.decl, self.t = name, decl, t
        if decl.get("description"):
            self.body.addWidget(label(decl["description"], cls="param-desc"))
            self.body.addSpacing(4)
        self.ctl_host = vbox()
        self.body.addLayout(self.ctl_host)
        required = decl.get("required") is not False and "default" not in decl
        self.placeholder = (f"默认：{to_raw(t, decl['default'])}" if "default" in decl and decl["default"] is not None
                            else "必填：填固定值或接一条线" if required else "可不填")
        self.value, self.has, self.frm, self.on_set = None, False, None, None
        self.shown = object()  # 第一次 sync 一定写入
        self.raw = None
        self.ctl = None
        self.mode = None

    # 对应 multi：列表、对象、任意类型，或文字超过 40 字时用多行框
    def _multi(self):
        t = self.t
        return (t.startswith("list<") or t in ("object", "any")
                or (t == "string" and len(js_str(self.raw if self.raw is not None and self.raw is not False else "")) > 40))

    def sync(self, value, has, frm, on_set):
        self.value, self.has, self.on_set = value, has, on_set
        shown = to_raw(self.t, value) if has else (None if self.t == "boolean" else "")
        reset = shown != self.shown or type(shown) is not type(self.shown)
        if reset:  # 撤销、重做后跟着程序走
            self.shown = shown
            self.raw = shown
            self.set_error("")
        mode = "wired" if frm else "bool" if self.t == "boolean" else "multi" if self._multi() else "line"
        if mode != self.mode or frm != self.frm:
            self.frm = frm
            self._build(mode)
        elif reset:
            self._show_raw()

    def _build(self, mode, keep_focus=False):
        old = self.ctl
        if old is not None and hasattr(old, "on_commit"):
            old.on_commit = None  # 换控件引起的失焦不算离开
        self.mode = mode
        clear(self.ctl_host)
        if mode == "wired":
            self.ctl = label(f"由连线提供：来自 {code_html(self.frm)}", cls="wired", rich=True)
        elif mode == "bool":
            self.ctl = combo([("", self.placeholder), (True, "是"), (False, "否")], self._bool_key(), self._pick)
        elif mode == "multi":
            self.ctl = CommitText(js_str(self.raw if self.raw is not None else ""), self.placeholder, self.commit, rows=2)
            self.ctl.textChanged.connect(self._typed)
        else:
            self.ctl = CommitLine(js_str(self.raw if self.raw is not None else ""), self.placeholder, self.commit)
            self.ctl.textChanged.connect(self._typed)
        self.ctl_host.addWidget(self.ctl)
        if keep_focus and old is not None:
            # 文字超过 40 字换成多行框（或删短了换回单行框）：焦点和光标跟过去，接着打字
            self.ctl.setFocus()
            if hasattr(self.ctl, "moveCursor"):
                c = self.ctl.textCursor()
                c.movePosition(c.MoveOperation.End)
                self.ctl.setTextCursor(c)
            else:
                self.ctl.end(False)

    def _bool_key(self):
        return "" if self.raw is None else bool(self.raw)

    def _show_raw(self):
        if self.mode == "bool":
            set_combo(self.ctl, self._bool_key())
        elif self.mode in ("line", "multi"):
            self.ctl.set_text_keep(js_str(self.raw if self.raw is not None else ""))

    def _typed(self, *_):
        self.raw = self.ctl.text()
        if ("multi" if self._multi() else "line") != self.mode:
            QTimer.singleShot(0, self._switch)  # 不在控件自己的信号处理里换掉它

    def _switch(self):
        want = "multi" if self._multi() else "line"
        if self.mode in ("line", "multi") and want != self.mode:
            self._build(want, keep_focus=True)

    def _pick(self, v):
        v = None if v == "" else v
        self.raw = v
        self.commit(v)

    def commit(self, nxt=None):
        """留空：去掉固定值（有的话）；填得不对：显示原因，不提交；值没变：不提交"""
        if nxt is None or (self.t != "boolean" and js_str(nxt).strip() == ""):
            self.set_error("")
            if self.has:
                self.on_set(UNSET)
            return
        try:
            v = parse_value(self.t, nxt)
        except ValueError as e:
            self.set_error(str(e))
            return
        self.set_error("")
        if not self.has or json_stringify(v) != json_stringify(self.value):
            self.on_set(v)


# ---------- 节点名称、循环最多项数 ----------
class LabelEditor(FieldBox):
    """节点名称：留空用模块名"""

    def __init__(self, label_, fallback, on_set):
        super().__init__("名称", gap=12)
        self.label = object()
        self.edit = CommitLine("", fallback, self.commit)
        self.body.addWidget(self.edit)
        self.sync(label_, fallback, on_set)

    def sync(self, label_, fallback, on_set):
        self.on_set = on_set
        self.edit.setPlaceholderText(fallback or "")
        if label_ != self.label:
            self.label = label_
            self.edit.set_text_keep(label_ or "")

    def commit(self, v):
        if (v or "").strip() != (self.label or ""):
            self.on_set(v)


class MaxItemsEditor(FieldBox):
    def __init__(self, value, on_set):
        super().__init__("最多处理多少项", gap=12)
        self.value = object()
        self.edit = CommitLine("", "", self.commit)
        self.edit.setInputMethodHints(Qt.InputMethodHint.ImhDigitsOnly)
        self.body.addWidget(self.edit)
        self.sync(value, on_set)

    def sync(self, value, on_set):
        self.on_set = on_set
        if value != self.value or type(value) is not type(self.value):
            self.value = value
            self.edit.set_text_keep(js_str(value) if value is not None else "")

    def commit(self, v):
        n = js_number(v)
        if not (is_finite(n) and float(n).is_integer()) or n < 1:
            self.set_error("需要大于 0 的整数")
            return
        self.set_error("")
        if n != self.value:
            self.on_set(int(n))


# ---------- 选中的连线 ----------
class EdgeDetail(QWidget):
    def __init__(self, edge, on_delete):
        super().__init__()
        lay = vbox(self, spacing=0)
        control = bool(edge.get("control"))
        lay.addWidget(label("控制线" if control else "连线", cls="h3"))
        lay.addSpacing(12)
        lay.addWidget(label("条件分支走到这一路时，才运行它指向的节点。" if control else "把左边节点产生的值，送到右边节点的输入。", cls="desc"))
        lay.addSpacing(12)
        lay.addWidget(label(f"{code_html(edge.get('from'))} → {code_html(edge.get('to'))}", rich=True, select=True))
        lay.addSpacing(12)
        scope = edge.get("scope") or []
        if scope:
            lay.addWidget(label(f"在循环 {' > '.join(scope)} 里", cls="muted"))
        if on_delete:
            lay.addSpacing(12)
            lay.addWidget(button("删除这条线", kind="danger", wide=True, on=on_delete))
        lay.addSpacing(8)
        lay.addWidget(label("也可以选中后按 Delete 删除。", cls="hint"))


# ---------- 工作流自己的输入、输出 ----------
TYPES = ["string", "integer", "number", "boolean", "object", "any", "file",
         "list<string>", "list<integer>", "list<number>", "list<boolean>", "list<object>", "list<any>"]


class PortsEditor(QWidget):
    """side：inputs / outputs；ports：程序里的声明；ops：控制器的 port_ops()"""

    def __init__(self, side, ports, ops):
        super().__init__()
        self.side = side
        self.lay = vbox(self, spacing=12)
        self.empty = label(f"还没有{'输入' if side == 'inputs' else '输出'}。", cls="muted")
        self.lay.addWidget(self.empty)
        self.cards = {}
        self.list = vbox(spacing=12)
        self.lay.addLayout(self.list)
        self.ops = ops
        self.add = button(f"添加{'一个输入' if side == 'inputs' else '一个输出'}", small=True, wide=True, on=lambda: self.ops["add"]())
        self.lay.addWidget(self.add)
        self.sync(ports, ops)

    def sync(self, ports, ops):
        self.ops = ops
        ports = ports or {}
        names = list(ports)
        self.empty.setVisible(not names)
        # 按名字对应卡片（改名就是一张新卡片，和网页版的 key 相同）
        for n in list(self.cards):
            if n not in ports:
                self.cards.pop(n).deleteLater()
        for i, n in enumerate(names):
            card = self.cards.get(n)
            if card is None:
                card = self.cards[n] = PortCard(self.side, n, ports[n])
            if self.list.indexOf(card) != i:
                self.list.insertWidget(i, card)
            card.sync(ports[n], names, ops)


class PortCard(QFrame):
    def __init__(self, side, name, decl):
        super().__init__()
        self.setProperty("cls", "box")
        self.side, self.name = side, name
        self.lay = vbox(self, spacing=0, margins=(12, 12, 12, 12))
        self.decl, self.names, self.ops = decl, [], None
        self._desc_seen = object()
        self._def_seen = object()
        self._type_seen = None

        ref = "$in" if side == "inputs" else "$out"
        self.f_name = FieldBox("名字", f"连线里用它：{ref}.{name}", gap=8)
        self.name_edit = CommitLine(name, "", self.rename)
        self.f_name.body.addWidget(self.name_edit)
        self.lay.addWidget(self.f_name)

        self.f_type = FieldBox("类型", gap=8)
        self.type_host = vbox()
        self.f_type.body.addLayout(self.type_host)
        self.lay.addWidget(self.f_type)

        self.f_desc = FieldBox("说明", gap=8)
        self.desc_edit = CommitLine("", "给使用者看的说明，例如：要总结的网址", self._commit_desc, enter=False)
        self.f_desc.body.addWidget(self.desc_edit)
        self.lay.addWidget(self.f_desc)

        if side == "inputs":
            row = hbox(spacing=6, margins=(0, 0, 0, 8))
            self.optional = QCheckBox()
            self.optional.clicked.connect(lambda on: self.ops["fields"](self.name, {"required": False if on else UNSET}))
            self.optional_text = label("", cls="check-row", wrap=False)
            row.addWidget(self.optional)
            row.addWidget(self.optional_text, 1)
            self.lay.addLayout(row)
            self.f_def = FieldBox("默认值", gap=8)
            self.def_host = vbox()
            self.f_def.body.addLayout(self.def_host)
            self.lay.addWidget(self.f_def)
            self.def_ctl = None
            self._def_kind = None

        self.remove = button(f"删除{'这个输入' if side == 'inputs' else '这个输出'}", kind="danger", small=True,
                             on=lambda: self.ops["remove"](self.name))
        self.lay.addWidget(self.remove, 0, Qt.AlignmentFlag.AlignLeft)

    def sync(self, decl, names, ops):
        self.decl, self.names, self.ops = decl, names, ops
        t = decl.get("type")
        if t != self._type_seen:
            self._type_seen = t
            clear(self.type_host)
            opts = TYPES if t in TYPES else [t, *TYPES]
            self.type_box = combo([(x, f"{type_label(x)}（{x}）") for x in opts], t, lambda v: self.ops["type"](self.name, v))
            self.type_host.addWidget(self.type_box)
        if decl.get("description") != self._desc_seen:
            self._desc_seen = decl.get("description")
            self.desc_edit.set_text_keep(decl.get("description") or "")
        if self.side != "inputs":
            return
        has_default = "default" in decl
        self.optional.setChecked(decl.get("required") is False or has_default)
        self.optional.setEnabled(not has_default)
        self.optional_text.setText("可以不填" + ("（有默认值）" if has_default else ""))
        key = (has_default, json_stringify(decl.get("default")) if has_default else None, t)
        if key == self._def_seen:
            return
        self._def_seen = key
        want = "bool" if t == "boolean" else "line"
        if self._def_kind != want:  # 布尔和其他类型用的控件不同
            self._def_kind = want
            clear(self.def_host)
            if want == "bool":
                self.def_ctl = combo([("", "没有默认值"), (True, "是"), (False, "否")], None,
                                     lambda v: self.ops["fields"](self.name, {"default": UNSET if v == "" else v}))
            else:
                self.def_ctl = CommitLine("", "没有默认值", self._commit_default)
            self.def_host.addWidget(self.def_ctl)
        if want == "bool":
            set_combo(self.def_ctl, bool(decl["default"]) if has_default else "")
        else:
            self.def_ctl.set_text_keep(js_str(to_raw(t, decl["default"])) if has_default else "")

    def rename(self, v):
        if v == self.name:
            return self.f_name.set_error("")
        if not NAME_RE.fullmatch(v):
            return self.f_name.set_error("小写英文字母开头，后面跟小写字母、数字或下划线")
        if v in self.names:
            return self.f_name.set_error(f"已经有叫 {v} 的了")
        self.f_name.set_error("")
        self.ops["rename"](self.name, v)

    def _commit_desc(self, v):
        if v != (self.decl.get("description") or ""):
            self.ops["fields"](self.name, {"description": v})

    def _commit_default(self, v):
        t = self.decl.get("type")
        if t != "boolean" and js_str(v if v is not None else "").strip() == "":
            self.f_def.set_error("")
            if "default" in self.decl:
                self.ops["fields"](self.name, {"default": UNSET})
            return
        try:
            value = parse_value(t, v)
        except ValueError as e:
            self.f_def.set_error(str(e))
            return
        self.f_def.set_error("")
        self.ops["fields"](self.name, {"default": value})


# ---------- 没有选中节点时：工作流的名称和说明 ----------
class MetaEditor(QWidget):
    def __init__(self, name, intent, on_set):
        super().__init__()
        lay = vbox(self)
        lay.addWidget(label("这个工作流", cls="h3"))
        lay.addSpacing(4)
        lay.addWidget(label("点画布上的节点可以改它的参数；点“输入”“输出”可以增删工作流的输入和输出。", cls="muted"))
        lay.addSpacing(12)
        self.name, self.intent = object(), object()
        f1 = FieldBox("名称")
        self.name_edit = CommitLine("", "", self._commit_name, enter=False)
        f1.body.addWidget(self.name_edit)
        lay.addWidget(f1)
        f2 = FieldBox("要做什么")
        self.intent_edit = CommitText("", "", self._commit_intent, rows=3)
        f2.body.addWidget(self.intent_edit)
        lay.addWidget(f2)
        self.sync(name, intent, on_set)

    def sync(self, name, intent, on_set):
        self.on_set = on_set
        if name != self.name:
            self.name = name
            self.name_edit.set_text_keep(name or "")
        if intent != self.intent:
            self.intent = intent
            self.intent_edit.set_text_keep(intent or "")

    def _commit_name(self, v):
        if v.strip() and v != self.name:
            self.on_set({"name": v.strip()})

    def _commit_intent(self, v):
        if v != (self.intent or ""):
            self.on_set({"intent": v})


# ---------- 程序：当前工作流的 JSON 原文 ----------
