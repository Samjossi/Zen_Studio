"""实时输出速度纯函数用例：滚动窗口优先，本轮全程均值兜底。

不实例化 ChatPanel，无 Qt 依赖：samples 为 (monotonic, 累计字符数) 队列。
"""
from collections import deque

import pytest

from ide.gui.panels.chat.panel import _live_or_average_speed, _windowed_speed


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


def test_windowed_speed_short_span_allowed():
    samples = deque([(0.0, 0), (1.0, 400)])
    assert _windowed_speed(samples, now=1.0) == 100.0


def test_windowed_speed_zero_growth_returns_zero():
    # 工具/审批间隙：无新字符流入，速率走低为 0 而非报错或 None
    samples = deque([(0.0, 100), (5.0, 100)])
    assert _windowed_speed(samples, now=5.0) == 0.0


def test_windowed_speed_partial_window_uses_actual_span():
    # 样本仅覆盖窗口内 4 秒：分母取实际跨度 4 秒，非窗口全长 10 秒
    samples = deque([(6.0, 0), (10.0, 160)])
    assert _windowed_speed(samples, now=10.0) == 10.0


def test_live_speed_prefers_window_rate():
    samples = deque([(20.0, 400), (25.0, 600)])
    assert _live_or_average_speed(
        samples, cumulative_chars=600, turn_elapsed=100.0, now=25.0,
    ) == (10.0, "近 10 秒均值")


def test_live_speed_falls_back_to_turn_average_with_single_sample():
    samples = deque([(0.0, 400)])
    assert _live_or_average_speed(
        samples, cumulative_chars=400, turn_elapsed=20.0, now=20.0,
    ) == (5.0, "本轮均值")


def test_live_speed_falls_back_to_turn_average_with_zero_span():
    samples = deque([(10.0, 0), (10.0, 400)])
    assert _live_or_average_speed(
        samples, cumulative_chars=400, turn_elapsed=20.0, now=10.0,
    ) == (5.0, "本轮均值")


def test_live_speed_no_output_returns_zero():
    assert _live_or_average_speed(
        deque(), cumulative_chars=0, turn_elapsed=20.0, now=20.0,
    ) == (0.0, "本轮均值")


@pytest.mark.parametrize("turn_elapsed", [0.0, -1.0])
def test_live_speed_nonpositive_turn_elapsed_returns_zero(turn_elapsed):
    assert _live_or_average_speed(
        deque(), cumulative_chars=400, turn_elapsed=turn_elapsed, now=20.0,
    ) == (0.0, "本轮均值")
