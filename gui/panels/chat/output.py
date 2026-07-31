"""输出区：消息列表、追加、流式块上屏、AI 活动块、自动滚动。

AI 活动块（1602 计划 T4/T5：对话区 AI 活动信息充分展示）：
- 工具调用行 append_tool_call / 状态流转行 append_tool_update——追加式
  上屏（一级不做逐工具就地改写，QTextBrowser 定位成本与收益不匹配）；
- todo 清单块 upsert_todo_block——锚点就地更新（D2-A）：_todo_anchor
  簿记 (start, end) document position，快照到达整段替换；
  轮次收尾 reset_activity_anchors 作废（防跨轮锚点串位）。

渲染纪律与 append_reasoning_chunk 同构：显式 QTextCharFormat 上屏
（防格式经光标位置继承）+ 光标 End 插入 + 滚底。三类活动块的文本
格式与 llm/providers/acp.py 的兜底文本（_tool_call_fallback 等）
保持一致——改一处须同步另一处。

1836 计划 L1 三项：
- T1 推理标题提取：reasoning 块先缓冲至标题行闭合（含 "\n" 或
  end_reasoning 收尾），经 reasoning_heading.split_heading 四级正则
  提取标题加粗上屏、正文维持灰斜体；无标题则整段灰斜体；
- T3 流式合帧节流：append_stream_chunk 入缓冲 + 30ms QTimer 单发
  冲刷；任何非正文上屏（reasoning/工具行/todo/收尾）先 _flush_stream
  强制冲刷保次序，轮次收尾防残帧。
"""
from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QFont, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QTextBrowser

from gui.panels.chat.reasoning_heading import split_heading
from gui.popups import exec_standard_context_menu

_STREAM_FLUSH_MS = 30  # 流式合帧节流间隔（人眼无感下限，1836 计划 D5）


