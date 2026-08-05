"""图片查看器：QGraphicsView 承载位图 / SVG / GIF 的就地预览。

（2026-07-29，见 文档/修改记录/2026-0729-1102_图片文件预览功能实施计划 T1–T3）
形态：内嵌 ViewerPanel 图片页（QStackedLayout 与 CodeViewer 文本页切换）。
能力：默认实际像素 100% 显示（可切 fit 适应窗口）、锚点滚轮缩放、拖拽平移、同目录循环翻页、
GIF 动画（QMovie）、SVG 矢量渲染、棋盘格透明底、超大图防护。

协议合规（见 work options/2026-0729-1028_theia实质代码审计与协议补全建议.md）：
- theia-zen（EPL-2.0）：零接触，无任何代码/思想移植。
- PyGPT（MIT，© Marcin Szczygliński）：仅参考其数值常量（缩放步进 1.25、
  缩放区间 5%–1600%、单边 32768px / 总像素 80MP 防护阈值）与交互思路
  （锚点缩放、同目录循环导航）——数值与交互思想不受著作权保护，
  本文件为 QGraphicsView 体系的独立实现，未复制其 QLabel 体系代码。
- Multi_Cli_Studio（MIT，© Austin-Patrician）：棋盘格透明底交互思路借鉴
  （其实现为 CSS 渐变，不可移植），此处 QPainter 自绘棋盘独立实现。
"""
from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QColor, QImageReader, QMovie, QPainter, QPixmap
from PySide6.QtSvgWidgets import QGraphicsSvgItem
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsPixmapItem,
    QGraphicsScene,
    QGraphicsView,
    QWidget,
)

#: 可预览图片扩展名（不含点）：QImageReader 支持集 + 手工补 svg
IMAGE_EXTS: frozenset[str] = frozenset(
    bytes(fmt).decode() for fmt in QImageReader.supportedImageFormats()
) | {"svg"}

#: 缩放步进与区间（参考 PyGPT 数值：1.25× 步进、5%–1600%）
ZOOM_STEP = 1.25
ZOOM_MIN = 0.05
ZOOM_MAX = 16.0
#: 超大图防护（参考 PyGPT 阈值：单边 32768px / 总像素 80MP，防解码撑爆内存）
MAX_IMAGE_SIDE = 32768
MAX_IMAGE_PIXELS = 80_000_000
#: 棋盘格单格边长（px；两格拼一周期）
CHECKER_CELL_PX = 8


