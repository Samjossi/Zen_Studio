"""上下文用量徽章 tooltip 计时信息：纯函数用例（不实例化 ChatPanel，无 Qt 依赖）。

覆盖 `_format_duration` 分档与 `_compose_usage_tooltip` 各段有无/跨天/
进行中/上轮三态收尾文案（2026-1022-1519 计划）；speed 参数的输出速度
行（2026-1023-1632 计划）；live_speed 参数的实时输出速度行
（2026-1025-1657 计划）。
"""
import time
from datetime import datetime
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

import ide.gui.panels.chat.model_bar as model_bar_mod
import ide.gui.panels.chat.panel as panel_mod
import ide.gui.settings as settings_mod
from ide.gui.panels.chat.panel import (
    ChatPanel,
    _compose_usage_tooltip,
    _format_duration,
)
from ide.llm import TurnSpeed, UsageStats
from ide.llm.registry import BackendSpec

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
    assert _compose_usage_tooltip(None, None, None, None, None, None, NOW) == ""


def test_compose_tooltip_usage_only_keeps_legacy_shape():
    usage = UsageStats(used=20806, size=262144, cost=None, source="transcript")
    text = _compose_usage_tooltip(usage, None, None, None, None, None, NOW)
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
            None, None, None, None, None, NOW)
        assert note in text


def test_compose_tooltip_session_same_day():
    start = datetime(2026, 10, 22, 13, 23)
    text = _compose_usage_tooltip(None, start, None, None, None, None, NOW)
    assert text == (
        "上下文用量：暂不可用（未收到有效 agent 数据）\n"
        "会话开始：13:23（已持续 2 小时 7 分）")


def test_compose_tooltip_session_cross_day_has_date():
    start = datetime(2026, 10, 21, 23, 0)
    text = _compose_usage_tooltip(None, start, None, None, None, None, NOW)
    assert "会话开始：10-21 23:00" in text


def test_compose_tooltip_turn_in_progress():
    turn_start = datetime(2026, 10, 22, 15, 29, 15)
    text = _compose_usage_tooltip(None, None, None, turn_start, None, None, NOW)
    assert "本轮进行中：已用 45 秒" in text


def test_compose_tooltip_last_turn_outcomes():
    started = datetime(2026, 10, 22, 15, 16, 37)
    ended = datetime(2026, 10, 22, 15, 18, 0)
    for outcome in ("完成", "失败", "手动停止"):
        text = _compose_usage_tooltip(
            None, None,
            {"started": started, "ended": ended, "outcome": outcome},
            None, None, None, NOW)
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
    text = _compose_usage_tooltip(usage, start, last, turn_start, None, None, NOW)
    lines = text.split("\n")
    assert lines[0].startswith("上下文已用 20,806 / 上限 262,144 tokens（8%）")
    assert lines[1] == "数据来源：agent 实时推送"
    assert lines[2].startswith("会话开始：15:12")
    assert lines[3] == "本轮进行中：已用 45 秒"
    assert lines[4] == "上轮任务：用时 1 分 23 秒（15:18 完成）"


def test_compose_tooltip_speed_line():
    speed = TurnSpeed(output_tokens=1358, gen_seconds=73.0)
    text = _compose_usage_tooltip(None, None, None, None, None, speed, NOW)
    assert text == "上轮输出速度：18.6 tok/s（输出 1,358 tokens / 生成 1 分 13 秒）"


def test_compose_tooltip_speed_follows_last_turn():
    last = {
        "started": datetime(2026, 10, 22, 15, 16, 37),
        "ended": datetime(2026, 10, 22, 15, 18, 0),
        "outcome": "完成",
    }
    speed = TurnSpeed(output_tokens=500, gen_seconds=25.0)
    text = _compose_usage_tooltip(None, None, last, None, None, speed, NOW)
    lines = text.split("\n")
    assert lines[0] == "上下文用量：暂不可用（未收到有效 agent 数据）"
    assert lines[1].startswith("上轮任务：")
    assert lines[2].startswith("上轮输出速度：20.0 tok/s")


