"""platform_info 纯函数断言：platform 注入双向 + offscreen 默认不误判。"""
from core.platform_info import is_wayland, is_x11


def test_is_wayland():
    assert is_wayland("wayland") is True
    assert is_wayland("xcb") is False
    assert is_wayland("offscreen") is False


def test_is_x11():
    assert is_x11("xcb") is True
    assert is_x11("wayland") is False
    assert is_x11("offscreen") is False


def test_default_offscreen_neither():
    # conftest 将 GUI 用例钉在 offscreen；无注入时两方向均须为 False，
    # 保证 Wayland 专属窗口壳不会在测试环境误启用
    assert is_wayland() is False
    assert is_x11() is False
