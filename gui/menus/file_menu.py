"""文件菜单：打开文件 / 新建窗口（空白窗口）/ 在新窗口打开文件夹（多开进程）/
打开文件夹（换根关旧窗）/ 打开配置目录 / 最近打开的项目（动态子菜单）/ 退出。

全部菜单项不绑定快捷键（选型 §4.4：保持简单）。
「退出」setMenuRole(NoRole) 防 macOS 系统菜单抢走（PyGPT 经验）。
「新建窗口」（2026-08-31 起为空白窗口，work plans/2026-0831-2350 计划）：
不绑定任何目录起空窗（对齐 VS Code New Window；取代 2026-0722-1901 的
同根多开语义），空窗内经「打开文件夹」就地填充。
「在新窗口打开文件夹」（换根多开）与「打开文件夹」（换根关旧窗，
文档/修改记录/2026-0724-1806）语义互斥对照：前者留旧窗，后者探活后关旧窗，
进程级「替换当前工作区」。
一窗一根（同计划 D1–D3）：同一工作区根同时只允许一个窗口，以上任何入口
命中已占用根时新进程唤活已有窗口后以退出码 3 退出（不再起重复窗口）。
「最近打开的项目」（文档/修改记录/2026-0724-1003）：aboutToShow 动态重建，
记录源为 MainWindow 启动（一进程绑定一工作区根）→ RecentProjectsStore
（全局共享，存 config/recent_projects.json），回放在新窗口绑定该根
（已被占用则唤活已有窗口，含点击当前根的自激唤活）。
"""
from pathlib import Path

from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMainWindow, QMenu, QMenuBar

from gui.menus.registry import ActionRegistry


def build(menubar: QMenuBar, ctx: QMainWindow, actions: ActionRegistry) -> None:
    menu = menubar.addMenu("文件(&F)")

    action = menu.addAction("打开文件(&O)…")
    action.triggered.connect(ctx.open_file_dialog)
    actions.register("file.open", action)

    action = menu.addAction("新建窗口(&N)")
    action.triggered.connect(ctx.new_window)
    actions.register("file.new_window", action)

    action = menu.addAction("在新窗口打开文件夹(&W)…")
    action.triggered.connect(ctx.open_folder_in_new_window)
    actions.register("file.open_folder", action)

    action = menu.addAction("打开文件夹(&D)…")
    action.triggered.connect(ctx.open_folder_here)
    actions.register("file.open_folder_here", action)

    action = menu.addAction("打开配置目录(&C)")
    action.triggered.connect(ctx.open_config_dir)
    actions.register("file.open_config_dir", action)

    recent_menu = menu.addMenu("最近打开的项目(&R)")
    recent_menu.aboutToShow.connect(lambda: _rebuild_recent_menu(recent_menu, ctx))
    actions.register("file.recent_projects", recent_menu.menuAction())

    menu.addSeparator()

    action = menu.addAction("退出(&Q)")
    action.setMenuRole(QAction.MenuRole.NoRole)  # 防 macOS 抢入应用菜单
    action.triggered.connect(ctx.close)
    actions.register("file.quit", action)


def _rebuild_recent_menu(menu: QMenu, ctx: QMainWindow) -> None:
    """aboutToShow 动态重建：记录逐条生成；空列表占位；末尾「清除列表」。

    显示名 `文件夹名  —  父目录`（同名项目可辨识），toolTip 全路径。
    """
    menu.clear()
    paths = ctx.recent_projects.list()
    if paths:
        for path in paths:
            p = Path(path)
            action = menu.addAction(f"{p.name}  —  {p.parent}")
            action.setToolTip(path)
            action.triggered.connect(
                lambda checked=False, target=path: ctx.open_recent_project(target))
    else:
        # 禁用占位项：防子菜单整个消失导致菜单位置跳动
        placeholder = menu.addAction("（空）")
        placeholder.setEnabled(False)
    menu.addSeparator()
    clear = menu.addAction("清除列表(&L)")
    clear.setEnabled(bool(paths))
    clear.triggered.connect(ctx.recent_projects.clear)
