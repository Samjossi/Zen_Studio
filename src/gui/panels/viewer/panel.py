"""文件查看面板：标题行（路径 + Git 差异徽标 + 状态提示）+ 查看器 + 外部修改自动重载。

AI-first 主频场景：agent 直接写盘为主修改路径，`QFileSystemWatcher` 监视当前文件，
外部修改（AI 写盘）→ 防抖自动重载并保留滚动位置，标题行提示"已重新加载"。

Git 差异徽标（2026-07-20，见 文档/修改记录/2026-0720-0131 计划阶段三）：
set_git_service() 注入 GitStatusService 后，open_file 查询 numstat，
标题行路径后追加 `+a -b` 徽标（无改动/非仓库不显示）；外部重载时发射
externally_reloaded 供主窗口联动刷新 Git 状态。

查找浮层（2026-07-20，见 文档/修改记录/2026-0720-0510 计划任务 5.1）：
右上角悬浮（不占布局，对齐终端查找浮层形态），当前文档搜索 + 命中高亮
（经 CodeViewer.set_search_highlights，与当前行高亮合并上屏）+ 上一个/下一个；
编辑菜单「查找」按焦点分发进入，Esc 关闭。

图片预览（2026-07-29，见 文档/修改记录/2026-0729-1102_图片文件预览功能实施计划）：
QStackedLayout 双页——文本页 CodeViewer（兼占位提示）/ 图片页 ImageViewer，
open_file 按扩展名分流；图片页标题行显示 ◀ ▶ 适应 100% 按钮，
查找浮层在图片页降级为弱提示。

音视频播放（2026-07-29，见 文档/修改记录/2026-0729-1120_音视频播放功能实施计划）：
QStackedLayout 第三页 MediaViewer（QMediaPlayer 就地播放），open_file 按
扩展名分流；任何离开媒体页的路径（切文本/图片/占位）一律 stop() 释放；
解码失败经 MediaViewer.failed 信号回落文本页占位提示。

Markdown 渲染预览（2026-07-29，见 文档/修改记录/2026-0729-1155_Markdown渲染预览与
Typora打开功能实施计划）：QStackedLayout 第四页 MarkdownView（QTextBrowser
+ setMarkdown GFM 渲染），.md/.markdown 直进渲染页；
查找浮层降级弱提示；中栏不设「使用 Typora 打开」入口（2026-08-06
用户拍板翻案 0659 计划 D3/D4——右栏文件树右键有同款入口，中栏
整体移除，右栏保留）。

Markdown 阅览/源码双模式开关（2026-08-06，见 文档/修改记录/2026-0806-0327_Markdown
阅览源码双模式滑块开关计划）：推翻上节「不做源码↔渲染双模式」决策——标题行
新增「动态标签 + ToggleSwitch」开关组（仅 Markdown 页可见，镜像图片/PDF 按钮组
按页显隐先例），关=阅览模式（渲染页，蓝）/ 开=源码模式（复用文本页只读
CodeViewer + Pygments 高亮，蛋黄色；仅显示源码不可编辑）；打开新 md 复位
到阅览模式（不跨文件记忆）；源码模式下查找浮层自动恢复可用。

PDF 预览（2026-07-29，见 文档/修改记录/2026-0729-1212_PDF文件预览功能实施计划）：
QStackedLayout 第五页 PdfViewer（QPdfView 连续滚动渲染），.pdf 直进 PDF 页；
标题行 ◀ ▶ 翻页 + 缩放/适配 + 「外部打开」按钮组；加密/损坏/加载失败
回落文本页占位提示；外部修改重载恢复页码/缩放；查找浮层降级弱提示；
离开 PDF 页一律 close_document() 释放文档。
标题行路径相对化（2026-08-05，见 文档/修改记录/2026-0805-2048_标题行路径
相对化与截断显示计划）：打包客户实机反馈标题行被完整绝对路径占满——
标题行/提示内嵌路径一律经 _display_path() 单点格式化（resolve 归一 →
相对 workspace_root 相对化 → 超 60 字符保尾中间省略，根外回退绝对），
tooltip 兜底完整绝对路径；标题行三标签字号严格派生为「全局字号 − 4pt」
（无地板，镜像 settings_dialog._TITLE_FONT_DELTA_PT = +4 先例）。
"""
from pathlib import Path

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QStackedLayout,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from core.git.service import GitStatusService
from gui.panels.find_bar import FindBar
from gui.panels.viewer.code_viewer import CodeViewer
from gui.panels.viewer.highlighter import PygmentsHighlighter
from gui.panels.viewer.image_viewer import IMAGE_EXTS, ImageViewer
from gui.panels.viewer.markdown_view import MARKDOWN_EXTS, MarkdownView
from gui.panels.viewer.media_viewer import AUDIO_EXTS, VIDEO_EXTS, MediaViewer
from gui.panels.viewer.pdf_viewer import PDF_EXTS, PdfViewer
from gui.panels.viewer.toggle_switch import ToggleSwitch
from gui.settings import KEY_FONT_SIZE, KEY_THEME
from gui.theme import load_settings, get_theme_palette

