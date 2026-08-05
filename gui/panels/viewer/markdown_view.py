"""Markdown 渲染预览：QTextBrowser + setMarkdown（GitHub 方言）。

（2026-07-29，见 文档/修改记录/2026-0729-1155_Markdown渲染预览与Typora打开功能实施计划 T2–T4）
形态：内嵌 ViewerPanel Markdown 页（QStackedLayout 第四页，.md/.markdown
经 open_file 分流直进渲染页——原决策不做源码↔渲染双模式，已于 2026-08-06
被 0327 计划推翻：标题行开关可切源码模式，本页为「阅览模式」默认态）。
中栏不设「使用 Typora 打开」入口（2026-08-06 用户拍板翻案 0659 计划
D3/D4：右栏文件树右键有同款入口，中栏整体移除、右栏保留）。

能力：GFM 渲染（表格/任务列表/删除线/围栏代码块）、相对图片/链接以 md
所在目录为基准解析（searchPaths + baseUrl）、链接三分发（http→系统浏览器、
工作区内文件→file_link_clicked 信号、#锚点→页内跳转）、主题样式表重建、
全局字号跟随、大文件 1MB 截断守卫、选区带自绘（2026-0731-2055 方案 A）。

选区带自绘（方案 A）：原生选区带 = QTextLine 整行高（含 leading），而
Qt 排版把 leading 全部垫在行框底部——思源黑体 lineGap 6.5px@10pt 导致
带上缘留白 2px/下缘 7px 视觉偏下。此处以 qss 透明化抑制原生带，
paintEvent 基类绘制后按行 ascent+descent 墨盒上下对称加留白自绘
半透明圆角带（色取主题 accent，文字保持原色可读）。

协议合规：theia-zen（EPL-2.0）零接触；setMarkdown 为 Qt 内建 API，
VS_Code_Python（MIT）仅证实其 PySide6 可用性，无代码移植。
"""
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QFont, QPalette, QTextDocument
from PySide6.QtWidgets import QApplication, QFrame, QMenu, QTextBrowser, QWidget

from gui.popups import make_translucent_popup
from gui.selection_band import SUPPRESSION_QSS, paint_selection_band

#: 可渲染预览的 Markdown 扩展名（不含点，与 IMAGE_EXTS 同规约；不含 mdx——决策不启用）
MARKDOWN_EXTS: frozenset[str] = frozenset({"md", "markdown"})

#: 大文件守卫：超过 1 MB 截断渲染（与 ViewerPanel 文本页 MAX_BYTES 一致）
MAX_BYTES = 1_048_576


