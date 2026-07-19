"""帮助菜单：关于对话框。"""
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMenuBar

from gui.menus.registry import ActionRegistry


def build(menubar: QMenuBar, ctx: QMainWindow, actions: ActionRegistry) -> None:
    menu = menubar.addMenu("帮助(&H)")

    action = menu.addAction("关于 Zen Studio(&A)")
    action.setMenuRole(QAction.MenuRole.NoRole)  # 防 macOS 抢入应用菜单
    action.triggered.connect(ctx.show_about)
    actions.register("help.about", action)
