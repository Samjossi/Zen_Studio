"""主题体系：唯一模板 assets/themes/base.qss + 每主题一包色值令牌（THEME_PALETTES）。

模板化（2026-07-20，见 文档/修改记录/2026-0720-1046_主题模板化与去明暗族分类
实施计划.md，C2 全量模板化）：五套 qss 副本合并为唯一模板 base.qss，
$token 占位符由 string.Template 按 THEME_PALETTES[主题名] 渲染（缺键即抛
KeyError，fail-fast）。耦合色（如菜单/下拉同底色）物理同源，不可能漂移；
新增主题 = THEME_PALETTES 加一行字典。

资源包（去共享化，反转 2026-07-20 单套化）：语法高亮/终端 ANSI/查看器行号/
Git 状态色/Git Graph 徽标/聊天面板六套资源包，由四主题共享一份模块级常量
改为每主题自带一份内联副本。Why：单套化当时的前提是深色主题移除后只剩
亮色系、色相趋同；如今主题家族已分化为蓝（cloud/sky）/橙（wheat）/
绿（mint）阵营，继续共享含蓝的包等于强迫非蓝主题接受异色。每主题色系统
一自闭，新增主题六包逐值手写。

字体（2026-07-19，见 文档/修改记录/2026-0719-1152_字体库统一计划.md）：
双字体族全部自带、统一注册自 assets/fonts/，不引用系统字体——
① UI 族：思源黑体 Source Han Sans CN（SIL OFL 1.1，7 档注册其三）；
② 等宽族：更纱黑体 Sarasa Term SC（SIL OFL 1.1，中英 2:1 严格等宽，
供终端与代码查看器）。注册失败仅打印告警（属打包错误），Qt 自然回退兜底。

配置读写（2026-07-19）：通用持久化已抽到 ide/gui/settings.py，
本模块仅保留主题相关的校验包装与样式应用。
"""
from pathlib import Path
from string import Template
from typing import TypedDict

from pygments.token import Token
from PySide6.QtGui import QColor, QFont, QFontDatabase, QPalette
from PySide6.QtWidgets import QApplication

from ide.core.paths import PROJECT_ROOT, THEMES_ASSETS_DIR
from ide.gui.settings import (
    DEFAULT_SETTINGS,
    DEFAULT_THEME,
    KEY_FONT_SIZE,
    KEY_THEME,
    KEY_WINDOW_BORDER,
    AppSettings,
    load_settings as _load_raw_settings,
    update_settings,
)

#: QSS 模板为只读资源（非用户数据），随 assets/ 收编进包——曾误置 config/themes/
#: 致打包态静默丢失全部 QSS 样式（文档/修改记录/2026-0725-1053 计划 T8 修复）
THEME_TEMPLATE_FILE = THEMES_ASSETS_DIR / "base.qss"

# ----------------------------------------------------------------------
# 资源包 schema（六套资源的键集合 TypedDict 定义；色值不再设模块级常量，
# 去共享化后内联于各主题条目——why 见模块 docstring 与 THEME_PALETTES 注释）
# ----------------------------------------------------------------------

#: 终端配色包键（ANSI 16 色 + 默认前后景 + 查找命中叠加色）
class TerminalPack(TypedDict):
    default_fg: str
    default_bg: str
    black: str
    red: str
    green: str
    brown: str
    blue: str
    magenta: str
    cyan: str
    white: str
    brightblack: str
    brightred: str
    brightgreen: str
    brightbrown: str
    brightblue: str
    brightmagenta: str
    brightcyan: str
    brightwhite: str
    find_bg: str
    find_cur: str


#: 查看器控件配色包键（行号/当前行/查找命中）
class ChromePack(TypedDict):
    line_number_fg: str
    line_number_bg: str
    current_line_bg: str
    find_bg: str
    find_cur: str


#: Git 状态色包键（状态枚举见 ide/core/git/status.py）
class GitStatusPack(TypedDict):
    modified: str
    untracked: str
    deleted: str
    ignored: str
    conflict: str


#: Git 提交历史图配色包键（refs 徽标四型 + hash 文本色；图线色复用
#: 各主题终端包色键不另设键——1542 计划 D4/D6。kind 枚举见 ide/core/git/log.py）
class GitGraphPack(TypedDict):
    head: str
    branch: str
    tag: str
    remote: str
    hash_fg: str


#: 聊天面板配色包键
class ChatPack(TypedDict):
    reasoning_fg: str
    usage_hot_fg: str
    tool_fg: str
    tool_error_fg: str
    timeline_read_fg: str
    timeline_write_fg: str
    timeline_tool_fg: str
    user_bubble_bg: str
    tool_output_bg: str
    #: diff 卡 hunk 逐行着色（0645 融合计划 T9，1425 封存款 K7 色值直抄；
    #: 仅新轨卡片视图消费）
    diff_add_fg: str
    diff_del_fg: str
    #: 变更对比视图整行底色与 hunk 分块带四键（1914 计划 D2：
    #: 三家参考实现共识——整行浅红/浅绿底色是「一眼认出 diff」的主信号，
    #: 前景色只是辅助层；hunk 头蓝字蓝带不复用 reasoning_fg 灰，
    #: 与上下文行拉开第二视觉层次）
    diff_add_bg: str
    diff_del_bg: str
    diff_hunk_fg: str
    diff_hunk_bg: str


