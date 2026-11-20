import pytest

from ide.gui.panels.chat.panel import ChatPanel


class SignalStub:
    def __init__(self):
        self.events = []

    def emit(self, *args):
        self.events.append(args)


class OutputStub:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


class TimelineStub:
    def end_turn(self):
        pass


class TimerStub:
    def stop(self):
        pass


class InputStub:
    def toPlainText(self):
        return "draft"


class PanelHarness:
    def __init__(self):
        self._llm_name = "kimi-acp"
        self._worker = object()
        self._last_chunk_at = 0.0
        self.stop_requests = []
        self._turn_started_at = None
        self._stream_buffer = "partial"
        self._history = [{"role": "user", "content": "hello"}]
        self._sent_attachments = []
        self._sent_text = "hello"
        self._has_seen_reasoning = False
        self._providers = {}
        self._pending_send = True
        self._stop_reason = "user"
        self.output = OutputStub()
        self.timeline = TimelineStub()
        self._stall_timer = TimerStub()
        self.input = InputStub()
        self.turn_finished = SignalStub()
        self.turn_completed = SignalStub()
        self.pending_fires = 0
        self.busy = True

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

    def _set_busy(self, busy):
        self.busy = busy

    def _fire_pending_send(self):
        self.pending_fires += 1
        self._pending_send = False
        return True


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
    assert completed.pending_fires == 1

    failed = PanelHarness()
    ChatPanel._on_finished(failed, "request failed")
    assert failed.pending_fires == 0
    assert failed._pending_send is False

    stopped = PanelHarness()
    ChatPanel._on_stopped(stopped, "user")
    assert stopped.pending_fires == 0
    assert stopped._pending_send is False
