"""菜单栏装配器：按序调用各菜单构建模块，action 统一登记注册表。

新增顶层菜单 = 新建 `<name>_menu.py`（实现 `build(menubar, ctx, actions)`，
ctx 为 MainWindow 本身）+ 在 MODULES 元组中登记位置；
不触碰任何现有菜单代码（文档/选型记录/2026-0720-0433 选型方案 A2）。
"""
from PySide6.QtWidgets import QMainWindow, QMenu, QMenuBar

from gui.menus import (
    edit_menu,
    file_menu,
    help_menu,
    settings_menu,
    terminal_menu,
    view_menu,
)
from gui.menus.registry import ActionRegistry
from gui.popups import make_translucent_popup

#: 构建顺序即菜单栏顺序：文件/编辑/视图/终端 → 设置 → 帮助
MODULES = (file_menu, edit_menu, view_menu, terminal_menu, settings_menu, help_menu)


class MenuBar:
    """装配 MainWindow 菜单栏；持有全局 action 注册表。

    自定义标题栏（2026-07-30，文档/修改记录/2026-0730-0007 计划 T8）：菜单栏改由
    MainWindow 显式创建并经构造参数传入（reparent 进中央容器，居于 TitleBar 之下），
    不再占用 QMainWindow 菜单槽——`window.menuBar()` 惰性建栏会顶掉槽位冲突方。
    """

    def __init__(self, window: QMainWindow, menu_bar: QMenuBar) -> None:
        self._window = window
        self._menu_bar = menu_bar
        self.actions = ActionRegistry()

    def setup(self) -> None:
        """按 MODULES 顺序构建全部顶层菜单；浮层统一透明化。"""
        menubar = self._menu_bar
        for module in MODULES:
            module.build(menubar, self._window, self.actions)
        # 全部菜单（含子菜单）浮层透明化：qss 圆角外的矩形窗口底不再外露
        for menu in menubar.findChildren(QMenu):
            make_translucent_popup(menu)

    def get(self, key: str):
        """按注册表键名取 action（如 "view.terminal"）；未登记返回 None。"""
        return self.actions.get(key)
