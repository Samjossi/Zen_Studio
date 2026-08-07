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

1836 计划 L2 四项（QTextBrowser HTML 子集可达规格，摘 Kilo Code 设计）：
- L2-1 用户消息气泡卡：append_user_message 改 table+bgcolor 单格卡片
  （Qt 富文本经典技法；圆角不可达取直角卡），底色 ChatPack.user_bubble_bg；
- L2-2 工具行层级升级：图标/摘要维持 tool_fg 灰，标题加粗默认前景，
  双格式分段（整行单色的观感升级）；
- L2-3 bash 工具卡：完成态 execute 工具行下追加等宽字体输出卡
  （table+bgcolor + 自带更纱黑体，`$ 命令` 头 + 输出体 + 超限行尾注），
  底色 ChatPack.tool_output_bg；输出正文由协议层净化截尾；
- L2-5 文件路径可点击：_flush_stream 冲刷后对本次区间正则扫描反引号
  `路径[:行号]` 片段，就地施加锚点字符格式（零文本改动、零重排），
  点击经 anchorClicked 信号外抛（setOpenLinks(False) 拦截默认导航）。

2026-0801-0438 计划（文件引用链接着色补全）：
- T1 @路径 分支：_FILE_LINK_RE 升级单正则双分支，输入框 _mention_text
  产出的 @相对路径 引用（AI 复读时同形）一并链接化；
- T2 存在性校验：@分支查盘着色（D4 防 AI 臆造路径误着色），工作区根
  经 set_workspace_root 注入；
- T3 用户气泡卡链接化：append_user_message 转义后 _linkify_html 单趟
  替换为 <a>+<span> 内联色链接，点击链路与正文锚点同一出口。

