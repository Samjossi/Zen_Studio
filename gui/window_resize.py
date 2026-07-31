"""无边框窗口八向边缘缩放热区（2026-07-30，文档/修改记录/2026-0730-0043 计划阶段二）。

背景：`Qt.WindowType.FramelessWindowHint` 无边框化后原生缩放热区消失，
鼠标移到窗口边缘显示子控件自有光标（如终端/编辑器 I-beam），无法拉大拉小。

选型（计划 §3.2 B 方案）：窗口级事件过滤器 + 八向热区判定，
`QWindow.startSystemResize(edges)` 优先（Qt6 原生 API，Wayland xdg-toplevel /
X11 均由 WM 接管，流畅无闪烁——与标题栏拖拽 `startSystemMove` 技术路线对称），
返回 False 时记录起始几何与全局坐标，手动 `setGeometry` 兜底（MouseMove 驱动，
释放结束）。QSizeGrip 仅右下角单向、第三方库引入重依赖，均否决。

事件路径（决策点 D2 定案）：QApplication 级事件过滤器（比递归给子控件装过滤器
简单，动态增删子控件无需追踪；过滤器对全应用事件可见，天然覆盖未开
mouseTracking 的子控件——hover 移动事件沿父链上冒至开了 WA_MouseTracking 的
MainWindow，过滤器即可收全）。

光标管理：进入热区对事件命中的叶子控件 setCursor（覆盖其 I-beam 等自有光标），
离开热区 unsetCursor 还原——「光标不被子控件 I-beam 覆盖」验收机制即此。

守卫：最大化/全屏态热区整体禁用（与原生窗口行为一致）；手动兜底路径几何
钳制 minimumSize（MainWindow 已设 900×500，决策点 D3）；热区为窗口内缩判定，
不向外扩（外扩需透明像素边距，侵入布局边距体系，本期不做）。
"""
from typing import cast

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget

#: 热区厚度（px，窗口内缩判定）
RESIZE_MARGIN = 6

_NO_EDGES = Qt.Edge(0)


def _edges_at(window: QWidget, pos: QPoint) -> Qt.Edge:
    """窗口本地坐标 → 命中边标志位组合（左/右/上/下 + 四角由组合自然得出）。"""
    edges = _NO_EDGES
    if pos.x() < RESIZE_MARGIN:
        edges |= Qt.Edge.LeftEdge
    elif pos.x() >= window.width() - RESIZE_MARGIN:
        edges |= Qt.Edge.RightEdge
    if pos.y() < RESIZE_MARGIN:
        edges |= Qt.Edge.TopEdge
    elif pos.y() >= window.height() - RESIZE_MARGIN:
        edges |= Qt.Edge.BottomEdge
    return edges


def _cursor_for(edges: Qt.Edge) -> Qt.CursorShape:
    """命中边组合 → 缩放光标形状（↔↕↘↙）。"""
    horizontal = edges & (Qt.Edge.LeftEdge | Qt.Edge.RightEdge)
    vertical = edges & (Qt.Edge.TopEdge | Qt.Edge.BottomEdge)
    if horizontal and vertical:
        # 左上/右下 = 主对角线（\），右上/左下 = 副对角线（/）
        if (edges & Qt.Edge.LeftEdge and edges & Qt.Edge.TopEdge) or (
                edges & Qt.Edge.RightEdge and edges & Qt.Edge.BottomEdge):
            return Qt.CursorShape.SizeFDiagCursor
        return Qt.CursorShape.SizeBDiagCursor
    if horizontal:
        return Qt.CursorShape.SizeHorCursor
    return Qt.CursorShape.SizeVerCursor


