"""用量菜单：菜单栏「设置」之后的 Token 用量热力图直达入口。

顶层单 action、点击直达（用户明确要求不再下钻一级），与视图菜单入口
（view.token_usage）同一槽 show_token_usage，非模态单例共享。
"""
from PySide6.QtWidgets import QMainWindow, QMenuBar

from ide.gui.menus.registry import ActionRegistry


def build(menubar: QMenuBar, ctx: QMainWindow, actions: ActionRegistry) -> None:
    action = menubar.addAction("用量(&U)")
    action.triggered.connect(ctx.show_token_usage)
    actions.register("usage.token", action)
