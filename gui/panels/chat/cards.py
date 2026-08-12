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

横向滚动根治（2026-08-06，work plans/2026-0806-0401 计划 T1/T2）：
- _ElidedLabel 单行省略标签：卡片标题/副标题/todo 条目的长文本按可用宽
  ElideRight 截断 + tooltip 全文兜底；minimumSizeHint 归零 + Ignored
  水平策略，根治 QLabel 长文本 minimumSizeHint=全文像素宽沿布局链把
  ChatTranscriptView 容器撑出横向滚动条的病根（§2.1 探针实证）。
"""
import json
import re
from collections import OrderedDict
from html import escape as _html_escape
from html import unescape as _html_unescape
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui.theme import get_mono_family
from gui.panels.chat.permission_dialog import OTHER_HINT_TEXT
from core.paths import PROJECT_ROOT

#: body 限高内滚动上限（0645 §2.4：固定最大高度 + 块内滚动条，
#: 卡片自身高度有界，不撑爆 QScrollArea 布局）
BODY_MAX_HEIGHT = 320

#: 开合状态 Map 容量（P3 直译 kilocode tool-open-state.ts LRU 2000）
_OPEN_STATE_CAPACITY = 2000

#: 默认开合约定（P2 / 0640-D4-A；2026-0803 拍板翻案：bash 改为运行中开、
#: 完成自动折，对齐子代理卡）：bash 运行中开（完成自动折）、diff 折、
#: read/search/fetch 折、MCP/未知折、子代理运行中开（完成自动折）；
#: todo 常开不走本表（不可折）
_DEFAULT_OPEN = {
    "execute": True,
    "edit": False,
    "read": False,
    "search": False,
    "fetch": False,
    "think": True,
    "other": False,
}

#: 工具 kind → header 图标（0807 计划 D2-C/D3 拍板：彩色 emoji 集，
#: 内置 Noto Color Emoji 回退链渲染、跨机器一致；选默认 emoji 呈现的
#: 单码点字形，无需 VS16；emoji 自带色彩不吃 tool_fg，显眼为设计意图）
_KIND_ICONS = {
    "execute": "💻",
    "edit": "📝",
    "read": "📄",
    "search": "🔍",
    "fetch": "🌐",
    "think": "🤖",
    "other": "🧩",
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
# 单行省略标签（0401 计划 D1/T1）
# ----------------------------------------------------------------------
_TAG_RE = re.compile(r"<[^>]*>")


def _plain_of(text: str) -> str:
    """富文本 → 纯文本（宽度测量 / tooltip 用）：去标签 + HTML 实体反转。"""
    if "<" not in text:
        return text
    return _html_unescape(_TAG_RE.sub("", text))


class _ElidedLabel(QLabel):
    """单行省略标签：长文本按可用宽 ElideRight 截断上屏，tooltip 落全文。

    病根（0401 计划 §2.1 探针实证）：QLabel 长文本 minimumSizeHint =
    全文像素宽且不换行，沿布局链一路上推（卡片 header → CollapsibleCard
    → ChatTranscriptView._container → QScrollArea），把对话区容器撑出
    横向滚动条。本类 minimumSizeHint 归零（默认 Preferred 策略不变），
    布局可任意收缩；resizeEvent 随 splitter 拖动实时重算省略。
    """

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent)
        self._full_text = ""
        if text:
            self.setText(text)

    def minimumSizeHint(self) -> QSize:
        """最小宽贡献归零（行高保留）：卡片可随左栏收缩到任意窄。"""
        return QSize(0, self.fontMetrics().height())

    def setText(self, text: str) -> None:  # Qt 覆写（驼峰命名随 Qt）
        self._full_text = text or ""
        self._refresh_text()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh_text()

    def _refresh_text(self) -> None:
        """按当前可用宽重算上屏文本。

        富文本（DiffCard 两段式双色副标题）：够宽原样上屏保双色，
        不够宽退化为纯文本省略（截断态弃样式保可读，tooltip 同纯文本）。
        """
        full = self._full_text
        width = self.width()
        plain = _plain_of(full)
        self.setToolTip(plain)
        if not full or width <= 0:
            super().setText(full)
            return
        if _TAG_RE.search(full) \
                and self.fontMetrics().horizontalAdvance(plain) <= width:
            super().setText(full)  # 够宽：富文本原样（双色不丢）
        else:
            super().setText(self.fontMetrics().elidedText(
                plain, Qt.TextElideMode.ElideRight, width))


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

    def _content_pixel_height(self) -> int:
        """内容像素高度钩子。默认走 doc.size().height()——QTextDocumentLayout
        （BodyHtml）该值单位即像素，语义正确；BodyText 覆写（见其 docstring）。"""
        return int(self.document().size().height())

    def _fit_height(self) -> None:
        doc = self.document()
        width = self.viewport().width()
        if width > 0:
            doc.setTextWidth(width)
        target = self._content_pixel_height() + 6
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
        self.setObjectName("CardBodyText")
        # ID 选择器作用域（0811 左栏右键菜单透明修复）：无选择器的
        # background 声明会沿父子链级联进 Qt 以本控件为父即时创建的
        # 标准右键菜单，覆盖主题底色致菜单全透明——禁无选择器写法
        self.setStyleSheet(
            "#CardBodyText { background: transparent; border: none; }")
        if mono:
            self.setFont(QFont(get_mono_family()))
        if text:
            self.setPlainText(text)
        self._init_hug(max_height)

    def _content_pixel_height(self) -> int:
        """QPlainTextDocumentLayout 的 size().height() 单位是块数（行数）非像素
        ——0741 空白缺陷根因：4 行文本算出 4+6=10px 被钳到一行高 22px 锁死。
        逐块累加 blockBoundingRect（像素、含折行——长行 wrap 后一块多行
        算足），再加上下 documentMargin，得真实内容像素高。"""
        doc = self.document()
        layout = doc.documentLayout()
        total = 0.0
        block = doc.begin()
        while block.isValid():
            total += layout.blockBoundingRect(block).height()
            block = block.next()
        return int(total + doc.documentMargin() * 2)

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
        # 同 BodyText：ID 选择器作用域，防无选择器 background 级联进
        # 右键菜单（0811 左栏右键菜单透明修复实证根因）
        self.setObjectName("CardBodyHtml")
        self.setStyleSheet(
            "#CardBodyHtml { background: transparent; border: none; }")
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
        # 局部豁免全局 QToolButton 实体化范式（0807 计划 D1-A）：全局 QSS
        # padding 2px 8px + border 2px 吃掉 20px 固定宽的 18px，▸ 被裁成
        # 竖线；此处恢复 autoRaise 轻量文本钮，内容盒全量可用
        self._chevron.setStyleSheet(
            "QToolButton { background: transparent; border: none; padding: 0px; }"
            "QToolButton:hover { background: transparent; border: none; }")
        self._chevron.clicked.connect(lambda: self.set_open(not self._open, user=True))

        self._icon_label = QLabel(icon, self)
        # 图标列定宽居中（0807 计划 D4-A）：字形宽 10–17px 漂移曾致各卡
        # title x 在 46–53 间不齐；定宽 20 后全族归一
        self._icon_label.setFixedWidth(20)
        self._icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._icon_label.setStyleSheet(f"color: {colors.tool_fg};")
        self._title_label = _ElidedLabel(title, self)
        title_font = self._title_label.font()
        title_font.setBold(True)
        self._title_label.setFont(title_font)
        self._subtitle_label = _ElidedLabel(subtitle, self)
        self._subtitle_label.setStyleSheet(f"color: {colors.tool_fg};")
        self._status_label = QLabel(self)
        self._copy_button = QToolButton(self)
        self._copy_button.setAutoRaise(True)
        self._copy_button.setText("⧉")
        # 克制形态复制钮（2026-0803 拍板）：容器全透明无边框，静止低对比
        # 灰（reasoning_fg 复用主题灰，单一来源），hover 加深（tool_fg）+
        # tooltip；定宽 20 与图标列对齐，防 ⧉/✓ 切换时 header 抖动
        self._copy_button.setFixedSize(20, 20)
        self._copy_button.setStyleSheet(
            f"QToolButton {{ background: transparent; border: none; padding: 0px;"
            f" color: {colors.reasoning_fg}; }}"
            f"QToolButton:hover {{ background: transparent; border: none;"
            f" color: {colors.tool_fg}; }}"
            f"QToolButton:disabled {{ color: {colors.tool_fg}; }}")
        self._copy_button.setVisible(False)  # T11：子类 enable_copy 挂载

        header = QHBoxLayout()
        header.setContentsMargins(6, 4, 6, 4)
        header.setSpacing(5)
        header.addWidget(self._chevron)
        header.addWidget(self._icon_label)
        # 标题/副标题拉伸因子 3:2 分享剩余空间（0401 计划实施修正）：
        # 取代原 addStretch 弹性间隔——零拉伸因子时缺口分配顺序会把长
        # 标题压到近 0 宽；拉伸因子保证两标签按 3:2 确定性分割可用宽，
        # 短文本时标签左对齐延展，视觉与原弹性间隔方案一致（状态列仍居右）
        header.addWidget(self._title_label, 3)
        header.addWidget(self._subtitle_label, 2)
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

    def enable_copy(self, getter, tooltip: str = "复制回复") -> None:
        """卡片复制按钮（T11：跨块选择丢失的补偿，0640-D6-A）。

        点击后 ⧉ → ✓ 停留 1.2s 作「已复制」确认反馈（期间禁点防连击），
        随后自动恢复 ⧉。
        """
        self._copy_button.setVisible(True)
        self._copy_button.setToolTip(tooltip)
        self._copy_button.clicked.connect(lambda: self._do_copy(getter))

    def _do_copy(self, getter) -> None:
        """写剪贴板 + ✓ 确认闪烁（1.2s 后 _restore_copy_icon 恢复 ⧉）。"""
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(getter())
        self._copy_button.setText("✓")
        self._copy_button.setEnabled(False)
        QTimer.singleShot(1200, self._restore_copy_icon)

    def _restore_copy_icon(self) -> None:
        self._copy_button.setText("⧉")
        self._copy_button.setEnabled(True)


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
    #: update 帧 title 是否覆盖卡标题（0919 计划 T3：kimi 系 edit 的
    #: in_progress 帧 title 为「Editing <路径>」路径内嵌——DiffCard 置
    #: False 标题恒定工具名，路径改走副标题两段式）
    _accept_title_update = True

    def __init__(self, colors: CardColors, open_state: OpenStateMap,
                 payload: dict,
                 workspace_root: Path | None = None) -> None:
        self._tid = payload.get("tool_call_id") or ""
        self._kind = payload.get("tool_kind") or "other"
        self._open_state = open_state
        self._user_toggled = False
        self._input_detail_attached = False  # T1 入参回填去重（首帧优先）
        #: 略缩图相对路径解析基准（0158 计划 T2，transcript 经工厂注入；
        #: None 时降级 PROJECT_ROOT——agent 工作目录与 IDE 项目根通常一致）
        self._workspace_root = workspace_root
        super().__init__(
            colors,
            _KIND_ICONS.get(self._kind, "🧩"),
            payload.get("title") or "?",
            payload.get("summary") or "")
        # 虚分派重设副标题（0919 T3）：构造器直写 _subtitle_label 绕过
        # 子类覆写，此处补一道让 DiffCard 两段式对首帧 summary 同样生效
        self.set_subtitle(payload.get("summary") or "")
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
            f"#CardBodyText {{ background: transparent; border: none;"
            f" color: {self._colors.tool_error_fg}; }}")
        self.add_body_widget(body)
        self.enable_copy(lambda body=body: body.toPlainText())

    # ------------------------------------------------------------------
    # 状态流转（路由层 append_tool_update 直达）
    # ------------------------------------------------------------------
    def apply_update(self, payload: dict) -> None:
        status = payload.get("status")
        if self._accept_title_update and (title := payload.get("title")):
            self.set_title(title)
        # 0919 计划 T3：副标题迟到刷新（edit/read 路径在 update 帧才到
        # 的后端——kimi 系；其余卡无 summary 键，no-op 无害）
        if summary := payload.get("summary"):
            self.set_subtitle(summary)
        # 0806 计划 T1：入参迟到回填（kimi 系首帧空壳、rawInput 随
        # in_progress 帧补发；首帧优先不覆盖，走既有 GUI 线程通道）
        if detail := payload.get("input_detail"):
            self._set_input_detail(detail)
        # 0158 计划 T1：入参图片路径同频分发（MediaReadCard 略缩图补渲；
        # 基类 no-op，专卡覆写）
        if media_path := payload.get("media_path"):
            self._set_media_path(media_path)
        # 0812-0918 计划 T2-2：execute 迟到 command 同频分发（kimi 系首帧
        # 空壳场景 `$ ` 命令头补挂；基类 no-op，BashCard 覆写）
        if command := payload.get("command"):
            self._set_command(command)
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

    def _set_input_detail(self, detail: str) -> None:
        """update 帧迟到入参回填钩子（0806 计划 T1）：首帧未挂入参区时
        补挂；已挂不覆盖（首帧优先）。McpCard 覆写（其入参区常驻）。"""
        if self._attach_input_detail and not self._input_detail_attached:
            self._add_input_detail(detail)

    def _set_media_path(self, path: str) -> None:
        """入参图片路径钩子（0158 计划 T1）：基类 no-op，
        MediaReadCard 覆写渲染略缩图（幂等，首帧优先）。"""

    def _set_command(self, command: str) -> None:
        """execute 迟到 command 钩子（0812-0918 计划 T2-2）：基类 no-op，
        BashCard 覆写补挂 `$ ` 命令头（幂等，首帧优先）。"""

    def _add_input_detail(self, detail: str) -> None:
        """通用入参区（D3）：body 末弱化灰小块，「尽可能全」的兜底保证。"""
        self._input_detail_attached = True
        label = QLabel("入参", self)
        label.setStyleSheet(f"color: {self._colors.tool_fg}; font-size: 90%;")
        text = BodyText(detail, mono=True)
        text.setStyleSheet(
            f"#CardBodyText {{ background: transparent; border: none;"
            f" color: {self._colors.tool_fg}; }}")
        self.add_body_widget(label)
        self.add_body_widget(text)


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
        self._note = QLabel(self)
        self._note.setStyleSheet(f"color: {self._colors.tool_fg};")
        self._note.setVisible(False)
        self.add_body_widget(self._note)
        self.enable_copy(self._copy_text)

    def _set_command(self, command: str) -> None:
        """`$ ` 粗体命令头（0812-0918 计划 T2-2 兼作迟到补挂钩子）：
        首帧齐备由 _build_body 挂；kimi 系首帧空壳场景 command 随
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
            self._output.setStyleSheet(
                f"#CardBodyText {{ background: transparent; border: none;"
                f" color: {self._colors.tool_error_fg}; }}")
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

    0919 计划 T3：kimi 系 diff/路径在 in_progress update 帧才到（首帧
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
        # kimi 系 diff/路径在 in_progress 帧到达（0919 T1 实证）
        self._render_diff(payload)

    def _on_completed(self, payload: dict) -> None:
        self._render_diff(payload)

    def _render_diff(self, payload: dict) -> None:
        """徽标 + hunk body 挂载（首帧 _build_body 与 update 帧两钩子共用）。"""
        if self._diff_attached:
            return
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
            # 行数徽标常驻标题行右侧（复制钮旁，与 DiffCard 徽标同位同手法）
            badge = QLabel(f"{total} 行", self)
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
    """

    def _build_body(self, payload: dict) -> None:
        self._summary = BodyText()
        self._summary.setVisible(False)
        self.add_body_widget(self._summary)

    def _on_completed(self, payload: dict) -> None:
        summary = payload.get("result_summary") or payload.get("output")
        if summary:
            self._summary.set_text(summary)
            self._summary.setVisible(True)
            self.enable_copy(self._summary.toPlainText)
        if not self._user_toggled:  # 完成自动折（P2；手动记忆优先）
            self.set_open(False)


def _parse_answered_text(output: str) -> dict | None:
    """reasonix ask 出参文本形态 → answers 字典（0812-0952 计划 T3）。

    实证蓝本（帧存档/ask_reasonix_*.json）：reasonix completed
    帧出参非 JSON，而是 `The user answered:` 起头、逐行 `- 键: 答案` 的
    纯文本（kimi 为 `{"answers": {...}}` JSON）。键是问题短标题（header）
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
            self._pending = QLabel("等待用户回答…", self)
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
        hint = QLabel("请选择：", box)
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
        other_hint = QLabel(OTHER_HINT_TEXT, box)
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
            q_label = QLabel(f"❓ {question}", self)
            q_label.setWordWrap(True)
            font = q_label.font()
            font.setBold(True)
            q_label.setFont(font)
            a_label = QLabel("等待用户回答…", self)
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
            # reasonix ask 出参文本形态（0812-0952 计划 T3，非 JSON）
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

    背景：reasonix 的 request_permission 与 session/update 帧 toolCallId
    **双轨不一致**（`ask-1-q1` vs `call_00_…`，实证
    帧存档/ask_reasonix_*.json），id 定位必然 miss 降级弹窗。
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