选区带自绘（2026-0731-2055 方案 A 聊天区推广）：原生带因思源黑体
leading 全部垫底而偏下，qss 透明化抑制后经 gui/selection_band.py
公共辅助自绘墨盒上下对称留白带（与 Markdown 预览页同机理）。
"""
import re
from html import escape as _html_escape
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPalette, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QApplication, QTextBrowser

from gui.panels.chat.reasoning_heading import split_heading
from gui.popups import exec_standard_context_menu
from gui.selection_band import SUPPRESSION_QSS, paint_selection_band
from gui.theme import get_mono_family

_STREAM_FLUSH_MS = 30  # 流式合帧节流间隔（人眼无感下限，1836 计划 D5）

#: L2-5 文件路径链接正则（2026-0801-0438 计划 T1 升级，单正则双分支）：
#: - 反引号分支（bt_* 组）：`路径` / `路径:行号`（kilocode TextPartDisplay
#:   给 Markdown code 片段打 .file-link 的等价物；本仓纯文本渲染保留
#:   反引号，锚点区间含反引号，文本零改动，强制扩展名，L2-5 语义不变）；
#: - @分支（at_* 组）：@路径——输入框拖放/粘贴 _mention_text 的产出
#:   形态（目录带尾 /，工作区外为绝对路径）；(?<![\w@]) 防邮箱
#:   user@host 形态的 @ 误命中，尾标点由使用处 rstrip 裁剪。
_FILE_LINK_RE = re.compile(
    r"`(?P<bt_path>[^\s`]+?\.[A-Za-z0-9]{1,10})(?::(?P<bt_line>\d+))?`"
    r"|(?<![\w@])@(?P<at_path>[^\s`@]+)")

#: @分支尾标点裁剪集：中英文句读均不入链接范围（含全角。，；：？！）」）
_AT_TRAILING_PUNCT = ".,;:!?)]}\"'。，；：？！）】」"


class ChatOutput(QTextBrowser):
    """聊天消息显示区（纯文本渲染 + AI 活动富块）。"""

    def __init__(
        self,
        reasoning_color: str,
        tool_color: str,
        error_color: str,
        user_bubble_bg: str,
        tool_output_bg: str,
        link_fg: str,
        parent=None,
    ) -> None:
        """
        :param reasoning_color: 思维链前景色（主题资源包 chat.reasoning_fg 注入）；
            兼作 todo 完成项弱化色（复用不新增键，1602 计划 T7）与输出卡
            超限行尾注色
        :param tool_color: 工具行/todo 清单前景色（chat.tool_fg）
        :param error_color: 失败状态行前景色（chat.tool_error_fg）
        :param user_bubble_bg: 用户消息气泡卡底色（chat.user_bubble_bg，L2-1）
        :param tool_output_bg: bash 输出卡底色（chat.tool_output_bg，L2-3）
        :param link_fg: 正文文件路径链接色（chat.timeline_read_fg 复用——
            VS Code textLink-foreground 同源值，L2-5）
        :param parent: 父控件
        """
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)  # L2-5：拦截默认导航，anchorClicked 外抛
        # 样式由主题 qss 统一（透明融入侧栏，无边框）
        self.setObjectName("ChatOutput")
        # 选区带自绘（2055 计划方案 A 聊天区推广）：抑原生带，paintEvent 自绘
        self.setStyleSheet(SUPPRESSION_QSS)
        self._reasoning_color = QColor(reasoning_color)
        self._tool_color = QColor(tool_color)
        self._error_color = QColor(error_color)
        self._user_bubble_bg = QColor(user_bubble_bg)
        self._tool_output_bg = QColor(tool_output_bg)
        self._link_color = QColor(link_fg)
        #: @路径 存在性校验的工作区基准（0438 计划 T2，panel 构造后注入）；
        #: None = 未注入（独立控件用法），@分支降级为不校验
        self._workspace_root: Path | None = None
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

    def set_workspace_root(self, root: str) -> None:
        """@路径 引用存在性校验的工作区基准（0438 计划 T2/D4）。"""
        self._workspace_root = Path(root)

    def set_activity_colors(self, tool_fg: str, error_fg: str) -> None:
        """主题切换时更新工具行/失败行前景色（仅影响此后追加的块）。"""
        self._tool_color = QColor(tool_fg)
        self._error_color = QColor(error_fg)

    def set_card_colors(
        self, user_bubble_bg: str, tool_output_bg: str, link_fg: str
    ) -> None:
        """主题切换时更新气泡卡/输出卡底色与链接色（仅影响此后追加的块）。

        0438 计划 T4 已知取舍：气泡卡内链接色为 HTML 内联样式，主题切换
        对已渲染历史消息不重渲染（新消息生效），与既有块同一语义。
        """
        self._user_bubble_bg = QColor(user_bubble_bg)
        self._tool_output_bg = QColor(tool_output_bg)
        self._link_color = QColor(link_fg)

    def wheelEvent(self, event) -> None:
        """禁用 Qt 内建 Ctrl+滚轮缩放字体：Ctrl 按下时吞掉事件，其余走基类滚动。"""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.accept()
            return
        super().wheelEvent(event)

    def contextMenuEvent(self, event) -> None:
        """标准编辑菜单透明化（见 gui/popups.py 与 0751 计划 §3.1）。"""
        exec_standard_context_menu(self, event)

    def paintEvent(self, event) -> None:
        """基类绘制（原生选区带已透明化）后，叠绘半透明对称选区带。

        2055 计划方案 A 聊天区推广：带色复用 link_fg（timeline_read_fg，
        与文件链接同源蓝，守不新增键纪律），缺省回退 QPalette Highlight。
        """
        super().paintEvent(event)
        color = self._link_color or QApplication.palette().color(
            QPalette.ColorRole.Highlight)
        paint_selection_band(self, color)

    def append_message(self, role: str, content: str) -> None:
        """追加一条完整消息（role 为显示名，如"系统"）；用户消息走 append_user_message。"""
        self._flush_stream()
        self.append(f"<b>{role}：</b>")
        self.append(f"{content}<br>")
        self._scroll_to_bottom()

    def append_user_message(self, content: str, images: list | None = None) -> None:
        """用户消息气泡卡上屏（L2-1）：table 单格 + bgcolor 直角卡。

        Qt 富文本不支持圆角，直角灰底卡即「用户消息有卡、AI 消息裸文」
        层级差（1836 计划 D4）的载体可达形态；内容 HTML 转义防用户输入
        的 `<`/`&` 破坏结构（多行换行转 <br>）。

        images（0340 方案 B 计划 T4）：文本后追加每图 `<img>` 缩略
        （Qt 富文本本地 file URI 直渲，宽限 200 防撑破气泡卡；回显
        依赖落盘文件在盘——D7 惰性清理保最近 20 个兜底）。
        """
        self._flush_stream()
        # 0438 计划 T3：先转义后链接化（转义产物 &lt; 等实体语法与路径
        # 字符集不冲突；<a> 标签在转义后插入不破结构），再换行转 <br>
        body = self._linkify_html(_html_escape(content)).replace("\n", "<br>")
        for img in images or []:
            uri = Path(img["path"]).resolve().as_uri()
            body += f'<br><img src="{uri}" width="200">'
        self.append(
            f'<table width="100%" cellspacing="0" cellpadding="8">'
            f'<tr><td bgcolor="{self._user_bubble_bg.name()}">'
            f"<b>我：</b>{body}</td></tr></table>")
        self.append("")  # 卡后与后续内容的间距空行
        self._scroll_to_bottom()

    # （0634 计划 D4 排队气泡三方法已随 0807-2305 计划 c1 拍板整轨
    #  拆除——排队发送改为单条待发驻留输入框，不再上屏排队气泡）

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
        """工具调用行上屏：`◐ ▸ title — summary`（L2-2 双格式分段）。

        图标/摘要 tool_fg 灰、标题加粗默认前景——「标题是动作主体、摘要是
        附属参数」的层级差（摘 kilo-ui basic-tool 标题行骨架设计）。
        is_subagent（task 子代理，D5）时图标改 `⧉`。文本内容与
        acp._tool_call_fallback 兜底文本保持一致（改一处须同步）。
        """
        self._flush_stream()
        icon = "⧉" if payload.get("is_subagent") else "▸"
        fmt_dim = QTextCharFormat()
        fmt_dim.setForeground(self._tool_color)
        fmt_title = QTextCharFormat()
        fmt_title.setFontWeight(QFont.Weight.Bold)
        self._insert_at_end("\n", QTextCharFormat(), scroll=False)
        self._insert_at_end(f"◐ {icon} ", fmt_dim, scroll=False)
        self._insert_at_end(payload.get("title") or "?", fmt_title, scroll=False)
        if summary := payload.get("summary"):
            self._insert_at_end(f" — {summary}", fmt_dim, scroll=False)
        self._insert_at_end("\n", QTextCharFormat())

    def append_tool_update(self, payload: dict) -> None:
        """状态流转行上屏：completed `✔ ▸ title`；failed `✖ ▸ title（错误首行）`。

        in_progress 一级不上屏（D1：bash 长任务中途快照帧频繁，上屏即刷屏；
        工具行本身的 ◐ 已表达「进行中」）。一级不做就地改写，状态变化追加
        新行。文本内容与 acp._tool_update_fallback 兜底文本保持一致。
        L2-2：标题加粗（失败行连标题整体 error 色）；L2-3：execute 工具
        携带 output 时追加 bash 输出卡。
        """
        status = payload.get("status")
        if status not in ("completed", "failed"):
            return
        self._flush_stream()
        name = payload.get("title") or payload.get("tool_call_id") or "?"
        failed = status == "failed"
        fmt_dim = QTextCharFormat()
        fmt_dim.setForeground(self._error_color if failed else self._tool_color)
        fmt_title = QTextCharFormat()
        fmt_title.setFontWeight(QFont.Weight.Bold)
        if failed:  # 失败行标题随错误色（完成行标题默认前景拉开层级）
            fmt_title.setForeground(self._error_color)
        self._insert_at_end(f"{'✖' if failed else '✔'} ▸ ", fmt_dim, scroll=False)
        self._insert_at_end(name, fmt_title, scroll=False)
        if failed and (error := payload.get("error")):
            self._insert_at_end(f"（{error}）", fmt_dim, scroll=False)
        self._insert_at_end("\n", QTextCharFormat(), scroll=False)
        if output := payload.get("output"):  # L2-3 bash 输出卡
            self._insert_tool_output_card(
                payload.get("command"), output, payload.get("output_total_lines"))
        self._scroll_to_bottom()

    def _insert_tool_output_card(
        self, command: str | None, output: str, total_lines: int | None
    ) -> None:
        """bash 输出卡（L2-3）：table 单格等宽字体块——`$ 命令` 头 + 输出体。

        输出正文已由协议层净化（ANSI/`\\r`）并截尾（末 N 行 + 原始行数），
        本方法纯渲染；超限行补灰字尾注（1425 封存款 K6 降级规格摘用）。
        insertHtml 于文档末（表格自带块边界，不经光标格式继承，与显式
        QTextCharFormat 纪律同效）。
        """
        body_lines = output.split("\n")
        lines = []
        if command:
            lines.append(f"<b>$ {_html_escape(command.splitlines()[0])}</b>")
        lines.extend(_html_escape(line) for line in body_lines)
        if total_lines and total_lines > len(body_lines):
            lines.append(
                f'<font color="{self._reasoning_color.name()}">'
                f"…… 共 {total_lines} 行（仅显示末 {len(body_lines)} 行）</font>")
        card = (
            f'<table width="100%" cellspacing="0" cellpadding="6">'
            f'<tr><td bgcolor="{self._tool_output_bg.name()}">'
            f'<font face="{get_mono_family()}">{"<br>".join(lines)}</font>'
            f"</td></tr></table>")
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertHtml(card)
        cursor.insertText("\n", QTextCharFormat())  # 卡后空行间距

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
        """T3 冲刷：缓冲正文按默认格式落盘（各非正文上屏方法的次序守卫）。

        L2-5：落盘后对本区间文件引用片段（反引号路径 / @路径）就地施加
        锚点格式。
        """
        self._flush_timer.stop()
        if not self._stream_pending:
            return
        text, self._stream_pending = self._stream_pending, ""
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)
        start = cursor.position()
        cursor.insertText(text, QTextCharFormat())
        self._linkify_range(start, text)
        self._scroll_to_bottom()

    def _linkify_range(self, start: int, text: str) -> None:
        """L2-5：冲刷区间内文件引用片段就地锚点化（零重排）。

        区间限定本次冲刷文本（start = 插入前文档末位 position，insertText
        的 \\n 与块分隔符 1:1 对应）：规避全文档重复扫描；跨 30ms 合帧
        边界被截断的路径片段不链接化（已知取舍，概率极低）。点击由
        anchorClicked 信号外抛，路由层解析跳查看器。
        0438 计划 T1：@路径 分支与反引号分支共用 _iter_file_links。
        """
        for mstart, mend, path, line in self._iter_file_links(text):
            cursor = QTextCursor(self.document())
            cursor.setPosition(start + mstart)
            cursor.setPosition(start + mend, QTextCursor.MoveMode.KeepAnchor)
            fmt = QTextCharFormat()
            fmt.setAnchor(True)
            fmt.setAnchorHref(f"file:{path}" + (f"#L{line}" if line else ""))
            fmt.setForeground(self._link_color)
            fmt.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SingleUnderline)
            cursor.setCharFormat(fmt)

    def _linkify_html(self, escaped: str) -> str:
        """0438 计划 T3：HTML 转义文本中的文件引用 → <a> 内联色链接。

        供用户气泡卡使用（HTML 通道，_linkify_range 的文本游标不适用）。
        Qt 富文本 <a> 颜色取 palette Link 不可控，嵌 <span style=color>
        覆盖为 _link_color；单趟拼接避免替换产物被二次扫描。
        """
        color = self._link_color.name()
        parts, last = [], 0
        for mstart, mend, path, line in self._iter_file_links(escaped):
            href = f"file:{path}" + (f"#L{line}" if line else "")
            parts.append(escaped[last:mstart])
            parts.append(
                f'<a href="{href}"><span style="color:{color}">'
                f"{escaped[mstart:mend]}</span></a>")
            last = mend
        if not parts:
            return escaped
        parts.append(escaped[last:])
        return "".join(parts)

    def _iter_file_links(self, text: str):
        """文件引用统一判定（两渲染通道共用，0438 计划 3.1）。

        产出 (start, end, path, line|None)：反引号分支区间含反引号、
        免存在性校验（L2-5 语义）；@分支区间含 @、尾标点裁剪、须经
        存在性校验（D3/D4：@是显式引用动作，误报防护靠查盘而非字符集）。
        """
        for match in _FILE_LINK_RE.finditer(text):
            if (bt_path := match.group("bt_path")) is not None:
                yield match.start(), match.end(), bt_path, match.group("bt_line")
                continue
            raw = match.group("at_path").rstrip(_AT_TRAILING_PUNCT)
            if not raw or not self._mention_exists(raw):
                continue
            yield match.start(), match.start() + 1 + len(raw), raw, None

    def _mention_exists(self, path: str) -> bool:
        """@路径 存在性校验（0438 计划 D4）：绝对路径直查，相对按工作区根。

        命中频次受 @引用 数量约束（每条消息个位数），os.stat 走系统缓存
        代价可忽略，不做结果缓存（负缓存会对新建文件产生陈旧误判）。
        """
        p = Path(path)
        if not p.is_absolute():
            if self._workspace_root is None:
                return True  # 未注入根（独立控件用法）降级为不校验
            p = self._workspace_root / p
        return p.exists()

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