#: 大文件守卫：超过 1 MB 截断显示并提示
MAX_BYTES = 1_048_576
#: watcher 防抖（编辑器保存常触发多次 fileChanged）
RELOAD_DEBOUNCE_MS = 150
#: 状态提示展示时长
HINT_TIMEOUT_MS = 3000
#: 标题行显示路径字符上限：超过则保尾中间省略（文件名优先级高于目录链，
#: 字符级确定性方案，放弃像素级 elidedText 的 resizeEvent 重算，见 2048 计划 D3）
_PATH_DISPLAY_MAX_CHARS = 60
#: 标题行三标签（路径/徽标/提示）相对全局字号的派生步长（pt）：严格 −4、
#: 无地板钳制（用户定夺：默认 10pt 本身很小，客户实机必然调大全局字号，
#: 地板反而破坏恒定视觉层级，见 2048 计划 D7/R7）；镜像
#: settings_dialog._TITLE_FONT_DELTA_PT = +4 先例（相对派生而非绝对值）
_TITLE_ROW_FONT_DELTA_PT = -4


class ViewerPanel(QWidget):
    """中栏（上）文件查看面板（只读 + 语法高亮 + 外部修改自动重载）。"""

    #: 外部修改自动重载完成时发射（供主窗口联动刷新 Git 状态）
    externally_reloaded = Signal()

    def __init__(self, parent: QWidget | None = None,
                 workspace_root: str | None = None) -> None:
        super().__init__(parent)
        self._current_path: str | None = None
        #: Git 状态服务（set_git_service 注入；None = 差异徽标不启用）
        self._git_service: GitStatusService | None = None
        #: 相对化基准（MainWindow 注入；resolve 归一化存根）。None = 退化
        #: 为绝对路径现状（探针/测试直接实例化不受累，见 2048 计划 D1）
        self._workspace_root: str | None = (
            str(Path(workspace_root).resolve()) if workspace_root else None
        )

        title_row = self._build_title_row()

        # 高亮/行号配色取自主题调色板（资源包下沉，每主题自带全套）
        palette = get_theme_palette(load_settings()[KEY_THEME])
        self._stack = self._build_pages(palette)

        # PanelCard 圆角卡片包裹：标题行 + 查看器整体入卡，卡片统一描边
        # （CodeViewer 自身描边已由 qss 去除，防双重边框）
        card = QFrame(self)
        card.setObjectName("PanelCard")
        # 自定义 QFrame 的 qss 背景需 WA_StyledBackground 才会绘制
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        card_layout = QVBoxLayout(card)
        card_layout.addWidget(title_row)
        card_layout.addLayout(self._stack, 1)
        card_layout.setContentsMargins(6, 2, 6, 6)
        card_layout.setSpacing(0)

        layout = QVBoxLayout(self)
        layout.addWidget(card, 1)
        # 面板外边距：卡片不贴窗口边缘与 splitter 把手（苹果风卡片间距）
        layout.setContentsMargins(6, 6, 6, 2)
        layout.setSpacing(0)

        self._init_file_watch()
        self._build_find_bar()
        # 标题行字号自初始化：启动不走 _apply_font_size（仅设置调整/恢复默认
        # 两调用点），全靠 app 字体继承——减 4 派生须此处显式落地一次（2048 D7）
        self._apply_title_row_font()

    def _build_pages(self, palette: dict) -> QStackedLayout:
        """五页查看器装配（文本/图片/媒体/Markdown/PDF）：信号接线 + 入栈。"""
        self.viewer = CodeViewer(palette["chrome"], self)
        self._highlighter = PygmentsHighlighter(self.viewer.document(), palette["syntax"])
        # 图片页：位图/SVG/GIF 预览（与文本页 QStackedLayout 切换）
        self.image_viewer = ImageViewer(palette, self)
        self.image_viewer.info_changed.connect(self._show_hint)
        # 媒体页：视频/音频就地播放（解码失败经 failed 信号回落占位）
        self.media_viewer = MediaViewer(palette, self)
        self.media_viewer.failed.connect(self._on_media_failed)
        # Markdown 页：.md/.markdown GFM 渲染预览（工作区内链接点击转 open_file）
        self.markdown_view = MarkdownView(palette, self)
        self.markdown_view.file_link_clicked.connect(self.open_file)
        # PDF 页：.pdf 就地预览（QPdfView 连续滚动；外部打开失败转弱提示）
        self.pdf_viewer = PdfViewer(palette, self)
        self.pdf_viewer.page_info_changed.connect(self._show_hint)
        self.pdf_viewer.external_failed.connect(self._show_hint)
        stack = QStackedLayout()
        for page in (self.viewer, self.image_viewer, self.media_viewer,
                     self.markdown_view, self.pdf_viewer):
            stack.addWidget(page)
        return stack

    def _build_title_row(self) -> QWidget:
        """标题行：路径标签 + Git 差异徽标 + 图片按钮组 + 提示标签。"""
        self._path_label = QLabel("（未打开文件）", self)
        self._path_label.setObjectName("PanelTitle")  # 样式由主题 qss 统一
        self._git_badge = QLabel("", self)
        self._git_badge.setObjectName("PanelHint")  # 与提示同款弱化的次要文字样式
        self._git_badge.setVisible(False)
        self._hint_label = QLabel("", self)
        self._hint_label.setObjectName("PanelHint")

        title_row = QWidget(self)
        title_layout = QHBoxLayout(title_row)
        title_layout.addWidget(self._path_label, 1)
        title_layout.addWidget(self._git_badge)
        title_layout.addWidget(self._build_image_buttons(title_row))
        title_layout.addWidget(self._build_pdf_buttons(title_row))
        title_layout.addWidget(self._build_md_switch(title_row))
        title_layout.addWidget(self._hint_label)
        title_layout.setContentsMargins(4, 2, 4, 2)
        return title_row

    def _build_image_buttons(self, parent: QWidget) -> QWidget:
        """图片页按钮组：◀ ▶ 翻页 + 实际像素/适应（仅图片页可见，文本页隐藏）。"""
        self._image_buttons = QWidget(parent)
        row = QHBoxLayout(self._image_buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        for text, tip, slot in (
            ("◀", "上一张（同目录）", lambda: self.image_viewer.step(-1)),
            ("▶", "下一张（同目录）", lambda: self.image_viewer.step(1)),
            ("实际像素", "实际像素（100% 显示）", lambda: self.image_viewer.actual()),
            ("适应", "适应窗口", lambda: self.image_viewer.fit()),
        ):
            button = QToolButton(self._image_buttons)
            button.setText(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        self._image_buttons.setVisible(False)
        return self._image_buttons

    def _build_md_switch(self, parent: QWidget) -> QWidget:
        """Markdown 模式开关组：动态标签 + 滑块开关（仅 Markdown 页可见）。

        两态对色表达「模式差异」：关=阅览模式（GFM 渲染页，主题蓝），
        开=源码模式（只读源码 CodeViewer，蛋黄色）。槽经 toggled 惰性化：
        标题行构建早于页面切换逻辑，接线在 _build_pages 之后（同图片按钮先例，
        槽内引用 self.markdown_view 等成员仅运行时触达）。
        """
        self._md_switch_box = QWidget(parent)
        row = QHBoxLayout(self._md_switch_box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(4)
        self._md_mode_label = QLabel("阅览模式", self._md_switch_box)
        self._md_mode_label.setObjectName("PanelHint")  # 与提示同款次要文字样式
        self._md_switch = ToggleSwitch(self._md_switch_box,
                                       off_color="#0765d4",   # 阅览 = 蓝
                                       on_color="#f5b301")    # 源码 = 蛋黄色
        self._md_switch.setToolTip("切换 Markdown 阅览/源码模式（源码只读，不可编辑）")
        self._md_switch.toggled.connect(self._on_md_mode_toggled)
        row.addWidget(self._md_mode_label)
        row.addWidget(self._md_switch)
        self._md_switch_box.setVisible(False)
        return self._md_switch_box

    def _on_md_mode_toggled(self, checked: bool) -> None:
        """开关 toggled 槽：标签文本 + 页面切换（守卫：仅当前为 md 文件时生效）。"""
        self._md_mode_label.setText("源码模式" if checked else "阅览模式")
        if not self._current_path:
            return
        if Path(self._current_path).suffix.lower().lstrip(".") not in MARKDOWN_EXTS:
            return  # 双保险：非 Markdown 场景信号误触不切页（0327 计划 D6）
        if not checked:
            # 回阅览模式：MarkdownView 文档仍在（setMarkdown 内容未丢），
            # 仅切页不重渲染，零开销瞬时切换
            self._stack.setCurrentWidget(self.markdown_view)
            return
        # 源码模式：复用文本页 CodeViewer（只读 + Pygments 高亮），
        # 读盘走与文本页同套 1MB 截断守卫 + UTF-8 解码
        result = self._read_text(Path(self._current_path))
        if isinstance(result, str):
            return self._show_hint(f"（源码读取失败：{result}）")
        text, _truncated = result
        self.viewer.setPlainText(text)
        self._highlighter.set_source(Path(self._current_path).name, text)
        self._stack.setCurrentWidget(self.viewer)

    @staticmethod
    def _read_text(p: Path) -> tuple[str, bool] | str:
        """读盘 + 1MB 截断守卫 + UTF-8 解码：成功返回 (文本, 是否截断)，失败返回原因。

        与文本页 open_file 主路径同套守卫（0327 计划 T3：提取共用，防双份守卫漂移）。
        """
        try:
            raw = p.read_bytes()
        except OSError as e:
            return e.strerror or str(e)
        truncated = len(raw) > MAX_BYTES
        if truncated:
            raw = raw[:MAX_BYTES]
        # 截断可能切断 UTF-8 多字节字符：宽容解码丢弃尾部残缺（MarkdownView 同例）
        try:
            text = raw.decode("utf-8", errors="ignore" if truncated else "strict")
        except UnicodeDecodeError:
            return "编码非 UTF-8"
        return text, truncated

    def _build_pdf_buttons(self, parent: QWidget) -> QWidget:
        """PDF 页按钮组：◀ ▶ 翻页 + 缩放/适配 + 外部打开（仅 PDF 页可见）。

        槽经 lambda 惰性化：标题行构建早于 pdf_viewer 创建（同图片按钮先例）。
        """
        self._pdf_buttons = QWidget(parent)
        row = QHBoxLayout(self._pdf_buttons)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(2)
        for text, tip, slot in (
            ("◀", "上一页", lambda: self.pdf_viewer.step_page(-1)),
            ("▶", "下一页", lambda: self.pdf_viewer.step_page(1)),
            ("缩小", "缩小（×0.8）", lambda: self.pdf_viewer.zoom_out()),
            ("放大", "放大（×1.25）", lambda: self.pdf_viewer.zoom_in()),
            ("适宽", "适应宽度", lambda: self.pdf_viewer.fit_width()),
            ("适页", "适应页面", lambda: self.pdf_viewer.fit_page()),
            ("外部打开", "在系统 PDF 阅读器中打开", lambda: self.pdf_viewer.open_external()),
        ):
            button = QToolButton(self._pdf_buttons)
            button.setText(text)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            row.addWidget(button)
        self._pdf_buttons.setVisible(False)
        return self._pdf_buttons

    def _init_file_watch(self) -> None:
        """外部变更监视：fileChanged → 去抖重载（编辑器等连续写合并为一次）。"""
        self._watcher = QFileSystemWatcher(self)
        self._watcher.fileChanged.connect(self._on_file_changed)
        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(RELOAD_DEBOUNCE_MS)
        self._debounce.timeout.connect(self._reload)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    @property
    def current_path(self) -> str | None:
        """当前查看文件的绝对路径（未打开为 None）。"""
        return self._current_path

    def open_file(self, path: str, line: int | None = None) -> None:
        """打开文件：媒体/图片/Markdown 分流对应页；文本读取 → 守卫判定 → 上屏高亮 → 更新 watcher。

        :param line: 指定时文本页定位到该行（1 起始；对话区文件路径链接，
            1836 计划 L2-5；非文本分流页忽略）
        """
        p = Path(path)
        if not p.is_file():
            return self._show_placeholder(f"（文件不存在：{self._display_path(path)}）")
        # 打开新文件前清除标题行残留提示（如上一文件的「已被删除」常驻/瞬时提示），
        # 后续回落分支会按需重新设置，时序兼容
        self._hint_label.clear()
        suffix = p.suffix.lower().lstrip(".")
        # PDF 分流须先于图片：QImageReader 自带 pdf 图像插件（IMAGE_EXTS 含 pdf），
        # 顺序靠后会被图片页截胡（扩展名并非互斥，实施实测发现）
        if suffix in PDF_EXTS:
            return self._open_pdf(p)
        if suffix in VIDEO_EXTS or suffix in AUDIO_EXTS:
            return self._open_media(p)
        if suffix in IMAGE_EXTS:
            return self._open_image(p)
        if suffix in MARKDOWN_EXTS:
            return self._open_markdown(p)
        self.media_viewer.stop()  # 离开媒体页：停播释放（生命周期红线）
        self.pdf_viewer.close_document()  # 离开 PDF 页：释放文档
        try:
            raw = p.read_bytes()
        except OSError as e:
            # 重格式化拆分：OSError 字符串内嵌绝对路径（如 [Errno 13] ...: '/abs/...'），
            # 不原样拼接——显示路径走相对化，错误原因取 strerror（2048 计划 T2-2）
            return self._show_placeholder(
                f"（读取失败：{self._display_path(str(p))}：{e.strerror or e}）")

        truncated = len(raw) > MAX_BYTES
        if truncated:
            raw = raw[:MAX_BYTES]
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return self._show_placeholder(f"（二进制文件，共 {len(raw)} 字节，不支持预览）", path=str(p))

        # 渲染 + 监视（重命名替换保存的场景需重建监视路径）
        self._watch(str(p))
        self._stack.setCurrentWidget(self.viewer)
        self._image_buttons.setVisible(False)
        self._pdf_buttons.setVisible(False)
        self._md_switch_box.setVisible(False)
        scroll = self.viewer.verticalScrollBar().value() if self._current_path == str(p) else 0
        self.viewer.setPlainText(text)
        self._highlighter.set_source(p.name, text)
        self.viewer.verticalScrollBar().setValue(scroll)
        if line is not None and line >= 1:  # L2-5：链接带行号 → 定位该行
            block = self.viewer.document().findBlockByNumber(line - 1)
            if block.isValid():
                self.viewer.setTextCursor(QTextCursor(block))
                self.viewer.centerCursor()

        title = self._display_path(str(p)) + ("（已截断：超过 1 MB）" if truncated else "")
        self._path_label.setText(title)
        self._path_label.setToolTip(self._full_path_tooltip(str(p)))
        self._current_path = str(p)
        self.refresh_git_badge()
        # 查找浮层开启中换文件/重载：按新文档重搜；未开启则清残留高亮
        if self._find_bar.isVisible():
            self._update_search()
        else:
            self._clear_search()

    def _open_image(self, p: Path) -> None:
        """图片页上屏：ImageViewer 加载 + watcher 挂载 + Git 徽标刷新。

        加载失败（损坏/解码失败/超防护阈值）回落文本页占位提示。
        """
        self.media_viewer.stop()  # 离开媒体页：停播释放（生命周期红线）
        self.pdf_viewer.close_document()  # 离开 PDF 页：释放文档
        if error := self.image_viewer.open_image(p):
            return self._show_placeholder(f"（图片无法预览：{error}）", path=str(p))
        self._watch(str(p))
        self._stack.setCurrentWidget(self.image_viewer)
        self._image_buttons.setVisible(True)
        self._pdf_buttons.setVisible(False)
        self._md_switch_box.setVisible(False)
        self._path_label.setText(self._display_path(str(p)))
        self._path_label.setToolTip(self._full_path_tooltip(str(p)))
        self._current_path = str(p)
        self.refresh_git_badge()
        # 查找浮层绑定文本文档，切图片页即关闭并清残留高亮
        if self._find_bar.isVisible():
            self._hide_find()

    def _open_media(self, p: Path) -> None:
        """媒体页上屏：MediaViewer 挂源 + watcher 挂载 + Git 徽标刷新。

        解码失败异步经 MediaViewer.failed → _on_media_failed 回落占位提示。
        """
        self.media_viewer.open_media(p)  # 内部先 stop 旧源（生命周期红线）
        self.pdf_viewer.close_document()  # 离开 PDF 页：释放文档
        self._watch(str(p))
        self._stack.setCurrentWidget(self.media_viewer)
        self._image_buttons.setVisible(False)
        self._pdf_buttons.setVisible(False)
        self._md_switch_box.setVisible(False)
        self._path_label.setText(self._display_path(str(p)))
        self._path_label.setToolTip(self._full_path_tooltip(str(p)))
        self._current_path = str(p)
        self.refresh_git_badge()
        # 查找浮层绑定文本文档，切媒体页即关闭并清残留高亮
        if self._find_bar.isVisible():
            self._hide_find()

    def _open_markdown(self, p: Path) -> None:
        """Markdown 页上屏：MarkdownView 渲染 + watcher 挂载 + Git 徽标刷新。

        读取/解码失败回落文本页占位提示（与图片页同模式）。
        """
        self.media_viewer.stop()  # 离开媒体页：停播释放（生命周期红线）
        self.pdf_viewer.close_document()  # 离开 PDF 页：释放文档
        if error := self.markdown_view.open_markdown(p):
            return self._show_placeholder(f"（Markdown 无法预览：{error}）", path=str(p))
        self._watch(str(p))
        self._stack.setCurrentWidget(self.markdown_view)
        self._image_buttons.setVisible(False)
        self._pdf_buttons.setVisible(False)
        self._md_switch_box.setVisible(True)
        truncated = self.markdown_view.truncated
        self._path_label.setText(
            self._display_path(str(p)) + ("（已截断：超过 1 MB）" if truncated else ""))
        self._path_label.setToolTip(self._full_path_tooltip(str(p)))
        self._current_path = str(p)
        self.refresh_git_badge()
        # 打开新 md 复位开关到阅览模式：toggled 触发标签自动回「阅览模式」，
        # 无需额外同步（0327 计划 D5）；复位在上屏之后，槽内守卫防误切页
        self._md_switch.setChecked(False)
        # 查找浮层绑定文本文档，切 Markdown 页即关闭并清残留高亮
        if self._find_bar.isVisible():
            self._hide_find()

    def _open_pdf(self, p: Path) -> None:
        """PDF 页上屏：PdfViewer 加载 + watcher 挂载 + Git 徽标刷新。

        加载失败（加密/损坏/格式非法）回落文本页占位提示（与图片页同模式）。
        """
        self.media_viewer.stop()  # 离开媒体页：停播释放（生命周期红线）
        if error := self.pdf_viewer.open_pdf(p):
            return self._show_placeholder(
                f"（PDF 无法预览：{error}，可在外部程序打开）", path=str(p))
        self._watch(str(p))
        self._stack.setCurrentWidget(self.pdf_viewer)
        self._image_buttons.setVisible(False)
        self._pdf_buttons.setVisible(True)
        self._md_switch_box.setVisible(False)
        self._path_label.setText(self._display_path(str(p)))
        self._path_label.setToolTip(self._full_path_tooltip(str(p)))
        self._current_path = str(p)
        self.refresh_git_badge()
        # 查找浮层绑定文本文档，切 PDF 页即关闭并清残留高亮
        if self._find_bar.isVisible():
            self._hide_find()

    def _on_media_failed(self, reason: str) -> None:
        """媒体解码失败（后端缺失/编码不支持）：回落文本页占位提示。"""
        self._show_placeholder(
            f"（无法播放：{reason}，请用外部程序打开）", path=self._current_path)

    def set_git_service(self, service: GitStatusService | None) -> None:
        """注入 Git 状态服务（None 表示禁用差异徽标）。"""
        self._git_service = service
        self.refresh_git_badge()

    def refresh_git_badge(self) -> None:
        """重查当前文件 numstat 并刷新标题行 `+a -b` 徽标。"""
        service = self._git_service
        line_stats = (
            service.numstat_of(self._current_path)
            if service is not None and service.is_enabled and self._current_path
            else None
        )
        if line_stats is None:
            self._git_badge.setVisible(False)
            self._git_badge.setToolTip("")
            return
        added, deleted = line_stats
        self._git_badge.setText(f"+{added} -{deleted}")
        self._git_badge.setToolTip(f"相对 HEAD：新增 {added} 行，删除 {deleted} 行")
        self._git_badge.setVisible(True)

    def apply_theme(self, theme: str) -> None:
        """切换主题：同步高亮器与各页查看器配色包（入参为主题名）。"""
        palette = get_theme_palette(theme)
        self._highlighter.set_theme(palette["syntax"])
        self.viewer.apply_theme(palette["chrome"])
        self.image_viewer.apply_theme(palette)
        self.media_viewer.apply_theme(palette)
        self.markdown_view.apply_theme(palette)
        self.pdf_viewer.apply_theme(palette)

    def refresh_font(self) -> None:
        """全局字号调整：查看器等宽字体重建（行号栏宽随新字宽重算），渲染页字体跟随。"""
        self.viewer.refresh_font()
        self.markdown_view.refresh_font()
        self._apply_title_row_font()  # 标题行 −4pt 派生随全局字号调整跟随

    # ------------------------------------------------------------------
    # 标题行路径/字号显示（2048 计划：相对化 + 截断 + −4pt 派生）
    # ------------------------------------------------------------------
    def _display_path(self, path: str) -> str:
        """标题行/提示上屏路径统一格式化：归一 → 相对化 → 截断（单点收口）。

        归一 resolve 绝对化（兼解 symlink、兼容对话区链接发来的相对入参）→
        有根则 relative_to 相对化（ValueError = 根外文件/ symlink 出根，回退
        绝对路径）→ 超 _PATH_DISPLAY_MAX_CHARS 保尾中间省略为 `…/…文件名`。
        """
        display = self._full_path_tooltip(path)
        if self._workspace_root is not None:
            try:
                display = str(Path(display).relative_to(self._workspace_root))
            except ValueError:
                pass  # 根外文件（含 symlink 出根）：显示绝对路径（提示真实性优先）
        if len(display) > _PATH_DISPLAY_MAX_CHARS:
            # 保尾中间省略：文件名始终可见（同名歧义由 tooltip 全路径兜底）
            display = "…/" + display[-(_PATH_DISPLAY_MAX_CHARS - 2):]
        return display

    def _full_path_tooltip(self, path: str) -> str:
        """tooltip 用的归一化绝对路径（resolve 解 symlink、兼容相对入参）。"""
        try:
            return str(Path(path).resolve())
        except OSError:  # 极端入参（非法字符等）：原样兜底
            return path

    def _apply_title_row_font(self) -> None:
        """标题行三标签字号相对派生：严格「全局字号 − 4pt」（无地板）。

        仅缩三个文字标签（路径/徽标/提示），按钮组不缩——QToolButton 是
        点击目标，随标签缩小会同时缩小热区，可用性受损（2048 计划 D7）。
        """
        size = load_settings()[KEY_FONT_SIZE] + _TITLE_ROW_FONT_DELTA_PT
        for label in (self._path_label, self._git_badge, self._hint_label,
                      self._md_mode_label):
            font = QFont(label.font())
            font.setPointSize(size)
            label.setFont(font)

    # ------------------------------------------------------------------
    # 查找浮层（右上角悬浮；当前文档搜索 + 命中高亮 + 上一个/下一个）
    # ------------------------------------------------------------------
    def _build_find_bar(self) -> None:
        """装配共用 FindBar 组件（外观/定位/按键自闭环），搜索语义接本面板。"""
        self._find_bar = FindBar(self.viewer, "查找（当前文档）")
        self._find_matches: list[QTextCursor] = []
        self._find_current = -1
        self._find_bar.input.textChanged.connect(self._update_search)
        self._find_bar.step_requested.connect(self._find_step)
        self._find_bar.close_requested.connect(self._hide_find)

    def show_find(self) -> None:
        """打开查找浮层（编辑菜单「查找」焦点分发入口）；图片/媒体/渲染页降级弱提示。"""
        if self._stack.currentWidget() is self.image_viewer:
            return self._show_hint("图片不支持查找")
        if self._stack.currentWidget() is self.media_viewer:
            return self._show_hint("媒体文件不支持查找")
        if self._stack.currentWidget() is self.markdown_view:
            return self._show_hint("Markdown 渲染页不支持查找")
        if self._stack.currentWidget() is self.pdf_viewer:
            return self._show_hint("PDF 页不支持查找")
        self._find_bar.show_and_focus()
        self._update_search()

    def _hide_find(self) -> None:
        self._find_bar.setVisible(False)
        self._clear_search()
        self.viewer.setFocus()

    def _update_search(self) -> None:
        """收集当前文档全部命中并高亮；有命中即跳转首个。"""
        text = self._find_bar.input.text()
        self._find_matches = []
        if text:
            doc = self.viewer.document()
            cursor = doc.find(text, 0)
            while not cursor.isNull():
                self._find_matches.append(QTextCursor(cursor))  # 拷贝：find 复用同一对象
                cursor = doc.find(text, cursor.position())
        self._find_current = 0 if self._find_matches else -1
        self._apply_find(jump=True)

    def _find_step(self, delta: int) -> None:
        """上一个/下一个：环形步进并跳转。"""
        if self._find_matches:
            self._find_current = (self._find_current + delta) % len(self._find_matches)
            self._apply_find(jump=True)

    def _apply_find(self, jump: bool) -> None:
        """高亮上屏 + 视口跳转当前命中。"""
        self.viewer.set_search_highlights(self._find_matches, self._find_current)
        if jump and self._find_current >= 0:
            self.viewer.setTextCursor(self._find_matches[self._find_current])
            self.viewer.centerCursor()

    def _clear_search(self) -> None:
        self._find_matches = []
        self._find_current = -1
        self.viewer.set_search_highlights([], -1)

    # ------------------------------------------------------------------
    # 外部修改自动重载
    # ------------------------------------------------------------------
    def _watch(self, path: str) -> None:
        if self._watcher.files():
            self._watcher.removePaths(self._watcher.files())
        self._watcher.addPath(path)

    def _on_file_changed(self, _path: str) -> None:
        self._debounce.start()  # 防抖：连续写多次只重载一次

    def _reload(self) -> None:
        if not self._current_path:
            return
        if not Path(self._current_path).is_file():
            if self._watcher.files():
                self._watcher.removePaths(self._watcher.files())
            # 删除/重命名/移动对 watcher 不可区分，统一报「已被删除」；
            # 瞬时提示（非 sticky）：持续语义由占位状态承担，常驻文案会张冠李戴
            return self._show_placeholder(
                f"（文件已被删除：{self._display_path(self._current_path)}）",
                sticky=False)
        self.open_file(self._current_path)
        self._show_hint("已重新加载（外部修改）")
        self.externally_reloaded.emit()

    # ------------------------------------------------------------------
    # 显示辅助
    # ------------------------------------------------------------------
    def _show_placeholder(self, text: str, path: str | None = None, sticky: bool = True) -> None:
        """占位提示：清空正文并回落文本页。

        :param sticky: True 常驻提示（默认，读取失败等需用户感知持续状态的场景）；
            False 瞬时提示（HINT_TIMEOUT_MS 后自动消失，文件删除分支用，
            持续语义由「（未打开文件）」占位状态承担）
        """
        if self._watcher.files():
            self._watcher.removePaths(self._watcher.files())
        self.media_viewer.stop()  # 离开媒体页：停播释放（生命周期红线）
        self.pdf_viewer.close_document()  # 离开 PDF 页：释放文档
        self._stack.setCurrentWidget(self.viewer)  # 占位提示统一回落文本页
        self._image_buttons.setVisible(False)
        self._pdf_buttons.setVisible(False)
        self._md_switch_box.setVisible(False)
        self.viewer.setPlainText("")
        self._highlighter.set_source("", "")
        if path:
            self._path_label.setText(self._display_path(path))
            self._path_label.setToolTip(self._full_path_tooltip(path))
        else:
            self._path_label.setText("（未打开文件）")
            self._path_label.setToolTip("")
        self._current_path = None
        self._git_badge.setVisible(False)
        self._show_hint(text, sticky=sticky)

    def _show_hint(self, text: str, sticky: bool = False) -> None:
        """标题行提示：sticky 常驻，非 sticky 经 HINT_TIMEOUT_MS 后自动清除。

        文件删除分支走非 sticky（瞬时），其余占位提示（读取失败/二进制等）为常驻。
        """
        self._hint_label.setText(text)
        # 提示全文 tooltip：sticky 长文案被布局挤压时可悬停看全（2048 计划 T2-3）
        self._hint_label.setToolTip(text)
        if not sticky:
            QTimer.singleShot(HINT_TIMEOUT_MS, self._hint_label.clear)