class EdgeResizeController(QObject):
    """无边框窗口八向缩放控制器；随目标窗口生命周期（parent = window）。

    使用：``EdgeResizeController(main_window)`` 一行安装——内部开窗口
    WA_MouseTracking 并向 QApplication 装事件过滤器，无需其他接线。
    """

    def __init__(self, window: QWidget) -> None:
        super().__init__(window)
        self._window = window
        # 子控件未开 tracking 时 hover 移动事件上冒至窗口，保证过滤器收全
        window.setAttribute(Qt.WidgetAttribute.WA_MouseTracking, True)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        #: hover 光标当前覆盖的控件（离开热区时 unsetCursor 还原）
        self._cursor_widget: QWidget | None = None
        #: 手动兜底状态（_manual_edges == _NO_EDGES 即未在手动缩放）
        self._manual_edges = _NO_EDGES
        self._start_geometry = QRect()
        self._start_global = QPoint()

    # ------------------------------------------------------------------
    # 事件过滤（QApplication 级；仅响应目标窗口内事件）
    # ------------------------------------------------------------------
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        etype = event.type()
        # 手动兜底结束（释放不吞，交还原控件）
        if etype == QEvent.Type.MouseButtonRelease and self._manual_edges:
            self._manual_edges = _NO_EDGES
            return False
        # 覆盖光标的控件自身 Leave（移出窗口）→ 还原光标
        if etype == QEvent.Type.Leave and watched is self._cursor_widget:
            self._clear_cursor()
            return False
        if not isinstance(watched, QWidget) or watched.window() is not self._window:
            return False
        if etype == QEvent.Type.MouseMove:
            mouse_event = cast(QMouseEvent, event)
            if self._manual_edges:
                self._manual_resize(mouse_event.globalPosition().toPoint())
                return True
            if mouse_event.buttons():
                return False
            self._update_hover(watched, mouse_event.globalPosition().toPoint())
        elif etype == QEvent.Type.MouseButtonPress:
            return self._try_start_resize(cast(QMouseEvent, event))
        return False

    # ------------------------------------------------------------------
    # 缩放发起：startSystemResize 优先，手动 setGeometry 兜底
    # ------------------------------------------------------------------
    def _try_start_resize(self, event: QMouseEvent) -> bool:
        if event.button() != Qt.MouseButton.LeftButton:
            return False
        window = self._window
        if window.isMaximized() or window.isFullScreen():
            return False
        global_pos = event.globalPosition().toPoint()
        edges = _edges_at(window, window.mapFromGlobal(global_pos))
        if not edges:
            return False
        handle = window.windowHandle()
        if handle is not None and handle.startSystemResize(edges):
            return True  # WM 接管；吞掉按下，防边缘文本选择/终端焦点串扰
        # 兜底：WM 不支持 startSystemResize 时手动跟随
        self._manual_edges = edges
        self._start_geometry = window.geometry()
        self._start_global = global_pos
        return True

    def _manual_resize(self, global_pos: QPoint) -> None:
        """手动兜底：按命中边平移对应边界，钳制 minimumSize（三栏布局不塌）。"""
        dx = global_pos.x() - self._start_global.x()
        dy = global_pos.y() - self._start_global.y()
        rect = QRect(self._start_geometry)
        min_w = self._window.minimumWidth()
        min_h = self._window.minimumHeight()
        edges = self._manual_edges
        if edges & Qt.Edge.LeftEdge:
            rect.setLeft(min(rect.left() + dx, rect.right() - min_w + 1))
        if edges & Qt.Edge.RightEdge:
            rect.setRight(max(rect.right() + dx, rect.left() + min_w - 1))
        if edges & Qt.Edge.TopEdge:
            rect.setTop(min(rect.top() + dy, rect.bottom() - min_h + 1))
        if edges & Qt.Edge.BottomEdge:
            rect.setBottom(max(rect.bottom() + dy, rect.top() + min_h - 1))
        self._window.setGeometry(rect)

    # ------------------------------------------------------------------
    # hover 光标管理（覆盖叶子控件自有光标，离开即还原）
    # ------------------------------------------------------------------
    def _update_hover(self, watched: QWidget, global_pos: QPoint) -> None:
        window = self._window
        if window.isMaximized() or window.isFullScreen():
            self._clear_cursor()
            return
        edges = _edges_at(window, window.mapFromGlobal(global_pos))
        if not edges:
            self._clear_cursor()
            return
        cursor = _cursor_for(edges)
        if self._cursor_widget is not watched:
            self._clear_cursor()
            watched.setCursor(cursor)
            self._cursor_widget = watched
        elif watched.cursor().shape() != cursor:
            # 同控件内边⇄角滑动：更新光标形状
            watched.setCursor(cursor)

    def _clear_cursor(self) -> None:
        if self._cursor_widget is not None:
            self._cursor_widget.unsetCursor()
            self._cursor_widget = None
