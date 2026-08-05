"""对话区卡片轨道视图（0645 融合计划期二 T3/T5/T10）：ChatTranscriptView。

载体（0640-D1-B 拍板）：QScrollArea + 块级 QWidget + QVBoxLayout +
底部 stretch——块序列取代旧轨「行文本流」，折叠是 QWidget.setVisible
真显隐，状态流转是属性更新。与旧轨 ChatOutput 鸭式同接口
（append_message / append_user_message / begin_stream / append_stream_chunk /
append_reasoning_chunk / end_reasoning / end_stream / append_tool_call /
append_tool_update / upsert_todo_block / reset_activity_anchors /
append_queued_user_message / promote_queued / discard_queued（0634 计划 D4
排队气泡三方法）/ set_workspace_root / anchorClicked / set_*_colors），
panel 路由层按设置项选轨后零分支复用同一代码路径。

块组件（0640 §4 架构图落地）：
- UserBubbleBlock  用户气泡（L2-1 形态平移：灰底直角卡 + 链接化 + 图片回显）
- TextStreamBlock  正文流式块（30ms 合帧节流 + 文件链接化平移；
                   自身高度贴内容无内滚动——主内容块不限高）
- SystemBlock      系统消息块
- 工具卡/思维链卡/TODO 卡见 cards.py（CollapsibleCard 族）

跨块文本选择丢失（widget 路线固有代价，0640 §9）以块内选择 + 卡片
复制按钮（T11）补偿；旧轨双轨开关为回退通道。

横向滚动根治（2026-08-06，work plans/2026-0806-0401 计划 T3）：
横向滚动条 ScrollBarAlwaysOff 硬关闭——治本在 cards.py _ElidedLabel
（单行 QLabel 长文本撑宽容器的病根截断 + 最小宽归零）与 Body 块
WidgetWidth 换行，本策略兜底任何未来块类型异常撑宽只裁边不滚动。
"""
from html import escape as _html_escape
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QTextCharFormat, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui.panels.chat.cards import (
    BodyHtml,
    CardColors,
    OpenStateMap,
    ThinkingCard,
    TodoCard,
    ToolCard,
    make_tool_card,
)
from gui.panels.chat.file_links import (
    iter_file_links,
    linkify_html,
    make_mention_checker,
)
from gui.panels.chat.reasoning_heading import split_heading
from gui.popups import exec_standard_context_menu

_STREAM_FLUSH_MS = 30  # 流式合帧节流间隔（1836 计划 D5 平移）

#: 回合间距（2000 计划 D4）：消息与消息之间的布局层固定间距，与块内
#: 间距（setSpacing(6)）构成两级体系；布局层抽象不入文本（D1-B），
#: 复制/选择/断言零污染，数值单一常量一处可调
_TURN_GAP_PX = 14