#: 标题栏配色包键（无边框自绘标题栏 TitleBar 专用，见 ide/gui/title_bar.py；
#: 2026-0730-0043 计划决策点 D1 落 B 方案：A 方案复用 sidebar_bg 与 window_bg
#: 色差 <2% 无区分度，故每主题一包四键。close_hover 为跨主题通用警示红
#: #c75450，入令牌便于后续微调——决策点 D4）
class TitleBarPack(TypedDict):
    bg: str
    text: str
    hover: str
    close_hover: str


#: UI 警示色单一来源（危险操作/高危选项文案；PermissionDialog 危险原因行、
#: 设置中心 auto_all 档说明共用。与 git_status 包 deleted 键语义无关——
#: 后者是文件状态色，色值碰巧相同，不引用本常量）
WARNING_COLOR = "#c0392b"

# ----------------------------------------------------------------------
# 主题调色板注册表（qss 令牌 + 六资源包；注册顺序即设置中心外观页下拉顺序）
# ----------------------------------------------------------------------

#: qss 令牌键集合（校验脚本据此剔除资源包键，双向比对模板占位符）
QSS_TOKEN_KEYS = (
    "accent", "text", "muted_text",
    "window_bg", "window_border", "sidebar_bg", "card_bg",
    "border", "border_hover", "input_bg", "combo_hover_bg",
    "popup_bg", "popup_border", "popup_separator", "menu_border",
    "list_item_hover",
    "button_hover_bg", "button_pressed_bg",
    "button_disabled_text", "button_disabled_bg", "button_disabled_border",
    "separator", "splitter_hover",
    "scrollbar_handle", "scrollbar_handle_hover",
    "tooltip_bg", "tooltip_text",
)

#: 资源包键集合（非 qss 令牌，渲染时不参与替换）
PACK_KEYS = ("label", "syntax", "terminal", "chrome", "git_status", "chat",
             "title_bar", "git_graph")

