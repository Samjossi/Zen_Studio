"""自定义标题栏（2026-07-30，见 work plans/2026-0730-0007_自定义标题栏增量实施计划.md）。

配合 `Qt.WindowType.FramelessWindowHint` 无边框窗口使用：
- 左侧：Logo（SVG 优先，PNG 兜底，16×16）+ 标题文字（跟随 windowTitle，
  多开窗口的根路径标注经 windowTitleChanged 信号同步，不丢现有功能）；
- 右侧：最小化（—）/ 最大化（□⇄❐）/ 关闭（×）三按钮；
- 交互：左键拖拽移动（优先 `QWindow.startSystemMove()` 由 WM 接管，
  Wayland/X11 流畅无闪烁；不支持时降级手动 move 兜底）、双击切换最大化、
  最大化态拖拽自动还原并按光标横向比例跟随。

配色（2026-07-30，work plans/2026-0730-0043 计划阶段一）：原固定深色四硬编码
收编主题令牌体系——决策点 D1 落 B 方案，每主题 `title_bar` 令牌包
（bg/text/hover/close_hover 四键，见 gui/theme.py THEME_PALETTES）；
A 方案（复用 sidebar_bg 等现有令牌）经核算 sidebar_bg 与 window_bg 色差 <2%，
四亮色主题下标题栏与菜单栏/内容区无区分度，否决。关闭 hover 红 #c75450
跨主题通用，入令牌包便于后续微调（D4）。`apply_theme` 挂
MainWindow.switch_theme 转发链即时联动（同 viewer_panel/settings_dialog 先例），
启动主题于构造时经 load_settings 自检初始化。
"""
from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QMouseEvent, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QToolButton, QWidget

from core.paths import LOGO_DIR
from gui.theme import KEY_THEME, TitleBarPack, get_theme_palette, load_settings

#: 标题栏定高（px）；三按钮宽 46（VS Code 系惯例尺寸）
TITLE_BAR_HEIGHT = 32
BUTTON_WIDTH = 46


def build_qss(pack: TitleBarPack) -> str:
    """按主题 title_bar 令牌包生成内联 QSS（收编后为唯一样式来源）。"""
    return f"""
#TitleBar {{
    background: {pack["bg"]};
}}
#TitleBar QLabel {{
    color: {pack["text"]};
    background: transparent;
}}
#TitleBar QToolButton {{
    background: transparent;
    border: none;
    color: {pack["text"]};
}}
#TitleBar QToolButton:hover {{
    background: {pack["hover"]};
}}
#TitleBar QToolButton#titleBarClose:hover {{
    background: {pack["close_hover"]};
    color: #ffffff;
}}
"""


