"""卡片通用技术基础件：无厂商语义的 Qt 外壳、正文块与工具卡生命周期。

本模块只承接折叠/展开、复制、主题色、基础 body、图片显示、开合记忆和
工具卡状态流转；不出现任何厂商工具名、私有载荷字段或后台 ID 分支。具体卡片
格式、工具名分派与载荷解释分别归 `vendors/<vendor>/cards.py`、`factory.py`
和 `presenter.py`。
"""
import json
import re
from collections import OrderedDict
from html import escape as _html_escape
from html import unescape as _html_unescape
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ide.gui.popups import exec_standard_context_menu, make_label_selectable
from ide.gui.selection_band import BandLabel, SUPPRESSION_QSS, paint_selection_band
from ide.gui.theme import get_mono_family
from ide.core.paths import PROJECT_ROOT

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


def _selectable(label: QLabel) -> QLabel:
    """开鼠标拖选复制 + 右键精简菜单：QLabel 默认仅键盘可选，卡片文本
    （问答对、todo 条目、标题等）用户有整段复制诉求；Qt 内建右键菜单为
    英文原生形态，统一委托 popups.make_label_selectable 拦截改弹中文
    精简两项。选中色经 base.qss 全局 selection 规则沿控件树继承，无需
    逐卡配。"""
    return make_label_selectable(label)


class _ElidedLabel(BandLabel):
    """单行省略标签：长文本按可用宽 ElideRight 截断上屏，tooltip 落全文。

    病根（0401 计划 §2.1 探针实证）：QLabel 长文本 minimumSizeHint =
    全文像素宽且不换行，沿布局链一路上推（卡片 header → CollapsibleCard
    → ChatTranscriptView._container → QScrollArea），把对话区容器撑出
    横向滚动条。本类 minimumSizeHint 归零（默认 Preferred 策略不变），
    布局可任意收缩；resizeEvent 随 splitter 拖动实时重算省略。
    """

    def __init__(self, text: str = "", parent=None) -> None:
        super().__init__(parent=parent)
        self._full_text = ""
        _selectable(self)
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
def _body_stylesheet(object_name: str, extra: str = "") -> str:
    """正文块样式表单点拼接：抑制声明常驻，附加声明只经此汇入。

    Qt 样式表是整表替换语义——对正文块裸调 setStyleSheet 覆写（上色/
    斜体等）会冲掉 SUPPRESSION_QSS，原生偏下选区带回归并与自绘带
    双层叠加（2026-10-17 用户真机截图实证）；故构造期样式与事后附加
    样式统一走本函数，objectName 作 ID 选择器（0811 规约不变）。
    """
    sheet = (f"#{object_name} {{ background: transparent; border: none; "
             + SUPPRESSION_QSS)
    if extra:
        sheet += " " + extra
    return sheet + " }"


class _HugHeightMixin:
    """内容高度自适应 + 上限封顶：doc 实际高 ≤ BODY_MAX_HEIGHT 时贴内容，
    超限封顶出块内滚动条（防单卡撑爆 QScrollArea 布局）。"""

    def _init_hug(self, max_height: int | None, bottom_padding: int = 6) -> None:
        """bottom_padding：拟合高度在文档内容之外追加的底部补偿像素，
        默认 6 保持存量卡片行为；气泡等需收紧的场景传小值。"""
        self._max_height = max_height
        self._bottom_padding = bottom_padding
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
        target = self._content_pixel_height() + self._bottom_padding
        if self._max_height is not None:
            target = min(target, self._max_height)
        target = max(target, 22)
        if target != self.height():
            self.setFixedHeight(target)
            # Qt 只在文档宽度变化引发重排（documentSizeChanged）时重算滚动条
            # 范围，纯高度 resize 不重算——setHtml 瞬间控件仍是默认高，范围按
            # 旧高度定格后残留（0831 气泡内滚动条常显缺陷根因）。内容未超限
            # 时滚动条本不该出现，手动归零；超限（封顶出块内滚动条）场景不动
            bar = self.verticalScrollBar()
            if (bar.maximum() > 0
                    and self._content_pixel_height() <= self.viewport().height()):
                bar.setRange(0, 0)