class TextStreamBlock(BodyHtml):
    """正文流式块：30ms 合帧节流 + 冲刷区间文件引用就地锚点化（L2-5 平移）。

    主内容块不限高（max_height=None）、无内滚动，自身高度贴内容；
    跨帧被截断的路径片段不链接化（同旧轨已知取舍）。
    """

    def __init__(self, role: str, link_color, mention_exists, parent=None) -> None:
        super().__init__("", max_height=None, parent=parent)
        self._link_color = link_color
        self._mention_exists = mention_exists
        self._stream_pending = ""
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(_STREAM_FLUSH_MS)
        self._flush_timer.timeout.connect(self.flush_stream)
        if role:
            cursor = self.textCursor()
            fmt = QTextCharFormat()
            fmt.setFontWeight(600)
            # 前缀与正文同行（2000 计划 D3-A）：去换行对齐旧轨
            # <b>AI：</b> 与用户气泡 <b>我：</b> 形态，块内无空段落
            cursor.insertText(f"{role}：", fmt)

    def append_chunk(self, chunk: str) -> None:
        """流式块入缓冲，30ms 单发定时器聚合冲刷（T3 平移）。"""
        self._stream_pending += chunk
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def flush_stream(self) -> None:
        """冲刷：缓冲正文按默认格式落块 + 本区间文件引用锚点化。"""
        self._flush_timer.stop()
        if not self._stream_pending:
            return
        text, self._stream_pending = self._stream_pending, ""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        start = cursor.position()
        cursor.insertText(text, QTextCharFormat())
        for mstart, mend, path, line in iter_file_links(text, self._mention_exists):
            link_cursor = QTextCursor(self.document())
            link_cursor.setPosition(start + mstart)
            link_cursor.setPosition(start + mend, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setAnchor(True)
            fmt.setAnchorHref(f"file:{path}" + (f"#L{line}" if line else ""))
            fmt.setForeground(self._link_color)
            fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
            link_cursor.setCharFormat(fmt)

    def contextMenuEvent(self, event) -> None:
        """标准编辑菜单透明化（与旧轨同，gui/popups.py 先例）。"""
        exec_standard_context_menu(self, event)


class UserBubbleBlock(QFrame):
    """用户气泡块（L2-1 形态平移）：灰底直角卡 + 链接化 + 图片缩略回显。

    排队形态（0634 计划 D4）：queued=True 时卡内顶部加「排队中」灰徽标
    + 右上角 ×删钮（语义对齐 kilocode Queued 徽标，可删不可编辑）；
    出队发送时 promote() 摘除徽标行转正式气泡（同一控件改写不重复
    append），删除×经 remove_requested 外抛由 panel 剔除队列项。
    """

    #: 锚点点击外抛（QUrl，transcript 转发）
    link_clicked = Signal(QUrl)

    #: 排队气泡 ×删（仅排队态连接；promote 后徽标行销毁不再发射）
    remove_requested = Signal()

    def __init__(
        self,
        content: str,
        images: list | None,
        colors: CardColors,
        mention_exists,
        parent=None,
        *,
        queued: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ChatUserBubble")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(
            f"#ChatUserBubble {{ background-color: {colors.user_bubble_bg};"
            f" border-radius: 6px; }}")
        body = linkify_html(_html_escape(content), colors.link_fg, mention_exists)
        body = body.replace("\n", "<br>")
        for img in images or []:
            uri = Path(img["path"]).resolve().as_uri()
            body += f'<br><img src="{uri}" width="200">'
        text = BodyHtml(f"<b>我：</b>{body}", max_height=None, parent=self)
        text.anchorClicked.connect(self.link_clicked)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        #: 排队徽标行（queued 才建；promote 摘除后归 None）
        self._queued_header: QWidget | None = None
        if queued:
            self._queued_header = self._build_queued_header(colors)
            layout.addWidget(self._queued_header)
        layout.addWidget(text)

    def _build_queued_header(self, colors: CardColors) -> QWidget:
        """排队徽标行：左「排队中」灰徽标 + 右 ×删钮（徽标灰复用 tool_fg，
        不新增主题键）。"""
        header = QWidget(self)
        row = QHBoxLayout(header)
        row.setContentsMargins(0, 0, 0, 0)
        badge = QLabel("排队中", header)
        badge.setStyleSheet(f"color: {colors.tool_fg};")
        row.addWidget(badge)
        row.addStretch(1)
        remove = QToolButton(header)
        remove.setText("×")
        remove.setFixedSize(16, 16)
        remove.setToolTip("删除排队消息")
        remove.setCursor(Qt.CursorShape.ArrowCursor)
        remove.setStyleSheet(
            f"QToolButton {{ border: none; font-weight: 600; padding: 0;"
            f" color: {colors.tool_fg}; }}")
        remove.clicked.connect(self.remove_requested)
        row.addWidget(remove)
        return header

    def promote(self) -> None:
        """出队发送：摘除徽标行转正式气泡（0634 计划 D4）。"""
        if self._queued_header is not None:
            self.layout().removeWidget(self._queued_header)
            self._queued_header.deleteLater()
            self._queued_header = None


class SystemBlock(BodyHtml):
    """系统消息块（后端切换提示 / 附件提示等）。"""

    def __init__(self, role: str, content: str, parent=None) -> None:
        super().__init__(
            f"<b>{_html_escape(role)}：</b>{_html_escape(content)}",
            max_height=None, parent=parent)


class ChatTranscriptView(QScrollArea):
    """卡片轨道对话区视图（旧轨 ChatOutput 鸭式同接口，见模块 docstring）。

    开合状态 Map 存于本实例（多标签天然隔离，0640 §9 落实例方案）；
    主题色经 CardColors 袋共享引用注入，主题切换仅影响此后新建的块
    （与旧轨同语义）。
    """

    #: 正文文件路径链接点击（与旧轨 anchorClicked 同签名 QUrl；
    #: panel._on_output_link 零改动复用）
    anchorClicked = Signal(QUrl)

    def __init__(self, chat_pack: dict, parent=None) -> None:
        """
        :param chat_pack: 主题 ChatPack（diff_add_fg/diff_del_fg 为 0645
            计划新增键；链接色复用 timeline_read_fg，单一来源纪律）
        """
        super().__init__(parent)
        self._colors = CardColors(
            chat_pack["reasoning_fg"], chat_pack["tool_fg"],
            chat_pack["tool_error_fg"], chat_pack["user_bubble_bg"],
            chat_pack["tool_output_bg"], chat_pack["timeline_read_fg"],
            chat_pack["diff_add_fg"], chat_pack["diff_del_fg"])
        self._open_state = OpenStateMap()
        self._workspace_root: Path | None = None

        self.setWidgetResizable(True)
        # 横向滚动硬关闭（0401 计划 D3/T3）：内容自适应由 _ElidedLabel
        # 截断 + Body 块 WidgetWidth 换行治本，此处兜底——任何块异常撑宽
        # 只裁边，永不出现横向滚动条
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setObjectName("ChatTranscript")
        self.setStyleSheet("#ChatTranscript { background: transparent; border: none; }")
        self._container = QWidget(self)
        self._container.setStyleSheet("background: transparent;")
        self._blocks = QVBoxLayout(self._container)
        self._blocks.setContentsMargins(8, 6, 8, 8)
        self._blocks.setSpacing(6)
        self._blocks.addStretch(1)
        self.setWidget(self._container)

        #: 当前正文流式块（首个正文帧懒建 / end_stream、reset 清）
        self._stream_block: TextStreamBlock | None = None
        #: 待落块的角色前缀（2000 计划 D2-A 懒建：begin_stream 簿记，
        #: 首个正文帧到达才建块写入——思维链/工具开头的回合不留孤块）
        self._pending_role = ""
        #: 当前思维链卡（reasoning 首帧懒建 / end_reasoning 收尾）
        self._thinking_card: ThinkingCard | None = None
        #: 工具卡表（toolCallId → ToolCard，轮次收尾清簿记不清屏幕）
        self._tool_cards: dict[str, ToolCard] = {}
        #: 本轮 todo 卡（快照整刷；轮次收尾作废簿记，下一轮新卡）
        self._todo_card: TodoCard | None = None

    # ------------------------------------------------------------------
    # 注入与主题（panel 构造/切换链路）
    # ------------------------------------------------------------------
    def set_workspace_root(self, root: str) -> None:
        """@路径 引用存在性校验的工作区基准（0438 计划 T2 平移）。"""
        self._workspace_root = Path(root)

    def set_reasoning_color(self, color: str) -> None:
        self._colors.reasoning_fg = color

    def set_activity_colors(self, tool_fg: str, error_fg: str) -> None:
        self._colors.tool_fg = tool_fg
        self._colors.tool_error_fg = error_fg

    def set_card_colors(
        self, user_bubble_bg: str, tool_output_bg: str, link_fg: str
    ) -> None:
        self._colors.user_bubble_bg = user_bubble_bg
        self._colors.tool_output_bg = tool_output_bg
        self._colors.link_fg = link_fg

    def set_diff_colors(self, add_fg: str, del_fg: str) -> None:
        self._colors.diff_add_fg = add_fg
        self._colors.diff_del_fg = del_fg

    # ------------------------------------------------------------------
    # 消息块（鸭式接口：append_message / append_user_message / 流式三件套）
    # ------------------------------------------------------------------
    def append_message(self, role: str, content: str) -> None:
        """系统消息块上屏（先冲刷正文防次序颠倒，与旧轨 _flush_stream 同律）。"""
        self._flush_stream()
        self._add_block(SystemBlock(role, content, self._container))

    def append_user_message(self, content: str, images: list | None = None) -> None:
        self._flush_stream()
        block = UserBubbleBlock(
            content, images, self._colors, self._mention_checker(), self._container)
        block.link_clicked.connect(self.anchorClicked)
        self._add_block(block)
        self._add_turn_gap()  # 用户消息后回合间距（2000 计划 Part 2）

    # ------------------------------------------------------------------
    # 排队气泡三方法（0634 计划 D4，旧轨 ChatOutput 同构接口）
    # ------------------------------------------------------------------
    def append_queued_user_message(self, content: str, images: list | None = None):
        """排队气泡上屏：用户气泡 + 「排队中」徽标 + ×；返回句柄（块本身）。

        不入回合间距（转正后紧随新轮 AI 流，间距归 end_stream 回合末）；
        ×删经块 remove_requested 信号由 panel 路由剔除队列项。
        """
        self._flush_stream()
        block = UserBubbleBlock(
            content, images, self._colors, self._mention_checker(),
            self._container, queued=True)
        block.link_clicked.connect(self.anchorClicked)
        self._add_block(block)
        return block

    def promote_queued(self, handle) -> None:
        """出队发送：句柄块徽标摘除转正式气泡（同一控件改写不重复 append）。"""
        if handle is not None:
            handle.promote()
            self._scroll_to_bottom()

    def discard_queued(self, handle) -> None:
        """×删/停止清空：句柄块从布局移除并销毁。"""
        if handle is not None:
            self._blocks.removeWidget(handle)
            handle.deleteLater()

    def begin_stream(self, role: str) -> None:
        """开始一条流式消息：懒建——只簿记角色，首个正文帧才建块写前缀。

        2000 计划 D2-A：抢先建块会让思维链/工具开头的回合留下只含
        「AI：」的孤块（空段落渲染为空白行）；懒建后无正文即无块。
        """
        self._flush_stream()
        self._pending_role = role

    def append_stream_chunk(self, chunk: str) -> None:
        if self._stream_block is None:
            self._create_stream_block()  # 懒建落块（含 begin_stream 簿记的前缀）
        self._stream_block.append_chunk(chunk)
        self._scroll_to_bottom()

    def append_reasoning_chunk(self, chunk: str) -> None:
        """思维链首帧懒建 ThinkingCard（流式展开），后续帧直递。"""
        self._flush_stream()
        if self._thinking_card is None:
            self._thinking_card = ThinkingCard(
                self._colors, split_heading, self._container)
            self._add_block(self._thinking_card)
        self._thinking_card.append_chunk(chunk)
        self._scroll_to_bottom()

    def end_reasoning(self) -> None:
        """思维链收尾：完成自动折叠（用户手动记忆优先，P4）。"""
        if self._thinking_card is not None:
            self._thinking_card.finish()
            self._thinking_card = None
        self._flush_stream()

    def end_stream(self) -> None:
        """流式收尾：强制冲刷防残帧；回合末插间距与下条消息分隔。"""
        self._flush_stream()
        self._pending_role = ""
        self._add_turn_gap()  # AI 回合末回合间距（2000 计划 Part 2）

    # ------------------------------------------------------------------
    # AI 活动块（鸭式接口：tool_call / tool_call_update / todo）
    # ------------------------------------------------------------------
    def append_tool_call(self, payload: dict) -> None:
        """工具卡上屏：工厂分派专类；toolCallId 簿记供状态流转寻卡。"""
        self._flush_stream()
        card = make_tool_card(self._colors, self._open_state, payload)
        self._add_block(card)
        if tid := payload.get("tool_call_id"):
            self._tool_cards[tid] = card

    def append_tool_update(self, payload: dict) -> None:
        """状态流转：寻卡属性更新（◐→✔/✖ 同卡更新、无新增块）。"""
        self._flush_stream()
        tid = payload.get("tool_call_id") or ""
        card = self._tool_cards.get(tid)
        if card is None:
            # 容错：tool_call 帧缺失（部分更新直达）——按 update 载荷补建卡
            card = make_tool_card(self._colors, self._open_state, payload)
            self._add_block(card)
            if tid:
                self._tool_cards[tid] = card
        card.apply_update(payload)
        self._scroll_to_bottom()

    def upsert_todo_block(self, entries: list) -> None:
        """todo 卡上屏/整刷：首帧建卡，后续快照 set_entries 整刷（弃锚点）。"""
        self._flush_stream()
        if self._todo_card is None:
            self._todo_card = TodoCard(self._colors, self._container)
            self._add_block(self._todo_card)
        self._todo_card.set_entries(entries)
        self._scroll_to_bottom()

    def reset_activity_anchors(self) -> None:
        """轮次收尾作废活动簿记（屏幕块保留；下一轮 todo/思维链新卡）。"""
        self._todo_card = None
        self._thinking_card = None
        self._stream_block = None
        self._pending_role = ""
        self._tool_cards.clear()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _mention_checker(self):
        return make_mention_checker(self._workspace_root)

    def _link_qcolor(self):
        from PySide6.QtGui import QColor
        return QColor(self._colors.link_fg)

    def _add_block(self, widget: QWidget) -> None:
        """块追加（底部 stretch 前插入）+ 滚底（布局沉降后事件循环末执行）。"""
        self._blocks.insertWidget(self._blocks.count() - 1, widget)
        widget.show()
        self._scroll_to_bottom()

    def _create_stream_block(self) -> None:
        """懒建落块（2000 计划 D2-A）：首个正文帧到达才建正文块，
        前缀取 begin_stream 簿记的角色（用后即清，续段块无前缀）。"""
        role, self._pending_role = self._pending_role, ""
        self._stream_block = TextStreamBlock(
            role, self._link_qcolor(), self._mention_checker(), self._container)
        self._stream_block.link_clicked.connect(self.anchorClicked)
        self._add_block(self._stream_block)

    def _add_turn_gap(self) -> None:
        """回合间距（2000 计划 D1-B/D4）：布局层固定间距项，底部 stretch
        前插入；纯布局项非 widget，不进任何块文本（复制/断言零污染）。"""
        self._blocks.insertSpacing(self._blocks.count() - 1, _TURN_GAP_PX)

    def _flush_stream(self) -> None:
        """各非正文上屏方法的次序守卫：冲刷即封存（用后作废，同思维链卡纪律）。

        不置 None 会让后续文本帧回流首个正文块（1015 修复计划 §2：
        块 A 在布局中位于工具卡之前，总结回流即置顶）。
        """
        if self._stream_block is not None:
            self._stream_block.flush_stream()
            self._stream_block = None

    def _scroll_to_bottom(self) -> None:
        QTimer.singleShot(0, self._do_scroll)

    def _do_scroll(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
