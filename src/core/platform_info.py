"""平台探测：显示服务器差异判断的唯一入口。

Wayland 与 X11 的行为差异（窗口装饰/阴影归属、位置恢复等）来自
合成器策略而非代码缺陷；全项目任何平台分支只许调用本模块，
禁止各处散落环境变量或字符串判断。
"""
from PySide6.QtGui import QGuiApplication


def _current_platform() -> str:
    app = QGuiApplication.instance()
    return app.platformName() if app is not None else ""


def is_wayland(platform: str | None = None) -> bool:
    """当前会话是否为 Wayland。

    :param platform: 测试注入口；None = 读当前 QGuiApplication
    """
    return (platform if platform is not None else _current_platform()) == "wayland"


def is_x11(platform: str | None = None) -> bool:
    """当前会话是否为 X11（Qt 平台名 xcb）。

    :param platform: 测试注入口；None = 读当前 QGuiApplication
    """
    return (platform if platform is not None else _current_platform()) == "xcb"
