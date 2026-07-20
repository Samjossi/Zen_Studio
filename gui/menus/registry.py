"""Action 注册表：菜单动作全局可寻址（选型方案 A2 核心件）。

键名规范：`菜单.动作`（如 `view.terminal`、`file.open_folder`），
主题等动态组用 `菜单.动作.<载荷>`（如 `appearance.theme.cloud`）。
任何模块经 `MainWindow.menus.get("view.terminal")` 按名取 action
改勾选态/启停，解决"菜单勾选态 ↔ 面板内按钮"跨模块同步
（见 文档/选型记录/2026-0720-0433_菜单栏与设置体系方案选型.md）。
"""
from PySide6.QtGui import QAction

# ----------------------------------------------------------------------
# 跨模块共享的注册表键（注册处与消费处一律引用常量，禁止裸字符串）
# ----------------------------------------------------------------------
#: 视图菜单面板显隐键（view_menu 注册 / MainWindow 显隐单一入口消费）
KEY_VIEW_CHAT = "view.chat"
KEY_VIEW_EXPLORER = "view.explorer"
KEY_VIEW_TERMINAL = "view.terminal"
KEY_VIEW_CHANGES = "view.changes"
#: 噪音过滤开关键（view_menu 注册 / MainWindow 工作区切换后同步勾选态）
KEY_VIEW_NOISE_FILTER = "view.noise_filter"


def theme_action_key(theme: str) -> str:
    """外观主题 action 键（view_menu 按注册表枚举注册 / MainWindow.switch_theme 勾选）。"""
    return f"appearance.theme.{theme}"


class ActionRegistry:
    """`dict[str, QAction]` 薄封装：登记 / 按名取用。"""

    def __init__(self) -> None:
        self._actions: dict[str, QAction] = {}

    def register(self, key: str, action: QAction) -> QAction:
        """登记 action 并原样返回（供构建处链式使用）。"""
        self._actions[key] = action
        return action

    def get(self, key: str) -> QAction | None:
        """按名取 action；未登记返回 None（调用方判空）。"""
        return self._actions.get(key)
