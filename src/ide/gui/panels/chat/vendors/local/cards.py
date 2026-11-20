"""Local 工具卡具体实现：本厂商载荷格式、工具卡类与问题卡注册表。

具体卡片由 `ide.gui.panels.chat.vendors.local.factory.make_tool_card` 分派；跨帧账本、迟到帧
闸门与旧轨输出裁剪由 `ide.gui.panels.chat.vendors.local.presenter` 持有。本模块只 import 本厂商
models 的纯数据类型与无厂商语义的 card_base 基础件。
"""
import json
from html import escape as _html_escape
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ide.gui.panels.chat.card_base import (
    BODY_MAX_HEIGHT,
    BodyHtml,
    BodyText,
    CardColors,
    OpenStateMap,
    ToolCard as BaseToolCard,
    _ElidedLabel,
    _selectable,
    compose_mcp_output_html,
    media_thumb_html,
)
from ide.gui.panels.chat.permission_dialog import OTHER_HINT_TEXT
from ide.gui.selection_band import BandLabel
from ide.gui.theme import get_mono_family

# ----------------------------------------------------------------------
# 厂商工具卡外壳：解释本厂商载荷字段，驱动 card_base 的通用生命周期
# ----------------------------------------------------------------------
class ToolCard(BaseToolCard):
    """Local 工具卡外壳：只解释本厂商 mapper 产出的卡片字段。"""

    def __init__(self, colors: CardColors, open_state: OpenStateMap,
                 payload: dict,
                 workspace_root: Path | None = None) -> None:
        super().__init__(
            colors, open_state, payload, workspace_root,
            subtitle=payload.get("summary") or "")
        self._build_body(payload)
        self.attach_input_detail(payload.get("input_detail") or "")

    def _build_body(self, payload: dict) -> None:
        """各专类 body 构建（构造器尾调用）。"""

    def _on_failed(self, payload: dict) -> None:
        """默认错误全文块（红色等宽；BashCard 覆写为输出区红字）。"""
        detail = payload.get("error_detail") or payload.get("error")
        if detail:
            self._set_error_body(detail)

    def apply_update(self, payload: dict) -> None:
        self.apply_title(payload.get("title"))
        self.apply_subtitle(payload.get("summary"))
        self.attach_input_detail(payload.get("input_detail") or "")
        if media_path := payload.get("media_path"):
            handler = getattr(self, "_set_media_path", None)
            if handler is not None:
                handler(media_path)
        if command := payload.get("command"):
            handler = getattr(self, "_set_command", None)
            if handler is not None:
                handler(command)
        self.apply_status(payload.get("status"), payload)


