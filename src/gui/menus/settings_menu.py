"""设置菜单：设置中心… / 打开配置文件 / 恢复默认设置（三项入口菜单）。

设置中心为唯一偏好配置面（文档/修改记录/2026-0722-1344 计划）：菜单复选框仅
"是/否"二态表达力，枚举/数值/文本/布尔设置项一律入设置中心页面注册表
（新增流程见 gui/settings_dialog.py 模块 docstring 四步），禁止再向本菜单
直挂偏好项。高频原位操作由 ModelBar（模型切换）与视图菜单（面板显隐/主题）
承担；原始 settings.json 经「打开配置文件」入只读查看器（修改由 AI 落盘）。
"""
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMenuBar

from gui.menus.registry import ActionRegistry


def build(menubar: QMenuBar, ctx: QMainWindow, actions: ActionRegistry) -> None:
    menu = menubar.addMenu("设置(&S)")

    # 设置中心对话框（唯一偏好配置面入口；不绑定快捷键——快捷键位留给未来
    # 更高频操作，2026-0722-1344 计划附加裁决）
    action = menu.addAction("设置中心(&P)…")
    action.triggered.connect(ctx.open_settings_dialog)
    actions.register("settings.center", action)

    menu.addSeparator()

    # AI-first 落盘通道：只读查看 settings.json，快捷键可达不依赖对话框
    action = menu.addAction("打开配置文件(&J)")
    action.triggered.connect(ctx.open_settings_file)
    actions.register("settings.open_json", action)

    # 破坏性操作通行做法：菜单直达 + 确认框（MainWindow.reset_settings）
    action = menu.addAction("恢复默认设置(&D)…")
    action.setMenuRole(QAction.MenuRole.NoRole)  # 防 macOS 抢入应用菜单
    action.triggered.connect(ctx.reset_settings)
    actions.register("settings.reset", action)
