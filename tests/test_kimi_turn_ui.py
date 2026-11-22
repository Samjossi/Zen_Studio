import pytest
from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from ide.gui.controllers import GitStatusController
from ide.gui.panels.chat.panel import ChatPanel
from ide.gui.panels.chat.vendors.kimi.presenter import KimiCardPresenter
from ide.llm.base import BackendCapabilities, Chunk
from ide.llm.providers.kimi.updates import KimiUpdateMapper


class SignalStub:
    def __init__(self):
        self.events = []

    def emit(self, *args):
        self.events.append(args)


class OutputStub:
    def __init__(self):
        self.stream_chunks = []
        self.messages = []

    def append_stream_chunk(self, text):
        self.stream_chunks.append(text)

    def append_message(self, role, text):
        self.messages.append((role, text))

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class TimelineStub:
    def end_turn(self):
        pass


class TimerStub:
    def stop(self):
        pass


class InputStub:
    def __init__(self):
        self._placeholder = ""
        self._text = "draft"

    def toPlainText(self):
        return self._text

    def setPlainText(self, text):
        self._text = text

    def clear(self):
        self._text = ""

    def placeholderText(self):
        return self._placeholder

    def setPlaceholderText(self, text):
        self._placeholder = text


class AttachmentsStub:
    def count(self):
        return 0

    def attachments(self):
        return []

    def clear(self):
        pass


class ProviderStub:
    def __init__(self, *, turn_pending=False, recovery_pending=False):
        self._turn_pending = turn_pending
        self._recovery_pending = recovery_pending

    @property
    def turn_pending(self):
        return self._turn_pending

    def capabilities(self):
        return BackendCapabilities(
            stall_watchdog=False, recovery_pending=self._recovery_pending)


class PanelHarness:
    """装配最小桩件，直接调 ChatPanel 的真实方法体（与现有
    test_i7/i8/i13 同构；新增属性保持默认值以覆盖新分支）。"""

    _LONG_TASK_HINT_S = ChatPanel._LONG_TASK_HINT_S

    def __init__(self):
        self._llm_name = "kimi-kap"
        self._worker = object()
        self._last_chunk_at = 0.0
        self.stop_requests = []
        self._turn_started_at = None
        self._stream_buffer = "partial"
        self._history = [{"role": "user", "content": "hello"}]
        self._sent_attachments = []
        self._sent_text = "hello"
        self._has_seen_reasoning = False
        self._providers = {"kimi-kap": ProviderStub()}
        self._pending_send = True
        self._stop_reason = "user"
        self._sync_active = False
        self._sync_started_at = None
        self.output = OutputStub()
        self.timeline = TimelineStub()
        self._stall_timer = TimerStub()
        self.input = InputStub()
        self.attachments = AttachmentsStub()
        self.turn_finished = SignalStub()
        self.turn_completed = SignalStub()
        self.files_maybe_changed = SignalStub()
        self._presenter = KimiCardPresenter()
        self.sent_turns = []
        self.busy = True
        self._busy = True

    def request_stop(self, reason="user"):
        self.stop_requests.append(reason)

    def _flush_turn_consumption(self):
        pass

    def _clear_speed_samples(self):
        pass

    def _restore_sent_attachments(self):
        pass

    def _start_usage_grace(self):
        pass

    def _refresh_usage_label(self):
        pass

    def _apply_image_capability(self):
        pass

    def _stall_watchdog_enabled(self, provider=None):
        return ChatPanel._stall_watchdog_enabled(self, provider)

    def _set_busy(self, busy):
        self.busy = busy
        self._busy = busy

    def _refresh_placeholder(self):
        ChatPanel._refresh_placeholder(self)

    def _sync_elapsed_hint(self):
        return ChatPanel._sync_elapsed_hint(self)

    def _get_provider(self, name):
        return self._providers.get(name)

    def _blocked_by_archive(self):
        return False

    def _supports_images(self):
        return False

    def _send_turn(self, text, images, provider):
        self.sent_turns.append((text, images))

    def _fire_pending_send(self):
        return ChatPanel._fire_pending_send(self)


@pytest.mark.parametrize("last_chunk_at", [0.0, 999.0], ids=["silent", "active-frame"])
def test_i7_i8_kimi_never_runs_stall_watchdog(monkeypatch, last_chunk_at):
    panel = PanelHarness()
    panel._last_chunk_at = last_chunk_at
    monkeypatch.setattr("ide.gui.panels.chat.panel.time.monotonic", lambda: 1000.0)

    ChatPanel._stall_watchdog_tick(panel)

    assert panel.stop_requests == []


def test_i13_only_normal_completion_fires_pending_send():
    completed = PanelHarness()
    ChatPanel._on_finished(completed, "")
    # 补发接续成功：busy 不间断，完成音留给接续轮（原语义）
    assert completed.sent_turns == [("draft", [])]
    assert completed._pending_send is False
    assert completed.turn_completed.events == []
    assert completed.busy is True

    no_pending = PanelHarness()
    no_pending._pending_send = False
    ChatPanel._on_finished(no_pending, "")
    # 无接续：解除 busy 并播一次完成
    assert no_pending.turn_completed.events == [(True,)]
    assert no_pending.busy is False

    failed = PanelHarness()
    ChatPanel._on_finished(failed, "request failed")
    assert failed.sent_turns == []
    assert failed._pending_send is False
    assert failed.turn_completed.events == [(False,)]

    stopped = PanelHarness()
    ChatPanel._on_stopped(stopped, "user")
    assert stopped.sent_turns == []
    assert stopped._pending_send is False


# ---------------------------------------------------------------- T3 闸门与同步态