class BodyText(_HugHeightMixin, QPlainTextEdit):
    """body 等宽/正文只读块（限高内滚动；mono=True 用更纱黑体）。"""

    def __init__(
        self,
        text: str = "",
        mono: bool = False,
        max_height: int | None = BODY_MAX_HEIGHT,
        bottom_padding: int = 6,
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
        self.setStyleSheet(_body_stylesheet(self.objectName()))
        if mono:
            self.setFont(QFont(get_mono_family()))
        if text:
            self.setPlainText(text)
        self._init_hug(max_height, bottom_padding)

    def set_extra_style(self, extra: str) -> None:
        """附加声明（颜色/斜体等）统一入口：重建整表，抑制声明常驻。

        禁对本类裸调 setStyleSheet——整表替换会丢抑制声明（根因见
        _body_stylesheet）；scripts/check_qss_selector_guard.py 静态
        拦截。
        """
        self.setStyleSheet(_body_stylesheet(self.objectName(), extra))

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

    def paintEvent(self, event) -> None:
        """基类绘制后叠绘选区对称带。

        Qt 原生选区带 = 整行高（含 leading），思源黑体 leading 垫底
        致高亮视觉偏下——抑制后按行墨盒对称自绘（详见 selection_band）。
        """
        super().paintEvent(event)
        paint_selection_band(self, QApplication.palette().color(
            QPalette.ColorRole.Highlight))

    def contextMenuEvent(self, event) -> None:
        """只读正文统一右键菜单：精简后恰为「复制」「全选」两项。

        不覆写则回落 Qt 原生标准菜单（英文、带主题图标、含快捷键
        显示后缀），与 AI 回复流式块的精简菜单形态不一致。
        """
        exec_standard_context_menu(self, event, simplify=True)


class BodyHtml(_HugHeightMixin, QTextBrowser):
    """body 富文本只读块（diff 三色等需行级着色的场景；限高内滚动）。"""

    #: 锚点点击外抛（transcript 转发给面板解析跳查看器）
    link_clicked = Signal(QUrl)

    def __init__(
        self,
        html: str = "",
        mono: bool = False,
        max_height: int | None = BODY_MAX_HEIGHT,
        bottom_padding: int = 6,
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
        self.setStyleSheet(_body_stylesheet(self.objectName()))
        if mono:
            self.setFont(QFont(get_mono_family()))
        self.anchorClicked.connect(self.link_clicked)
        if html:
            self.setHtml(html)
        self._init_hug(max_height, bottom_padding)

    def set_extra_style(self, extra: str) -> None:
        """附加声明统一入口（同 BodyText，禁裸 setStyleSheet，见该处）。"""
        self.setStyleSheet(_body_stylesheet(self.objectName(), extra))

    def paintEvent(self, event) -> None:
        """基类绘制后叠绘选区对称带（同 BodyText，见该处注释）。"""
        super().paintEvent(event)
        paint_selection_band(self, QApplication.palette().color(
            QPalette.ColorRole.Highlight))

    def contextMenuEvent(self, event) -> None:
        """只读正文统一右键菜单（同 BodyText，见该处注释）。"""
        exec_standard_context_menu(self, event, simplify=True)


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
# 无厂商语义的图片/输出 HTML 技术件（具体厂商只决定“展示哪个路径/数据”）
# ----------------------------------------------------------------------
#: MCP 卡出参图片显示限宽（QTextBrowser 不支持 max-width，用 width 属性硬限）
MCP_IMAGE_WIDTH = 480
#: 入参略缩图 width 硬限（320px = 出参图 480px 的 2/3，定位是“辨认”）
MEDIA_THUMB_WIDTH = 320
#: 略缩图文件大小护栏（QTextBrowser 内嵌为全量解码，width 只限显示不限加载）
MEDIA_THUMB_MAX_BYTES = 10 * 1024 * 1024


def pretty_json_fallback(text: str) -> str:
    """出参 JSON pretty 化兜底（非 JSON 原样返回）。"""
    stripped = text.strip()
    if not stripped.startswith(("[", "{")):
        return text
    try:
        return json.dumps(json.loads(stripped), ensure_ascii=False, indent=2)
    except ValueError:
        return text


def compose_mcp_output_html(output: str, images: list) -> str:
    """MCP 出参 HTML：文本逐行 `<br>`（等宽、可折行）+ 图片逐个 `<img>`。"""
    parts = []
    if output:
        mono = get_mono_family()
        escaped = _html_escape(pretty_json_fallback(output))
        parts.append(
            f'<font face="{mono}">' + "<br>".join(escaped.split("\n")) + "</font>")
    for src in images:
        parts.append(f'<img src="{_html_escape(src)}" width="{MCP_IMAGE_WIDTH}">')
    return "".join(parts)


def media_thumb_html(path: str, workspace_root: Path | None = None) -> str | None:
    """媒体入参路径 → 略缩图 HTML；文件缺失/过大时按既有降级语义返回。"""
    p = Path(path)
    if not p.is_absolute():
        p = (workspace_root or PROJECT_ROOT) / p
    if not p.is_file():
        return None
    if p.stat().st_size > MEDIA_THUMB_MAX_BYTES:
        return "<i>（图片过大，未生成略缩图）</i>"
    return f'<img src="{_html_escape(p.as_uri())}" width="{MEDIA_THUMB_WIDTH}">'


# ----------------------------------------------------------------------
# 工具卡通用生命周期外壳（厂商卡片只解释自己的载荷）
# ----------------------------------------------------------------------
class ToolCard(CollapsibleCard):
    """工具卡通用外壳：状态流转 ◐→✔/✖、开合记忆、复制、入参区与错误块。

    本类不读取具体载荷字段；厂商卡片在自身模块解析 payload 后经
    apply_title/apply_subtitle/attach_input_detail/apply_status 驱动本外壳。
    子类只覆写 _build_body 与 _on_progress/_on_completed/_on_failed 钩子。
    """

    _attach_input_detail = True
    #: 更新帧 title 是否覆盖卡标题（DiffCard 等路径内嵌标题的场景置 False）
    _accept_title_update = True
    #: 是否承接子代理内嵌帧（仅厂商 SubagentCard 置 True）
    supports_children = False

    def __init__(
        self,
        colors: CardColors,
        open_state: OpenStateMap,
        payload: dict,
        workspace_root: Path | None = None,
        *,
        subtitle: str = "",
    ) -> None:
        self._tid = payload.get("tool_call_id") or ""
        self._kind = payload.get("tool_kind") or "other"
        self._open_state = open_state
        self._user_toggled = False
        self._input_detail_attached = False
        #: 略缩图相对路径解析基准；None 时降级 PROJECT_ROOT
        self._workspace_root = workspace_root
        super().__init__(
            colors,
            _KIND_ICONS.get(self._kind, "🧩"),
            payload.get("title") or "?",
            "",
        )
        # 虚分派重设副标题：子类（DiffCard）的两段式对首帧 summary 也生效
        self.set_subtitle(subtitle or "")
        self.set_status("◐", colors.tool_fg)
        self._resolve_initial_open()

    # ------------------------------------------------------------------
    # 子类钩子（payload 对基类不透明）
    # ------------------------------------------------------------------
    def _build_body(self, payload: dict) -> None:
        """各专类 body 构建（厂商卡片构造器尾调用）。"""

    def _on_progress(self, payload: dict) -> None:
        """in_progress 帧（BashCard 尾滚用；其余卡忽略）。"""

    def _on_completed(self, payload: dict) -> None:
        """completed 帧（输出正文/成果摘要/行数尾注落 body）。"""

    def _on_failed(self, payload: dict) -> None:
        """failed 帧钩子（厂商卡片按自身错误字段渲染）。"""

    def _set_error_body(self, text: str) -> None:
        """failed 错误全文块（红色等宽，自动展开由 apply_status 统一）。"""
        body = BodyText(text, mono=True)
        body.set_extra_style(f"color: {self._colors.tool_error_fg};")
        self.add_body_widget(body)
        self.enable_copy(lambda body=body: body.toPlainText())

    # ------------------------------------------------------------------
    # 厂商卡片 → 外壳的稳定驱动接口
    # ------------------------------------------------------------------
    def apply_title(self, title: str | None) -> None:
        if self._accept_title_update and title:
            self.set_title(title)

    def apply_subtitle(self, subtitle: str | None) -> None:
        if subtitle:
            self.set_subtitle(subtitle)

    def attach_input_detail(self, detail: str | None) -> None:
        """迟到入参回填入口：首帧优先，已挂不覆盖（McpCard 自行处理常驻区）。"""
        if detail:
            self._set_input_detail(detail)

    def _set_input_detail(self, detail: str) -> None:
        if self._attach_input_detail and not self._input_detail_attached:
            self._add_input_detail(detail)

    def apply_status(self, status: str | None, payload: dict | None = None) -> None:
        """状态流转（◐→✔/✖）；failed 自动展开（异常必见）。"""
        payload = payload or {}
        if status == "completed":
            self.set_status("✔", self._colors.tool_fg)
            self._on_completed(payload)
        elif status == "failed":
            self.set_status("✖", self._colors.tool_error_fg)
            self.set_title_color(self._colors.tool_error_fg)
            self._on_failed(payload)
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
        saved = self._open_state.get(
            f"{self._kind}:{self._tid}") if self._tid else None
        self.set_open(
            saved if saved is not None else _DEFAULT_OPEN.get(self._kind, False))

    def _add_input_detail(self, detail: str) -> None:
        """通用入参区（D3）：body 末弱化灰小块，「尽可能全」的兜底保证。"""
        self._input_detail_attached = True
        label = _selectable(BandLabel("入参", self))
        label.setStyleSheet(f"color: {self._colors.tool_fg}; font-size: 90%;")
        text = BodyText(detail, mono=True)
        text.set_extra_style(f"color: {self._colors.tool_fg};")
        self.add_body_widget(label)
        self.add_body_widget(text)


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
        self._text.set_extra_style(
            f"color: {colors.reasoning_fg}; font-style: italic;")
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
