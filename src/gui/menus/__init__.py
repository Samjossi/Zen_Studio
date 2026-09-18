"""菜单包：模块化菜单（每菜单一文件）+ 全局 Action 注册表。

对外导出 `MenuBar` 装配器；MainWindow 以 `MenuBar(self).setup()` 一行完成构建。
"""
from gui.menus.assembler import MenuBar

__all__ = ["MenuBar"]
