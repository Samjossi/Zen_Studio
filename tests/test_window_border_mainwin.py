"""主窗 1px 主题色描边四边完整性像素级回归（1600 计划 §12.3）。

背景：首轮实施漏掉「自定义标题栏覆盖顶边」缺陷——Qt 子控件绘制在父控件
QSS 边框之上，零边距时不透明底的 TitleBar 正好盖住 #WindowShell 顶边
1px 描边。首轮走查探针用透明底 QLabel 充当窗体内容，结构上不存在
「不透明子控件压边框带」的情形，四边断言全过但真机顶边缺失——**探针复刻
保真度决定断言有效性**，本探针按 §12.3 复刻真实主窗结构：壳顶放一个不
透明底栏（内联 QSS 底色 + WA_StyledBackground，同 TitleBar 覆盖形态），
壳底放一个不透明底栏（同 QStatusBar 迁壳形态），再断言开/关两态截帧的
四边中点像素。

断言口径（姊妹项目同款，避开角部抗锯齿区）：
- 开态：四边中点 == 主题 accent，壳面内部 == window_bg；
- 关态（window_border_override 覆盖）：四边中点 == window_bg，无残留。

回归绑定生产值：壳外层内边距取 MainWindow.SHELL_OUTER_MARGIN_PX
（§12.2 修复的常量本体）；另设反向用例（margin=0）实证本探针能检出
该缺陷——探针结构件（不透明顶栏）的必要性证明，防未来简化探针时
静默失效。
"""
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QMenuBar, QStatusBar, QVBoxLayout, QWidget

from ide.gui.main_window import MainWindow
from ide.gui.theme import (
    get_theme_palette,
    list_available_themes,
    render_theme,
    window_border_override,
)

_SHELL_W, _SHELL_H = 400, 300


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app
    app.setStyleSheet("")


def _build_shell(margin_px: int, palette: dict) -> QWidget:
    """复刻 main_window._build_layout 的可视壳结构段（含不透明顶/底栏）。"""
    shell = QWidget()
    shell.setObjectName("WindowShell")
    shell.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    shell.resize(_SHELL_W, _SHELL_H)
    outer = QVBoxLayout(shell)
    outer.setContentsMargins(margin_px, margin_px, margin_px, margin_px)
    outer.setSpacing(0)
    # 不透明顶栏：同 TitleBar 形态（内联 QSS 不透明底 + WA_StyledBackground）——
    # 压边框带缺陷的结构元凶，探针必要结构件
    top = QWidget(shell)
    top.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    top.setStyleSheet(f"background: {palette['title_bar']['bg']};")
    top.setFixedHeight(36)
    outer.addWidget(top)
    # 菜单栏（透明底，不占边框带断言）
    outer.addWidget(QMenuBar(shell))
    # 内容面：壳底色
    body = QWidget(shell)
    body.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    body.setStyleSheet(f"background: {palette['window_bg']};")
    outer.addWidget(body)
    # 不透明底栏：同 QStatusBar 迁壳形态（对称覆盖底边框带）
    bottom = QStatusBar(shell)
    bottom.setStyleSheet(f"background: {palette['title_bar']['bg']};")
    outer.addWidget(bottom)
    return shell


def _grab_shell(shell: QWidget, theme: str, border_enabled: bool) -> QImage:
    app = QApplication.instance()
    app.setStyleSheet(render_theme(theme)
                      + window_border_override(border_enabled))
    shell.show()
    QApplication.processEvents()
    return shell.grab().toImage()


def _px(img: QImage, x: int, y: int) -> QColor:
    return QColor(img.pixel(x, y))


def _edge_midpoints(img: QImage) -> dict[str, QColor]:
    w, h = img.width(), img.height()
    return {
        "top": _px(img, w // 2, 0),
        "bottom": _px(img, w // 2, h - 1),
        "left": _px(img, 0, h // 2),
        "right": _px(img, w - 1, h // 2),
        "inner": _px(img, w // 2, h // 2),
    }


@pytest.mark.parametrize("theme", list_available_themes())
@pytest.mark.parametrize("border_enabled", [True, False])
def test_shell_border_four_edges(theme, border_enabled, qapp):
    """开态四边中点 == accent、内部 == 窗底；关态四边 == 窗底无残留。"""
    palette = get_theme_palette(theme)
    shell = _build_shell(MainWindow.SHELL_OUTER_MARGIN_PX, palette)
    img = _grab_shell(shell, theme, border_enabled)
    edges = _edge_midpoints(img)
    expected_edge = QColor(palette["accent"] if border_enabled
                           else palette["window_bg"])
    expected_inner = QColor(palette["window_bg"])
    for side in ("top", "bottom", "left", "right"):
        assert edges[side] == expected_edge, \
            f"{theme} 开={border_enabled} {side} 边中点 " \
            f"{edges[side].name()} != {expected_edge.name()}"
    assert edges["inner"] == expected_inner, \
        f"{theme} 开={border_enabled} 内部面片 {edges['inner'].name()} " \
        f"!= {expected_inner.name()}"
    shell.hide()


def test_probe_detects_zero_margin_defect(qapp):
    """反向实证：margin=0 时顶边被不透明顶栏盖掉（缺陷复现），证明本探针
    的不透明顶栏是检出 §12 缺陷的必要结构件，防探针被简化后静默失效。"""
    palette = get_theme_palette("cloud")
    shell = _build_shell(0, palette)
    img = _grab_shell(shell, "cloud", True)
    top = _px(img, img.width() // 2, 0)
    assert top != QColor(palette["accent"]), \
        "margin=0 时顶边中点竟仍为 accent——不透明顶栏未盖边，" \
        "探针结构不再复刻真实主窗，断言有效性存疑"
    shell.hide()
