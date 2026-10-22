"""视图菜单：面板显隐 / 恢复默认布局 / Git 刷新 / 提交历史图 / 文件树刷新。

面板显隐沿用「单一入口」法：勾选动作与面板头部「−」按钮汇入 MainWindow
的 set_xxx_visible（setChecked 不触发 triggered，勾选态须一并同步）。
"""
from PySide6.QtWidgets import QMainWindow, QMenuBar

from gui.menus.registry import (
    KEY_VIEW_CHAT,
    KEY_VIEW_CHANGES,
    KEY_VIEW_EXPLORER,
    KEY_VIEW_TERMINAL,
    ActionRegistry,
)


def build(menubar: QMenuBar, ctx: QMainWindow, actions: ActionRegistry) -> None:
    menu = menubar.addMenu("视图(&V)")

    # 面板显隐组（勾选动作 → MainWindow 单一入口）
    for key, label, slot in (
        (KEY_VIEW_CHAT, "聊天面板(&H)", ctx.set_chat_visible),
        (KEY_VIEW_EXPLORER, "文件树面板(&E)", ctx.set_explorer_visible),
        (KEY_VIEW_TERMINAL, "终端面板(&T)", ctx.set_terminal_visible),
        (KEY_VIEW_CHANGES, "变更面板(&C)", ctx.set_changes_visible),
    ):
        action = menu.addAction(label)
        action.setCheckable(True)
        action.setChecked(True)
        action.triggered.connect(slot)
        actions.register(key, action)

    menu.addSeparator()

    action = menu.addAction("恢复默认布局(&R)")
    action.triggered.connect(ctx.reset_layout)
    actions.register("view.reset_layout", action)

    menu.addSeparator()

    action = menu.addAction("刷新 Git 状态(&G)")
    # 空窗守卫（2026-0831-2350 计划 D4）：无工作区根时 git_controller /
    # file_explorer 为 None，点击静默跳过
    action.triggered.connect(
        lambda: ctx.git_controller is not None and ctx.git_controller.refresh())
    actions.register("view.git_refresh", action)

    # 提交历史图弹窗（1507 计划）：非模态单例，菜单项不做 enable 联动
    # ——非仓库点击照样弹窗走窗内降级占位（计划 D5）；空窗由
    # MainWindow.show_git_graph 内守卫（状态栏提示，不弹窗）
    action = menu.addAction("提交历史图(&L)")
    action.triggered.connect(ctx.show_git_graph)
    actions.register("view.git_graph", action)

    # 文件树手动刷新：watcher 溢出/外部批量改动致状态滞留时由用户触发全量重读
    action = menu.addAction("刷新文件树(&F)")
    action.triggered.connect(
        lambda: ctx.file_explorer is not None and ctx.file_explorer.refresh())
    actions.register("view.file_tree_refresh", action)