#: 六包内联约定（去共享化后色值写在各主题条目内，此处记跨包 why）：
#: - 终端 find_* 为 #AARRGGBB 带透明度（AnsiPalette 单独取用，不进 ANSI
#:   颜色名查表）；brown 即 ANSI yellow。
#: - 蓝键归位（theme 资源包去共享化改造计划 §5.3）：syntax 的 Keyword/
#:   Name.Class/Generic.Heading/Generic.Subheading、terminal 的 blue/
#:   brightblue、chrome 的 current_line_bg、git_status 的 modified、chat 的
#:   timeline_read/write_fg 与 diff_hunk_fg/bg——cloud/sky 留蓝族（各自
#:   accent 同族），wheat 归橙族、mint 归绿族（各自 accent 同族深色档）；
#:   Name.Class/Heading/Subheading 取本主题 Keyword 同族较浅档（浅于
#:   Keyword、深于注释灰，维持旧层次关系）。
#: - 青非蓝（决策点 D1）：git_graph.remote 与 chat.timeline_tool_fg 全主题
#:   保留 #168396，终端 cyan/brightcyan 同理不动。
#: - chat.usage_hot_fg/tool_error_fg 同引 WARNING_COLOR 单一来源；
#:   timeline_read_fg 兼作正文文件路径链接色（1836 计划 L2-5 复用不新增键）。
#:
#: 主题注册表：键 = 主题名；label = 菜单显示名；其余为 qss 令牌 + 六资源包。
#: 新增主题 = 加一行字典（六包逐值手写）。
#:
#: 1259 教训：四主题 combo_hover_bg 为 transparent——hover 伪态背景不触发
#: 选中背景接管，透明等价于未设置（见 base.qss 下拉框段注释）；QComboBox 普通态
#: 永不设 background-color（check_theme_tokens.py 反模式断言机器拦截）。
THEME_PALETTES: dict[str, dict] = {
    "cloud": {
        "label": "云白",
        "accent": "#0765d4",
        "window_border": "#0765d4",  # 主窗 1px 轮廓边色 = accent（1600 计划 D3）
        "text": "#1d1d1f",
        "muted_text": "#86868b",
        "window_bg": "#ffffff",
        "sidebar_bg": "#fafafb",
        "card_bg": "#ffffff",
        "border": "#d2d2d7",
        "border_hover": "#c0c0c5",
        "input_bg": "#ffffff",
        "combo_hover_bg": "transparent",
        "popup_bg": "#f5f5f7",
        "popup_border": "rgba(0, 0, 0, 0.15)",
        "menu_border": "rgba(0, 0, 0, 0.15)",
        "popup_separator": "rgba(0, 0, 0, 0.10)",
        "list_item_hover": "rgba(7, 101, 212, 0.07)",
        "button_hover_bg": "rgba(7, 101, 212, 0.07)",
        "button_pressed_bg": "rgba(7, 101, 212, 0.12)",
        "button_disabled_text": "rgba(29, 29, 31, 0.35)",
        "button_disabled_bg": "#f9f9fa",
        "button_disabled_border": "#e8e8ea",
        "separator": "#e5e5e5",
        "splitter_hover": "rgba(0, 0, 0, 0.12)",
        "scrollbar_handle": "rgba(0, 0, 0, 0.22)",
        "scrollbar_handle_hover": "rgba(0, 0, 0, 0.38)",
        "tooltip_bg": "#323236",
        "tooltip_text": "#f5f5f7",
        "title_bar": {
            "bg": "#e8e8ec", "text": "#1d1d1f",
            "hover": "rgba(0, 0, 0, 0.07)", "close_hover": "#c75450",
        },
        "syntax": {
            Token.Keyword: {"color": "#0a5cc4", "bold": True},
            Token.Keyword.Namespace: {"color": "#7A3DB8", "bold": True},
            Token.Name.Builtin: {"color": "#7A3DB8"},
            Token.Name.Function: {"color": "#803080"},
            Token.Name.Class: {"color": "#4a8ad4", "bold": True},
            Token.Name.Decorator: {"color": "#806000"},
            Token.String: {"color": "#007A1C"},
            Token.Number: {"color": "#A0522D"},
            Token.Comment: {"color": "#888888", "italic": True},
            Token.Operator: {"color": "#333333"},
            Token.Generic.Heading: {"color": "#4a8ad4", "bold": True},
            Token.Generic.Subheading: {"color": "#4a8ad4", "bold": True},
            Token.Generic.Strong: {"bold": True},
            Token.Generic.Emph: {"italic": True},
            Token.Error: {"color": "#CC0000"},
        },
        "terminal": {
            "default_fg": "#1a1a1a", "default_bg": "#ffffff",
            "black": "#000000", "red": "#c41a16", "green": "#007a1c",
            "brown": "#a05000", "blue": "#075cb8", "magenta": "#a02c91",
            "cyan": "#168396", "white": "#767676",
            "brightblack": "#555555", "brightred": "#e5484d",
            "brightgreen": "#18a058", "brightbrown": "#c26a00",
            "brightblue": "#2e86e8", "brightmagenta": "#c044ae",
            "brightcyan": "#00a7b5", "brightwhite": "#000000",
            "find_bg": "#37ffdc64", "find_cur": "#6effb400",
        },
        "chrome": {
            "line_number_fg": "#999999", "line_number_bg": "#f5f5f5",
            "current_line_bg": "#eaf1fb",
            "find_bg": "#fff3bf", "find_cur": "#ffd43b",
        },
        "git_status": {
            "modified": "#0765d4",   # accent 同族蓝（去共享化归位）
            "untracked": "#1f8a3d",  # 绿
            "deleted": "#c0392b",    # 红
            "ignored": "#a8a8a8",    # 灰
            "conflict": "#d40000",   # 强红
        },
        "chat": {
            "reasoning_fg": "#888888",
            "usage_hot_fg": WARNING_COLOR,
            "tool_fg": "#6f6f6f",
            "tool_error_fg": WARNING_COLOR,
            "timeline_read_fg": "#0765d4",
            "timeline_write_fg": "#3b8fe8",
            "timeline_tool_fg": "#168396",
            "user_bubble_bg": "#ededf0",
            "tool_output_bg": "#f5f5f7",
            "diff_add_fg": "#3a8437",
            "diff_del_fg": "#ed4831",
            # 整行底色取 GitHub 亮色 diff 同族极浅实底（白底下 alpha 与实底等效，
            # 实底规避 Qt 富文本 alpha 叠底渲染差异）
            "diff_add_bg": "#e6ffec",
            "diff_del_bg": "#ffebe9",
            "diff_hunk_fg": "#0a5cc4",
            "diff_hunk_bg": "#e2eefa",
        },
        "git_graph": {
            "head": "#a02c91",     # 品红（HEAD 特殊位）
            "branch": "#1f8a3d",   # 绿（本地分支）
            "tag": "#a05000",      # 棕橙（标签）
            "remote": "#168396",   # 青（远程分支，D1：青非蓝保留）
            "hash_fg": "#767676",  # 灰（短 hash 弱化）
        },
    },
    "wheat": {
        "label": "暖米",
        "accent": "#c26a1a",
        "window_border": "#c26a1a",  # 主窗 1px 轮廓边色 = accent（1600 计划 D3）
        "text": "#463f33",
        "muted_text": "#9a8f7d",
        "window_bg": "#fffefd",
        "sidebar_bg": "#fefdfb",
        "card_bg": "#ffffff",
        "border": "#ddd2c0",
        "border_hover": "#cbbca4",
        "input_bg": "#ffffff",
        "combo_hover_bg": "transparent",
        "popup_bg": "#f2ede3",
        "popup_border": "rgba(0, 0, 0, 0.15)",
        "menu_border": "rgba(120, 90, 40, 0.25)",
        "popup_separator": "rgba(120, 90, 40, 0.15)",
        "list_item_hover": "rgba(120, 90, 40, 0.08)",
        "button_hover_bg": "rgba(194, 106, 26, 0.08)",
        "button_pressed_bg": "rgba(194, 106, 26, 0.13)",
        "button_disabled_text": "rgba(70, 63, 51, 0.35)",
        "button_disabled_bg": "#fcfaf6",
        "button_disabled_border": "#eae2d3",
        "separator": "#e8e0d2",
        "splitter_hover": "rgba(120, 90, 40, 0.18)",
        "scrollbar_handle": "rgba(0, 0, 0, 0.22)",
        "scrollbar_handle_hover": "rgba(0, 0, 0, 0.38)",
        "tooltip_bg": "#323236",
        "tooltip_text": "#f5f5f7",
        "title_bar": {
            "bg": "#f0e9dc", "text": "#463f33",
            "hover": "rgba(120, 90, 40, 0.10)", "close_hover": "#c75450",
        },
        "syntax": {
            Token.Keyword: {"color": "#9a5410", "bold": True},
            Token.Keyword.Namespace: {"color": "#7A3DB8", "bold": True},
            Token.Name.Builtin: {"color": "#7A3DB8"},
            Token.Name.Function: {"color": "#803080"},
            Token.Name.Class: {"color": "#b5772a", "bold": True},
            Token.Name.Decorator: {"color": "#806000"},
            Token.String: {"color": "#007A1C"},
            Token.Number: {"color": "#A0522D"},
            Token.Comment: {"color": "#888888", "italic": True},
            Token.Operator: {"color": "#333333"},
            Token.Generic.Heading: {"color": "#b5772a", "bold": True},
            Token.Generic.Subheading: {"color": "#b5772a", "bold": True},
            Token.Generic.Strong: {"bold": True},
            Token.Generic.Emph: {"italic": True},
            Token.Error: {"color": "#CC0000"},
        },
        "terminal": {
            "default_fg": "#1a1a1a", "default_bg": "#ffffff",
            "black": "#000000", "red": "#c41a16", "green": "#007a1c",
            # brown 槽明度下调（D2 拍板唯一例外）：归位后 blue 槽进橙族
            # #8c5a10，与原 brown 色相仅差 5.8° 撞色，靠明度差 44 级分辨
            "brown": "#5f3200", "blue": "#8c5a10", "magenta": "#a02c91",
            "cyan": "#168396", "white": "#767676",
            "brightblack": "#555555", "brightred": "#e5484d",
            "brightgreen": "#18a058", "brightbrown": "#8f4c00",
            "brightblue": "#c07818", "brightmagenta": "#c044ae",
            "brightcyan": "#00a7b5", "brightwhite": "#000000",
            "find_bg": "#37ffdc64", "find_cur": "#6effb400",
        },
        "chrome": {
            "line_number_fg": "#999999", "line_number_bg": "#f5f5f5",
            "current_line_bg": "#f8f0e4",
            "find_bg": "#fff3bf", "find_cur": "#ffd43b",
        },
        "git_status": {
            "modified": "#b85e14",   # accent 同族橙（去共享化归位，🟡 走查重点）
            "untracked": "#1f8a3d",  # 绿
            "deleted": "#c0392b",    # 红
            "ignored": "#a8a8a8",    # 灰
            "conflict": "#d40000",   # 强红
        },
        "chat": {
            "reasoning_fg": "#888888",
            "usage_hot_fg": WARNING_COLOR,
            "tool_fg": "#6f6f6f",
            "tool_error_fg": WARNING_COLOR,
            "timeline_read_fg": "#b85e14",
            "timeline_write_fg": "#d08a3e",
            "timeline_tool_fg": "#168396",
            "user_bubble_bg": "#ededf0",
            "tool_output_bg": "#f5f5f7",
            "diff_add_fg": "#3a8437",
            "diff_del_fg": "#ed4831",
            "diff_add_bg": "#e6ffec",
            "diff_del_bg": "#ffebe9",
            "diff_hunk_fg": "#9a5410",
            "diff_hunk_bg": "#f3e8d8",
        },
        "git_graph": {
            "head": "#a02c91",     # 品红（HEAD 特殊位）
            "branch": "#1f8a3d",   # 绿（本地分支）
            "tag": "#a05000",      # 棕橙（标签）
            "remote": "#168396",   # 青（远程分支，D1：青非蓝保留）
            "hash_fg": "#767676",  # 灰（短 hash 弱化）
        },
    },
    "sky": {
        "label": "晴空",
        "accent": "#0284c7",
        "window_border": "#0284c7",  # 主窗 1px 轮廓边色 = accent（1600 计划 D3）
        "text": "#1e293b",
        "muted_text": "#7d8da0",
        "window_bg": "#fdfeff",
        "sidebar_bg": "#fbfdfe",
        "card_bg": "#ffffff",
        "border": "#c6d9e6",
        "border_hover": "#adc9dc",
        "input_bg": "#ffffff",
        "combo_hover_bg": "transparent",
        "popup_bg": "#edf3f8",
        "popup_border": "rgba(0, 0, 0, 0.15)",
        "menu_border": "rgba(2, 132, 199, 0.25)",
        "popup_separator": "rgba(2, 132, 199, 0.15)",
        "list_item_hover": "rgba(2, 132, 199, 0.08)",
        "button_hover_bg": "rgba(2, 132, 199, 0.08)",
        "button_pressed_bg": "rgba(2, 132, 199, 0.13)",
        "button_disabled_text": "rgba(30, 41, 59, 0.35)",
        "button_disabled_bg": "#fafcfd",
        "button_disabled_border": "#d9e6ee",
        "separator": "#dde7ee",
        "splitter_hover": "rgba(2, 132, 199, 0.18)",
        "scrollbar_handle": "rgba(0, 0, 0, 0.22)",
        "scrollbar_handle_hover": "rgba(0, 0, 0, 0.38)",
        "tooltip_bg": "#323236",
        "tooltip_text": "#f5f5f7",
        "title_bar": {
            "bg": "#dfeaf3", "text": "#1e293b",
            "hover": "rgba(2, 132, 199, 0.10)", "close_hover": "#c75450",
        },
        "syntax": {
            Token.Keyword: {"color": "#0369a1", "bold": True},
            Token.Keyword.Namespace: {"color": "#7A3DB8", "bold": True},
            Token.Name.Builtin: {"color": "#7A3DB8"},
            Token.Name.Function: {"color": "#803080"},
            Token.Name.Class: {"color": "#3f96c8", "bold": True},
            Token.Name.Decorator: {"color": "#806000"},
            Token.String: {"color": "#007A1C"},
            Token.Number: {"color": "#A0522D"},
            Token.Comment: {"color": "#888888", "italic": True},
            Token.Operator: {"color": "#333333"},
            Token.Generic.Heading: {"color": "#3f96c8", "bold": True},
            Token.Generic.Subheading: {"color": "#3f96c8", "bold": True},
            Token.Generic.Strong: {"bold": True},
            Token.Generic.Emph: {"italic": True},
            Token.Error: {"color": "#CC0000"},
        },
        "terminal": {
            "default_fg": "#1a1a1a", "default_bg": "#ffffff",
            "black": "#000000", "red": "#c41a16", "green": "#007a1c",
            "brown": "#a05000", "blue": "#0369a1", "magenta": "#a02c91",
            "cyan": "#168396", "white": "#767676",
            "brightblack": "#555555", "brightred": "#e5484d",
            "brightgreen": "#18a058", "brightbrown": "#c26a00",
            "brightblue": "#0ea5e9", "brightmagenta": "#c044ae",
            "brightcyan": "#00a7b5", "brightwhite": "#000000",
            "find_bg": "#37ffdc64", "find_cur": "#6effb400",
        },
        "chrome": {
            "line_number_fg": "#999999", "line_number_bg": "#f5f5f5",
            "current_line_bg": "#e6f3fa",
            "find_bg": "#fff3bf", "find_cur": "#ffd43b",
        },
        "git_status": {
            "modified": "#0284c7",   # accent 同族蓝（去共享化归位）
            "untracked": "#1f8a3d",  # 绿
            "deleted": "#c0392b",    # 红
            "ignored": "#a8a8a8",    # 灰
            "conflict": "#d40000",   # 强红
        },
        "chat": {
            "reasoning_fg": "#888888",
            "usage_hot_fg": WARNING_COLOR,
            "tool_fg": "#6f6f6f",
            "tool_error_fg": WARNING_COLOR,
            "timeline_read_fg": "#0284c7",
            "timeline_write_fg": "#38bdf8",
            "timeline_tool_fg": "#168396",
            "user_bubble_bg": "#ededf0",
            "tool_output_bg": "#f5f5f7",
            "diff_add_fg": "#3a8437",
            "diff_del_fg": "#ed4831",
            "diff_add_bg": "#e6ffec",
            "diff_del_bg": "#ffebe9",
            "diff_hunk_fg": "#0369a1",
            "diff_hunk_bg": "#ddf2fc",
        },
        "git_graph": {
            "head": "#a02c91",     # 品红（HEAD 特殊位）
            "branch": "#1f8a3d",   # 绿（本地分支）
            "tag": "#a05000",      # 棕橙（标签）
            "remote": "#168396",   # 青（远程分支，D1：青非蓝保留）
            "hash_fg": "#767676",  # 灰（短 hash 弱化）
        },
    },
    "mint": {
        "label": "薄荷",
        "accent": "#15803d",
        "window_border": "#15803d",  # 主窗 1px 轮廓边色 = accent（1600 计划 D3）
        "text": "#20301f",
        "muted_text": "#7f9a84",
        "window_bg": "#fdfffd",
        "sidebar_bg": "#fbfefb",
        "card_bg": "#ffffff",
        "border": "#c8dcc9",
        "border_hover": "#b2ceb4",
        "input_bg": "#ffffff",
        "combo_hover_bg": "transparent",
        "popup_bg": "#edf5ed",
        "popup_border": "rgba(0, 0, 0, 0.15)",
        "menu_border": "rgba(21, 128, 61, 0.25)",
        "popup_separator": "rgba(21, 128, 61, 0.15)",
        "list_item_hover": "rgba(21, 128, 61, 0.08)",
        "button_hover_bg": "rgba(21, 128, 61, 0.08)",
        "button_pressed_bg": "rgba(21, 128, 61, 0.13)",
        "button_disabled_text": "rgba(32, 48, 31, 0.35)",
        "button_disabled_bg": "#fafdfa",
        "button_disabled_border": "#dce9dd",
        "separator": "#dfe9df",
        "splitter_hover": "rgba(21, 128, 61, 0.18)",
        "scrollbar_handle": "rgba(0, 0, 0, 0.22)",
        "scrollbar_handle_hover": "rgba(0, 0, 0, 0.38)",
        "tooltip_bg": "#323236",
        "tooltip_text": "#f5f5f7",
        "title_bar": {
            "bg": "#dfeddf", "text": "#20301f",
            "hover": "rgba(21, 128, 61, 0.10)", "close_hover": "#c75450",
        },
        "syntax": {
            Token.Keyword: {"color": "#0f6e3f", "bold": True},
            Token.Keyword.Namespace: {"color": "#7A3DB8", "bold": True},
            Token.Name.Builtin: {"color": "#7A3DB8"},
            Token.Name.Function: {"color": "#803080"},
            Token.Name.Class: {"color": "#3d9060", "bold": True},
            Token.Name.Decorator: {"color": "#806000"},
            Token.String: {"color": "#007A1C"},
            Token.Number: {"color": "#A0522D"},
            Token.Comment: {"color": "#888888", "italic": True},
            Token.Operator: {"color": "#333333"},
            Token.Generic.Heading: {"color": "#3d9060", "bold": True},
            Token.Generic.Subheading: {"color": "#3d9060", "bold": True},
            Token.Generic.Strong: {"bold": True},
            Token.Generic.Emph: {"italic": True},
            Token.Error: {"color": "#CC0000"},
        },
        "terminal": {
            "default_fg": "#1a1a1a", "default_bg": "#ffffff",
            "black": "#000000", "red": "#c41a16", "green": "#007a1c",
            # blue 槽同族内微调（🟡 走查定稿）：原 §5.3 初值 #0f6e4f/#189a63
            # 与 green/brightgreen 色相贴脸（距 12~55），hue 上移青侧——
            # blue 171° 置于 green 134° 与 cyan 191° 之间，brightblue 165° 同理
            "brown": "#a05000", "blue": "#0e5f53", "magenta": "#a02c91",
            "cyan": "#168396", "white": "#767676",
            "brightblack": "#555555", "brightred": "#e5484d",
            "brightgreen": "#18a058", "brightbrown": "#c26a00",
            "brightblue": "#1fad8a", "brightmagenta": "#c044ae",
            "brightcyan": "#00a7b5", "brightwhite": "#000000",
            "find_bg": "#37ffdc64", "find_cur": "#6effb400",
        },
        "chrome": {
            "line_number_fg": "#999999", "line_number_bg": "#f5f5f5",
            "current_line_bg": "#e9f4ec",
            "find_bg": "#fff3bf", "find_cur": "#ffd43b",
        },
        "git_status": {
            "modified": "#0f6e3f",   # accent 同族深绿（去共享化归位，🟡 走查重点）
            "untracked": "#1f8a3d",  # 绿（中绿档，与 modified 深绿靠明度区分）
            "deleted": "#c0392b",    # 红
            "ignored": "#a8a8a8",    # 灰
            "conflict": "#d40000",   # 强红
        },
        "chat": {
            "reasoning_fg": "#888888",
            "usage_hot_fg": WARNING_COLOR,
            "tool_fg": "#6f6f6f",
            "tool_error_fg": WARNING_COLOR,
            "timeline_read_fg": "#15803d",
            "timeline_write_fg": "#3fa96b",
            "timeline_tool_fg": "#168396",
            "user_bubble_bg": "#ededf0",
            "tool_output_bg": "#f5f5f7",
            "diff_add_fg": "#3a8437",
            "diff_del_fg": "#ed4831",
            "diff_add_bg": "#e6ffec",
            "diff_del_bg": "#ffebe9",
            "diff_hunk_fg": "#0f6e3f",
            "diff_hunk_bg": "#dff0e4",
        },
        "git_graph": {
            "head": "#a02c91",     # 品红（HEAD 特殊位）
            "branch": "#1f8a3d",   # 绿（本地分支）
            "tag": "#a05000",      # 棕橙（标签）
            "remote": "#168396",   # 青（远程分支，D1：青非蓝保留）
            "hash_fg": "#767676",  # 灰（短 hash 弱化）
        },
    },
}

