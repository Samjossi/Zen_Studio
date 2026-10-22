"""上下文用量徽章 tooltip 计时信息：纯函数用例（不实例化 ChatPanel，无 Qt 依赖）。

覆盖 `_format_duration` 分档与 `_compose_usage_tooltip` 各段有无/跨天/
进行中/上轮三态收尾文案（2026-1022-1519 计划）。
"""
from datetime import datetime

from gui.panels.chat.panel import _compose_usage_tooltip, _format_duration
from llm import UsageStats

NOW = datetime(2026, 10, 22, 15, 30)


def test_format_duration_seconds():
    assert _format_duration(0) == "0 秒"
    assert _format_duration(59) == "59 秒"


def test_format_duration_minutes():
    assert _format_duration(60) == "1 分 0 秒"
    assert _format_duration(83.9) == "1 分 23 秒"
    assert _format_duration(3599) == "59 分 59 秒"


def test_format_duration_hours():
    assert _format_duration(3600) == "1 小时 0 分"
    assert _format_duration(7620) == "2 小时 7 分"


def test_format_duration_negative_clamped():
    assert _format_duration(-5) == "0 秒"


def test_compose_tooltip_all_empty():
    assert _compose_usage_tooltip(None, None, None, None, NOW) == ""


def test_compose_tooltip_usage_only_keeps_legacy_shape():
    usage = UsageStats(used=20806, size=262144, cost=None, source="transcript")
    text = _compose_usage_tooltip(usage, None, None, None, NOW)
    assert text == (
        "上下文已用 20,806 / 上限 262,144 tokens（8%）\n"
        "数据来源：kimi 会话记录（agent 落盘的接口真值）")


def test_compose_tooltip_usage_source_levels():
    for source, note in (
        ("push", "数据来源：agent 实时推送"),
        ("estimate", "数据来源：IDE 估算（会话文本 ÷4，非精确计量，仅量级参考）"),
    ):
        text = _compose_usage_tooltip(
            UsageStats(used=1, size=100, cost=None, source=source),
            None, None, None, NOW)
        assert note in text


def test_compose_tooltip_session_same_day():
    start = datetime(2026, 10, 22, 13, 23)
    text = _compose_usage_tooltip(None, start, None, None, NOW)
    assert text == "会话开始：13:23（已持续 2 小时 7 分）"


def test_compose_tooltip_session_cross_day_has_date():
    start = datetime(2026, 10, 21, 23, 0)
    text = _compose_usage_tooltip(None, start, None, None, NOW)
    assert text.startswith("会话开始：10-21 23:00")


def test_compose_tooltip_turn_in_progress():
    turn_start = datetime(2026, 10, 22, 15, 29, 15)
    text = _compose_usage_tooltip(None, None, None, turn_start, NOW)
    assert "本轮进行中：已用 45 秒" in text


def test_compose_tooltip_last_turn_outcomes():
    started = datetime(2026, 10, 22, 15, 16, 37)
    ended = datetime(2026, 10, 22, 15, 18, 0)
    for outcome in ("完成", "失败", "手动停止"):
        text = _compose_usage_tooltip(
            None, None,
            {"started": started, "ended": ended, "outcome": outcome},
            None, NOW)
        assert f"上轮任务：用时 1 分 23 秒（15:18 {outcome}）" in text


def test_compose_tooltip_full_segments():
    usage = UsageStats(used=20806, size=262144, cost=None, source="push")
    start = datetime(2026, 10, 22, 15, 12)
    turn_start = datetime(2026, 10, 22, 15, 29, 15)
    last = {
        "started": datetime(2026, 10, 22, 15, 16, 37),
        "ended": datetime(2026, 10, 22, 15, 18, 0),
        "outcome": "完成",
    }
    text = _compose_usage_tooltip(usage, start, last, turn_start, NOW)
    lines = text.split("\n")
    assert lines[0].startswith("上下文已用 20,806 / 上限 262,144 tokens（8%）")
    assert lines[1] == "数据来源：agent 实时推送"
    assert lines[2].startswith("会话开始：15:12")
    assert lines[3] == "本轮进行中：已用 45 秒"
    assert lines[4] == "上轮任务：用时 1 分 23 秒（15:18 完成）"
