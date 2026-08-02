"""KiloCode 式卡片折叠组件族（0645 融合计划期二 T4/T6/T7/T8/T9/T10）。

对标 kilocode `basic-tool.tsx` / `tool-default-open.ts` / `tool-open-state.ts`
的可迁移模式（0640 §1.3 P1–P8 实证）：

- CollapsibleCard：chevron + 图标 + 标题 + 副标题 + body 统一外壳（P1）；
  折叠是 QWidget.setVisible 真显隐，状态流转是 label 属性更新——
  QTextBrowser 锚点机制整套淘汰（0640 §2）；
- 默认开合约定（P2）：bash 开 / diff 折 / todo 常开 / MCP 折 /
  子代理运行中开完成折；failed 卡一律自动展开（异常必见，0645 §2.1）；
- OpenStateMap：`{tool_kind}:{toolCallId}` → bool，LRU 2000（P3 直译
  kilocode tool-open-state.ts）；存于各 ChatTranscriptView 实例——
  多标签天然隔离（0640 §9「T4 落地时定」落实例方案）；
- 信息规格按 0645 §2.1 总表：各卡 body 全量信息 + 通用入参区（D3）+
  软上限尾注；body 一律限高内滚动（BodyText/BodyHtml，§2.4）。

主题色经 CardColors 注入（ChatPack 键直引，单一来源纪律）；主题切换
语义与旧轨一致——仅影响此后新建的卡（0640 继承 1836 取舍）。
"""
from collections import OrderedDict
from html import escape as _html_escape

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui.theme import get_mono_family

#: body 限高内滚动上限（0645 §2.4：固定最大高度 + 块内滚动条，
#: 卡片自身高度有界，不撑爆 QScrollArea 布局）
BODY_MAX_HEIGHT = 320

#: 开合状态 Map 容量（P3 直译 kilocode tool-open-state.ts LRU 2000）
_OPEN_STATE_CAPACITY = 2000

#: 默认开合约定（P2 / 0640-D4-A）：bash 开、diff 折、read/search/fetch 折、
#: MCP/未知折、子代理运行中开（完成自动折）；todo 常开不走本表（不可折）
_DEFAULT_OPEN = {
    "execute": True,
    "edit": False,
    "read": False,
    "search": False,
    "fetch": False,
    "think": True,
    "other": False,
}

_KIND_ICONS = {
    "execute": "$",
    "edit": "✎",
    "read": "☐",
    "search": "☐",
    "fetch": "☐",
    "think": "⧉",
    "other": "☐",
}


class CardColors:
    """卡片族主题色袋（ChatPack 键 + 链接色，构造时注入，可随主题切换更新）。

    各卡构造时按引用共享同一实例：apply_theme 改本实例字段即对「此后新建
    的卡」生效（与旧轨 set_*_color 系列「仅影响此后追加的块」同语义）。
    """

    def __init__(
        self,
        reasoning_fg: str,
        tool_fg: str,
        tool_error_fg: str,
        user_bubble_bg: str,
        tool_output_bg: str,
        link_fg: str,
        diff_add_fg: str,
        diff_del_fg: str,
    ) -> None:
        self.reasoning_fg = reasoning_fg
        self.tool_fg = tool_fg
        self.tool_error_fg = tool_error_fg
        self.user_bubble_bg = user_bubble_bg
        self.tool_output_bg = tool_output_bg
        self.link_fg = link_fg
        self.diff_add_fg = diff_add_fg
        self.diff_del_fg = diff_del_fg


class OpenStateMap:
    """开合状态持久化 Map（P3）：`{tool_kind}:{toolCallId}` → bool，LRU 2000。

    用户手动开合是唯一写入源（自动开合不入图——自动行为每次按默认约定
    重算，只有用户显式选择值得跨块重建记忆，与 kilocode 同语义）。
    """

    def __init__(self, capacity: int = _OPEN_STATE_CAPACITY) -> None:
        self._states: OrderedDict[str, bool] = OrderedDict()
        self._capacity = capacity

    def get(self, key: str) -> bool | None:
        return self._states.get(key)

    def set(self, key: str, is_open: bool) -> None:
        self._states[key] = is_open
        self._states.move_to_end(key)
        while len(self._states) > self._capacity:
            self._states.popitem(last=False)