#: 未知主题名/资源包键的回退主题（防御性兜底；正常路径 is_valid 已拦截）
FALLBACK_THEME = DEFAULT_THEME


def get_theme_palette(theme: str) -> dict:
    """主题名 → 调色板（含 qss 令牌与六资源包）；未注册回退 FALLBACK_THEME。"""
    return THEME_PALETTES.get(theme, THEME_PALETTES[FALLBACK_THEME])


def git_status_color(theme: str, status: str) -> str | None:
    """主题名 + Git 状态 → 十六进制色值；未知状态返回 None（不着色）。"""
    return get_theme_palette(theme)["git_status"].get(status)


def git_graph_color(theme: str, kind: str) -> str | None:
    """主题名 + refs 徽标类型/hash_fg → 十六进制色值；未知键返回 None。"""
    return get_theme_palette(theme)["git_graph"].get(kind)


#: 自带 UI 字体目录（assets/fonts/思源黑体/，全量 7 档，运行时注册其中三档）
BUNDLED_FONTS_DIR = PROJECT_ROOT / "assets" / "fonts" / "思源黑体"
#: 注册三档即可支撑正文/标题/强调层级；其余字重留目录备用
BUNDLED_FONT_FILES = (
    "SourceHanSansCN-Regular.otf",
    "SourceHanSansCN-Medium.otf",
    "SourceHanSansCN-Bold.otf",
)
BUNDLED_FAMILY = "Source Han Sans CN"

