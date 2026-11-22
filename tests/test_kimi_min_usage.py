"""Kimi 极简链路只读轮内用量：纯文件、节流、隔离与 GUI 入口。"""
import ast
import json
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path

import pytest

from ide.gui.panels.chat.panel import ChatPanel, _compose_usage_tooltip
from ide.llm.base import UsageStats
from ide.llm.providers.kimi import usage_poll


@pytest.fixture
def wire_dir():
    base = Path(".temp") / f"kimi_min_usage_{uuid.uuid4().hex}"
    base.mkdir(parents=True)
    try:
        yield base
    finally:
        shutil.rmtree(base, ignore_errors=True)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def advance(self, seconds=4.0):
        self.now += seconds


def request(*, model="k3", **extra):
    return {"type": "llm.request", "turnStep": "step", "modelAlias": model, **extra}


def usage(*, other=1, cache_read=2, cache_creation=3, output=4, scope="turn"):
    return {
        "type": "usage.record", "usageScope": scope,
        "usage": {
            "inputOther": other, "inputCacheRead": cache_read,
            "inputCacheCreation": cache_creation, "output": output,
        },
    }


def measured(tokens):
    return {"type": "token_counting.measured", "tokens": tokens}


def write_records(session_dir: Path, records, *, append=False):
    path = usage_poll.wire_path(session_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)
             for r in records]
    with path.open("a" if append else "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    return path


def make_poller(session_dir, clock=None):
    clock = clock or Clock()
    poller = usage_poll.UsagePoller(
        context_sizes_source=lambda: {"k3": 1000, "other": 2000},
        session_dir_lookup=lambda session_id: session_dir,
        clock=clock,
    )
    poller.ensure_context_sizes()
    return poller, clock


def test_session_index_uses_last_match_and_skips_bad_lines(wire_dir, monkeypatch):
    index = wire_dir / "session_index.jsonl"
    index.write_text("\n".join([
        json.dumps({"sessionId": "s1", "sessionDir": "/old"}),
        "not-json",
        json.dumps({"sessionId": "s2", "sessionDir": "/other"}),
        json.dumps({"sessionId": "s1", "sessionDir": "/new"}),
    ]) + "\n", encoding="utf-8")
    monkeypatch.setattr(usage_poll, "session_index_path", lambda: index)

    assert usage_poll.session_dir_of("s1") == Path("/new")
    assert usage_poll.session_dir_of("missing") is None
    assert usage_poll.session_dir_of("s1") == Path("/new")
    index.unlink()
    assert usage_poll.session_dir_of("s1") is None


def test_incremental_record_measured_priority_and_model_alias(wire_dir):
    write_records(wire_dir, [request(), usage()])
    poller, clock = make_poller(wire_dir)

    first = poller.poll("sid", "fallback")
    assert first == UsageStats(10, 1000, None, "transcript")
    assert poller.poll("sid", "fallback") is first

    write_records(wire_dir, [usage(output=9)], append=True)
    clock.advance()
    assert poller.poll("sid", "fallback").used == 15

    write_records(wire_dir, [request(model="other"), measured(500)], append=True)
    clock.advance()
    assert poller.poll("sid", "fallback") == UsageStats(500, 2000, None, "transcript")

    write_records(wire_dir, [usage(output=20)], append=True)
    clock.advance()
    assert poller.poll("sid", "fallback").used == 500


@pytest.mark.parametrize("binding", [
    {"type": "profile.bind", "modelAlias": "k3"},
    {"type": "context.append_loop_event"},
], ids=["profile-bind", "usage-record"])
def test_cli_272_profile_and_usage_records_supply_model_alias(wire_dir, binding):
    write_records(wire_dir, [
        binding,
        {"type": "usage.record", "usageScope": "turn", "model": "k3",
         "usage": usage()["usage"]},
        measured(500),
    ])
    poller, _clock = make_poller(wire_dir)

    stats = poller.poll("sid", None)

    assert stats == UsageStats(500, 1000, None, "transcript")


def test_empty_missing_bad_lines_and_fields_preserve_last_valid(wire_dir):
    poller, clock = make_poller(wire_dir)
    assert poller.poll("sid", "k3") is None

    path = write_records(wire_dir, [])
    clock.advance()
    assert poller.poll("sid", "k3") is None

    write_records(wire_dir, [request(), usage(output=9)])
    clock.advance()
    valid = poller.poll("sid", "k3")
    assert valid.used == 15

    write_records(wire_dir, [
        "not-json", "[]", measured(True), measured(-1),
        usage(other=True), usage(other=-1), usage(scope="session"),
        {"type": "usage.record", "usageScope": "turn", "usage": {}},
    ], append=True)
    clock.advance()
    assert poller.poll("sid", "k3") is valid

    path.unlink()
    clock.advance()
    assert poller.poll("sid", "k3") is valid


def test_partial_line_is_buffered_until_append(wire_dir):
    path = write_records(wire_dir, [request()])
    poller, clock = make_poller(wire_dir)
    with path.open("a", encoding="utf-8") as stream:
        stream.write('{"type":"token_counting.measured","tokens":')
    clock.advance()
    assert poller.poll("sid", "k3") is None

    with path.open("a", encoding="utf-8") as stream:
        stream.write('321}\n')
    clock.advance()
    stats = poller.poll("sid", "k3")
    assert stats is not None and stats.used == 321


def test_tail_window_large_tool_output_and_truncation(wire_dir, monkeypatch):
    monkeypatch.setattr(usage_poll, "WIRE_TAIL_BYTES", 128)
    write_records(wire_dir, [request(pad="x" * 400), measured(500)])
    poller, clock = make_poller(wire_dir)
    assert poller.poll("sid", "k3").used == 500

    write_records(wire_dir, [
        {"type": "tool.result", "payload": "x" * 70_000}, measured(777),
    ], append=True)
    clock.advance()
    assert poller.poll("sid", "k3").used == 777

    monkeypatch.setattr(usage_poll, "WIRE_TAIL_BYTES", 64 * 1024)
    write_records(wire_dir, [request(), usage(output=9)])
    clock.advance()
    stats = poller.poll("sid", "k3")
    assert stats is not None and stats.used == 15


def test_four_second_throttle_and_lifecycle_force_immediate_sample(wire_dir, monkeypatch):
    write_records(wire_dir, [request(), measured(100)])
    poller, clock = make_poller(wire_dir)
    original = usage_poll.read_usage_incremental
    calls = []

    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(usage_poll, "read_usage_incremental", counted)
    assert poller.poll("sid", "k3").used == 100
    write_records(wire_dir, [measured(200)], append=True)
    clock.now = 2.0
    assert poller.poll("sid", "k3").used == 100
    assert len(calls) == 1

    clock.now = 4.0
    assert poller.poll("sid", "k3").used == 200
    assert len(calls) == 2

    poller.reset()
    clock.now = 5.0
    assert poller.poll("sid", "k3").used == 200
    assert len(calls) == 3

    poller.invalidate_model()
    clock.now = 6.0
    assert poller.poll("sid", "k3").used == 200
    assert len(calls) == 4


def test_session_directory_failure_backoff_and_success_cache(wire_dir):
    clock = Clock()
    lookups = []

    def lookup(session_id):
        lookups.append(clock.now)
        return wire_dir if len(lookups) >= 3 else None

    poller = usage_poll.UsagePoller(
        context_sizes_source=lambda: {"k3": 1000},
        session_dir_lookup=lookup, clock=clock)
    poller.ensure_context_sizes()
    for now, expected_calls in ((0, 1), (2, 1), (4, 2), (8, 2), (12, 3), (16, 3)):
        clock.now = float(now)
        assert poller.poll("sid", "k3") is None
        assert len(lookups) == expected_calls

    poller.reset()
    clock.now = 17.0
    poller.poll("sid", "k3")
    assert len(lookups) == 4


def test_context_catalog_retries_only_until_nonempty(wire_dir):
    payloads = [{}, {"k3": 1000}]
    calls = []

    def source():
        calls.append(1)
        return payloads.pop(0)

    poller = usage_poll.UsagePoller(
        context_sizes_source=source, session_dir_lookup=lambda session_id: wire_dir)
    poller.ensure_context_sizes()
    poller.ensure_context_sizes()
    poller.ensure_context_sizes()
    assert len(calls) == 2
    assert poller.context_sizes == {"k3": 1000}


def test_unknown_window_never_fabricates_percentage(wire_dir):
    write_records(wire_dir, [request(model="unknown"), measured(100)])
    poller = usage_poll.UsagePoller(
        context_sizes_source=lambda: {"k3": 1000},
        session_dir_lookup=lambda session_id: wire_dir)
    poller.ensure_context_sizes()
    assert poller.poll("sid", "unknown") is None


def test_sessions_have_independent_cursors_and_watermarks(wire_dir):
    first_dir = wire_dir / "first"
    second_dir = wire_dir / "second"
    write_records(first_dir, [request(), measured(100)])
    write_records(second_dir, [request(), measured(800)])
    poller, clock = make_poller(first_dir)
    poller._session_dir_lookup = lambda session_id: (
        first_dir if session_id == "first" else second_dir)

    assert poller.poll("first", "k3").used == 100
    assert poller.poll("second", "k3").used == 800
    clock.advance()
    assert poller.poll("first", "k3").used == 100


def test_concurrent_incremental_reads_and_writes(wire_dir):
    write_records(wire_dir, [request()])
    poller, clock = make_poller(wire_dir)
    stop = threading.Event()
    errors = []

    def poll_loop():
        try:
            while not stop.is_set():
                stats = poller.poll("sid", "k3")
                if stats is not None:
                    assert stats.size == 1000
        except Exception as exc:  # noqa: BLE001 - 带回主线程断言
            errors.append(exc)

    thread = threading.Thread(target=poll_loop)
    thread.start()
    try:
        for index in range(100):
            clock.advance()
            write_records(wire_dir, [usage(output=index)], append=True)
    finally:
        stop.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert errors == []
    clock.advance()
    final = poller.poll("sid", "k3")
    assert final is not None and final.used == 105


def test_usage_poll_module_has_no_acp_qt_or_subprocess_dependency():
    path = Path("src/ide/llm/providers/kimi/usage_poll.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    assert not any(
        name == "subprocess" or name.startswith("PySide")
        or ".acp" in name for name in imports)


class PollProviderStub:
    def __init__(self, values):
        self.values = iter(values)

    def poll_usage(self):
        return next(self.values)


class UsagePanelHarness:
    def __init__(self, values):
        self._llm_name = "kimi-acp"
        self._providers = {"kimi-acp": PollProviderStub(values)}
        self._usage = None
        self._usage_grace_deadline = None
        self._busy = True
        self._sync_active = False
        self._history = [{"role": "user", "content": "unchanged"}]
        self._turn_consumption = None
        self.refreshes = 0

    def _refresh_usage_label(self):
        self.refreshes += 1


def test_existing_gui_tick_applies_stats_and_preserves_on_empty():
    stats = UsageStats(420, 1000, None, "transcript")
    panel = UsagePanelHarness([None, stats, None])
    history = list(panel._history)

    for _ in range(3):
        ChatPanel._poll_usage_tick(panel)

    assert panel._usage is stats
    assert panel.refreshes == 1
    assert panel._history == history
    assert panel._turn_consumption is None
    tooltip = _compose_usage_tooltip(
        stats, datetime.now(), None, None, None, None, datetime.now())
    assert "42%" in tooltip
    assert "数据来源：agent 会话记录" in tooltip