#: MCP 卡出参图片显示限宽（0806 计划 T4：QTextBrowser 不支持 max-width，
#: 用 width 属性硬限，防 base64 大图撑破卡片布局）
_MCP_IMAGE_WIDTH = 480


def _pretty_json_fallback(text: str) -> str:
    """出参 JSON pretty 化兜底（0806 计划 T4：协议层漏网场景——如旧会话
    历史重放——渲染层 set 前再探测一次；非 JSON 原样返回）。"""
    stripped = text.strip()
    if not stripped.startswith(("[", "{")):
        return text
    try:
        return json.dumps(json.loads(stripped), ensure_ascii=False, indent=2)
    except ValueError:
        return text


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
        in_label = QLabel("入参", self)
        in_label.setStyleSheet(f"color: {self._colors.tool_fg}; font-size: 90%;")
        self.add_body_widget(in_label)
        self._input = BodyText(payload.get("input_detail") or "（无）", mono=True)
        self.add_body_widget(self._input)
        self._out_label = QLabel("出参", self)
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
        self._output.setHtml(self._compose_output_html(output, images))
        self._output.setVisible(True)

    def _compose_output_html(self, output: str, images: list) -> str:
        """出参 HTML 组装：text 转义逐行 `<br>` 连接（等宽字体 + WidgetWidth
        折行——不用 `<pre>`：其不折行会把卡片撑出横向滚动条，0401 计划
        横向根治纪律；DiffCard 同款手法）+ images 逐个 `<img>`（width 硬限）。"""
        parts = []
        if output:
            mono = get_mono_family()
            escaped = _html_escape(_pretty_json_fallback(output))
            parts.append(
                f'<font face="{mono}">' + "<br>".join(escaped.split("\n")) + "</font>")
        for src in images:
            parts.append(f'<img src="{_html_escape(src)}" width="{_MCP_IMAGE_WIDTH}">')
        return "".join(parts)