def test_compose_tooltip_speed_none_no_line():
    text = _compose_usage_tooltip(
        UsageStats(used=1, size=100, cost=None, source="push"),
        None, None, None, None, None, NOW)
    assert "输出速度" not in text


def test_compose_tooltip_speed_zero_duration_no_line():
    speed = TurnSpeed(output_tokens=100, gen_seconds=0.0)
    text = _compose_usage_tooltip(None, None, None, None, None, speed, NOW)
    assert text == ""


def test_compose_tooltip_live_speed_line():
    turn_start = datetime(2026, 10, 22, 15, 29, 15)
    text = _compose_usage_tooltip(None, None, None, turn_start, 14.24, None, NOW)
    lines = text.split("\n")
    assert lines[0] == "上下文用量：等待首轮模型计量"
    assert lines[1] == "本轮进行中：已用 45 秒"
    assert lines[2] == "实时输出速度：~14.2 tok/s（近 10 秒均值，字符估算）"


def test_compose_tooltip_live_speed_before_last_turn():
    turn_start = datetime(2026, 10, 22, 15, 29, 15)
    last = {
        "started": datetime(2026, 10, 22, 15, 16, 37),
        "ended": datetime(2026, 10, 22, 15, 18, 0),
        "outcome": "完成",
    }
    speed = TurnSpeed(output_tokens=500, gen_seconds=25.0)
    text = _compose_usage_tooltip(None, None, last, turn_start, 8.0, speed, NOW)
    lines = text.split("\n")
    assert lines[0] == "上下文用量：等待首轮模型计量"
    assert lines[1].startswith("本轮进行中：")
    assert lines[2].startswith("实时输出速度：~8.0 tok/s")
    assert lines[3].startswith("上轮任务：")
    assert lines[4].startswith("上轮输出速度：")


def test_compose_tooltip_live_speed_none_no_line():
    turn_start = datetime(2026, 10, 22, 15, 29, 15)
    text = _compose_usage_tooltip(None, None, None, turn_start, None, None, NOW)
    assert "实时输出速度" not in text


def test_compose_tooltip_without_usage_explains_wait_and_unavailable():
    turn_start = datetime(2026, 10, 22, 15, 29, 15)
    text = _compose_usage_tooltip(
        None, NOW, None, turn_start, None, None, NOW)
    assert "上下文用量：等待首轮模型计量" in text

    last = {"started": turn_start, "ended": NOW, "outcome": "完成"}
    text = _compose_usage_tooltip(None, NOW, last, None, None, None, NOW)
    assert "上下文用量：暂不可用（未收到有效 agent 数据）" in text


def test_compose_tooltip_real_usage_replaces_unavailable_reason():
    usage = UsageStats(used=1, size=100, cost=None, source="push")
    text = _compose_usage_tooltip(usage, NOW, None, None, None, None, NOW)
    assert "上下文用量：" not in text
    assert "上下文已用 1 / 上限 100 tokens（1%）" in text


_TEMP_DIR = Path(__file__).resolve().parents[1] / ".temp" / "test_usage_badge_timing"


class _FakeSignal:
    def connect(self, _slot):
        pass


class _FakeWorker:
    def __init__(self, *_args):
        self.chunk_received = _FakeSignal()
        self.finished_with_error = _FakeSignal()
        self.stopped_by_user = _FakeSignal()

    def start(self):
        pass

    def request_stop(self, _reason="user"):
        pass

    def disconnect(self, *_args):
        pass

    def setParent(self, _parent):
        pass

    def wait(self, _timeout=None):
        return True


class _FakeProvider:
    def __init__(self):
        self.stats = None
        self.polls = 0
        self.closed = False

    def poll_usage(self):
        self.polls += 1
        return self.stats

    def close(self):
        self.closed = True


