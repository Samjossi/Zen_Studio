"""弹出浮层透明化：消除"矩形窗口套圆角内容"的双框观感。

背景（2026-07-20）：主题 qss 给 QMenu（border-radius: 8px）与下拉
弹出列表（命中 QListView 全局规则 border-radius: 6px）设了圆角，
但 Linux 上顶级弹出窗口（Qt.Popup）是不透明矩形——圆角只作用于
控件绘制层，窗口四个直角仍被背景填满，视觉上像矩形里包着圆角矩形。

修复（方案 A）：弹出窗口设 WA_TranslucentBackground，让圆角外区域
真正透明；NoDropShadowWindowHint 去掉窗口管理器按矩形外框绘制的
投影（否则透明后仍残留矩形阴影）。

QComboBox 补充（2026-07-20 第二轮，0751 计划 §3.2 实验链定论）：
下拉弹出容器（QComboBoxPrivateContainer）矩形底的真凶是**调色板填
充**——容器 / view / 视口三层的 Window/Base 角色背景填充，NoFrame
与 WA_TranslucentBackground 均无法去除（离屏实验：仅透明+NoFrame
四角仍为不透明灰 rgba(153,153,154,255)）。须对三层控件做
autoFillBackground(False) + Window/Base 角色全透明；容器面板
（StyledPanel）仍以 NoFrame 去除（内部 view 的 qss 圆角规则完整
提供背景/描边/圆角，去框与透明化无视觉损失）。

规约（2026-07-20）：后续任何新建 QMenu 一律经 make_translucent_popup()、
新建 QComboBox 一律经 make_translucent_combo_popup() 处理后再使用；
菜单栏装配器已对全部菜单批量处理，菜单模块内无需单独调用。

标准右键菜单（2026-07-20 第二轮）：文本类控件（QTextEdit/QTextBrowser/
QPlainTextEdit/QLineEdit）右键弹出的标准编辑菜单由 Qt 内部即时创建，
不经过显式 new QMenu 的任何修复点，须用 exec_standard_context_menu()
替代控件默认 contextMenuEvent（见 0751 第二轮修复计划 §2.1/§3.1）。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QComboBox, QFrame, QLineEdit, QPlainTextEdit, QWidget


def make_translucent_popup(widget: QWidget) -> QWidget:
    """将顶级弹出浮层（QMenu / QComboBox 弹出容器）背景透明化。

    原样返回传入对象，便于创建处链式使用。
    """
    widget.setWindowFlags(
        widget.windowFlags() | Qt.WindowType.NoDropShadowWindowHint)
    widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    return widget


def make_translucent_combo_popup(combo: QComboBox) -> QComboBox:
    """QComboBox 下拉弹出层透明化（去容器面板 + 三层调色板填充透明）。

    判定标准见 0751 计划 §3.2：截图四角 alpha == 0、内部绘制完整。
    原样返回传入 combo，便于创建处链式使用。
    """
    popup = make_translucent_popup(combo.view().window())
    popup.setFrameShape(QFrame.Shape.NoFrame)
    # 三层调色板填充（矩形底真凶）：Window/Base 角色全透明
    for w in (popup, combo.view(), combo.view().viewport()):
        w.setAutoFillBackground(False)
        palette = w.palette()
        for role in (palette.ColorRole.Window, palette.ColorRole.Base):
            palette.setColor(role, QColor(0, 0, 0, 0))
        w.setPalette(palette)
    return combo


def exec_standard_context_menu(widget: QWidget, event) -> None:
    """标准编辑菜单透明化弹出：替代控件默认 contextMenuEvent。

    菜单项一律取自控件内建 createStandardContextMenu()（不重建、不丢项），
    仅经 make_translucent_popup() 透明化后 exec。
    （QLineEdit 版签名无 pos 参数，isinstance 分支兼容。）
    """
    if isinstance(widget, QLineEdit):
        menu = widget.createStandardContextMenu()
    else:
        menu = widget.createStandardContextMenu(event.pos())
    make_translucent_popup(menu)
    menu.exec(event.globalPos())
    menu.deleteLater()


class TranslucentMenuLineEdit(QLineEdit):
    """右键标准菜单透明化的 QLineEdit（查找框等零散实例直接用）。"""

    def contextMenuEvent(self, event) -> None:
        exec_standard_context_menu(self, event)


class TranslucentMenuPlainTextEdit(QPlainTextEdit):
    """右键标准菜单透明化的 QPlainTextEdit（权限对话框详情等直接用）。"""

    def contextMenuEvent(self, event) -> None:
        exec_standard_context_menu(self, event)