#: 入参略缩图 width 硬限（0158 计划 T2：320px = 出参图 480px 的 2/3——
#: 略缩图定位是「辨认」不是「细读」）
_MEDIA_THUMB_WIDTH = 320
#: 略缩图文件大小护栏（QTextBrowser 内嵌为全量解码，width 只限显示
#: 不限加载——大图拖慢滚动，超限不内嵌留占位行）
_MEDIA_THUMB_MAX_BYTES = 10 * 1024 * 1024


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
        html = self._thumb_html(path)
        if html is None:
            return  # 文件不存在/解析失败：静默降级（path 文本仍在）
        self._thumb = BodyHtml(html)
        idx = self._body_layout.indexOf(self._out_label)
        self._body_layout.insertWidget(idx, self._thumb)
        self._thumb_rendered = True

    def _thumb_html(self, path: str) -> str | None:
        """media_path → 略缩图 HTML：相对路径按工作区根解析为绝对再拼
        file://（kimi 下发 .tmp/... 相对路径，基准为 agent 工作目录——
        与 IDE 项目根通常一致，不一致时静默缺失，降级方向安全）。"""
        p = Path(path)
        if not p.is_absolute():
            p = (self._workspace_root or PROJECT_ROOT) / p
        if not p.is_file():
            return None
        if p.stat().st_size > _MEDIA_THUMB_MAX_BYTES:
            return "<i>（图片过大，未生成略缩图）</i>"
        return (f'<img src="{_html_escape(p.as_uri())}" '
                f'width="{_MEDIA_THUMB_WIDTH}">')


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
    - 首帧空壳场景（kimi 系）：清单随迟到 update 帧载荷出现
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
        self._todo_label = QLabel("清单", self)
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


