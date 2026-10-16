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
统一形态规约：右键菜单一律带 simplify=True——只读控件精简后恰为
「复制」「全选」两项（零散实例用 SimplifiedMenuTextBrowser /
SimplifiedMenuTextEdit），可编辑控件保留完整编辑项、去图标并中文化
（TranslucentMenuLineEdit / TranslucentMenuPlainTextEdit）；菜单项与
Ctrl+C/Ctrl+A 快捷键正交并存（精简只去文本显示后缀，按键处理不动）。

控件级 qss 规约（2026-08-11，左栏右键菜单透明修复）：文本类控件挂
控件级 setStyleSheet 必须带选择器（#对象名 / 类选择器），禁止无选择器
的 background 声明——标准右键菜单由 Qt 以控件为父即时创建，控件级
样式表沿父子链级联，无选择器声明等价通配，会把 background: transparent
灌进子代 QMenu、覆盖应用级主题底色致菜单全透明（cards.py
BodyText/BodyHtml 实证根因）。静态守卫：scripts/check_qss_selector_guard.py。

tooltip 双框（2026-10-13，设置中心问号图标反馈）：QToolTip 的深灰底
白字圆角是规范 §6.11 钦定，真缺陷仍是矩形底座残留（同 QMenu 根因）。
QTipLabel 由 Qt 内部即时创建、无显式 new 点，规约函数无从挂载——
用 install_translucent_tooltips(app) 装 app 级事件过滤器，在 Polish/Show
时机捕获 QTipLabel 批量补透明属性（方案 A 延伸）；MainWindow 启动挂载一次。
"""
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QIcon
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QTextBrowser,
    QTextEdit,
    QWidget,
)


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


class _TooltipTranslucencyFilter(QObject):
    """app 级事件过滤器：捕获 Qt 内部即时创建的 QTipLabel（tooltip 窗口）。

    QTipLabel 无显式 new 点，make_translucent_popup() 无从调用；在
    Polish/Show 时机（首帧绘制前）补 WA_TranslucentBackground +
    NoDropShadowWindowHint，消除矩形底座与矩形投影残留。
    """

    def eventFilter(self, watched: QObject, event) -> bool:
        if (event.type() in (QEvent.Type.Polish, QEvent.Type.Show)
                and watched.inherits("QTipLabel")):
            make_translucent_popup(watched)
        return False


#: 安装器幂等凭据：过滤器实例模块级持有（防 GC + 防重复叠装）
_tooltip_filter: _TooltipTranslucencyFilter | None = None


def install_translucent_tooltips(app: QApplication) -> None:
    """tooltip 双框修复：app 级安装 QTipLabel 透明化过滤器（幂等——
    重复调用不叠第二个过滤器，MainWindow 启动挂载一次即可）。
    """
    global _tooltip_filter
    if _tooltip_filter is not None:
        return
    _tooltip_filter = _TooltipTranslucencyFilter(app)
    app.installEventFilter(_tooltip_filter)


#: Qt 标准编辑菜单 action（objectName 稳定）→ 中文文案（应用未装载
#: QTranslator，标准菜单恒为英文；左栏精简菜单按此表汉化）
_STANDARD_MENU_LABELS = {
    "edit-undo": "撤销",
    "edit-redo": "重做",
    "edit-cut": "剪切",
    "edit-copy": "复制",
    "edit-paste": "粘贴",
    "edit-delete": "删除",
    "select-all": "全选",
}


def simplify_standard_menu(menu: QMenu) -> QMenu:
    """标准编辑菜单精简（2026-08-11 左栏会话区拍板）：纯文字命令名。

    - 去图标：edit-copy/select-all 等主题图标无信息量，置空；
    - 去快捷键提示：Qt 把快捷键以 \\t 后缀拼在 action.text() 里
      （"&Copy\\tCtrl+C"），截断即不显示（控件自身按键处理不受影响，
      Ctrl+C 等快捷键照常用）；
    - 删 Copy Link Location（objectName "link-copy"，QTextBrowser 标准
      菜单自带的小众项，左栏无使用场景）；
    - 文案中文化：应用未装载 QTranslator，Qt 标准菜单恒为英文，
      按 objectName 精确映射为中文（_STANDARD_MENU_LABELS）。

    原样返回传入菜单，便于链式使用。
    """
    for action in menu.actions():
        name = action.objectName()
        if name == "link-copy":
            menu.removeAction(action)
            continue
        if name in _STANDARD_MENU_LABELS:
            action.setText(_STANDARD_MENU_LABELS[name])
        else:
            text = action.text()
            if "\t" in text:
                action.setText(text.split("\t")[0])
        if not action.icon().isNull():
            action.setIcon(QIcon())
    return menu


def exec_standard_context_menu(widget: QWidget, event, simplify: bool = False) -> None:
    """标准编辑菜单透明化弹出：替代控件默认 contextMenuEvent。

    菜单项一律取自控件内建 createStandardContextMenu()（不重建、不丢项），
    仅经 make_translucent_popup() 透明化后 exec；simplify=True 时再经
    simplify_standard_menu() 精简——只读控件精简后恰为「复制」「全选」
    两项，可编辑控件保留完整编辑项仅去图标/中文化。
    （QLineEdit 版签名无 pos 参数，isinstance 分支兼容。）
    """
    if isinstance(widget, QLineEdit):
        menu = widget.createStandardContextMenu()
    else:
        menu = widget.createStandardContextMenu(event.pos())
    make_translucent_popup(menu)
    if simplify:
        simplify_standard_menu(menu)
    menu.exec(event.globalPos())
    menu.deleteLater()


class TranslucentMenuLineEdit(QLineEdit):
    """右键标准菜单透明化 + 精简（去图标/中文化）的 QLineEdit（查找框等零散实例直接用）。

    可编辑控件保留撤销/剪切/粘贴等完整编辑项，精简只去图标与快捷键
    显示后缀并中文化，不砍功能项。
    """

    def contextMenuEvent(self, event) -> None:
        exec_standard_context_menu(self, event, simplify=True)


class TranslucentMenuPlainTextEdit(QPlainTextEdit):
    """右键标准菜单透明化 + 精简的 QPlainTextEdit（权限对话框详情等直接用，同 TranslucentMenuLineEdit）。"""

    def contextMenuEvent(self, event) -> None:
        exec_standard_context_menu(self, event, simplify=True)


class SimplifiedMenuTextBrowser(QTextBrowser):
    """只读富文本展示：右键精简后恰为「复制」「全选」两项。

    对话框等零散只读 QTextBrowser 直接用——不覆写则回落 Qt 原生
    标准菜单（英文、带主题图标、含 Copy Link Location 与快捷键
    显示后缀），与全局只读右键形态不一致。Ctrl+C / Ctrl+A 快捷键
    与菜单项正交并存（控件内建按键处理不动）。
    """

    def contextMenuEvent(self, event) -> None:
        exec_standard_context_menu(self, event, simplify=True)


class SimplifiedMenuTextEdit(QTextEdit):
    """只读纯文本展示：右键精简后恰为「复制」「全选」两项（同 SimplifiedMenuTextBrowser）。

    构造即只读——该封装只用于展示场景；可编辑输入框请用
    TranslucentMenuPlainTextEdit（保留完整编辑项）。
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setReadOnly(True)

    def contextMenuEvent(self, event) -> None:
        exec_standard_context_menu(self, event, simplify=True)
