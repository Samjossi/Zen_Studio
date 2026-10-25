"""实时输出速度滚动窗口纯函数用例（`_windowed_speed`，2026-1025-1657 计划）。

不实例化 ChatPanel，无 Qt 依赖：samples 为 (monotonic, 累计字符数) 队列。
"""
from collections import deque

from gui.panels.chat.panel import _windowed_speed


def test_windowed_speed_normal_rate():
    samples = deque([(0.0, 0), (5.0, 200)])
    # 200 字符 ÷4 ÷ 5 秒 = 10.0 tok/s
    assert _windowed_speed(samples, now=5.0) == 10.0


def test_windowed_speed_prunes_samples_outside_window():
    samples = deque([(0.0, 0), (20.0, 400), (25.0, 600)])
    # 窗口 10s → 0s 采样点剪除；(600-400) ÷4 ÷ 5s = 10.0 tok/s
    assert _windowed_speed(samples, now=25.0) == 10.0


def test_windowed_speed_empty_or_single_sample_none():
    assert _windowed_speed(deque(), now=10.0) is None
    assert _windowed_speed(deque([(10.0, 400)]), now=10.0) is None


def test_windowed_speed_short_span_none():
    # 首末跨度 1 秒 < 最小跨度 2 秒：轮初抖动无意义
    samples = deque([(0.0, 0), (1.0, 400)])
    assert _windowed_speed(samples, now=1.0) is None


def test_windowed_speed_zero_growth_returns_zero():
    # 工具/审批间隙：无新字符流入，速率走低为 0 而非报错或 None
    samples = deque([(0.0, 100), (5.0, 100)])
    assert _windowed_speed(samples, now=5.0) == 0.0


def test_windowed_speed_partial_window_uses_actual_span():
    # 样本仅覆盖窗口内 4 秒：分母取实际跨度 4 秒，非窗口全长 10 秒
    samples = deque([(6.0, 0), (10.0, 160)])
    assert _windowed_speed(samples, now=10.0) == 10.0
