"""主窗口客户端自绘投影宿主（Wayland 专属）。

Wayland 下窗口装饰与阴影是客户端责任，合成器不代画；X11 由 Mutter
服务端绘阴影，本宿主不启用（防双重阴影）。启停判断单点收口在
core.platform_info.is_wayland()，本模块不感知平台。

实现：宿主包一层可视壳（#WindowShell），启用时四边留 SHADOW_MARGIN
透明边距，paintEvent 以离屏一次性生成的九宫格图元拼贴阴影——resize
仅重拼贴不重算模糊，拖拽缩放性能确定。四边为线性渐变边条，四角为
同一一维剖面的逐像素乘积（f(dx)×f(dy)，盒式模糊矩形角的物理结果；
径向渐变会在 45° 方向鼓出"深结"，与边条剖面不匹配）。最大化/全屏
时 set_shadow_active(False) 边距归零停绘（内容贴满物理边），由
MainWindow.changeEvent 驱动；阴影环整圈兼任缩放热区（window_resize
按 shadow_margin 内移判定基准，对标 macOS 手感）。
"""
import math

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import (
    QColor,
    QImage,
    QLinearGradient,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import QVBoxLayout, QWidget

#: 阴影环厚度（px，四边等宽；环区透明，供阴影落色并兼任缩放热区）
SHADOW_MARGIN = 15
#: 双层叠加，骨架对标 GNOME libadwaita window.csd（维护手册《窗口投影参数手册》
#: 2 号，收边线略去），浓度在其基础上整体减半——原生峰值在浅色主题
#: 白底下偏浓，减半后贴边峰值 ≈12% 黑：宽软层铺满全环定调浮起感，
#: 贴身层在邻内容窄带补浓度。六主题共用不随主题换色
_WIDE_ALPHA = 19
_TIGHT_ALPHA = 13
#: 贴身层厚度（px；仅占全环内侧一段，之外恒零）
_TIGHT_THICK = 7

#: 边条渐变采样挡位（一维剖面的分段线性逼近；加密至 1/32 并钉入贴身层
#: 边界膝点，否则两段衰减的交接处会留下可见折线糙感；角部逐像素
#: 乘积共用同一剖面函数，天然平滑）
_PROFILE_STOPS = tuple(sorted(
    {i / 32 for i in range(33)} | {_TIGHT_THICK / SHADOW_MARGIN}))


def _profile(t: float) -> float:
    """一维衰减剖面：t=0 贴可视边（峰值 1）、t=1 环外缘（归零）。

    cos 在 t=0 处零斜率——峰值与可视边软着陆，无生硬接缝线；
    2 次幂收紧尾部，扩散收敛、近似盒式模糊的自然衰减。
    """
    return math.cos(math.pi / 2 * t) ** 2


def _layers(dx: float) -> tuple[float, float]:
    """归一化环位 dx（0=可视边、1=环外缘）处的 (贴身层, 宽软层) alpha。"""
    wide = _WIDE_ALPHA * _profile(dx)
    px = dx * SHADOW_MARGIN
    tight = (_TIGHT_ALPHA * _profile(px / _TIGHT_THICK)
             if px < _TIGHT_THICK else 0.0)
    return tight, wide


def _over(top: float, bottom: float) -> int:
    """两层 alpha 叠乘合成（top over bottom）。"""
    return round(top + bottom * (255 - top) / 255)


def _edge_color(t: float) -> QColor:
    tight, wide = _layers(t)
    return QColor(0, 0, 0, _over(tight, wide))


def _paint_edge(size: tuple[int, int], gradient) -> QPixmap:
    pixmap = QPixmap(*size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    for pos in _PROFILE_STOPS:
        gradient.setColorAt(pos, _edge_color(pos))
    painter.fillRect(0, 0, size[0], size[1], gradient)
    painter.end()
    return pixmap


def _paint_corner(margin: int, flip_x: bool, flip_y: bool) -> QPixmap:
    """角部图元：每层 alpha = 剖面(dx) × 剖面(dy)，两层再叠乘合成。

    乘积与边条在接缝处数学连续（dx=0 时退化为边条值），且角部天然
    淡于边条；flip_* 标记内容角点朝向（如 br 角内容点在 pixmap 左上，
    dx=x、dy=y 均不翻转）。
    """
    image = QImage(margin, margin, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    for y in range(margin):
        dy = margin - 1 - y if flip_y else y
        t_y = (dy + 0.5) / margin
        tight_y, wide_y = _layers(t_y)
        for x in range(margin):
            dx = margin - 1 - x if flip_x else x
            t_x = (dx + 0.5) / margin
            tight_x, wide_x = _layers(t_x)
            alpha = _over(tight_x * tight_y / _TIGHT_ALPHA,
                          wide_x * wide_y / _WIDE_ALPHA)
            image.setPixelColor(x, y, QColor(0, 0, 0, alpha))
    return QPixmap.fromImage(image)


def _build_parts(margin: int) -> dict[str, QPixmap]:
    """离屏生成九宫格图元：四边条（线性渐变）+ 四角（剖面乘积）。"""
    parts = {
        # 边条渐变起点钉在邻内容侧（剖面 t=0 = 可视边）
        "l": _paint_edge((margin, 1), QLinearGradient(margin, 0, 0, 0)),
        "r": _paint_edge((margin, 1), QLinearGradient(0, 0, margin, 0)),
        "t": _paint_edge((1, margin), QLinearGradient(0, margin, 0, 0)),
        "b": _paint_edge((1, margin), QLinearGradient(0, 0, 0, margin)),
        "tl": _paint_corner(margin, flip_x=True, flip_y=True),
        "tr": _paint_corner(margin, flip_x=False, flip_y=True),
        "br": _paint_corner(margin, flip_x=False, flip_y=False),
        "bl": _paint_corner(margin, flip_x=True, flip_y=False),
    }
    return parts


class ShadowHost(QWidget):
    """可视壳宿主：shadow_on 时四边留透明环绘投影，否则零边距直通。"""

    def __init__(self, shell: QWidget, shadow_on: bool,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._active = shadow_on
        # 未启用平台永不激活（set_shadow_active 对 None 图元短路）
        self._parts = _build_parts(SHADOW_MARGIN) if shadow_on else None
        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        margin = SHADOW_MARGIN if shadow_on else 0
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.addWidget(shell)

    def shadow_margin(self) -> int:
        """当前阴影环厚度（0 = 已收起；缩放热区判定基准内移量）。"""
        return SHADOW_MARGIN if self._active else 0

    def set_shadow_active(self, active: bool) -> None:
        """最大化/全屏 ↔ 投影收放（边距归零即停绘，内容贴满物理边）。"""
        if self._parts is None or active == self._active:
            return
        self._active = active
        margin = SHADOW_MARGIN if active else 0
        self.layout().setContentsMargins(margin, margin, margin, margin)
        self.update()

    def paintEvent(self, event) -> None:
        if self._parts is not None and self._active:
            margin = SHADOW_MARGIN
            rect = self.rect()
            inner = rect.adjusted(margin, margin, -margin, -margin)
            painter = QPainter(self)
            # 四边拉伸拼贴（纵向条占满内高，横向条占满内宽）
            painter.drawPixmap(
                QRect(inner.left(), 0, inner.width(), margin), self._parts["t"])
            painter.drawPixmap(
                QRect(inner.left(), inner.bottom() + 1, inner.width(), margin),
                self._parts["b"])
            painter.drawPixmap(
                QRect(0, inner.top(), margin, inner.height()), self._parts["l"])
            painter.drawPixmap(
                QRect(inner.right() + 1, inner.top(), margin, inner.height()),
                self._parts["r"])
            # 四角原样落位
            painter.drawPixmap(0, 0, self._parts["tl"])
            painter.drawPixmap(inner.right() + 1, 0, self._parts["tr"])
            painter.drawPixmap(0, inner.bottom() + 1, self._parts["bl"])
            painter.drawPixmap(
                inner.right() + 1, inner.bottom() + 1, self._parts["br"])
            painter.end()
        super().paintEvent(event)
