"""PDF 查看器：QPdfView（QtPdf 内核渲染）承载 .pdf 的就地预览。

（2026-07-29，见 文档/修改记录/2026-0729-1212_PDF文件预览功能实施计划 T1–T3）
形态：QWidget 组合 QPdfView（组合优先，AFCP 2.4），内嵌 ViewerPanel PDF 页
（QStackedLayout 第五页，.pdf 经 open_file 分流直进）。
能力：MultiPage 连续滚动（决策锁定）、缩放三件套（步进 1.25× / 适应宽度 /
适应页面）、◀ ▶ 页码导航、文本选择复制（QPdfView 内建）、外部修改重载
状态保持（同路径恢复页码/缩放）、「在外部程序打开」兜底（加密/损坏/
渲染失败回落占位提示时可用）。

协议合规（见 work options/2026-0729-1028_theia实质代码审计与协议补全建议.md）：
- theia-zen（EPL-2.0）：仅取其"内核原生渲染"的思路佐证，未翻阅、未移植
  其 PdfHandler/iframe 端点任何 TypeScript 代码。
- Multi_Cli_Studio（MIT）：其 PDF 预览为前端 <object> 依赖 Webview 原生
  渲染，控件层不可移植，零代码移植。
- 本文件为 PySide6.QtPdf（QPdfView + QPdfDocument）体系的完全独立实现。
"""
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QMargins, QPointF, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPalette
from PySide6.QtPdf import QPdfDocument
from PySide6.QtPdfWidgets import QPdfView
from PySide6.QtWidgets import QVBoxLayout, QWidget

#: 可预览 PDF 扩展名（不含点，与 IMAGE_EXTS 同规约）
PDF_EXTS: frozenset[str] = frozenset({"pdf"})

#: 缩放步进与区间（步进值与图片页手感一致）
ZOOM_STEP = 1.25
ZOOM_MIN = 0.1
ZOOM_MAX = 8.0
#: 连续滚动页间距与页面四周留白（px）
PAGE_SPACING_PX = 6
#: QPdfDocument.Error → 中文原因映射（加载失败回落占位提示用）
LOAD_ERROR_REASONS: dict[QPdfDocument.Error, str] = {
    QPdfDocument.Error.IncorrectPassword: "PDF 已加密",
    QPdfDocument.Error.InvalidFileFormat: "文件损坏或格式非法",
    QPdfDocument.Error.FileNotFound: "文件不存在",
}
#: 映射表外错误的兜底原因
UNKNOWN_ERROR_REASON = "未知错误"


@dataclass(frozen=True)
class _ViewState:
    """重载状态保持：外部修改重载后恢复的视图三要素（AFCP 3.1 数据结构显式）。"""

    page: int
    zoom_factor: float
    zoom_mode: QPdfView.ZoomMode