class BashCard(ToolCard):
    """bash 卡（P5）：完整命令 + 输出全量 body（软上限保尾已由协议层执行）；
    运行中尾滚 5 行实时帧（路由层 200ms 节流），完成定格 +「共 N 行」尾注 +
    自动折叠（2026-0803 拍板对齐子代理卡；手动记忆优先；failed 不受影响——
    走 _on_failed + 强制展开路径，异常必见）。
    """

    def _build_body(self, payload: dict) -> None:
        self._command = ""
        self._set_command(payload.get("command") or "")
        self._output = BodyText(mono=True)
        self.add_body_widget(self._output)
        self._note = _selectable(BandLabel(self))
        self._note.setStyleSheet(f"color: {self._colors.tool_fg};")
        self._note.setVisible(False)
        self.add_body_widget(self._note)
        self.enable_copy(self._copy_text)

    def _set_command(self, command: str) -> None:
        """`$ ` 粗体命令头（0812-0918 计划 T2-2 兼作迟到补挂钩子）：
        首帧齐备由 _build_body 挂；本厂商首帧空壳场景 command 随
        update 帧迟到，经路由 _tool_commands 簿记注入后于本钩子在
        body 顶端补挂（幂等——已挂不重复）。"""
        if not command or self._command:
            return
        self._command = command
        cmd = BodyText(f"$ {command}", mono=True, max_height=None)
        cmd_font = cmd.font()
        cmd_font.setBold(True)
        cmd.setFont(cmd_font)
        # body 顶端插入（迟到补挂时输出区已在场，命令头须居其前）
        self._body_layout.insertWidget(0, cmd)

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
        if not self._user_toggled:  # 完成自动折（2026-0803 拍板，对齐子代理卡；手动记忆优先）
            self.set_open(False)

    def _on_failed(self, payload: dict) -> None:
        # bash 失败：错误全文进输出区（红字），兼保留已滚出的输出上文语义
        detail = payload.get("error_detail") or payload.get("error")
        if detail:
            self._output.set_extra_style(f"color: {self._colors.tool_error_fg};")
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

    0919 计划 T3：diff/路径在 in_progress update 帧才到（首帧
    空壳）——徽标/body 挂载抽为 _render_diff 供首帧与 update 帧共用
    （_diff_attached 去重，双帧各发一次 diff 时只挂一份）；标题恒定
    工具名（拒收「Editing <路径>」内嵌标题），副标题两段式「文件名
    （tool_fg 常态）· 目录（reasoning_fg 弱化）」对齐对标样式。
    """

    _accept_title_update = False
    #: diff 补挂去重标记（类级缺省；R3：首帧/completed 帧双发只挂一份）
    _diff_attached = False

    def set_subtitle(self, subtitle: str) -> None:
        """两段式路径副标题（0919 T3-1）：仅拆显示不改载荷语义；
        无路径分隔符时退化为纯文本（与基类一致）。"""
        text = subtitle or ""
        if "/" not in text:
            super().set_subtitle(text)
            return
        head, _, name = text.rstrip("/").rpartition("/")
        super().set_subtitle(
            f'{_html_escape(name)} · '
            f'<span style="color:{self._colors.reasoning_fg}">'
            f'{_html_escape(head + "/")}</span>')

    def _build_body(self, payload: dict) -> None:
        self._render_diff(payload)

    def _on_progress(self, payload: dict) -> None:
        # diff/路径在 in_progress 帧到达（0919 T1 实证）
        self._render_diff(payload)

    def _on_completed(self, payload: dict) -> None:
        self._render_diff(payload)

    def _render_diff(self, payload: dict) -> None:
        """徽标 + hunk body 挂载（首帧 _build_body 与 update 帧两钩子共用）。"""
        if self._diff_attached:
            return
        if diff_stat := payload.get("diff_stat"):
            badge = _selectable(BandLabel(self))
            add, _, delete = diff_stat.partition(" ")
            badge.setText(
                f'<span style="color:{self._colors.diff_add_fg}">{_html_escape(add)}</span>'
                f' <span style="color:{self._colors.diff_del_fg}">{_html_escape(delete)}</span>')
            # 徽标常驻标题行（插到状态图标前）
            self._header_layout.insertWidget(
                self._header_layout.indexOf(self._status_label), badge)
            self._diff_badge = badge
            self._diff_attached = True
        hunks = payload.get("diff_hunks") or []
        if not hunks:
            return
        self._diff_attached = True
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
    """read/search/fetch 输出正文卡（D1-A）：标题行右侧灰字徽标「N 行」
    （复制钮旁、状态图标左，与 DiffCard 徽标同位同手法），body 输出正文
    全量（软上限保头已由协议层执行）；默认折叠。
    """

    def _build_body(self, payload: dict) -> None:
        self._output = BodyText(mono=True)
        self._output.setVisible(False)
        self.add_body_widget(self._output)
        self._note = _selectable(BandLabel(self))
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
            # 行数徽标常驻标题行右侧（复制钮旁，与 DiffCard 徽标同位同手法）
            badge = _selectable(BandLabel(f"{total} 行", self))
            badge.setStyleSheet(f"color: {self._colors.tool_fg};")
            self._header_layout.insertWidget(
                self._header_layout.indexOf(self._status_label), badge)


class SubagentCard(ToolCard):
    """子代理卡（P8）：运行中展开、完成自动折叠（用户手动折叠记忆优先）；
    body = 成果摘要全量（F2 协议永久边界：子会话内部活动不可得，
    成果摘要是唯一可得产出）。

    0806 计划 T5 增强：Agent/Task 工具名分派复用本卡——出参在
    result_summary（think kind）缺省时回退 output 正文（kind=other 的
    Agent 调用协议层不产 result_summary，结果全文在 output 通道）。

    0813-1919 计划 T2 内嵌子卡片区（F2 边界部分翻案）：层级 ID 协议
    层级 ID 帧与 wire 旁路合成帧使子代理内部活动实时可得——
    带 parent_tool_call_id 的载荷经路由委派进本卡 body 顶端
    「子代理活动」区（懒建，首个子帧到达时现身），复用 make_tool_card
    工厂与 apply_update 纯方法；自有 OpenStateMap 实例防主流开合记忆
    串扰；区整体超 BODY_MAX_HEIGHT 出块内滚动不撑爆父卡；父卡完成后
    迟到子帧幂等回填不新建（_children_done）；子卡片区随父卡折叠
    一起显隐（body 内一个 section，CollapsibleCard 既有机制）。
    两级封顶：agent 侧一层委派，子卡区内不再嵌套 SubagentCard 区。
    """

    supports_children = True

    def _build_body(self, payload: dict) -> None:
        #: 内嵌子卡片簿记（子帧全串 tid → ToolCard；transcript._tool_cards
        #: 的嵌套版，父卡生命周期内有效，轮次收尾随主流簿记一并丢弃引用）
        self._tool_cards: dict[str, ToolCard] = {}
        #: 子卡独立开合记忆（0813-1919 计划 T2：与主流 OpenStateMap 隔离）
        self._child_open_state = OpenStateMap()
        #: 父卡终态标记：完成后迟到子帧幂等回填既有卡、不新建（T2）
        self._children_done = False
        self._children_label: QLabel | None = None
        self._children_host: QWidget | None = None
        self._children_scroll: QScrollArea | None = None
        self._children_layout: QVBoxLayout | None = None
        self._summary = BodyText()
        self._summary.setVisible(False)
        self.add_body_widget(self._summary)

    # ------------------------------------------------------------------
    # 内嵌子卡片区（0813-1919 计划 T2）
    # ------------------------------------------------------------------
    def append_child_call(self, payload: dict) -> None:
        """子代理内部 tool_call 帧上屏：工厂分派 + tid 簿记
        （transcript.append_tool_call 的嵌套版，同 30 行模式）。"""
        self._ensure_children_section()
        card = self._make_child_card(payload)
        self._children_layout.addWidget(card)
        if tid := payload.get("tool_call_id"):
            self._tool_cards[tid] = card
        self._fit_children_height()

    def append_child_update(self, payload: dict) -> None:
        """子帧状态流转：寻卡属性更新（transcript.append_tool_update
        嵌套版）；父卡完成后迟到子帧幂等回填不新建（T2 定格语义）。"""
        tid = payload.get("tool_call_id") or ""
        card = self._tool_cards.get(tid)
        if card is None:
            if self._children_done:
                return  # 定格后迟到陌生子帧：丢弃（不新建），既有卡回填仍放行
            self._ensure_children_section()
            card = self._make_child_card(payload)  # 容错补建（transcript 同语义）
            self._children_layout.addWidget(card)
            if tid:
                self._tool_cards[tid] = card
        card.apply_update(payload)

    def _ensure_children_section(self) -> None:
        """「子代理活动」区懒建（首个子帧到达时现身）：body 顶端插入
        标签行 + 限高滚动区（内容贴高至 BODY_MAX_HEIGHT 封顶出块内
        滚动，不撑爆父卡——QScrollArea 默认 sizeHint 不随内容贴高，
        高度由 _fit_children_height 显式拟合）。"""
        if self._children_layout is not None:
            return
        self._children_label = _selectable(BandLabel("子代理活动", self))
        self._children_label.setStyleSheet(
            f"color: {self._colors.tool_fg}; font-size: 90%;")
        self._children_host = QWidget(self)
        self._children_layout = QVBoxLayout(self._children_host)
        self._children_layout.setContentsMargins(0, 0, 0, 0)
        self._children_layout.setSpacing(4)
        self._children_scroll = QScrollArea(self)
        self._children_scroll.setWidgetResizable(True)
        self._children_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._children_scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._children_scroll.setMaximumHeight(BODY_MAX_HEIGHT)
        self._children_scroll.setWidget(self._children_host)
        # 子卡增删/body 高变化（LayoutRequest 沿父子链上传）→ 延迟重拟合
        self._children_host.installEventFilter(self)
        # body 顶端插入（阅读顺序：子代理活动 → 成果摘要）
        self._body_layout.insertWidget(0, self._children_label)
        self._body_layout.insertWidget(1, self._children_scroll)

    def _fit_children_height(self) -> None:
        """子卡片区高度拟合：内容 sizeHint ≤ BODY_MAX_HEIGHT 贴内容，
        超限封顶出块内滚动条（_HugHeightMixin 同策略的容器版）。
        延迟回调到达时卡片可能已在析构途中（截图 mock 场景实证），
        访问已死 C++ 对象静默放弃（探活纪律同 find_question_card）。"""
        if self._children_scroll is None or self._children_host is None:
            return
        try:
            # activate() 强制布局链同步重算——子卡开合后 sizeHint 未沉降
            # 时直接读会拿到旧值（mock 截图实证：拟合卡在折叠态高度）
            self._children_layout.activate()
            hint = self._children_host.sizeHint().height()
        except RuntimeError:
            return
        target = min(max(hint, 24), BODY_MAX_HEIGHT)
        if target != self._children_scroll.height():
            self._children_scroll.setFixedHeight(target)

    def eventFilter(self, watched, event) -> bool:
        """子卡片区内容高变化侦听（子卡开合/BodyText 自适应均经
        LayoutRequest 上传）；singleShot(0) 待尺寸链沉降后重拟合。
        接收者语境绑定滚动区：卡片先销毁则回调自动作废，不触死对象。"""
        if self._children_host is not None and watched is self._children_host \
                and event.type() == QEvent.Type.LayoutRequest \
                and self._children_scroll is not None:
            QTimer.singleShot(0, self._children_scroll, self._fit_children_height)
        return super().eventFilter(watched, event)

    def _make_child_card(self, payload: dict) -> ToolCard:
        """子卡片工厂（make_tool_card 复用）+ R3 护栏：子代理内
        AskUserQuestion 不嵌套交互卡（_QUESTION_CARD_REGISTRY 全局
        注册表单例语义防串扰），强制 McpCard 文本兜底显示。"""
        from ide.gui.panels.chat.vendors.local.factory import (
            is_question_tool, make_tool_card,
        )
        if is_question_tool(payload.get("title") or ""):
            return McpCard(self._colors, self._child_open_state, payload,
                           self._workspace_root)
        return make_tool_card(self._colors, self._child_open_state, payload,
                              workspace_root=self._workspace_root)

    # ------------------------------------------------------------------
    def _on_completed(self, payload: dict) -> None:
        self._children_done = True  # 定格：迟到子帧幂等回填不新建（T2）
        summary = payload.get("result_summary") or payload.get("output")
        if summary:
            self._summary.set_text(summary)
            self._summary.setVisible(True)
            self.enable_copy(self._summary.toPlainText)
        if not self._user_toggled:  # 完成自动折（P2；手动记忆优先）
            self.set_open(False)


def _parse_answered_text(output: str) -> dict | None:
    """历史 ask 出参文本形态 → answers 字典（0812-0952 计划 T3）。

    实证蓝本（文档/帧存档/ask_reasonix_*.json）：历史 completed
    帧出参非 JSON，而是 `The user answered:` 起头、逐行 `- 键: 答案` 的
    纯文本（结构化出参为 `{"answers": {...}}` JSON）。键是问题短标题（header）
    而非问题原文，与 _fill_answers「精确匹配失败按声明顺序分配」兜底衔接。
    """
    lines = output.strip().splitlines()
    if not lines or not lines[0].strip().startswith("The user answered"):
        return None
    answers: dict[str, str] = {}
    for line in lines[1:]:
        body = line.strip()
        if not body.startswith("-") or ":" not in body:
            continue
        key, _, value = body[1:].partition(":")
        key, value = key.strip(), value.strip()
        if key and value:
            answers[key] = value
    return answers or None


class QuestionCard(ToolCard):
    """AskUserQuestion 问答卡（0806 计划 T5）：body 为问答对列表——
    每个问题一行粗体 + 所选答案普通行；pending 显示「等待用户回答…」；
    completed 帧出参 `{"answers": {...}}` 解析后按问答对展示（不显示
    裸 JSON）。questions 可随 update 帧迟到回填（首帧空壳场景，
    _ensure_rows 幂等建行）。

    0807-0148 计划 T4 交互侧：pending 态经 activate_options 在 body 尾部
    激活选项按钮组（QUESTION_BRIDGE 串行调度），用户点击即回调回传
    optionId；completed 帧到达后按既有 _fill_answers 渲染问答对。
    终态（已作答/已 completed）卡片拒活（activate_options 返回 False），
    旧会话重放不复活交互按钮。
    """

    def _build_body(self, payload: dict) -> None:
        self._qa_rows: list[tuple[str, QLabel]] = []
        self._fallback: BodyText | None = None
        self._pending: QLabel | None = None
        self._answered = False            # 终态标记（completed 拒活交互）
        self._options_box: QWidget | None = None  # 激活中的选项按钮组
        self._ensure_rows(payload.get("questions") or [])
        if not self._qa_rows:
            self._pending = _selectable(BandLabel("等待用户回答…", self))
            self._pending.setStyleSheet(f"color: {self._colors.tool_fg};")
            self.add_body_widget(self._pending)
        # 卡片内交互注册（T4：QUESTION_BRIDGE 按 tool_call_id 定位本卡）；
        # 销毁自动注销，防野指针
        if self._tid:
            _QUESTION_CARD_REGISTRY[self._tid] = self
            self.destroyed.connect(
                lambda _obj=None, tid=self._tid: _QUESTION_CARD_REGISTRY.pop(tid, None))

    # ------------------------------------------------------------------
    # 交互侧（0807-0148 计划 T4）
    # ------------------------------------------------------------------
    def activate_options(self, options: list, on_chosen) -> bool:
        """pending 态激活选项按钮组；已终态/已激活/无选项返回 False
        （调用方降级 QuestionDialog 弹窗兜底）。

        按钮文案用 agent 提供的 name 原文（答案是选项不是审批动作，
        与 QuestionDialog 同纪律）；点击后全组禁点、选中项打 ✅ 即时反馈，
        completed 帧到达后由 _fill_answers 定格问答对。
        """
        if self._answered or self._options_box is not None or not options:
            return False
        box = QWidget(self)
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(0, 2, 0, 0)
        box_layout.setSpacing(4)
        hint = _selectable(BandLabel("请选择：", box))
        hint.setStyleSheet(f"color: {self._colors.tool_fg};")
        box_layout.addWidget(hint)
        buttons: list[QPushButton] = []
        for opt in options:
            button = QPushButton(opt.get("name") or opt.get("optionId", "?"), box)
            button.clicked.connect(
                lambda _checked=False, oid=opt.get("optionId"), b=button, bs=buttons:
                self._on_option_clicked(oid, b, bs, on_chosen))
            buttons.append(button)
            box_layout.addWidget(button)
        # 自由作答引导（0807-0445 方案 B，与 QuestionDialog 同一文案来源）：
        # ACP 通道回传不了自由文本，引导用户 Skip 后走正文输入
        other_hint = _selectable(BandLabel(OTHER_HINT_TEXT, box))
        other_hint.setWordWrap(True)
        other_hint.setStyleSheet(f"color: {self._colors.tool_fg};")
        box_layout.addWidget(other_hint)
        self._options_box = box
        self.add_body_widget(box)
        self.set_open(True)  # 激活必见（默认开合 other 为折，不展开用户看不见按钮）
        return True

    def deactivate_options(self) -> None:
        """撤销选项按钮组（超时_abort 路径：reader 已按拒绝兜底）。"""
        if self._options_box is not None:
            self._options_box.setVisible(False)
            self._options_box.deleteLater()
            self._options_box = None

    def _on_option_clicked(self, option_id, button, buttons, on_chosen) -> None:
        """选项点击：全组禁点 + 选中项 ✅ 即时反馈（定格等 completed 帧），
        回调回传 optionId（agent 提供原值，不臆造）。"""
        for b in buttons:
            b.setEnabled(False)
        button.setText(f"✅ {button.text()}")
        on_chosen(option_id)

    def _ensure_rows(self, questions: list) -> None:
        """问答行幂等补建（首帧空壳时 questions 随 update 帧迟到）。"""
        for question in questions:
            if any(q == question for q, _ in self._qa_rows):
                continue
            q_label = _selectable(BandLabel(f"❓ {question}", self))
            q_label.setWordWrap(True)
            font = q_label.font()
            font.setBold(True)
            q_label.setFont(font)
            a_label = _selectable(BandLabel("等待用户回答…", self))
            a_label.setWordWrap(True)
            a_label.setStyleSheet(f"color: {self._colors.tool_fg};")
            self.add_body_widget(q_label)
            self.add_body_widget(a_label)
            self._qa_rows.append((question, a_label))
        if self._qa_rows and self._pending is not None:
            self._pending.setVisible(False)

    def _on_progress(self, payload: dict) -> None:
        self._ensure_rows(payload.get("questions") or [])

    def _on_completed(self, payload: dict) -> None:
        # 终态拒活交互（T4：旧会话重放不复活按钮；激活中的按钮组随定格撤除）
        self._answered = True
        self.deactivate_options()
        self._ensure_rows(payload.get("questions") or [])
        output = payload.get("output") or ""
        answers = None
        dismissed_note = None
        try:
            obj = json.loads(output.strip()) if output.strip() else None
            if isinstance(obj, dict):
                candidate = obj.get("answers") if isinstance(obj.get("answers"), dict) else obj
                answers = candidate
                # 用户 Skip/dismiss（0807-0445 方案 B 引导路径的常态终态）：
                # answers 空 + note 说明，取 note 渲染而非裸 JSON
                if not obj.get("answers") and isinstance(obj.get("note"), str):
                    dismissed_note = obj["note"]
        except ValueError:
            pass
        if answers is None:
            # 历史 ask 出参文本形态（0812-0952 计划 T3，非 JSON）
            answers = _parse_answered_text(output)
        if answers and self._qa_rows:
            self._fill_answers(answers)
        elif answers:
            self._show_fallback(json.dumps(answers, ensure_ascii=False, indent=2))
        elif dismissed_note:
            self._show_fallback(f"⏭ {dismissed_note}")
        elif output:
            # 非 JSON 出参（用户自由文本回答等）：原文兜底展示
            self._show_fallback(output)

    def _fill_answers(self, answers: dict) -> None:
        """答案按问题文本精确匹配落行；匹配不上按声明顺序分配剩余值。"""
        used: set[str] = set()
        for question, a_label in self._qa_rows:
            answer = answers.get(question)
            if answer is not None:
                used.add(question)
            else:  # 键非问题原文（后端自定义键）：按序取未消费值
                for key, value in answers.items():
                    if key not in used:
                        answer, used = value, used | {key}
                        break
            if answer is not None:
                if not isinstance(answer, str):
                    answer = json.dumps(answer, ensure_ascii=False)
                a_label.setText(f"✅ {answer}")
                a_label.setStyleSheet("")

    def _show_fallback(self, text: str) -> None:
        """无结构化问答对时的出参兜底文本块（懒建）。"""
        if self._pending is not None:
            self._pending.setVisible(False)
        if self._fallback is None:
            self._fallback = BodyText(mono=True)
            self.add_body_widget(self._fallback)
        self._fallback.set_text(text)
        self._fallback.setVisible(True)


#: 待答 QuestionCard 注册表（0807-0148 计划 T4）：tool_call_id → 卡片；
#: 卡片构造时登记、销毁时注销。QUESTION_BRIDGE 按 tool_call_id 定位
#: 待答卡激活选项按钮组；未命中（旧轨渲染/重放）由桥降级弹窗兜底。
_QUESTION_CARD_REGISTRY: dict[str, "QuestionCard"] = {}


def find_question_card(tool_call_id: str) -> "QuestionCard | None":
    """按 tool_call_id 查待答 QuestionCard；已销毁条目惰性清理后返回 None。"""
    card = _QUESTION_CARD_REGISTRY.get(tool_call_id)
    if card is None:
        return None
    try:
        card.isVisible()  # 探活：已销毁的 QWidget 访问抛 RuntimeError
    except RuntimeError:
        _QUESTION_CARD_REGISTRY.pop(tool_call_id, None)
        return None
    return card


def find_pending_question_card(questions: list[str]) -> "QuestionCard | None":
    """按问题文本匹配待答 QuestionCard（0812-0952 计划 ⚠️3 E6 修订）。

    背景：历史 ask 协议的 request_permission 与 session/update 帧 toolCallId
    **双轨不一致**（`ask-1-q1` vs `call_00_…`，实证
    文档/帧存档/ask_reasonix_*.json），id 定位必然 miss 降级弹窗。
    改按问题文本精确匹配兜底：决策帧 rawInput.question 与渲染帧
    rawInput.questions[].question 同文（同一次提问的两条帧路），
    命中唯一即返回。多卡同文命中取注册最早者（同窗口 question 请求
    串行激活，常态至多一张待答卡在场）；已终态卡跳过。
    """
    want = {q.strip() for q in questions if isinstance(q, str) and q.strip()}
    if not want:
        return None
    for tid, card in list(_QUESTION_CARD_REGISTRY.items()):
        try:
            card.isVisible()  # 探活（同 find_question_card 惰性清理纪律）
        except RuntimeError:
            _QUESTION_CARD_REGISTRY.pop(tid, None)
            continue
        if card._answered:
            continue
        if {q for q, _ in card._qa_rows} & want:
            return card
    return None


class McpCard(ToolCard):
    """MCP/未知工具卡：body = 入参 + 出参两节（对标 kilocode McpTool；
    通用入参区不重复附，§2.2）。

    0806 计划 T4 升级：
    - 入参区常驻可回填（_set_input_detail：「（无）」时替换、已有内容
      不覆盖——首帧优先）；
    - 出参区载体 BodyText → BodyHtml（QTextBrowser）：text 经 `<pre>`
      保持等宽纯文本观感；images 通道（data-URI / file://）内嵌 `<img>`
      渲染（同 transcript 用户气泡模式），width 硬限防撑破布局。
    """

    _attach_input_detail = False

    def _build_body(self, payload: dict) -> None:
        in_label = _selectable(BandLabel("入参", self))
        in_label.setStyleSheet(f"color: {self._colors.tool_fg}; font-size: 90%;")
        self.add_body_widget(in_label)
        self._input = BodyText(payload.get("input_detail") or "（无）", mono=True)
        self.add_body_widget(self._input)
        self._out_label = _selectable(BandLabel("出参", self))
        self._out_label.setStyleSheet(f"color: {self._colors.tool_fg}; font-size: 90%;")
        self._out_label.setVisible(False)
        self.add_body_widget(self._out_label)
        self._output = BodyHtml(mono=True)
        self._output.setVisible(False)
        self.add_body_widget(self._output)

    def _set_input_detail(self, detail: str) -> None:
        """迟到入参回填：当前为「（无）」占位时替换；已有内容不覆盖（首帧优先）。"""
        if self._input.toPlainText() == "（无）":
            self._input.set_text(detail)

    def _on_completed(self, payload: dict) -> None:
        output = payload.get("output") or ""
        images = payload.get("images") or []
        if not output and not images:
            return
        self._out_label.setVisible(True)
        self._output.setHtml(compose_mcp_output_html(output, images))
        self._output.setVisible(True)


class MediaReadCard(McpCard):
    """ReadMediaFile 专用卡（0158 计划 T2）：McpCard 入参/出参两节之上，
    入参区 path 文本下方内嵌入参图片略缩图（阅读顺序：路径 → 缩略图 →
    出参）。仅人类查看，不回传 AI、不改协议语义。

    降级纪律（path 文本始终在场，略缩图静默缺失不破坏卡片）：
    - 文件不存在 → 不渲染；
    - 文件 >10MB → 不内嵌，留一行占位说明；
    - 略缩图（320px 辨认用）与出参图（480px 结果用）并存是刻意的：
      入参图回答「AI 要读什么」，出参图回答「AI 读到了什么」。
    """

    def _build_body(self, payload: dict) -> None:
        super()._build_body(payload)
        self._thumb: BodyHtml | None = None
        self._thumb_rendered = False  # 幂等簿记（首帧优先不覆盖）
        if media_path := payload.get("media_path"):
            self._set_media_path(media_path)

    def _set_media_path(self, path: str) -> None:
        """略缩图渲染（幂等）：解析 → 组装 → 插入入参与出参之间。"""
        if self._thumb_rendered:
            return
        html = media_thumb_html(path, self._workspace_root)
        if html is None:
            return  # 文件不存在/解析失败：静默降级（path 文本仍在）
        self._thumb = BodyHtml(html)
        idx = self._body_layout.indexOf(self._out_label)
        self._body_layout.insertWidget(idx, self._thumb)
        self._thumb_rendered = True


# ----------------------------------------------------------------------
# TodoListCard（0808-0627 计划 T2）与 todo 条目共享绘制
# ----------------------------------------------------------------------
def _make_todo_row(entry: dict, colors: CardColors, parent: QWidget,
                   highlight_active: bool = False) -> "_ElidedLabel":
    """todo 条目行（0808-0627 计划 T2 提取共享：TodoListCard 与 TodoCard
    共用，无双写）：☑/▶/☐ + 完成/取消项灰化删除线 + 长文本省略。

    highlight_active=True（TodoListCard 专用）：changed 项以 tool_fg
    醒目（协议层跨调用 diff 产物，渲染层不自行比对）；in_progress 项
    恒醒目（对齐 kilocode TUI 警告色语义）。False（plan 通道 TodoCard）
    维持原视觉零变化。
    """
    status = entry.get("status") or "pending"
    changed = highlight_active and bool(entry.get("changed"))
    label = _ElidedLabel(parent=parent)
    if status in ("completed", "cancelled"):
        label.setText(f"☑ {entry.get('content') or ''}")
        font = label.font()
        font.setStrikeOut(True)
        label.setFont(font)
        # 刚完成的变更项仍醒目（删除线保留，灰色让位 tool_fg）
        label.setStyleSheet(
            f"color: {colors.tool_fg if changed else colors.reasoning_fg};")
    elif status == "in_progress":
        label.setText(f"▶ {entry.get('content') or ''}")
        if highlight_active:
            label.setStyleSheet(f"color: {colors.tool_fg};")
    else:
        label.setText(f"☐ {entry.get('content') or ''}")
        if changed:
            label.setStyleSheet(f"color: {colors.tool_fg};")
    return label


def _fill_todo_list(layout: QVBoxLayout, entries: list,
                    colors: CardColors, parent: QWidget,
                    highlight_active: bool = False) -> tuple[int, int]:
    """清空 layout 并整刷条目（快照语义，幂等）；返回 (done, total)。"""
    while layout.count():
        item = layout.takeAt(0)
        if widget := item.widget():
            widget.deleteLater()
    done = 0
    for entry in entries:
        if (entry.get("status") or "pending") in ("completed", "cancelled"):
            done += 1
        layout.addWidget(
            _make_todo_row(entry, colors, parent, highlight_active))
    return done, len(entries)


def _strip_todos_line(detail: str) -> str:
    """入参区预格式化文本剔除 todos: JSON 行（清单区已渲染时信息重复；
    协议层 _format_input_detail 将 list 值紧凑化为单行，行首键名锚定）。
    """
    lines = [line for line in detail.split("\n")
             if not line.startswith("todos:")]
    return "\n".join(lines).strip("\n")


class TodoListCard(McpCard):
    """todowrite/TodoList 专用卡（0808-0627 计划 T2）：McpCard 入参/出参
    两节之间插清单区（阅读顺序：调用 → 清单 → 回执）。

    - 每次调用一张新卡由工具卡机制天然承担（append_tool_call 每
      toolCallId 建一卡），历史快照逐卡留痕、最新快照恒在对话流底部；
    - changed 高亮消费协议层跨调用 diff 产物（渲染层不自行比对），
      in_progress 项恒醒目（对齐 kilocode TUI 警告色语义）；
    - 清单区在场时入参区 todos: JSON 行抑制（信息重复）；
      todos 为空/残缺 → 清单区不建、入参 JSON 保留（静默降级兜底）；
    - 首帧空壳场景（本厂商）：清单随迟到 update 帧载荷出现
      （apply_update 整刷，幂等）。
    """

    def _build_body(self, payload: dict) -> None:
        entries = payload.get("todos") or []
        self._todo_label: QLabel | None = None
        self._todo_host: QWidget | None = None
        self._todo_layout: QVBoxLayout | None = None
        if entries:
            # 入参区 todos JSON 行抑制（无可视化时保留原文兜底）
            payload = {**payload, "input_detail": _strip_todos_line(
                payload.get("input_detail") or "")}
        super()._build_body(payload)
        if entries:
            self._ensure_todo_section()
            self._set_todos(entries)

    def _ensure_todo_section(self) -> None:
        """清单区构建（入参与出参之间；迟到回填场景首次调用）。"""
        if self._todo_layout is not None:
            return
        self._todo_label = _selectable(BandLabel("清单", self))
        self._todo_label.setStyleSheet(
            f"color: {self._colors.tool_fg}; font-size: 90%;")
        self._todo_host = QWidget(self)
        self._todo_layout = QVBoxLayout(self._todo_host)
        self._todo_layout.setContentsMargins(0, 0, 0, 0)
        self._todo_layout.setSpacing(2)
        idx = self._body_layout.indexOf(self._out_label)
        self._body_layout.insertWidget(idx, self._todo_host)
        self._body_layout.insertWidget(idx, self._todo_label)

    def _set_todos(self, entries: list) -> None:
        """清单区整刷 + 副标题 x/y 项完成（快照语义，幂等）。"""
        assert self._todo_layout is not None
        done, total = _fill_todo_list(
            self._todo_layout, entries, self._colors, self,
            highlight_active=True)
        self.set_subtitle(f"— {done}/{total} 项完成" if total else "")

    def apply_update(self, payload: dict) -> None:
        # 清单区先行：保证同帧 input_detail 回填经 _set_input_detail
        # 走 todos JSON 行抑制路径（否则 JSON 原文先入区、清单后建）
        if payload.get("todos"):
            self._ensure_todo_section()
        super().apply_update(payload)
        if payload.get("todos"):
            self._set_todos(payload["todos"])

    def _set_input_detail(self, detail: str) -> None:
        """迟到入参回填：清单区在场时 todos JSON 行抑制（同首帧口径）。"""
        if self._todo_layout is not None:
            detail = _strip_todos_line(detail)
        if detail:
            super()._set_input_detail(detail)


def _diff_soft_limit_note() -> int:
    """截断尾注行数（与协议层 _BODY_SOFT_LIMIT_LINES 同源表述；
    渲染层不引协议层私有常量，尾注数值硬编与协议层同步——改一处须同步）。
    """
    return 1000


class TodoCard(QFrame):
    """TODO 卡（P7）：📋 任务清单 — N/M 项完成 + 只读 checkbox 列表；
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
        icon = QLabel("📋", self)
        icon.setStyleSheet(f"color: {colors.tool_fg};")
        self._title = _selectable(BandLabel("任务清单", self))
        title_font = self._title.font()
        title_font.setBold(True)
        self._title.setFont(title_font)
        self._subtitle = _selectable(BandLabel(self))
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
        """快照整刷（双通道 todo 同源，F1）；完成/取消项灰 + 删除线。
        0808-0627 计划 T2：条目绘制收敛共享函数（与 TodoListCard 共用），
        plan 通道不高亮（highlight_active=False，视觉零变化）。"""
        done, total = _fill_todo_list(self._list, entries, self._colors, self)
        self._subtitle.setText(f"— {done}/{total} 项完成" if total else "")
