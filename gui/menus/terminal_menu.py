"""终端菜单：新建会话 / 清屏 / 重开 / 终止。

全部转调 TerminalPanel 公开方法（与头部「＋」、右键菜单同一实现路径）；
清屏/终止的启用态在菜单弹出前按活动会话存活态刷新。
"""
from PySide6.QtWidgets import QMainWindow, QMenuBar

from gui.menus.registry import ActionRegistry


def build(menubar: QMenuBar, ctx: QMainWindow, actions: ActionRegistry) -> None:
    panel = ctx.terminal_panel
    menu = menubar.addMenu("终端(&T)")

    action_new = menu.addAction("新建终端会话(&N)")
    action_new.triggered.connect(panel.new_session)
    actions.register("terminal.new", action_new)

    action_clear = menu.addAction("清屏(&L)")
    action_clear.triggered.connect(panel.clear_active)
    actions.register("terminal.clear", action_clear)

    menu.addSeparator()

    # 重开：无会话时等价新建（TerminalPanel.restart_active 语义），恒可用
    action_restart = menu.addAction("重开终端(&R)")
    action_restart.triggered.connect(panel.restart_active)
    actions.register("terminal.restart", action_restart)

    action_kill = menu.addAction("终止终端(&K)")
    action_kill.triggered.connect(panel.kill_active)
    actions.register("terminal.kill", action_kill)

    def _refresh_states() -> None:
        """弹出前按活动会话存活态刷新（无会话/已退出时清屏与终止灰显）。"""
        has_alive = panel.has_alive_session()
        action_clear.setEnabled(has_alive)
        action_kill.setEnabled(has_alive)

    menu.aboutToShow.connect(_refresh_states)
