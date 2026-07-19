"""编辑菜单：复制 / 全选 / 查找（AI-first 精简形态，只放浏览类操作）。

复制/全选转发焦点控件（点击菜单时原焦点控件不失焦，focusWidget 仍指向
之前的编辑目标）；启用态在菜单弹出前（aboutToShow）按焦点控件能力刷新。
查找按焦点分发：终端 → 终端查找浮层；其余 → 查看器查找浮层。
"""
from PySide6.QtWidgets import QMainWindow, QMenuBar

from gui.menus.registry import ActionRegistry


def build(menubar: QMenuBar, ctx: QMainWindow, actions: ActionRegistry) -> None:
    menu = menubar.addMenu("编辑(&E)")

    act_copy = menu.addAction("复制(&C)")
    act_copy.triggered.connect(ctx.copy_focused)
    actions.register("edit.copy", act_copy)

    act_all = menu.addAction("全选(&A)")
    act_all.triggered.connect(ctx.select_all_focused)
    actions.register("edit.select_all", act_all)

    menu.addSeparator()

    act_find = menu.addAction("查找(&F)")
    act_find.triggered.connect(ctx.find_focused)
    actions.register("edit.find", act_find)

    def _refresh_states() -> None:
        """弹出前按焦点控件能力刷新启用态（不支持的控件灰显）。"""
        act_copy.setEnabled(ctx.focus_supports(("copy", "copy_selection")))
        act_all.setEnabled(ctx.focus_supports(("selectAll",)))

    menu.aboutToShow.connect(_refresh_states)