#: 自带等宽字体目录（assets/fonts/更纱黑体/，终端与代码查看器专用）
MONO_FONTS_DIR = PROJECT_ROOT / "assets" / "fonts" / "更纱黑体"
MONO_FONT_FILES = (
    "SarasaTermSC-Regular.ttf",
    "SarasaTermSC-Bold.ttf",
)
MONO_FAMILY = "Sarasa Term SC"

#: 自带彩色 emoji 字体目录（assets/fonts/Noto彩色Emoji/，0807 计划 D2-C 拍板：
#: 思源黑体不覆盖 emoji——注册后 Qt 回退链自动拼接，🧠📎🖼 等全量彩色渲染、
#: 跨机器一致，不再依赖系统字体施舍；CBDT 位图字体，QRawFont.supportsCharacter
#: 对其误报 False（Qt 已知怪癖），实际光栅化正常——覆盖探测勿用于 emoji）
EMOJI_FONTS_DIR = PROJECT_ROOT / "assets" / "fonts" / "Noto彩色Emoji"
EMOJI_FONT_FILES = (
    "NotoColorEmoji.ttf",
)
EMOJI_FAMILY = "Noto Color Emoji"

#: 注册幂等标志：字号调整链（main_window._apply_font_size）会重入 apply_theme，
#: 字体只需注册一次
_are_fonts_registered = False


