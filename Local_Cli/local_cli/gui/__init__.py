"""GUI 模式入口：PySide6 桌面界面（实现计划 §4 阶段 3）。

PySide6 在 run_gui() 内惰性导入：CLI 协议模式（local acp）永远不触碰
Qt，前端 IDE 拉起子进程时不会因 GUI 依赖拖慢启动或引入显示服务器要求。
"""
import sys


def run_gui() -> int:
    from local_cli.gui.app import LocalGuiApp

    return LocalGuiApp(sys.argv).run()
