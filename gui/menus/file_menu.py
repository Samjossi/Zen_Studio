"""文件菜单：打开文件 / 在新窗口打开文件夹（多开进程）/ 打开配置目录 / 退出。

全部菜单项不绑定快捷键（选型 §4.4：保持简单）。
「退出」setMenuRole(NoRole) 防 macOS 系统菜单抢走（PyGPT 经验）。
"""
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMenuBar

from gui.menus.registry import ActionRegistry


def build(menubar: QMenuBar, ctx: QMainWindow, actions: ActionRegistry) -> None:
    menu = menubar.addMenu("文件(&F)")

    action = menu.addAction("打开文件(&O)…")
    action.triggered.connect(ctx.open_file_dialog)
    actions.register("file.open", action)

    action = menu.addAction("在新窗口打开文件夹(&W)…")
    action.triggered.connect(ctx.open_folder_in_new_window)
    actions.register("file.open_folder", action)

    action = menu.addAction("打开配置目录(&C)")
    action.triggered.connect(ctx.open_config_dir)
    actions.register("file.open_config_dir", action)

    menu.addSeparator()

    action = menu.addAction("退出(&Q)")
    action.setMenuRole(QAction.MenuRole.NoRole)  # 防 macOS 抢入应用菜单
    action.triggered.connect(ctx.close)
    actions.register("file.quit", action)