class ImageViewer(QGraphicsView):
    """图片预览控件：位图 / SVG 矢量 / GIF 动画 + 缩放平移翻页 + 棋盘格底。"""

    #: 展示信息变化（打开/翻页/缩放后），文案形如 "2/5 · 1920×1080 · 125%"
    info_changed = Signal(str)

    def __init__(self, palette: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setFrameShape(QFrame.Shape.NoFrame)  # 卡片统一描边，控件自身去框
        self.setRenderHints(
            QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing)
        # 滚轮缩放以鼠标位置为锚点（指向点不漂）
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        # 平移全由拖拽承担，滚动条隐藏（看图惯例）
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        self._item: QGraphicsPixmapItem | QGraphicsSvgItem | None = None
        self._movie: QMovie | None = None
        self._siblings: list[Path] = []  # 同目录图片集（翻页序列）
        self._index = -1
        self._image_size = QSize()
        self._fit_mode = False  # True=适应窗口（resize 自动重 fit）；False=实际像素/手动缩放

        self._bg_color = QColor(palette["window_bg"])
        self._checker_tile = self._build_checker_tile(palette)

    # ------------------------------------------------------------------
    # 打开与加载
    # ------------------------------------------------------------------
    def open_image(self, path: Path) -> str | None:
        """打开图片文件；成功返回 None，失败返回原因字符串（面板转占位提示）。"""
        self._stop_movie()
        self._scan_siblings(path)
        suffix = path.suffix.lower().lstrip(".")
        try:
            if suffix == "svg":
                error = self._load_svg(path)
            elif suffix == "gif":
                error = self._load_gif(path)
            else:
                error = self._load_raster(path)
        except OSError as e:
            return str(e)
        if error is not None:
            return error
        self.actual()  # 默认实际像素 100% 显示（actual 内已发 info_changed）
        return None

    def _load_raster(self, path: Path) -> str | None:
        """位图加载：QImageReader 读入（AutoTransform 尊重 EXIF 方向）。"""
        reader = QImageReader(str(path))
        reader.setAutoTransform(True)
        if guard := self._oversize_guard(reader.size()):
            return guard
        image = reader.read()
        if image.isNull():
            return "无法解码（文件损坏或格式不受支持）"
        self._set_pixmap(QPixmap.fromImage(image))
        return None

    def _load_gif(self, path: Path) -> str | None:
        """GIF 加载：QMovie 驱动动画；单帧或非法时退化静态位图。"""
        if guard := self._oversize_guard(QImageReader(str(path)).size()):
            return guard
        movie = QMovie(str(path))
        if not movie.isValid():
            return self._load_raster(path)
        movie.jumpToFrame(0)
        self._movie = movie
        self._set_pixmap(movie.currentPixmap())
        movie.frameChanged.connect(self._on_movie_frame)
        if movie.frameCount() > 1:  # 非动图退化静态（不启动播放）
            movie.start()
        return None

    def _load_svg(self, path: Path) -> str | None:
        """SVG 加载：QGraphicsSvgItem 矢量渲染，放大无损（仅渲染，无源码视图）。"""
        item = QGraphicsSvgItem(str(path))
        if not item.renderer().isValid():
            return "无法解析 SVG（文件损坏或格式不受支持）"
        self._scene.clear()
        self._item = item
        self._scene.addItem(item)
        self._scene.setSceneRect(item.boundingRect())
        self._image_size = item.boundingRect().size().toSize()
        return None

    def _set_pixmap(self, pixmap: QPixmap) -> None:
        self._scene.clear()
        self._item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(self._item.boundingRect())
        self._image_size = pixmap.size()

    @staticmethod
    def _oversize_guard(size: QSize) -> str | None:
        """超大图防护：单边/总像素超阈值返回原因；尺寸未知（无效）放行读后再判。"""
        if not size.isValid():
            return None
        w, h = size.width(), size.height()
        if max(w, h) > MAX_IMAGE_SIDE:
            return f"超过单边像素上限（{w}×{h}，上限 {MAX_IMAGE_SIDE}px）"
        if w * h > MAX_IMAGE_PIXELS:
            return f"超过总像素上限（{w}×{h}，上限 80MP）"
        return None

    def _on_movie_frame(self) -> None:
        if isinstance(self._item, QGraphicsPixmapItem) and self._movie is not None:
            self._item.setPixmap(self._movie.currentPixmap())

    def _stop_movie(self) -> None:
        if self._movie is not None:
            self._movie.stop()
            self._movie = None

    def hideEvent(self, event) -> None:
        """切页/关窗即停 GIF 播放，防后台空转（重新打开时 open_image 重建）。"""
        self._stop_movie()
        super().hideEvent(event)

    # ------------------------------------------------------------------
    # 同目录翻页
    # ------------------------------------------------------------------
    def _scan_siblings(self, path: Path) -> None:
        """扫描同目录图片集并定位当前索引（循环翻页序列）。"""
        self._siblings = sorted(
            p for p in path.parent.iterdir()
            if p.is_file() and p.suffix.lower().lstrip(".") in IMAGE_EXTS
        )
        try:
            self._index = self._siblings.index(path)
        except ValueError:
            self._index = 0

    def step(self, delta: int) -> None:
        """上一张（-1）/ 下一张（+1）：同目录循环步进。"""
        if len(self._siblings) < 2:
            return
        target = self._siblings[(self._index + delta) % len(self._siblings)]
        self.open_image(target)

    # ------------------------------------------------------------------
    # 缩放与平移
    # ------------------------------------------------------------------
    def fit(self) -> None:
        """适应窗口：整图等比缩放到视口内（进入 fit 模式，resize 自动重算）。"""
        if self._item is None:
            return
        self._fit_mode = True
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._update_drag()
        self.info_changed.emit(self.info())

    def actual(self) -> None:
        """实际像素：100% 显示（退出 fit 模式）。"""
        if self._item is None:
            return
        self._fit_mode = False
        self.resetTransform()
        self._update_drag()
        self.info_changed.emit(self.info())

    def wheelEvent(self, event) -> None:
        """滚轮缩放：1.25× 步进、5%–1600% 钳制、锚点为鼠标位置。"""
        if self._item is None:
            return super().wheelEvent(event)
        step = ZOOM_STEP if event.angleDelta().y() > 0 else 1 / ZOOM_STEP
        if not ZOOM_MIN <= self.transform().m11() * step <= ZOOM_MAX:
            return  # 已达缩放边界
        self._fit_mode = False
        self.scale(step, step)
        self._update_drag()
        self.info_changed.emit(self.info())

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._fit_mode and self._item is not None:
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        self._update_drag()

    def keyPressEvent(self, event) -> None:
        """Left/Right：同目录上一张/下一张。"""
        if event.key() == Qt.Key.Key_Left:
            return self.step(-1)
        if event.key() == Qt.Key.Key_Right:
            return self.step(1)
        super().keyPressEvent(event)

    def _update_drag(self) -> None:
        """图大于视口才允许拖拽平移（小图禁用抓手光标）。"""
        mode = QGraphicsView.DragMode.NoDrag
        if self._item is not None:
            shown = self.mapFromScene(self._scene.sceneRect()).boundingRect()
            if (shown.width() > self.viewport().width()
                    or shown.height() > self.viewport().height()):
                mode = QGraphicsView.DragMode.ScrollHandDrag
        self.setDragMode(mode)

    # ------------------------------------------------------------------
    # 信息与主题
    # ------------------------------------------------------------------
    def info(self) -> str:
        """展示信息：目录序号 i/N · 原始尺寸 · 当前缩放%（状态栏精简替代）。"""
        if self._item is None:
            return ""
        pos = f"{self._index + 1}/{len(self._siblings)}" if self._siblings else "-"
        percent = round(self.transform().m11() * 100)
        return f"{pos} · {self._image_size.width()}×{self._image_size.height()} · {percent}%"

    def apply_theme(self, palette: dict) -> None:
        """切换主题：页面底色与棋盘两色自调色板现有令牌派生（不新增令牌）。"""
        self._bg_color = QColor(palette["window_bg"])
        self._checker_tile = self._build_checker_tile(palette)
        self.viewport().update()

    @staticmethod
    def _build_checker_tile(palette: dict) -> QPixmap:
        """棋盘格 tile：8px 双格拼 16px 周期，两色取 window_bg / border 令牌。"""
        cell = CHECKER_CELL_PX
        tile = QPixmap(cell * 2, cell * 2)
        tile.fill(QColor(palette["border"]))
        painter = QPainter(tile)
        painter.fillRect(0, 0, cell, cell, QColor(palette["window_bg"]))
        painter.fillRect(cell, cell, cell, cell, QColor(palette["window_bg"]))
        painter.end()
        return tile

    def drawBackground(self, painter: QPainter, rect) -> None:
        """背景：页面底色平铺 + 图像区棋盘格（透明 PNG/GIF/SVG 可读性）。

        不透明位图场景棋盘被图像完全遮盖，无视觉副作用。
        """
        painter.fillRect(rect, self._bg_color)
        if self._item is not None:
            painter.drawTiledPixmap(self._scene.sceneRect().intersected(rect),
                                    self._checker_tile)