# ----------------------------------------------------------------------
# 主题注册表查询与渲染
# ----------------------------------------------------------------------
def list_available_themes() -> list[str]:
    """已注册主题名（注册表顺序，供菜单枚举）。"""
    return list(THEME_PALETTES)


def is_valid(theme: str) -> bool:
    """主题名已注册。"""
    return theme in THEME_PALETTES


def get_label(theme: str) -> str:
    """主题名 → 菜单显示名，未注册回退主题名本身。"""
    return THEME_PALETTES.get(theme, {}).get("label", theme)


def render_theme(theme: str) -> str:
    """按调色板渲染主题样式表（string.Template 全量替换，缺键抛 KeyError）。"""
    template = Template(THEME_TEMPLATE_FILE.read_text(encoding="utf-8"))
    return template.substitute(get_theme_palette(theme))


def window_border_override(enabled: bool) -> str:
    """主窗色边关闭态覆盖片段：同选择器后写胜出（QSS 层叠规则），空串 = 不追加。

    为什么用覆盖而非令牌换 transparent：border: none 让出 1px inset，
    语义「无描边」与「透明描边」分明。render_theme 保持纯函数，开关只作
    用于本片段（1600 计划 D4）。纯函数，单测直断。
    """
    return "" if enabled else "#WindowShell { border: none; }"