#: 工具名 → 卡片类二级分派表（0806 计划 T5，对标 kilocode ToolRegistry：
#: 字典注册式、未命中回退 tool_kind 分派，后续扩充只加表项；键为归一化
#: 后小写工具名。0158 计划 T2 增补 readmediafile——0806「无需专用卡」
#: 裁决随入参略缩图需求撤销；0808-0627 计划 T2 增补 todolist/todowrite
#: ——首帧特判合并会话级单卡的旧路线撤销，每次调用一张 TodoListCard；
#: 0812-0952 计划 T3 增补 ask——reasonix 提问工具（实证：update 帧
#: title="ask"、rawInput.questions 与 kimi 同构，
#: 帧存档/ask_reasonix_*.json）
_TOOL_NAME_CARDS: dict[str, type] = {
    "askuserquestion": QuestionCard,
    "ask": QuestionCard,
    "agent": SubagentCard,
    "task": SubagentCard,
    "readmediafile": MediaReadCard,
    "todolist": TodoListCard,
    "todowrite": TodoListCard,
}


def _normalize_tool_name(title: str) -> str:
    """工具名归一化（0806 计划 T5）：去 `mcp__server__` 命名空间前缀取末段，
    小写化——防真实工具名与分派表项失配回退 McpCard。"""
    name = title.strip()
    if "__" in name:
        name = name.split("__")[-1]
    return name.lower()


