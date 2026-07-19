"""弹出浮层透明化：消除"矩形窗口套圆角内容"的双框观感。

背景（2026-07-20）：主题 qss 给 QMenu（border-radius: 8px）与下拉
弹出列表（命中 QListView 全局规则 border-radius: 6px）设了圆角，
但 Linux 上顶级弹出窗口（Qt.Popup）是不透明矩形——圆角只作用于
控件绘制层，窗口四个直角仍被背景填满，视觉上像矩形里包着圆角矩形。

修复（方案 A）：弹出窗口设 WA_TranslucentBackground，让圆角外区域
真正透明；NoDropShadowWindowHint 去掉窗口管理器按矩形外框绘制的
投影（否则透明后仍残留矩形阴影）。

QComboBox 补充（2026-07-20 二次修复）：其弹出容器是 QFrame 子类
（QComboBoxPrivateContainer），frameShape 默认 StyledPanel——样式
引擎会为它绘制一个矩形面板，仅设透明背景无法去除，须显式 NoFrame
（背景与描边已由内部 view 的 qss 圆角规则完整提供，去框无影响）。

规约（2026-07-20）：后续任何新建 QMenu 一律经 make_translucent_popup()、
新建 QComboBox 一律经 make_translucent_combo_popup() 处理后再使用；
菜单栏装配器已对全部菜单批量处理，菜单模块内无需单独调用。
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QFrame, QWidget


def make_translucent_popup(widget: QWidget) -> QWidget:
    """将顶级弹出浮层（QMenu / QComboBox 弹出容器）背景透明化。

    原样返回传入对象，便于创建处链式使用。
    """
    widget.setWindowFlags(
        widget.windowFlags() | Qt.WindowType.NoDropShadowWindowHint)
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    return widget


def make_translucent_combo_popup(combo: QComboBox) -> QComboBox:
    """QComboBox 下拉弹出层透明化 + 去容器矩形面板（见模块 docstring）。

    原样返回传入 combo，便于创建处链式使用。
    """
    popup = make_translucent_popup(combo.view().window())
    popup.setFrameShape(QFrame.Shape.NoFrame)
    return combo
