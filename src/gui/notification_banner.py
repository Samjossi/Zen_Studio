"""任务完成顶部通知横幅：悬浮于主窗口顶部居中，自动消退。

本仓此前唯一的通知通道是状态栏 showMessage（主窗口约 20 处），无 toast/
banner 先例，本组件为首件；两行式布局（标题 + 摘要）参考 kilocode
notification-banner.tsx。单横幅设计：新通知到来替换旧内容并重置计时，
不做队列（完成提醒低频，队列反而让旧通知滞留在屏幕上误导用户）。
"""
from PySide6.QtCore import QEasingCurve, QEvent, QPropertyAnimation, Qt, QTimer
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QHBoxLayout,
    QLabel,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from gui.settings import KEY_THEME, load_settings
from gui.theme import WARNING_COLOR, get_theme_palette

#: 提问态强调色：区别于成功的主题 accent 与失败的 WARNING_COLOR；
#: 主题令牌无现成琥珀色，常量于此（深/浅底均可读的中性琥珀）
_QUESTION_ACCENT = "#b7791f"

#: 展示时长（毫秒）：到期后淡出隐藏
_DISPLAY_MS = 4000
#: 淡出动画时长（毫秒）
_FADE_MS = 300
#: 横幅最大宽度（px）；窗口过窄时随窗口收缩（留边距）
_MAX_WIDTH = 480
#: 距父窗口顶缘/侧缘的边距（px）
_TOP_MARGIN = 12
_SIDE_MARGIN = 40


class NotificationBanner(QFrame):
    """顶部悬浮通知横幅（QFrame 子控件叠放于父窗口，非模态、不抢焦点）。

    :param parent: 宿主主窗口（横幅随其几何定位；resize 经事件过滤器跟随）
    """

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        # 内联 qss 自绘背景（app 级 qss 不管辖本组件的动态令牌配色）
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._title_label = QLabel(self)
        self._title_label.setObjectName("notificationBannerTitle")
        close_button = QToolButton(self)
        close_button.setText("✕")
        close_button.setAutoRaise(True)
        close_button.clicked.connect(self.hide_banner)
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.addWidget(self._title_label, 1)
        title_row.addWidget(close_button, 0)

        self._body_label = QLabel(self)
        self._body_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(2)
        layout.addLayout(title_row)
        layout.addWidget(self._body_label)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._start_fade)

        self._opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self._opacity_effect)
        self._fade = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade.setDuration(_FADE_MS)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.finished.connect(self._on_fade_finished)

        parent.installEventFilter(self)  # 跟随父窗口 resize 重新定位
        self.hide()

    def show_completion(self, success: bool, detail: str = "") -> None:
        """任务完成/失败横幅薄封装：映射到 success/error 事件。"""
        self.show_notification("success" if success else "error", detail)

    def show_notification(self, kind: str, detail: str = "") -> None:
        """展示通知横幅（新通知替换旧内容并重置计时）。

        :param kind: 事件态（success/error/question），决定标题、默认正文与强调色
        """
        palette = get_theme_palette(load_settings()[KEY_THEME])
        accent, title, body = {
            "success": (palette["accent"], "任务完成", "AI 已完成本轮任务"),
            "error": (WARNING_COLOR, "任务失败", "AI 本轮请求失败"),
            "question": (_QUESTION_ACCENT, "需要你的回答", "AI 发起了提问，请在对话卡片中作答"),
        }.get(kind, (palette["accent"], "通知", ""))
        self.setStyleSheet(
            f"NotificationBanner {{ background: {palette['popup_bg']};"
            f" border: 1px solid {palette['popup_border']};"
            f" border-radius: 8px; }}"
            f"QLabel {{ border: none; background: transparent; }}"
            f"#notificationBannerTitle {{ color: {accent}; font-weight: bold; }}"
        )
        self._title_label.setText(title)
        self._body_label.setText(detail or body)
        self._body_label.setStyleSheet(f"color: {palette['text']};")

        self._fade.stop()  # 重置淡出状态：复现时先恢复全不透明
        self._opacity_effect.setOpacity(1.0)
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        self._hide_timer.start(_DISPLAY_MS)

    def hide_banner(self) -> None:
        """立即隐藏（关闭按钮路径，跳过淡出动画）。"""
        self._hide_timer.stop()
        self._fade.stop()
        self.hide()

    def eventFilter(self, watched, event) -> bool:
        if (watched is self.parent() and event.type() == QEvent.Type.Resize
                and self.isVisible()):
            self._reposition()
        return super().eventFilter(watched, event)

    def _reposition(self) -> None:
        parent = self.parent()
        if parent is None:
            return
        width = min(_MAX_WIDTH, max(200, parent.width() - 2 * _SIDE_MARGIN))
        self.setFixedWidth(width)
        self.adjustSize()
        x = (parent.width() - width) // 2
        self.move(x, _TOP_MARGIN)

    def _start_fade(self) -> None:
        self._fade.setStartValue(1.0)
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if self._opacity_effect.opacity() == 0.0:
            self.hide()
            self._opacity_effect.setOpacity(1.0)  # 复位备下次复现
