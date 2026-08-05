"""滑块开关控件（PySide6 自绘）：iOS 风格两态对色 + 滑动动画。

（2026-08-06，见 文档/修改记录/2026-0806-0327_Markdown阅览源码双模式滑块开关计划 T1）
用法与 QCheckBox 一致（isChecked()/setChecked()/toggled），便于替换复选框。
实现逐字采用 动态标签滑块开关_复用说明.md「完整代码 §1」，关键坑已规避：
动画目标属性必须注册为 Qt Property（Python 内置 @property 无效——运行时
警告 "animate a non-existing property offset"，动画不生效状态直接跳变）。

当前消费方：ViewerPanel 标题行 Markdown「阅览模式/源码模式」切换（仅 Markdown
页可见）。两态对色表达「模式差异」而非「彩色=开/灰色=关」的传统开关语义。
"""

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
)
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QAbstractButton

_TRACK_W = 40      # 轨道宽（像素）
_TRACK_H = 22      # 轨道高（像素）
_KNOB_MARGIN = 3   # 滑块内边距
_ANIM_MS = 150     # 滑动动画时长（毫秒）


class ToggleSwitch(QAbstractButton):
    """滑块开关：checkable，对外接口与 QCheckBox 一致"""

    def __init__(self, parent=None,
                 off_color="#0765d4",   # 关（左）轨道色
                 on_color="#f5b301",    # 开（右）轨道色
                 knob_color="#ffffff"):  # 滑块色
        super().__init__(parent)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._off_color = QColor(off_color)
        self._on_color = QColor(on_color)
        self._knob_color = QColor(knob_color)

        self._offset = 0.0  # 滑块位置比例：0.0（关，左）~ 1.0（开，右）
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(_ANIM_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutCubic)

        self.toggled.connect(self._start_anim)

    # --- 动画属性：必须是 Qt Property，Python 内置 property 无效 ---
    def get_offset(self) -> float:
        return self._offset

    def set_offset(self, value: float):
        self._offset = value
        self.update()

    offset = Property(float, get_offset, set_offset)

    def _start_anim(self, checked: bool):
        # 动画对象长期复用（KeepWhenStopped），切换时仅重设起止值
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def sizeHint(self) -> QSize:
        return QSize(_TRACK_W, _TRACK_H)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        track = QRectF(0.5, 0.5, _TRACK_W - 1, _TRACK_H - 1)
        radius = track.height() / 2

        # 轨道：两态对色
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._on_color if self.isChecked() else self._off_color)
        painter.drawRoundedRect(track, radius, radius)

        # 滑块：圆形，位置由动画属性 _offset 驱动
        knob_d = track.height() - 2 * _KNOB_MARGIN
        travel = track.width() - knob_d - 2 * _KNOB_MARGIN
        knob_x = track.left() + _KNOB_MARGIN + travel * self._offset
        knob = QRectF(knob_x, track.top() + _KNOB_MARGIN, knob_d, knob_d)
        painter.setBrush(self._knob_color)
        painter.drawEllipse(knob)

        painter.end()