class MarkdownView(QTextBrowser):
    """Markdown 只读渲染控件（GFM 方言 + 相对资源解析 + 链接分发）。"""

    #: 工作区内相对文件链接点击（参数为解析后的绝对路径；面板转 open_file）
    file_link_clicked = Signal(str)

    def __init__(
        self,
        palette: dict,
        parent: QWidget | None = None,
    ) -> None:
        """
        :param palette: 主题调色板（gui/theme.py get_theme_palette 全量包）
        :param parent: 父控件
        """
        super().__init__(parent)
        self.setFrameShape(QFrame.Shape.NoFrame)  # 卡片统一描边，控件自身去框
        self.setOpenLinks(False)  # 链接点击全由 anchorClicked 分发，不做内置导航
        self.anchorClicked.connect(self._dispatch_link)
        # 方案 A：抑制原生选区带（qss 继承自 QMainWindow 的 accent 色规则，
        # 控件级声明优先）；选中文字保持正文色，由自绘带承担高亮
        self.setStyleSheet(SUPPRESSION_QSS)

        self._current_path: Path | None = None
        self._truncated = False
        self._selection_color: QColor | None = None  # apply_theme 注入 accent

        self.refresh_font()
        self.apply_theme(palette)

    # ------------------------------------------------------------------
    # 打开与渲染
    # ------------------------------------------------------------------
    @property
    def truncated(self) -> bool:
        """本次打开是否触发 1MB 截断（面板据此追加标题行提示）。"""
        return self._truncated

    def open_markdown(self, path: Path) -> str | None:
        """打开 Markdown 文件并渲染；成功返回 None，失败返回原因字符串。

        读取/解码失败由面板回落文本页占位提示（与 ImageViewer.open_image 同模式）。
        """
        try:
            raw = path.read_bytes()
        except OSError as e:
            return str(e)
        self._truncated = len(raw) > MAX_BYTES
        if self._truncated:
            raw = raw[:MAX_BYTES]
        try:
            # 截断可能切断 UTF-8 多字节字符：截断场景宽容解码（丢弃尾部残缺字符），
            # 未截断保持严格解码以识别二进制伪装
            text = raw.decode("utf-8", errors="ignore" if self._truncated else "strict")
        except UnicodeDecodeError:
            return "编码非 UTF-8，不支持渲染预览"

        self._current_path = path
        # 相对图片/链接解析基准：md 所在目录（searchPaths 供 loadResource 查找图片，
        # baseUrl 供相对 URL 解析；尾随斜杠确保目录语义——否则 resolved 会把
        # 目录名当文件段替换掉）
        self.setSearchPaths([str(path.parent)])
        self.document().setBaseUrl(QUrl.fromLocalFile(str(path.parent) + "/"))
        # QTextBrowser.setMarkdown 绑定仅单参；文档级 setMarkdown 支持方言特性集
        self.document().setMarkdown(
            text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub)
        return None

    # ------------------------------------------------------------------
    # 链接分发（http→系统浏览器 / 工作区内文件→信号 / #锚点→页内跳转）
    # ------------------------------------------------------------------
    def _dispatch_link(self, url: QUrl) -> None:
        """anchorClicked 槽：按链接类型三分发。"""
        text = url.toString()
        if text.startswith("#"):
            self.setSource(url)  # 片段跳转：页内锚点滚动，不重新加载文档
            return
        if url.scheme() in ("http", "https", "mailto"):
            QDesktopServices.openUrl(url)
            return
        target = self._resolve_workspace_link(url)
        if target is not None:
            self.file_link_clicked.emit(str(target))

    def _resolve_workspace_link(self, url: QUrl) -> Path | None:
        """相对/file 链接解析为绝对路径（以当前 md 所在目录为基准）。"""
        if self._current_path is None:
            return None
        # 尾随斜杠：base 末段须为目录语义，resolved 才能正确拼接相对路径
        base = QUrl.fromLocalFile(str(self._current_path.parent) + "/")
        return Path(base.resolved(url).toLocalFile())

    def wheelEvent(self, event) -> None:
        """禁用 Qt 内建 Ctrl+滚轮缩放字体：Ctrl 按下时吞掉事件，其余走基类滚动。"""
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.accept()
            return
        super().wheelEvent(event)

    # ------------------------------------------------------------------
    # 右键菜单（单项「全选」）
    # ------------------------------------------------------------------
    def _build_context_menu(self, pos) -> QMenu:
        """装配右键菜单：单项「全选」（构建与弹出分离，可探针断言）。

        精简决策（2026-0806-0659 计划 D1/D2）：只读查看器高频路径为
        「全选 → Ctrl+C」，「全选」无前置条件随时可用，是右键菜单中唯一
        不依赖选区状态的有效项；复制走 Ctrl+C（有选区时）。
        """
        menu = QMenu(self)
        make_translucent_popup(menu)
        menu.addAction("全选", self.selectAll)
        return menu

    def contextMenuEvent(self, event) -> None:
        """右键弹出单项「全选」菜单（见 gui/popups.py 透明化规约）。"""
        menu = self._build_context_menu(event.pos())
        menu.exec(event.globalPos())
        menu.deleteLater()

    # ------------------------------------------------------------------
    # 主题与字号
    # ------------------------------------------------------------------
    def apply_theme(self, palette: dict) -> None:
        """切换主题：重建文档默认样式表（色值取调色板现有令牌派生，不新增令牌）。"""
        self.document().setDefaultStyleSheet(self._build_stylesheet(palette))
        self._selection_color = QColor(palette["accent"])  # 自绘选区带色源
        self.viewport().update()

    @staticmethod
    def _build_stylesheet(palette: dict) -> str:
        """md 各元素配色：Qt 富文本 CSS 子集内重建（h 系/code/blockquote/table/hr）。"""
        text = palette["text"]
        muted = palette["muted_text"]
        accent = palette["accent"]
        border = palette["border"]
        code_bg = palette["chrome"]["line_number_bg"]  # 代码块底借用行号栏底色令牌
        return f"""
            body {{ color: {text}; }}
            a {{ color: {accent}; text-decoration: none; }}
            code, pre {{ background-color: {code_bg}; }}
            blockquote {{ color: {muted}; margin-left: 16px; }}
            table td, table th {{ border: 1px solid {border}; padding: 4px; }}
            hr {{ color: {border}; background-color: {border}; }}
        """

    def refresh_font(self) -> None:
        """全局字号调整：文档默认字体跟随 app 字号重建（正文用 UI 比例字体）。"""
        if app := QApplication.instance():
            font = QFont(app.font())
            self.setFont(font)
            self.document().setDefaultFont(font)

    # ------------------------------------------------------------------
    # 选区带自绘（2026-0731-2055 方案 A：墨盒上下对称留白，矫正原生带偏下；
    # 实现已通用化至 gui/selection_band.py，聊天区 ChatOutput 共用）
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        """基类绘制（原生选区带已被透明化）后，叠绘半透明对称选区带。"""
        super().paintEvent(event)
        color = self._selection_color or QApplication.palette().color(
            QPalette.ColorRole.Highlight)
        paint_selection_band(self, color)