def test_status_chunk_sets_sync_and_updates_placeholder():
    panel = PanelHarness()
    panel._busy = True

    ChatPanel._on_chunk(panel, Chunk("status", "Kimi 后台轮次同步中…"))

    assert panel._sync_active is True
    assert "同步中" in panel.input.placeholderText()


def test_status_chunk_never_enters_stream_or_history():
    panel = PanelHarness()
    panel._stream_buffer = ""

    ChatPanel._on_chunk(panel, Chunk("status", "任意同步文案"))

    assert panel._stream_buffer == ""
    assert panel.output.stream_chunks == []


def test_finished_clears_sync_state():
    panel = PanelHarness()
    panel._sync_active = True
    panel._pending_send = False  # 无接续轮：收尾置闲

    ChatPanel._on_finished(panel, "")

    assert panel._sync_active is False
    assert panel.busy is False


def test_desync_finish_no_fanfare_no_fire():
    """失配收尾（D5/D8）：不播成功、不补发、草稿保留、提示核对副作用。"""
    panel = PanelHarness()

    ChatPanel._on_finished(
        panel, "轮次未确认完成：用户轮次在 tool_use 后提前完成；"
               "已发生的工具副作用请核对。本轮不会自动重发")

    assert panel.sent_turns == []
    assert panel._pending_send is False
    assert panel.turn_completed.events == [(False,)]
    assert any("[未完成]" in c for c in panel.output.stream_chunks)
    assert not any("[请求失败]" in c for c in panel.output.stream_chunks)
    assert any("工具副作用" in m[1] for m in panel.output.messages)


def test_turn_pending_gate_blocks_auto_fire():
    """D6 GUI 层闸门：provider 报告终态未确认 → 自动补发被拒绝、待发保留。"""
    panel = PanelHarness()
    panel._providers["kimi-kap"] = ProviderStub(turn_pending=True)

    fired = ChatPanel._fire_pending_send(panel)

    assert fired is False
    assert panel._pending_send is True  # 待发保留，草稿驻留输入框
    assert panel.sent_turns == []
    assert any("尚未落定" in m[1] for m in panel.output.messages)


def test_sync_placeholder_shows_elapsed_after_threshold(monkeypatch):
    """D10：同步超过阈值后占位符显示已运行时长；阈值内只有基础文案。"""
    import time as time_mod
    panel = PanelHarness()
    panel._busy = True
    panel._sync_active = True
    panel._sync_started_at = time_mod.monotonic() - 125
    monkeypatch.setattr(
        "ide.gui.panels.chat.panel.time.monotonic",
        lambda: panel._sync_started_at + 125)

    ChatPanel._refresh_placeholder(panel)

    assert "已 2 分 05 秒" in panel.input.placeholderText()
    assert "同步中" in panel.input.placeholderText()


def test_sync_placeholder_below_threshold_omits_elapsed():
    panel = PanelHarness()
    panel._busy = True
    panel._sync_active = True
    panel._sync_started_at = None  # 未记录起点：只显示基础文案

    ChatPanel._refresh_placeholder(panel)

    text = panel.input.placeholderText()
    assert "已" not in text.split("同步中")[1].split("…")[0]
    assert "同步中" in text


def test_normal_provider_turn_pending_clear_allows_fire():
    """对照：终态已确认的 provider 正常补发一次（A2/A3 正路径）。"""
    panel = PanelHarness()
    panel._providers["kimi-kap"] = ProviderStub(turn_pending=False)

    fired = ChatPanel._fire_pending_send(panel)

    assert fired is True
    assert panel._pending_send is False
    assert panel.sent_turns == [("draft", [])]


@pytest.mark.parametrize("kind,status,expected_signals", [
    ("edit", "completed", 1),
    ("execute", "failed", 1),
    ("read", "completed", 0),
    ("other", "completed", 0),
    ("edit", "in_progress", 0),
])
def test_kimi_activity_frames_drive_files_maybe_changed(
        kind, status, expected_signals):
    panel = PanelHarness()
    mapper = KimiUpdateMapper()
    first = mapper.map_session_update({
        "method": "session/update",
        "params": {
            "sessionId": "panel-refresh",
            "update": {
                "sessionUpdate": "tool_call",
                "toolCallId": "panel-tool",
                "title": "Tool",
                "kind": kind,
            },
        },
    })
    terminal = mapper.map_session_update({
        "method": "session/update",
        "params": {
            "sessionId": "panel-refresh",
            "update": {
                "sessionUpdate": "tool_call_update",
                "toolCallId": "panel-tool",
                "status": status,
            },
        },
    })

    ChatPanel._on_activity_chunk(panel, first)
    assert panel.files_maybe_changed.events == []
    ChatPanel._on_activity_chunk(panel, terminal)

    assert len(panel.files_maybe_changed.events) == expected_signals


def test_files_maybe_changed_uses_existing_git_debounce():
    app = QApplication.instance() or QApplication([])

    class TabsStub(QObject):
        files_maybe_changed = Signal()

    class GitControllerStub(QObject):
        schedule_refresh = GitStatusController.schedule_refresh

        def __init__(self):
            super().__init__()
            self.refresh_calls = 0
            self._debounce = QTimer(self)
            self._debounce.setSingleShot(True)
            self._debounce.setInterval(GitStatusController.REFRESH_DEBOUNCE_MS)
            self._debounce.timeout.connect(self.refresh)

        def refresh(self):
            self.refresh_calls += 1

    tabs = TabsStub()
    controller = GitControllerStub()
    tabs.files_maybe_changed.connect(controller.schedule_refresh)

    for _ in range(3):
        tabs.files_maybe_changed.emit()

    assert controller._debounce.interval() == 300
    assert controller._debounce.isActive()
    assert controller.refresh_calls == 0
    QTest.qWait(350)
    assert controller.refresh_calls == 1
    app.processEvents()