def _load_logo_pixmap(size: int = 16) -> QPixmap:
    """Logo 加载：SVG 优先（Qt svg 插件，矢量无损），失败回退成套 PNG 中最近尺寸；
    全缺失返回空 QPixmap（QLabel 显示空白，不阻断——与 main.build_app_icon 策略一致）。
    """
    pixmap = QPixmap(str(LOGO_DIR / "logo.svg"))
    if pixmap.isNull():
        pixmap = QPixmap(str(LOGO_DIR / f"logo_{size}.png"))
    if not pixmap.isNull() and pixmap.width() != size:
        pixmap = pixmap.scaled(
            size, size,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return pixmap


class TitleBar(QWidget):
    """无边框窗口自绘标题栏；父窗口须为顶层 QWidget（拖拽/三按钮均作用于 window()）。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("TitleBar")
        self.setFixedHeight(TITLE_BAR_HEIGHT)
        # qss background 对 QWidget 需显式开启样式绘制
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        # 启动主题初始化（构造期自检持久化配置；后续切换走 apply_theme 转发链）
        self.apply_theme(load_settings()[KEY_THEME])

        #: 手动拖拽兜底偏移（startSystemMove 可用时恒为 None）
        self._drag_offset: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 0, 0)
        layout.setSpacing(6)

        logo = QLabel(self)
        logo.setPixmap(_load_logo_pixmap())
        layout.addWidget(logo)

        self._title_label = QLabel(self)
        layout.addWidget(self._title_label)
        layout.addStretch(1)

        self._btn_min = self._make_button("—", "最小化")
        self._btn_max = self._make_button("□", "最大化")
        self._btn_close = self._make_button("×", "关闭", object_name="titleBarClose")
        layout.addWidget(self._btn_min)
        layout.addWidget(self._btn_max)
        layout.addWidget(self._btn_close)

        window = self.window()
        self._btn_min.clicked.connect(window.showMinimized)
        self._btn_max.clicked.connect(self.toggle_maximize)
        self._btn_close.clicked.connect(window.close)

        # 标题跟随 windowTitle（含多开窗口的根路径标注），窗口态变化同步 □⇄❐
        self._title_label.setText(window.windowTitle())
        window.windowTitleChanged.connect(self._title_label.setText)
        window.installEventFilter(self)
        self._sync_max_button()

    # ------------------------------------------------------------------
    # 主题联动（MainWindow.switch_theme 转发链，同五面板/settings_dialog 先例）
    # ------------------------------------------------------------------
    def apply_theme(self, theme: str) -> None:
        """按主题 title_bar 令牌包重刷内联 QSS（未注册主题名由调色板查询兜底）。"""
        self.setStyleSheet(build_qss(get_theme_palette(theme)["title_bar"]))

    # ------------------------------------------------------------------
    # 右侧三按钮
    # ------------------------------------------------------------------
    def _make_button(self, text: str, tooltip: str,
                     object_name: str = "") -> QToolButton:
        button = QToolButton(self)
        if object_name:
            button.setObjectName(object_name)
        button.setText(text)
        button.setToolTip(tooltip)
        button.setFixedSize(BUTTON_WIDTH, TITLE_BAR_HEIGHT)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        return button

    def toggle_maximize(self) -> None:
        """最大化 ⇄ 还原切换（按钮与双击标题栏共用单一入口）。"""
        window = self.window()
        if window.isMaximized():
            window.showNormal()
        else:
            window.showMaximized()

    def _sync_max_button(self) -> None:
        maximized = self.window().isMaximized()
        self._btn_max.setText("❐" if maximized else "□")
        self._btn_max.setToolTip("还原" if maximized else "最大化")

    def eventFilter(self, watched, event) -> bool:
        """窗口态变化（含任务栏还原/WM 快捷键最大化）→ 同步最大化按钮图标。"""
        if watched is self.window() and event.type() == QEvent.Type.WindowStateChange:
            self._sync_max_button()
        return super().eventFilter(watched, event)

    # ------------------------------------------------------------------
    # 拖拽移动与双击（子部件 QLabel 忽略鼠标事件上冒至此，按钮自吞不串扰）
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        window = self.window()
        if window.isMaximized():
            self._restore_for_drag(event)
        handle = window.windowHandle()
        if handle is not None and handle.startSystemMove():
            self._drag_offset = None
            return
        # 兜底：WM 不支持 startSystemMove 时手动跟随
        self._drag_offset = (
            event.globalPosition().toPoint() - window.frameGeometry().topLeft())

    def _restore_for_drag(self, event: QMouseEvent) -> None:
        """最大化态按下：还原窗口并平移，使光标在栏内横向比例不变地接续拖拽。"""
        window = self.window()
        ratio = event.position().x() / max(self.width(), 1)
        window.showNormal()
        new_x = event.globalPosition().x() - ratio * window.width()
        new_y = event.globalPosition().y() - TITLE_BAR_HEIGHT // 2
        window.move(int(new_x), int(new_y))

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if (self._drag_offset is not None
                and event.buttons() & Qt.MouseButton.LeftButton):
            self.window().move(event.globalPosition().toPoint() - self._drag_offset)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.toggle_maximize()