@pytest.fixture()
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def kimi_panel(monkeypatch, qapp):
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)
    settings_file = _TEMP_DIR / "settings.json"
    settings_file.unlink(missing_ok=True)
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(settings_mod, "SETTINGS_LOCK_FILE", _TEMP_DIR / "settings.lock")
    kimi = BackendSpec(
        name="kimi-acp", label="Kimi", vendor="kimi", vendor_label="Kimi",
        available=lambda: True, list_models=lambda: ["k3"],
        factory=lambda **_kw: None, efforts=("auto",), default_effort="auto")
    other = BackendSpec(
        name="fake-acp", label="Fake", vendor="fake", vendor_label="Fake",
        available=lambda: True, list_models=lambda: ["m1"],
        factory=lambda **_kw: _FakeProvider(), efforts=("auto",),
        default_effort="auto")
    specs = {spec.name: spec for spec in (kimi, other)}
    monkeypatch.setattr(
        model_bar_mod, "vendor_groups", lambda: {"kimi": [kimi], "fake": [other]})
    monkeypatch.setattr(model_bar_mod, "spec_of", lambda name: specs.get(name))
    monkeypatch.setattr(model_bar_mod, "vendor_of", lambda name: specs[name].vendor)
    monkeypatch.setattr(model_bar_mod, "resolve_efforts",
                        lambda spec, _model: (spec.efforts, spec.default_effort))
    monkeypatch.setattr(panel_mod, "spec_of", lambda name: specs.get(name))
    monkeypatch.setattr(panel_mod, "ChatWorker", _FakeWorker)
    panel = ChatPanel("kimi-acp", "k3", str(_TEMP_DIR))
    provider = _FakeProvider()
    panel._providers["kimi-acp"] = provider
    yield panel, provider
    panel.close()


def test_panel_badge_empty_state_persistence_and_grace_expiry(kimi_panel):
    panel, provider = kimi_panel
    assert panel._usage_label.text() == ""

    panel._send_turn("hello", [], provider)
    assert panel._usage_timer.isActive()
    assert panel._usage_label.text() == "—"
    assert "等待首轮模型计量" in panel._usage_label.toolTip()

    panel._on_finished("")
    assert not panel._busy
    assert panel._usage_timer.isActive()
    assert panel._usage_grace_deadline is not None
    assert "暂不可用" in panel._usage_label.toolTip()

    panel._send_turn("next", [], provider)
    assert panel._usage_grace_deadline is None
    provider.stats = UsageStats(used=20806, size=262144, cost=None, source="push")
    panel._poll_usage_tick()
    assert panel._usage_label.text() == "8%"
    assert "上下文已用 20,806" in panel._usage_label.toolTip()
    provider.stats = None
    panel._poll_usage_tick()
    assert panel._usage_label.text() == "8%"

    panel._on_finished("")
    panel._usage_grace_deadline = time.monotonic() - 1
    panel._poll_usage_tick()
    assert not panel._usage_timer.isActive()
    assert panel._usage_grace_deadline is None


def test_panel_stop_grace_backend_switch_and_non_kimi_finish(kimi_panel):
    panel, provider = kimi_panel
    panel._send_turn("hello", [], provider)
    panel._on_stopped("user")
    assert panel._usage_timer.isActive()
    assert panel._usage_grace_deadline is not None

    panel.set_model_selection("fake-acp", "m1")
    assert not panel._usage_timer.isActive()
    assert panel._usage_grace_deadline is None
    assert panel._usage_label.text() == ""

    other = panel._providers["fake-acp"]
    panel._send_turn("hello", [], other)
    panel._on_finished("")
    assert not panel._usage_timer.isActive()
    assert panel._usage_grace_deadline is None


def test_panel_close_cancels_usage_grace(kimi_panel):
    panel, provider = kimi_panel
    panel._send_turn("hello", [], provider)
    panel._on_finished("")
    assert panel._usage_timer.isActive()
    panel.close()
    assert not panel._usage_timer.isActive()
    assert panel._usage_grace_deadline is None
