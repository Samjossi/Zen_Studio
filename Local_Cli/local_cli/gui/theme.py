"""主题层：云白主题的语义令牌与 QSS 生成。

视觉唯一基准是《Zen_Studio_GUI设计风格规范_跨软件复刻指南》。颜色一律
经语义令牌注入 QSS 模板（验收清单 #10：零硬编码分散），QSS 字符串里
不出现任何裸色值。
"""
from __future__ import annotations


class CloudWhiteToken:
    """默认主题「云白」语义令牌（风格规范 §2.2/§2.6，命名按 §2.1 语义角色）。"""

    WINDOW_BACKGROUND = "#ffffff"        # 窗口底色
    ACCENT = "#0765d4"                   # 强调色（单源辐射，铁律 7）
    TEXT_PRIMARY = "#1d1d1f"             # 正文色
    TEXT_SECONDARY = "#86868b"           # 次级文字色
    BORDER = "#d2d2d7"                   # 描边色
    BORDER_HOVER = "#c0c0c5"             # 描边加深色（hover 感知 ~7%）
    OVERLAY_BACKGROUND = "#f5f5f7"       # 浮层底色（下拉弹出列表）
    OVERLAY_BORDER = "rgba(0, 0, 0, 0.15)"       # 浮层描边（半透明保层级）
    BUTTON_HOVER_TINT = "rgba(7, 101, 212, 0.07)"    # 按钮/列表悬停微染（accent × 7%）
    BUTTON_PRESSED_TINT = "rgba(7, 101, 212, 0.12)"  # 按钮按压染（accent × 12%）
    DISABLED_TEXT = "rgba(29, 29, 31, 0.35)"         # 按钮禁用文字
    DISABLED_BACKGROUND = "#f9f9fa"                  # 按钮禁用底
    DISABLED_BORDER = "#e8e8ea"                      # 按钮禁用描边
    SCROLLBAR_HANDLE = "rgba(0, 0, 0, 0.22)"         # 滚动条滑块（常态近隐形）
    SCROLLBAR_HANDLE_HOVER = "rgba(0, 0, 0, 0.38)"   # 滚动条滑块 hover
    TOOLTIP_BACKGROUND = "#323236"       # 工具提示背景（深灰反色）
    TOOLTIP_TEXT = "#f5f5f7"             # 工具提示文字
    THOUGHT_TEXT = "#888888"             # 思维链灰（§8.5）


class RadiusToken:
    """圆角三级递减（风格规范 §4：容器 → 内容 → 项，不可颠倒自创）。"""

    CONTAINER = 8   # 容器层：面板卡片、浮层
    CONTENT = 6     # 内容层：输入框、按钮、下拉框、工具提示
    ITEM = 3        # 项层：列表选中块、菜单项


#: QSS 模板：全部色值经令牌格式化注入，模板内零裸色值
_QSS_TEMPLATE = """
/* 窗口与面板：描边分层而非色块分区（铁律 1） */
QMainWindow {{
    background: {window_bg};
}}
QWidget {{
    color: {text_primary};
}}
QFrame#card {{
    background: {window_bg};
    border: 1px solid {border};
    border-radius: {radius_container}px;
}}
QLabel {{
    background: transparent;
}}
QLabel#panelTitle {{
    padding: 2px 8px;
}}
QLabel#hintText {{
    color: {text_secondary};
    padding: 2px 8px;
}}
QLabel#statusValue {{
    color: {text_primary};
}}

/* 按钮：白底 + 1px 描边 + 内容层圆角；hover 极淡强调色染（§6.2） */
QPushButton {{
    background: {window_bg};
    border: 1px solid {border};
    border-radius: {radius_content}px;
    padding: 3px 11px;
    color: {text_primary};
}}
QPushButton:hover {{
    background: {button_hover};
    border-color: {border_hover};
}}
QPushButton:pressed {{
    background: {button_pressed};
}}
QPushButton:disabled {{
    color: {disabled_text};
    background: {disabled_bg};
    border-color: {disabled_border};
}}

/* 输入框：focus 只换描边色，不变宽度不加外发光（铁律 2，§6.1） */
QLineEdit {{
    background: {window_bg};
    border: 1px solid {border};
    border-radius: {radius_content}px;
    padding: 3px 7px;
    selection-background-color: {accent};
}}
QLineEdit:focus {{
    border-color: {accent};
}}

/* 下拉框：本体透明底（§6.3 严禁设背景色，否则弹出列表选中行不可见） */
QComboBox {{
    background: transparent;
    border: 1px solid {border};
    border-radius: {radius_content}px;
    padding: 2px 7px;
}}
QComboBox:hover {{
    border-color: {border_hover};
}}
QComboBox::drop-down {{
    width: 20px;
    border: none;
}}
QComboBox QAbstractItemView {{
    background: {overlay_bg};
    color: {text_primary};
    border: 1px solid {overlay_border};
    border-radius: {radius_container}px;
    padding: 4px;
    selection-background-color: {accent};
    selection-color: {overlay_bg};
    outline: none;
}}

/* 日志/回复文本区：嵌在卡片内，透明无自身描边，卡片统一画框（§6.6） */
QPlainTextEdit {{
    background: transparent;
    border: none;
    selection-background-color: {accent};
}}

/* 状态栏：透明融入窗口底，不是独立色条（铁律 5，§6.10） */
QStatusBar {{
    background: transparent;
}}

/* 工具提示：深灰底白字反色、无边框（铁律 6，§6.11） */
QToolTip {{
    background: {tooltip_bg};
    color: {tooltip_text};
    border: none;
    border-radius: {radius_content}px;
    padding: 4px 8px;
}}

/* 滚动条：10px 轨道透明、无箭头、滑块半透明圆角（铁律 4，§6.9） */
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {scrollbar_handle};
    border-radius: 5px;
    min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{
    background: {scrollbar_handle_hover};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
    background: transparent;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {scrollbar_handle};
    border-radius: 5px;
    min-width: 30px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {scrollbar_handle_hover};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{
    background: transparent;
}}
"""


def build_qss() -> str:
    """生成云白主题的完整 QSS（令牌单源注入，换主题只换令牌类）。"""
    return _QSS_TEMPLATE.format(
        window_bg=CloudWhiteToken.WINDOW_BACKGROUND,
        accent=CloudWhiteToken.ACCENT,
        text_primary=CloudWhiteToken.TEXT_PRIMARY,
        text_secondary=CloudWhiteToken.TEXT_SECONDARY,
        border=CloudWhiteToken.BORDER,
        border_hover=CloudWhiteToken.BORDER_HOVER,
        overlay_bg=CloudWhiteToken.OVERLAY_BACKGROUND,
        overlay_border=CloudWhiteToken.OVERLAY_BORDER,
        button_hover=CloudWhiteToken.BUTTON_HOVER_TINT,
        button_pressed=CloudWhiteToken.BUTTON_PRESSED_TINT,
        disabled_text=CloudWhiteToken.DISABLED_TEXT,
        disabled_bg=CloudWhiteToken.DISABLED_BACKGROUND,
        disabled_border=CloudWhiteToken.DISABLED_BORDER,
        scrollbar_handle=CloudWhiteToken.SCROLLBAR_HANDLE,
        scrollbar_handle_hover=CloudWhiteToken.SCROLLBAR_HANDLE_HOVER,
        tooltip_bg=CloudWhiteToken.TOOLTIP_BACKGROUND,
        tooltip_text=CloudWhiteToken.TOOLTIP_TEXT,
        radius_container=RadiusToken.CONTAINER,
        radius_content=RadiusToken.CONTENT,
        radius_item=RadiusToken.ITEM,
    )