# ----------------------------------------------------------------------
# body 文本块（限高内滚动基础设施，0645 §2.4 统一挂载点）
# ----------------------------------------------------------------------
class _HugHeightMixin:
    """内容高度自适应 + 上限封顶：doc 实际高 ≤ BODY_MAX_HEIGHT 时贴内容，
    超限封顶出块内滚动条（防单卡撑爆 QScrollArea 布局）。"""

    def _init_hug(self, max_height: int | None) -> None:
        self._max_height = max_height
        self.document().contentsChanged.connect(self._fit_height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_height()

    def _fit_height(self) -> None:
        doc = self.document()
        width = self.viewport().width()
        if width > 0:
            doc.setTextWidth(width)
        target = int(doc.size().height()) + 6
        if self._max_height is not None:
            target = min(target, self._max_height)
        target = max(target, 22)
        if target != self.height():
            self.setFixedHeight(target)


class BodyText(_HugHeightMixin, QPlainTextEdit):
    """body 等宽/正文只读块（限高内滚动；mono=True 用更纱黑体）。"""

    def __init__(
        self,
        text: str = "",
        mono: bool = False,
        max_height: int | None = BODY_MAX_HEIGHT,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        # 透明融入卡片底色（卡片 QFrame 已带 bg）
        self.setStyleSheet("background: transparent; border: none;")
        if mono:
            self.setFont(QFont(get_mono_family()))
        if text:
            self.setPlainText(text)
        self._init_hug(max_height)

    def set_text(self, text: str) -> None:
        """整刷正文（运行中尾滚替换、完成定格的统一入口）。"""
        self.setPlainText(text)
        if self._max_height is not None:
            bar = self.verticalScrollBar()
            bar.setValue(bar.maximum())  # 尾滚语义：定格/刷新后视线在尾部


class BodyHtml(_HugHeightMixin, QTextBrowser):
    """body 富文本只读块（diff 三色等需行级着色的场景；限高内滚动）。"""

    #: 锚点点击外抛（transcript 转发给面板解析跳查看器）
    link_clicked = Signal(QUrl)

    def __init__(
        self,
        html: str = "",
        mono: bool = False,
        max_height: int | None = BODY_MAX_HEIGHT,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setReadOnly(True)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("background: transparent; border: none;")
        if mono:
            self.setFont(QFont(get_mono_family()))
        self.anchorClicked.connect(self.link_clicked)
        if html:
            self.setHtml(html)
        self._init_hug(max_height)


# ----------------------------------------------------------------------
# 可折叠卡外壳（P1）
# ----------------------------------------------------------------------
class CollapsibleCard(QFrame):
    """chevron + 图标 + 标题 + 副标题 + body 的统一卡片外壳。

    折叠 = body setVisible 真显隐（0640 §2）；set_open(user=True) 记为
    用户显式选择（子类持久化到 OpenStateMap，自动开合不入图）。
    """

    def __init__(
        self,
        colors: CardColors,
        icon: str,
        title: str,
        subtitle: str = "",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._colors = colors
        self._open = False
        self.setObjectName("ChatToolCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._chevron = QToolButton(self)
        self._chevron.setAutoRaise(True)
        self._chevron.setText("▸")
        self._chevron.setFixedSize(20, 20)
        self._chevron.clicked.connect(lambda: self.set_open(not self._open, user=True))

        self._icon_label = QLabel(icon, self)
        self._icon_label.setStyleSheet(f"color: {colors.tool_fg};")
        self._title_label = QLabel(title, self)
        title_font = self._title_label.font()
        title_font.setBold(True)
        self._title_label.setFont(title_font)
        self._subtitle_label = QLabel(subtitle, self)
        self._subtitle_label.setStyleSheet(f"color: {colors.tool_fg};")
        self._status_label = QLabel(self)
        self._copy_button = QToolButton(self)
        self._copy_button.setAutoRaise(True)
        self._copy_button.setText("复制")
        self._copy_button.setVisible(False)  # T11：子类 enable_copy 挂载

        header = QHBoxLayout()
        header.setContentsMargins(6, 4, 6, 4)
        header.setSpacing(5)
        header.addWidget(self._chevron)
        header.addWidget(self._icon_label)
        header.addWidget(self._title_label)
        header.addWidget(self._subtitle_label)
        header.addStretch(1)
        header.addWidget(self._copy_button)
        header.addWidget(self._status_label)
        self._header_layout = header

        self._body = QWidget(self)
        self._body_layout = QVBoxLayout(self._body)
        self._body_layout.setContentsMargins(10, 0, 8, 6)
        self._body_layout.setSpacing(4)
        self._body.setVisible(False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addWidget(self._body)
        self._refresh_style()

    def _refresh_style(self) -> None:
        """卡片底色（tool_output_bg 复用：工具卡外壳与旧轨输出卡同源色）。"""
        self.setStyleSheet(
            f"#ChatToolCard {{ background-color: {self._colors.tool_output_bg};"
            f" border-radius: 6px; }}")

    # ------------------------------------------------------------------
    def is_open(self) -> bool:
        return self._open

    def set_open(self, is_open: bool, user: bool = False) -> None:
        """开合切换（user=True 为用户显式操作，子类覆写 _on_user_toggle 持久化）。"""
        self._open = is_open
        self._chevron.setText("▾" if is_open else "▸")
        self._body.setVisible(is_open)
        if user:
            self._on_user_toggle(is_open)

    def _on_user_toggle(self, is_open: bool) -> None:
        """用户显式开合钩子（ToolCard 覆写持久化；ThinkingCard 覆写记手动记忆）。"""

    def set_title(self, title: str) -> None:
        self._title_label.setText(title)

    def set_subtitle(self, subtitle: str) -> None:
        self._subtitle_label.setText(subtitle)

    def set_status(self, text: str, color: str | None = None) -> None:
        self._status_label.setText(text)
        self._status_label.setStyleSheet(f"color: {color};" if color else "")

    def set_title_color(self, color: str) -> None:
        self._title_label.setStyleSheet(f"color: {color};")

    def add_body_widget(self, widget: QWidget) -> None:
        self._body_layout.addWidget(widget)

    def enable_copy(self, getter) -> None:
        """卡片复制按钮（T11：跨块选择丢失的补偿，0640-D6-A）。"""
        from PySide6.QtWidgets import QApplication
        self._copy_button.setVisible(True)
        self._copy_button.setToolTip("复制卡内容")
        self._copy_button.clicked.connect(
            lambda: QApplication.clipboard().setText(getter()))


# ----------------------------------------------------------------------
# 工具卡基类与各专类（0645 §2.1 规格表）
# ----------------------------------------------------------------------
class ToolCard(CollapsibleCard):
    """工具卡外壳基类：状态流转 ◐→✔/✖ 为属性更新（无锚点、行数不增）。

    子类经 _build_body 挂各专类 body；基类收尾统一挂通用入参区（D3）
    与初始开合（OpenStateMap 记忆优先 → 默认约定，P2/P3）。
    """

    #: MCP/未知工具卡 body 本身即入参+出参两节，不重复附通用入参区（§2.2）
    _attach_input_detail = True

    def __init__(self, colors: CardColors, open_state: OpenStateMap, payload: dict) -> None:
        self._kind = payload.get("tool_kind") or "other"
        self._tid = payload.get("tool_call_id") or ""
        self._open_state = open_state
        self._user_toggled = False
        super().__init__(
            colors,
            _KIND_ICONS.get(self._kind, "☐"),
            payload.get("title") or "?",
            payload.get("summary") or "")
        self.set_status("◐", colors.tool_fg)
        self._build_body(payload)
        if self._attach_input_detail and (detail := payload.get("input_detail")):
            self._add_input_detail(detail)
        self._resolve_initial_open()

    # ------------------------------------------------------------------
    # 子类钩子
    # ------------------------------------------------------------------
    def _build_body(self, payload: dict) -> None:
        """各专类 body 构建（基类 __init__ 尾调用）。"""

    def _on_progress(self, payload: dict) -> None:
        """in_progress 帧（BashCard 尾滚用；其余卡忽略）。"""

    def _on_completed(self, payload: dict) -> None:
        """completed 帧（输出正文/成果摘要/行数尾注落 body）。"""

    def _on_failed(self, payload: dict) -> None:
        """failed 帧（默认：错误全文落 body；§2.1 failed 行规格）。"""
        detail = payload.get("error_detail") or payload.get("error")
        if detail:
            self._set_error_body(detail)

    def _set_error_body(self, text: str) -> None:
        """failed 错误全文块（红色等宽，自动展开由 apply_update 统一）。"""
        body = BodyText(text, mono=True)
        body.setStyleSheet(
            f"background: transparent; border: none; color: {self._colors.tool_error_fg};")
        self.add_body_widget(body)
        self.enable_copy(lambda body=body: body.toPlainText())

    # ------------------------------------------------------------------
    # 状态流转（路由层 append_tool_update 直达）
    # ------------------------------------------------------------------
    def apply_update(self, payload: dict) -> None:
        status = payload.get("status")
        if title := payload.get("title"):
            self.set_title(title)
        if status == "completed":
            self.set_status("✔", self._colors.tool_fg)
            self._on_completed(payload)
        elif status == "failed":
            self.set_status("✖", self._colors.tool_error_fg)
            self.set_title_color(self._colors.tool_error_fg)
            self._on_failed(payload)
            # failed 卡自动展开（异常必见，§2.1）；自动开合不入 OpenStateMap
            self.set_open(True)
        elif status == "in_progress":
            self._on_progress(payload)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _on_user_toggle(self, is_open: bool) -> None:
        self._user_toggled = True
        if self._tid:
            self._open_state.set(f"{self._kind}:{self._tid}", is_open)

    def _resolve_initial_open(self) -> None:
        saved = self._open_state.get(f"{self._kind}:{self._tid}") if self._tid else None
        self.set_open(saved if saved is not None
                      else _DEFAULT_OPEN.get(self._kind, False))

    def _add_input_detail(self, detail: str) -> None:
        """通用入参区（D3）：body 末弱化灰小块，「尽可能全」的兜底保证。"""
        label = QLabel("入参", self)
        label.setStyleSheet(f"color: {self._colors.tool_fg}; font-size: 90%;")
        text = BodyText(detail, mono=True)
        text.setStyleSheet(
            f"background: transparent; border: none; color: {self._colors.tool_fg};")
        self.add_body_widget(label)
        self.add_body_widget(text)


class BashCard(ToolCard):
    """bash 卡（P5）：完整命令 + 输出全量 body（软上限保尾已由协议层执行）；
    运行中尾滚 5 行实时帧（路由层 200ms 节流），完成定格 +「共 N 行」尾注。
    """

    def _build_body(self, payload: dict) -> None:
        self._command = payload.get("command") or ""
        if self._command:
            cmd = BodyText(f"$ {self._command}", mono=True, max_height=None)
            cmd_font = cmd.font()
            cmd_font.setBold(True)
            cmd.setFont(cmd_font)
            self.add_body_widget(cmd)
        self._output = BodyText(mono=True)
        self.add_body_widget(self._output)
        self._note = QLabel(self)
        self._note.setStyleSheet(f"color: {self._colors.tool_fg};")
        self._note.setVisible(False)
        self.add_body_widget(self._note)
        self.enable_copy(self._copy_text)

    def _copy_text(self) -> str:
        head = f"$ {self._command}\n" if self._command else ""
        return head + self._output.toPlainText()

    def _on_progress(self, payload: dict) -> None:
        # 尾滚：替换非追加（协议层已截尾末 5 行）
        if output := payload.get("output"):
            self._output.set_text(output)

    def _on_completed(self, payload: dict) -> None:
        self._render_final(payload.get("output") or self._output.toPlainText(),
                           payload.get("output_total_lines"))

    def _on_failed(self, payload: dict) -> None:
        # bash 失败：错误全文进输出区（红字），兼保留已滚出的输出上文语义
        detail = payload.get("error_detail") or payload.get("error")
        if detail:
            self._output.setStyleSheet(
                f"background: transparent; border: none;"
                f" color: {self._colors.tool_error_fg};")
            self._output.set_text(detail)
        elif payload.get("output"):
            self._render_final(payload["output"], payload.get("output_total_lines"))

    def _render_final(self, output: str, total_lines: int | None) -> None:
        self._output.set_text(output)
        shown = len(output.split("\n")) if output else 0
        if total_lines and total_lines > shown:
            self._note.setText(f"…… 共 {total_lines} 行（仅显示末 {shown} 行）")
            self._note.setVisible(True)


class DiffCard(ToolCard):
    """diff 卡（P6，edit/write）：hunk 全量三色 body（@@ 灰 / + 绿 / − 红，
    软上限 1000 行协议层已截断）+ 标题行 +N −M 红绿徽标；默认折叠。
    无 diff 项时退化为纯标题行（edit diff 项非必填，不臆造）。
    """

    def _build_body(self, payload: dict) -> None:
        if diff_stat := payload.get("diff_stat"):
            badge = QLabel(self)
            add, _, delete = diff_stat.partition(" ")
            badge.setText(
                f'<span style="color:{self._colors.diff_add_fg}">{_html_escape(add)}</span>'
                f' <span style="color:{self._colors.diff_del_fg}">{_html_escape(delete)}</span>')
            # 徽标常驻标题行（插到状态图标前）
            self._header_layout.insertWidget(
                self._header_layout.indexOf(self._status_label), badge)
            self._diff_badge = badge
        hunks = payload.get("diff_hunks") or []
        if not hunks:
            return
        mono = get_mono_family()
        parts = []
        for hunk in hunks:
            parts.append(
                f'<font face="{mono}" color="{self._colors.reasoning_fg}">'
                f'{_html_escape(hunk["head"])}</font>')
            for prefix, text in hunk["lines"]:
                color = {"+": self._colors.diff_add_fg,
                         "-": self._colors.diff_del_fg}.get(prefix)
                line = f"{prefix} {text}" if prefix != " " else f"  {text}"
                if color:
                    parts.append(
                        f'<font face="{mono}" color="{color}">{_html_escape(line)}</font>')
                else:
                    parts.append(f'<font face="{mono}">{_html_escape(line)}</font>')
        if payload.get("diff_truncated"):
            parts.append(
                f'<font face="{mono}" color="{self._colors.reasoning_fg}">'
                f"…… diff 过长，仅显示前部（软上限 {_diff_soft_limit_note()} 行）</font>")
        self._diff_html = "<br>".join(parts)
        body = BodyHtml(self._diff_html)
        self.add_body_widget(body)
        self._copy_source = self._plain_diff(hunks)
        self.enable_copy(lambda: self._copy_source)

    @staticmethod
    def _plain_diff(hunks: list) -> str:
        lines = []
        for hunk in hunks:
            lines.append(hunk["head"])
            lines.extend(f"{prefix}{text}" if prefix != " " else f" {text}"
                         for prefix, text in hunk["lines"])
        return "\n".join(lines)


class TextOutputCard(ToolCard):
    """read/search/fetch 输出正文卡（D1-A）：标题行尾注「—— N 行」，
    body 输出正文全量（软上限保头已由协议层执行）；默认折叠。
    """

    def _build_body(self, payload: dict) -> None:
        self._output = BodyText(mono=True)
        self._output.setVisible(False)
        self.add_body_widget(self._output)
        self._note = QLabel(self)
        self._note.setStyleSheet(f"color: {self._colors.tool_fg};")
        self._note.setVisible(False)
        self.add_body_widget(self._note)

    def _on_completed(self, payload: dict) -> None:
        total = payload.get("output_total_lines")
        if output := payload.get("output"):
            self._output.set_text(output)
            self._output.setVisible(True)
            self.enable_copy(self._output.toPlainText)
            shown = len(output.split("\n"))
            if total and total > shown:
                self._note.setText(f"…… 共 {total} 行（仅显示前 {shown} 行）")
                self._note.setVisible(True)
        if total:
            base = payload.get("summary") or self._subtitle_label.text()
            self.set_subtitle(f"{base} —— {total} 行" if base else f"—— {total} 行")


class SubagentCard(ToolCard):
    """子代理卡（P8）：运行中展开、完成自动折叠（用户手动折叠记忆优先）；
    body = 成果摘要全量（F2 协议永久边界：子会话内部活动不可得，
    成果摘要是唯一可得产出）。
    """

    def _build_body(self, payload: dict) -> None:
        self._summary = BodyText()
        self._summary.setVisible(False)
        self.add_body_widget(self._summary)

    def _on_completed(self, payload: dict) -> None:
        if summary := payload.get("result_summary"):
            self._summary.set_text(summary)
            self._summary.setVisible(True)
            self.enable_copy(self._summary.toPlainText)
        if not self._user_toggled:  # 完成自动折（P2；手动记忆优先）
            self.set_open(False)


class McpCard(ToolCard):
    """MCP/未知工具卡：body = 入参 + 出参两节（对标 kilocode McpTool；
    通用入参区不重复附，§2.2）。
    """

    _attach_input_detail = False

    def _build_body(self, payload: dict) -> None:
        in_label = QLabel("入参", self)
        in_label.setStyleSheet(f"color: {self._colors.tool_fg}; font-size: 90%;")
        self.add_body_widget(in_label)
        self._input = BodyText(payload.get("input_detail") or "（无）", mono=True)
        self.add_body_widget(self._input)
        self._out_label = QLabel("出参", self)
        self._out_label.setStyleSheet(f"color: {self._colors.tool_fg}; font-size: 90%;")
        self._out_label.setVisible(False)
        self.add_body_widget(self._out_label)
        self._output = BodyText(mono=True)
        self._output.setVisible(False)
        self.add_body_widget(self._output)

    def _on_completed(self, payload: dict) -> None:
        if output := payload.get("output"):
            self._out_label.setVisible(True)
            self._output.set_text(output)
            self._output.setVisible(True)


def _diff_soft_limit_note() -> int:
    """截断尾注行数（与协议层 _BODY_SOFT_LIMIT_LINES 同源表述；
    渲染层不引协议层私有常量，尾注数值硬编与协议层同步——改一处须同步）。
    """
    return 1000


def make_tool_card(colors: CardColors, open_state: OpenStateMap, payload: dict) -> ToolCard:
    """工具卡工厂：按 tool_kind 分派专类（0645 §2.1 规格表）。"""
    kind = payload.get("tool_kind") or "other"
    if kind == "execute":
        return BashCard(colors, open_state, payload)
    if kind == "edit":
        return DiffCard(colors, open_state, payload)
    if kind in ("read", "search", "fetch"):
        return TextOutputCard(colors, open_state, payload)
    if kind == "think":
        return SubagentCard(colors, open_state, payload)
    return McpCard(colors, open_state, payload)


# ----------------------------------------------------------------------
# ThinkingCard（P4 / T6）与 TodoCard（P7 / T7）
# ----------------------------------------------------------------------
class ThinkingCard(CollapsibleCard):
    """思维链卡：流式展开、完成自动折叠（用户手动折叠记忆优先，P4/0640-D7-A）。

    标题经 reasoning_heading.split_heading 提取（1836 L1 资产平移）；
    body 灰斜体全文（与正文样式区分，复用 reasoning_fg）。
    """

    def __init__(self, colors: CardColors, split_heading, parent=None) -> None:
        super().__init__(colors, "…", "Thinking", "", parent)
        self._split_heading = split_heading
        self._buffer = ""
        self._user_toggled = False
        self._text = BodyText()
        self._text.setStyleSheet(
            f"background: transparent; border: none;"
            f" color: {colors.reasoning_fg}; font-style: italic;")
        self.add_body_widget(self._text)
        self.enable_copy(lambda: self._buffer)
        self.set_open(True)  # 流式展开

    def append_chunk(self, chunk: str) -> None:
        self._buffer += chunk
        self._text.set_text(self._buffer)
        if "\n" in self._buffer:
            title, _ = self._split_heading(self._buffer)
            if title:
                self.set_title(f"Thinking — {title}")

    def finish(self) -> None:
        """思维链结束：完成自动折叠（用户手动开合过则尊重记忆，P4）。"""
        if not self._user_toggled:
            self.set_open(False)

    def _on_user_toggle(self, is_open: bool) -> None:
        self._user_toggled = True


class TodoCard(QFrame):
    """TODO 卡（P7）：☑ 任务清单 — N/M 项完成 + 只读 checkbox 列表；
    常开不可折（0640-D4-A 对应 P7 强制展开）；set_entries 整刷（弃锚点）。
    """

    def __init__(self, colors: CardColors, parent=None) -> None:
        super().__init__(parent)
        self._colors = colors
        self.setObjectName("ChatToolCard")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"#ChatToolCard {{ background-color: {colors.tool_output_bg};"
            f" border-radius: 6px; }}")
        icon = QLabel("☑", self)
        icon.setStyleSheet(f"color: {colors.tool_fg};")
        self._title = QLabel("任务清单", self)
        title_font = self._title.font()
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._subtitle = QLabel(self)
        self._subtitle.setStyleSheet(f"color: {colors.tool_fg};")
        header = QHBoxLayout()
        header.setContentsMargins(6, 4, 6, 4)
        header.setSpacing(5)
        header.addWidget(icon)
        header.addWidget(self._title)
        header.addWidget(self._subtitle)
        header.addStretch(1)
        self._list = QVBoxLayout()
        self._list.setContentsMargins(10, 0, 8, 6)
        self._list.setSpacing(2)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addLayout(header)
        layout.addLayout(self._list)

    def set_entries(self, entries: list) -> None:
        """快照整刷（双通道 todo 同源，F1）；完成/取消项灰 + 删除线。"""
        while self._list.count():
            item = self._list.takeAt(0)
            if widget := item.widget():
                widget.deleteLater()
        done = total = 0
        for entry in entries:
            status = entry.get("status") or "pending"
            total += 1
            label = QLabel(self)
            if status in ("completed", "cancelled"):
                done += 1
                label.setText(f"☑ {entry.get('content') or ''}")
                font = label.font()
                font.setStrikeOut(True)
                label.setFont(font)
                label.setStyleSheet(f"color: {self._colors.reasoning_fg};")
            elif status == "in_progress":
                label.setText(f"▶ {entry.get('content') or ''}")
            else:
                label.setText(f"☐ {entry.get('content') or ''}")
            self._list.addWidget(label)
        self._subtitle.setText(f"— {done}/{total} 项完成" if total else "")
