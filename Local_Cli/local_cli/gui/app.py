"""应用层：QApplication 组装、主题注入与优雅退出接线。

退出可靠性按《Linux桌面关机优雅退出通用技术方案》§3.1（Qt 检测点①）：
xcb 平台插件注册的会话客户端在关机确认后收到 EndSession，Qt 发射
commitDataRequest——此时直接 quit()，禁止弹窗/交互/阻塞；
清理动作挂 aboutToQuit，随 quit() 自动触发，且清理逻辑幂等。
"""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from local_cli import AGENT_NAME, __version__
from local_cli.gui.main_window import MainWindow
from local_cli.gui.theme import build_qss


class LocalGuiApp:
    """GUI 模式的组装根：协作者在此显式接线（AFCP 2.3，组合优先）。"""

    def __init__(self, argv: list[str] | None = None) -> None:
        self._qt_app = QApplication(argv if argv is not None else sys.argv)
        self._qt_app.setApplicationName(AGENT_NAME)
        self._qt_app.setApplicationVersion(__version__)
        self._qt_app.setStyleSheet(build_qss())
        self._main_window = MainWindow()
        self._wire_graceful_exit()

    def _wire_graceful_exit(self) -> None:
        # 关机/注销确认后立即自行退出（检测点①）；offscreen/无会话总线环境
        # 该信号永不触发，连接本身无副作用（方案 §4 静默降级）
        self._qt_app.commitDataRequest.connect(lambda _session: self._qt_app.quit())
        # 清理挂 aboutToQuit：关窗 quit() 与关机 quit() 走同一条幂等收尾路径
        self._qt_app.aboutToQuit.connect(self._main_window.shutdown_service)

    def run(self) -> int:
        self._main_window.show()
        return self._qt_app.exec()