# ----------------------------------------------------------------------
# 持久化（通用读写见 ide/gui/settings.py；此处补主题有效性校验）
# ----------------------------------------------------------------------
def load_settings() -> AppSettings:
    """读取持久化配置，缺失字段回退默认值；无效主题名静默回退默认主题。"""
    settings = _load_raw_settings()
    if not is_valid(settings[KEY_THEME]):
        settings[KEY_THEME] = DEFAULT_SETTINGS[KEY_THEME]
    return settings


def save_theme(theme: str) -> None:
    """回写用户所选主题，实现持久化。"""
    update_settings({KEY_THEME: theme})


def _register_dir(directory: Path, files: tuple[str, ...]) -> bool:
    """注册一个字体目录下的指定文件；任一成功即视为该族可用。"""
    any_font_registered = False
    for name in files:
        path = directory / name
        if path.is_file():
            any_font_registered = (QFontDatabase.addApplicationFont(str(path)) >= 0) or any_font_registered
    return any_font_registered


def register_bundled_fonts() -> None:
    """注册库内三字体族（幂等）；缺失仅告警（属打包错误），Qt 自然回退兜底。"""
    global _are_fonts_registered
    if _are_fonts_registered:
        return
    _are_fonts_registered = True
    if not _register_dir(BUNDLED_FONTS_DIR, BUNDLED_FONT_FILES):
        print(f"[theme] 警告：UI 字体注册失败（{BUNDLED_FONTS_DIR}），Qt 将回退默认字体")
    if not _register_dir(MONO_FONTS_DIR, MONO_FONT_FILES):
        print(f"[theme] 警告：等宽字体注册失败（{MONO_FONTS_DIR}），等宽场景将回退 monospace")
    if not _register_dir(EMOJI_FONTS_DIR, EMOJI_FONT_FILES):
        print(f"[theme] 警告：emoji 字体注册失败（{EMOJI_FONTS_DIR}），emoji 将回退系统字体")