class ChatOutput(QTextBrowser):
    """聊天消息显示区（纯文本渲染 + AI 活动富块）。"""

    def __init__(
        self,
        reasoning_color: str,
        tool_color: str,
        error_color: str,
        parent=None,
    ) -> None:
        """
        :param reasoning_color: 思维链前景色（主题资源包 chat.reasoning_fg 注入）；
            兼作 todo 完成项弱化色（复用不新增键，1602 计划 T7）
        :param tool_color: 工具行/todo 清单前景色（chat.tool_fg）
        :param error_color: 失败状态行前景色（chat.tool_error_fg）
        :param parent: 父控件
        """
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        # 样式由主题 qss 统一（透明融入侧栏，无边框）
        self.setObjectName("ChatOutput")
        self._reasoning_color = QColor(reasoning_color)
        self._tool_color = QColor(tool_color)
        self._error_color = QColor(error_color)
        #: todo 块锚点（起始/结束 document position）；None = 本轮尚无 todo 块
        self._todo_anchor: tuple[int, int] | None = None
        #: T3 流式合帧：正文缓冲 + 单发冲刷定时器
        self._stream_pending = ""
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(_STREAM_FLUSH_MS)
        self._flush_timer.timeout.connect(self._flush_stream)
        #: T1 推理标题提取：标题行未闭合期间的缓冲与判定态
        self._reasoning_buffer = ""
        self._reasoning_decided = True

    def set_reasoning_color(self, color: str) -> None:
        """主题切换时更新思维链前景色（仅影响此后追加的块）。"""
        self._reasoning_color = QColor(color)

    def set_activity_colors(self, tool_fg: str, error_fg: str) -> None:
        """主题切换时更新工具行/失败行前景色（仅影响此后追加的块）。"""
        self._tool_color = QColor(tool_fg)
        self._error_color = QColor(error_fg)

    def contextMenuEvent(self, event) -> None:
        """标准编辑菜单透明化（见 gui/popups.py 与 0751 计划 §3.1）。"""
        exec_standard_context_menu(self, event)

    def append_message(self, role: str, content: str) -> None:
        """追加一条完整消息（role 为显示名，如"我"/"AI"）。"""
        self._flush_stream()
        self.append(f"<b>{role}：</b>")
        self.append(f"{content}<br>")
        self._scroll_to_bottom()

    def begin_stream(self, role: str) -> None:
        """开始一条流式消息（先上前缀）；重置 T1/T3 轮次态。"""
        self._flush_stream()
        self._reasoning_buffer = ""
        self._reasoning_decided = False
        self.append(f"<b>{role}：</b>")

    def append_stream_chunk(self, chunk: str) -> None:
        """流式块上屏（T3 合帧节流）：入缓冲，30ms 单发定时器聚合冲刷。

        显式默认格式在冲刷时施加（防思维链灰斜格式经光标位置继承）。
        """
        self._stream_pending += chunk
        if not self._flush_timer.isActive():
            self._flush_timer.start()

    def append_reasoning_chunk(self, chunk: str) -> None:
        """思维链块上屏：标题行闭合前缓冲（T1），闭合后标题加粗+正文灰斜体。"""
        self._flush_stream()
        if self._reasoning_decided:
            self._insert_reasoning_body(chunk)
            return
        self._reasoning_buffer += chunk
        if "\n" not in self._reasoning_buffer:
            return  # 标题行未闭合，等后续帧（或 end_reasoning 收尾判定）
        self._flush_reasoning_head()

    def end_reasoning(self) -> None:
        """思维链结束：收尾判定残缓冲标题，插入空行与正文分隔。"""
        if not self._reasoning_decided and self._reasoning_buffer:
            self._flush_reasoning_head()
        self._flush_stream()
        self._insert_at_end("\n\n", QTextCharFormat(), scroll=False)

    def end_stream(self) -> None:
        """结束一条流式消息（补空行分隔）；强制冲刷防残帧。"""
        self._flush_stream()
        self.append("<br>")

    # ------------------------------------------------------------------
    # AI 活动块（1602 计划 T4：工具调用行 / 状态流转行）
    # ------------------------------------------------------------------
    def append_tool_call(self, payload: dict) -> None:
        """工具调用行上屏：`◐ ▸ title — summary`（tool_fg 整行着色）。

        is_subagent（task 子代理，D5）时图标改 `⧉`。格式与
        acp._tool_call_fallback 兜底文本保持一致（改一处须同步）。
        """
        self._flush_stream()
        fmt = QTextCharFormat()
        fmt.setForeground(self._tool_color)
        icon = "⧉" if payload.get("is_subagent") else "▸"
        line = f"◐ {icon} {payload.get('title') or '?'}"
        if summary := payload.get("summary"):
            line += f" — {summary}"
        self._insert_at_end(f"\n{line}\n", fmt)

    def append_tool_update(self, payload: dict) -> None:
        """状态流转行上屏：completed `✔ ▸ title`；failed `✖ ▸ title（错误首行）`。

        in_progress 一级不上屏（D1：bash 长任务中途快照帧频繁，上屏即刷屏；
        工具行本身的 ◐ 已表达「进行中」）。一级不做就地改写，状态变化追加
        新行。格式与 acp._tool_update_fallback 兜底文本保持一致。
        """
        status = payload.get("status")
        if status not in ("completed", "failed"):
            return
        self._flush_stream()
        fmt = QTextCharFormat()
        name = payload.get("title") or payload.get("tool_call_id") or "?"
        if status == "failed":
            fmt.setForeground(self._error_color)
            line = f"✖ ▸ {name}"
            if error := payload.get("error"):
                line += f"（{error}）"
        else:
            fmt.setForeground(self._tool_color)
            line = f"✔ ▸ {name}"
        self._insert_at_end(f"{line}\n", fmt)

    # ------------------------------------------------------------------
    # todo 清单块（1602 计划 T5：锚点就地更新，D2-A）
    # ------------------------------------------------------------------
    def upsert_todo_block(self, entries: list) -> None:
        """todo 清单块上屏/就地更新：首帧插入并记录锚点，后续快照整段替换。

        位置不变性：追加式输出区中锚点之后的上屏（工具行/正文）只改动
        锚点之后的 position，锚点自身不受影响；唯一改写锚点区间的是本
        方法自身（替换后重算 end），故 int 簿记够用。降级预案：实测格式
        错乱则退回差异行追加（每次快照全量追加新块），仅需改本方法内部。
        """
        self._flush_stream()
        cursor = self.textCursor()
        if self._todo_anchor is None:
            cursor.movePosition(QTextCursor.MoveOperation.End)
            start = cursor.position()
            self._insert_todo_entries(cursor, entries)
            self._todo_anchor = (start, cursor.position())
        else:
            start, end = self._todo_anchor
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
            self._insert_todo_entries(cursor, entries)
            self._todo_anchor = (start, cursor.position())
        self._scroll_to_bottom()

    def _insert_todo_entries(self, cursor: QTextCursor, entries: list) -> None:
        """在 cursor 处插入清单块（首尾各一空行入锚点区间，替换时结构不变）。

        完成/取消项灰弱化 + 删除线（1425 封存款 K5 摘用，弱化色复用
        reasoning_fg）；格式与 acp._todo_fallback_text 兜底文本保持一致。
        """
        cursor.insertText("\n", QTextCharFormat())
        for entry in entries:
            status = entry.get("status") or "pending"
            fmt = QTextCharFormat()
            if status in ("completed", "cancelled"):
                fmt.setForeground(self._reasoning_color)
                fmt.setFontStrikeOut(True)
                mark = "[x]"
            elif status == "in_progress":
                fmt.setForeground(self._tool_color)
                mark = "[>]"
            else:
                fmt.setForeground(self._tool_color)
                mark = "[ ]"
            cursor.insertText(f"- {mark} {entry.get('content') or ''}\n", fmt)

    def reset_activity_anchors(self) -> None:
        """轮次收尾作废活动锚点：下一轮 todo 作为新块插入（防跨轮串位）。"""
        self._todo_anchor = None

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _flush_stream(self) -> None:
        """T3 冲刷：缓冲正文按默认格式落盘（各非正文上屏方法的次序守卫）。"""
        self._flush_timer.stop()
        if not self._stream_pending:
            return
        text, self._stream_pending = self._stream_pending, ""
        self._insert_at_end(text, QTextCharFormat())

    def _flush_reasoning_head(self) -> None:
        """T1 标题判定：缓冲推理文本经四级正则提取，标题加粗+正文灰斜体。"""
        title, body = split_heading(self._reasoning_buffer)
        if title:
            fmt = QTextCharFormat()
            fmt.setFontWeight(QFont.Weight.Bold)
            self._insert_at_end(f"{title}\n", fmt, scroll=False)
            if body:
                self._insert_reasoning_body(body, scroll=False)
        else:
            self._insert_reasoning_body(body, scroll=False)
        self._reasoning_buffer = ""
        self._reasoning_decided = True
        self._scroll_to_bottom()

    def _insert_reasoning_body(self, text: str, scroll: bool = True) -> None:
        """推理正文统一出口：灰字斜体，与正文样式区分。"""
        fmt = QTextCharFormat()
        fmt.setForeground(self._reasoning_color)
        fmt.setFontItalic(True)
        self._insert_at_end(text, fmt, scroll=scroll)

    def _insert_at_end(
        self, text: str, fmt: QTextCharFormat, scroll: bool = True
    ) -> None:
        """光标 End 处按显式格式插入文本（各上屏方法的统一出口）。"""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text, fmt)
        if scroll:
            self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        bar = self.verticalScrollBar()
        bar.setValue(bar.maximum())
