"""视图菜单：面板显隐 / 噪音过滤 / 恢复默认布局 / Git 刷新 / 外观（主题）。

面板显隐沿用「单一入口」法：勾选动作与面板头部「−」按钮汇入 MainWindow
的 set_xxx_visible（setChecked 不触发 triggered，勾选态须一并同步）。
主题互斥组用 QActionGroup(exclusive) + setData() 载荷单回调
（替代早期 N 闭包写法；文档/选型记录/2026-0720-0433 选型 §4.2）。
"""
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QMainWindow, QMenuBar

from gui.menus.registry import ActionRegistry
from gui.theme import available_themes, get_label, load_settings


def build(menubar: QMenuBar, ctx: QMainWindow, actions: ActionRegistry) -> None:
    menu = menubar.addMenu("视图(&V)")

    # 面板显隐组（勾选动作 → MainWindow 单一入口）
    for key, label, slot in (
        ("view.chat", "聊天面板(&H)", ctx.set_chat_visible),
        ("view.explorer", "文件树面板(&E)", ctx.set_explorer_visible),
        ("view.terminal", "终端面板(&T)", ctx.set_terminal_visible),
        ("view.changes", "变更面板(&C)", ctx.set_changes_visible),
    ):
        action = menu.addAction(label)
        action.setCheckable(True)
        action.setChecked(True)
        action.triggered.connect(slot)
        actions.register(key, action)

    menu.addSeparator()

    action = menu.addAction("过滤噪音目录(&N)")
    action.setCheckable(True)
    action.setChecked(True)
    action.triggered.connect(ctx.file_explorer.set_noise_filter)
    actions.register("view.noise_filter", action)

    action = menu.addAction("恢复默认布局(&R)")
    action.triggered.connect(ctx.reset_layout)
    actions.register("view.reset_layout", action)

    menu.addSeparator()

    action = menu.addAction("刷新 Git 状态(&G)")
    action.triggered.connect(ctx.refresh_git_status)
    actions.register("view.git_refresh", action)

    menu.addSeparator()

    # 外观子菜单：主题互斥组（注册表动态枚举；setData 载荷单回调）
    submenu = menu.addMenu("外观(&A)")
    group = QActionGroup(ctx)
    group.setExclusive(True)
    current = load_settings()["theme"]
    for name in available_themes():
        action = submenu.addAction(get_label(name))
        action.setCheckable(True)
        action.setData(name)
        action.setChecked(name == current)
        group.addAction(action)
        actions.register(f"appearance.theme.{name}", action)
    group.triggered.connect(lambda act: ctx.switch_theme(act.data()))