def get_mono_family() -> str:
    """等宽字体族名：Sarasa Term SC 可用则返回之，否则回退 Qt 泛型 monospace。"""
    if MONO_FAMILY in QFontDatabase.families():
        return MONO_FAMILY
    return "monospace"


def _apply_palette(app: QApplication, palette: dict) -> None:
    """按当前主题令牌刷新 app 级 QPalette（QSS 够不到的角色兜底）。

    根因：QSS 的 color 不沿控件树继承（1424 计划探针实证），未显式命中的
    文本控件（QLabel/QMenuBar/文本编辑类等）落到 palette Text 角色恒黑。
    QSS 已显式覆盖的控件以 QSS 为准（Qt 优先级天然如此），本函数只填兜底。
    （2026-0820-1642 计划 §4.1；不设 AlternateBase/BrightText/3D 斜面角色——
    本应用无消费方或 base.qss 已全量接管，理由见该计划角色映射表）
    """
    pal = app.palette()  # 基于现行调色板改，未触及角色保留平台默认
    pal.setColor(QPalette.ColorRole.Window, QColor(palette["window_bg"]))
    pal.setColor(QPalette.ColorRole.WindowText, QColor(palette["text"]))
    pal.setColor(QPalette.ColorRole.Base, QColor(palette["input_bg"]))
    pal.setColor(QPalette.ColorRole.Text, QColor(palette["text"]))
    pal.setColor(QPalette.ColorRole.Button, QColor(palette["card_bg"]))
    pal.setColor(QPalette.ColorRole.ButtonText, QColor(palette["text"]))
    pal.setColor(QPalette.ColorRole.ToolTipBase, QColor(palette["tooltip_bg"]))
    pal.setColor(QPalette.ColorRole.ToolTipText, QColor(palette["tooltip_text"]))
    pal.setColor(QPalette.ColorRole.Highlight, QColor(palette["accent"]))
    # 白字与 base.qss selection-color: #ffffff 既有硬编码一致；
    # ide/gui/selection_band.py 等选区带派生自 QPalette.Highlight，天然同源
    pal.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    pal.setColor(QPalette.ColorRole.Link, QColor(palette["accent"]))
    pal.setColor(QPalette.ColorRole.PlaceholderText, QColor(palette["muted_text"]))
    disabled = QColor(palette["button_disabled_text"])
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, disabled)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, disabled)
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, disabled)
    app.setPalette(pal)


def apply_theme(app: QApplication) -> None:
    """按当前配置应用主题样式表、QPalette 兜底色与全局字体。"""
    settings = load_settings()

    try:
        qss = render_theme(settings[KEY_THEME])
    except OSError:
        # 模板文件缺失（打包错误）时静默回退到 Qt 默认样式
        qss = ""
    app.setStyleSheet(qss + window_border_override(settings[KEY_WINDOW_BORDER]))

    _apply_palette(app, get_theme_palette(settings[KEY_THEME]))
    register_bundled_fonts()
    app.setFont(QFont(BUNDLED_FAMILY, settings[KEY_FONT_SIZE]))