def make_tool_card(colors: CardColors, open_state: OpenStateMap,
                   payload: dict,
                   workspace_root: Path | None = None) -> ToolCard:
    """工具卡工厂：工具名二级分派优先（T5），未命中按 tool_kind 分派
    专类（0645 §2.1 规格表）。workspace_root 透传卡片作略缩图相对路径
    解析基准（0158 计划 T2，仅 MediaReadCard 消费）。"""
    if title := payload.get("title"):
        if card_cls := _TOOL_NAME_CARDS.get(_normalize_tool_name(title)):
            return card_cls(colors, open_state, payload, workspace_root)
    kind = payload.get("tool_kind") or "other"
    if kind == "execute":
        return BashCard(colors, open_state, payload, workspace_root)
    if kind == "edit":
        return DiffCard(colors, open_state, payload, workspace_root)
    if kind in ("read", "search", "fetch"):
        return TextOutputCard(colors, open_state, payload, workspace_root)
    if kind == "think":
        return SubagentCard(colors, open_state, payload, workspace_root)
    return McpCard(colors, open_state, payload, workspace_root)


# ----------------------------------------------------------------------
# ThinkingCard（P4 / T6）与 TodoCard（P7 / T7）
# ----------------------------------------------------------------------
class ThinkingCard(CollapsibleCard):
    """思维链卡：流式展开、完成自动折叠（用户手动折叠记忆优先，P4/0640-D7-A）。

    标题经 reasoning_heading.split_heading 提取（1836 L1 资产平移）；
    body 灰斜体全文（与正文样式区分，复用 reasoning_fg）。
    """

    def __init__(self, colors: CardColors, split_heading, parent=None) -> None:
        super().__init__(colors, "🧠", "Thinking", "", parent)
        self._split_heading = split_heading
        self._buffer = ""
        self._user_toggled = False
        self._text = BodyText()
        self._text.setStyleSheet(
            f"#CardBodyText {{ background: transparent; border: none;"
            f" color: {colors.reasoning_fg}; font-style: italic; }}")
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
        """快照整刷（双通道 todo 同源，F1）；完成/取消项灰 + 删除线。
        0808-0627 计划 T2：条目绘制收敛共享函数（与 TodoListCard 共用），
        plan 通道不高亮（highlight_active=False，视觉零变化）。"""
        done, total = _fill_todo_list(self._list, entries, self._colors, self)
        self._subtitle.setText(f"— {done}/{total} 项完成" if total else "")