class PdfViewer(QWidget):
    """PDF 预览控件：QPdfView 连续滚动渲染 + 缩放/页码导航 + 外部打开兜底。"""

    #: 页码/缩放弱提示（文案形如 "第 3/12 页 · 125%"；面板接 _show_hint）
    page_info_changed = Signal(str)
    #: 外部打开失败（参数为原因字符串；面板转弱提示）
    external_failed = Signal(str)

    def __init__(self, palette: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._view = QPdfView(self)
        self._view.setFrameShape(QPdfView.Shape.NoFrame)  # 卡片统一描边，控件自身去框
        # 决策锁定：连续滚动（MultiPage），贴合现代 PDF 阅读习惯
        self._view.setPageMode(QPdfView.PageMode.MultiPage)
        margin = PAGE_SPACING_PX
        self._view.setDocumentMargins(QMargins(margin, margin, margin, margin))
        self._document: QPdfDocument | None = None
        self._current_path: Path | None = None
        #: 挂起的重载状态（隐藏态 jump 会被上屏布局重置，留待 showEvent 补恢复）
        self._pending_state: _ViewState | None = None
        self._view.pageNavigator().currentPageChanged.connect(self._on_page_changed)
        self._view.zoomFactorChanged.connect(self._on_zoom_changed)

        layout = QVBoxLayout(self)
        layout.addWidget(self._view)
        layout.setContentsMargins(0, 0, 0, 0)
        self.apply_theme(palette)

    # ------------------------------------------------------------------
    # 打开与加载
    # ------------------------------------------------------------------
    def open_pdf(self, path: Path) -> str | None:
        """打开 PDF 文件；成功返回 None，失败返回原因字符串（面板转占位提示）。

        同路径重载（外部修改）恢复页码/缩放状态；异路径重置为默认（适应宽度）。
        """
        state = self._capture_view_state() if self._current_path == path else None
        old = self._document
        document = QPdfDocument(self)
        error = document.load(str(path))
        if error is not QPdfDocument.Error.None_:
            document.close()
            document.deleteLater()
            return LOAD_ERROR_REASONS.get(error, UNKNOWN_ERROR_REASON)
        self._document = document
        self._current_path = path
        self._view.setDocument(document)
        if old is not None:
            old.close()
            old.deleteLater()
        if state is not None:
            self._pending_state = state
            self._apply_pending_state()  # 可见即恢复；隐藏态留待 showEvent
        else:
            self.fit_width()  # 默认适应宽度（决策锁定连续滚动的配套初始形态）
        self._emit_page_info()
        return None

    def showEvent(self, event) -> None:
        """PDF 页上屏（几何有效）时补恢复挂起的重载状态。"""
        super().showEvent(event)
        self._apply_pending_state()

    def _apply_pending_state(self) -> None:
        """可见时延迟一帧恢复挂起状态（隐藏态空转，等 showEvent）。

        上屏后 FitToWidth 布局会重算滚动位置，同步 jump 会被布局覆写
        （实测页码漂移一页），故延迟到事件循环下一轮、布局完成后恢复。
        """
        if self._pending_state is not None and self.isVisible():
            QTimer.singleShot(0, self._restore_pending)

    def _restore_pending(self) -> None:
        """应用挂起状态并清空（close_document 已清空时为空操作）。"""
        if self._pending_state is not None:
            state, self._pending_state = self._pending_state, None
            self._restore_view_state(state)

    def close_document(self) -> None:
        """释放文档（切页/占位回落时调用）：关闭文件句柄释放内存。"""
        self._current_path = None
        self._pending_state = None
        if self._document is not None:
            self._document.close()

    def _capture_view_state(self) -> _ViewState | None:
        """记录旧文档的页码/缩放/缩放模式（无文档时为 None）。"""
        if self._document is None or self._current_path is None:
            return None
        return _ViewState(
            page=self._view.pageNavigator().currentPage(),
            zoom_factor=self._view.zoomFactor(),
            zoom_mode=self._view.zoomMode(),
        )

    def _restore_view_state(self, state: _ViewState) -> None:
        """同路径重载后恢复页码/缩放（页码钳制在新文档页数内，防外部修改缩页越界）。"""
        self._view.setZoomMode(state.zoom_mode)
        if state.zoom_mode is QPdfView.ZoomMode.Custom:
            self._view.setZoomFactor(state.zoom_factor)
        count = self._document.pageCount() if self._document is not None else 0
        page = max(0, min(state.page, count - 1)) if count else 0
        self._view.pageNavigator().jump(page, QPointF())

    # ------------------------------------------------------------------
    # 缩放与页码导航
    # ------------------------------------------------------------------
    def zoom_in(self) -> None:
        """放大 1.25×（Custom 模式，0.1–8.0 钳制）。"""
        self._zoom_by(ZOOM_STEP)

    def zoom_out(self) -> None:
        """缩小 1/1.25×（Custom 模式，0.1–8.0 钳制）。"""
        self._zoom_by(1 / ZOOM_STEP)

    def fit_width(self) -> None:
        """适应宽度（ZoomMode.FitToWidth）。"""
        self._set_zoom_mode(QPdfView.ZoomMode.FitToWidth)

    def fit_page(self) -> None:
        """适应页面（ZoomMode.FitInView）。"""
        self._set_zoom_mode(QPdfView.ZoomMode.FitInView)

    def step_page(self, delta: int) -> None:
        """上一页（-1）/ 下一页（+1）：首尾页钳制不越界。"""
        if self._document is None or self._document.pageCount() == 0:
            return
        navigator = self._view.pageNavigator()
        page = max(0, min(navigator.currentPage() + delta,
                          self._document.pageCount() - 1))
        navigator.jump(page, QPointF())

    def _zoom_by(self, factor: float) -> None:
        """步进缩放：切 Custom 模式并按 factor 倍率调整（钳制区间内）。"""
        if self._document is None:
            return
        target = self._view.zoomFactor() * factor
        if not ZOOM_MIN <= target <= ZOOM_MAX:
            return  # 已达缩放边界（与图片页同手感）
        self._view.setZoomMode(QPdfView.ZoomMode.Custom)
        self._view.setZoomFactor(target)

    def _set_zoom_mode(self, mode: QPdfView.ZoomMode) -> None:
        if self._document is not None:
            self._view.setZoomMode(mode)

    # ------------------------------------------------------------------
    # 外部打开兜底
    # ------------------------------------------------------------------
    def open_external(self) -> None:
        """「在外部程序打开」：QDesktopServices 调系统 PDF 阅读器。"""
        if self._current_path is None:
            return
        url = QUrl.fromLocalFile(str(self._current_path))
        if not QDesktopServices.openUrl(url):
            self.external_failed.emit("无法调起系统 PDF 阅读器")

    # ------------------------------------------------------------------
    # 弱提示与主题
    # ------------------------------------------------------------------
    def _emit_page_info(self) -> None:
        """页码/缩放弱提示：第 i/N 页 · 缩放%（无文档不发射）。"""
        if self._document is None or self._document.pageCount() == 0:
            return
        page = self._view.pageNavigator().currentPage() + 1
        percent = round(self._view.zoomFactor() * 100)
        self.page_info_changed.emit(f"第 {page}/{self._document.pageCount()} 页 · {percent}%")

    def _on_page_changed(self, _page: int) -> None:
        self._emit_page_info()

    def _on_zoom_changed(self, _factor: float) -> None:
        self._emit_page_info()

    def apply_theme(self, palette: dict) -> None:
        """切换主题：视口背景色自调色板现有令牌派生（不新增令牌，同 ImageViewer 模式）。"""
        viewport = self._view.viewport()
        qt_palette = viewport.palette()
        color = QColor(palette["window_bg"])
        for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base):
            qt_palette.setColor(role, color)
        viewport.setPalette(qt_palette)
        viewport.setAutoFillBackground(True)
        viewport.update()
