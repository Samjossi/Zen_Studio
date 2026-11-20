"""DSH 会话日志读取：generation 选择、zstd 分支、坏行与用量差量。

纯文件夹具，不启动 dsh；zstd 命令以 monkeypatch 替身覆盖成功/失败/
缺失三条路径，避免依赖开发机是否安装 zstd。临时目录落在项目 `.temp/`。
"""
import json
import logging
import shutil
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from ide.llm.providers.dsh import session_log

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SESSION_ID = "sid"


@pytest.fixture
def workdir():
    root = PROJECT_ROOT / ".temp" / f"pytest-s4-dsh-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _layout(workdir: Path, files: dict[str, str | bytes],
            session_id: str = SESSION_ID) -> Path:
    session = (workdir / "home" / "sessions" / "--proj--" / session_id)
    session.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        path = session / name
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            path.write_text(data, encoding="utf-8")
    return session


def _point_home(monkeypatch, workdir: Path) -> None:
    monkeypatch.setenv("DSH_HOME", str(workdir / "home"))


def _usage_event(input_tokens: int = 1, output_tokens: int = 2,
                 cache_read: int | None = None,
                 reasoning: int | None = None) -> dict:
    usage = {"inputTokens": input_tokens, "outputTokens": output_tokens}
    if cache_read is not None:
        usage["cacheReadTokens"] = cache_read
    if reasoning is not None:
        usage["reasoningTokens"] = reasoning
    return {"type": "assistant/message", "data": {"usage": usage}}


def _jsonl(*records: dict) -> str:
    return "\n".join(json.dumps(r) for r in records) + "\n"


# ----------------------------------------------------------------------
# 定位与 generation 选择
# ----------------------------------------------------------------------
def test_session_log_path_selects_highest_generation(workdir, monkeypatch):
    session = _layout(workdir, {
        "session.jsonl": "",
        "session.v1.jsonl": "",
        "session.v2.jsonl.zstd": b"",
        "session.v3.jsonl": "",
        "session.v9.jsonl.bak": "",
        "other.jsonl": "",
    })
    _point_home(monkeypatch, workdir)
    assert session_log.session_log_path(SESSION_ID) == session / "session.v3.jsonl"


def test_session_log_path_prefers_plain_text_for_same_generation(
        workdir, monkeypatch):
    session = _layout(workdir, {
        "session.v2.jsonl": "",
        "session.v2.jsonl.zstd": b"",
    })
    _point_home(monkeypatch, workdir)
    assert session_log.session_log_path(SESSION_ID) == session / "session.v2.jsonl"


def test_session_log_path_returns_none_when_missing(workdir, monkeypatch):
    _point_home(monkeypatch, workdir)
    assert session_log.session_log_path(SESSION_ID) is None
    assert session_log.session_log_path("") is None


def test_session_log_path_uses_home_default(workdir, monkeypatch):
    monkeypatch.delenv("DSH_HOME", raising=False)
    monkeypatch.setattr(session_log.Path, "home",
                        lambda: workdir / "home")
    assert session_log.dsh_home() == workdir / "home" / ".dsh"


# ----------------------------------------------------------------------
# 记录读取与 zstd 分支
# ----------------------------------------------------------------------
def test_read_session_records_skips_blank_bad_and_truncated_lines(workdir):
    path = workdir / "session.jsonl"
    path.write_text(
        '{"ok": 1}\n\nnot-json\n{"ok": 2}\n{"truncated": ',
        encoding="utf-8")
    assert session_log.read_session_records(path) == [
        {"ok": 1}, {"ok": 2}]


def test_read_session_records_missing_file_returns_none(workdir):
    assert session_log.read_session_records(workdir / "absent.jsonl") is None


def test_read_session_records_zstd_missing_warns_and_skips(
        workdir, monkeypatch, caplog):
    path = workdir / "session.v1.jsonl.zstd"
    path.write_bytes(b"compressed")
    monkeypatch.setattr(session_log.shutil, "which", lambda _name: None)
    with caplog.at_level(logging.WARNING, logger=session_log.__name__):
        assert session_log.read_session_records(path) is None
    assert "dsh 用量统计需 zstd 解压会话日志，系统未安装，跳过采集" \
        in caplog.text


def test_read_session_records_zstd_decompresses(workdir, monkeypatch):
    path = workdir / "session.v1.jsonl.zstd"
    path.write_bytes(b"compressed")
    calls = []

    def fake_run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=b'{"ok": 1}\n{"ok": 2}\n')

    monkeypatch.setattr(session_log.shutil, "which", lambda _name: "/bin/zstd")
    monkeypatch.setattr(session_log.subprocess, "run", fake_run)
    assert session_log.read_session_records(path) == [{"ok": 1}, {"ok": 2}]
    assert calls == [
        (["zstd", "-dc", str(path)],
         {"capture_output": True, "timeout": 15})]


def test_read_session_records_zstd_failure_returns_none(workdir, monkeypatch):
    path = workdir / "session.v1.jsonl.zstd"
    path.write_bytes(b"bad")
    monkeypatch.setattr(session_log.shutil, "which", lambda _name: "/bin/zstd")
    monkeypatch.setattr(
        session_log.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=2, stdout=b""))
    assert session_log.read_session_records(path) is None


def test_read_session_records_zstd_timeout_returns_none(workdir, monkeypatch):
    path = workdir / "session.v1.jsonl.zstd"
    path.write_bytes(b"bad")
    monkeypatch.setattr(session_log.shutil, "which", lambda _name: "/bin/zstd")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(["zstd", "-dc"], 15)

    monkeypatch.setattr(session_log.subprocess, "run", timeout)
    assert session_log.read_session_records(path) is None


# ----------------------------------------------------------------------
# usage 事件筛选
# ----------------------------------------------------------------------
def test_usage_events_filters_missing_and_invalid_fields():
    records = [
        {"type": "user/message", "data": {"usage": {"inputTokens": 1}}},
        {"type": "assistant/message"},
        {"type": "assistant/message", "data": {}},
        {"type": "assistant/message", "data": {"usage": None}},
        {"type": "assistant/message", "data": {"usage": {}}},
        {"type": "assistant/message",
         "data": {"usage": {"inputTokens": 1, "outputTokens": 2,
                            "cacheReadTokens": -1}}},
        {"type": "assistant/message",
         "data": {"usage": {"inputTokens": True, "outputTokens": 2}}},
        {"type": "assistant/message",
         "data": {"usage": {"inputTokens": "1", "outputTokens": 2}}},
        _usage_event(3, 4),
        _usage_event(5, 6, cache_read=7, reasoning=99),
    ]
    events = session_log.usage_events(records)
    assert events == records[-2:]


# ----------------------------------------------------------------------
# 轮次消耗差量
# ----------------------------------------------------------------------
def test_turn_consumption_sums_and_advances_baseline(workdir, monkeypatch):
    _layout(workdir, {"session.v1.jsonl": _jsonl(
        _usage_event(1, 10, cache_read=2, reasoning=5),
        _usage_event(3, 20, cache_read=4),
        _usage_event(5, 30),
    )})
    _point_home(monkeypatch, workdir)

    consumption, count = session_log.turn_consumption(SESSION_ID, 0)
    assert count == 3
    assert consumption is not None
    assert (consumption.input_other, consumption.input_cache_read,
            consumption.input_cache_creation, consumption.output) == (9, 6, 0, 60)
    assert consumption.turn_id == "sid:3", "reasoningTokens 是输出子集，不重复计入"

    assert session_log.turn_consumption(SESSION_ID, count) == (None, 3)


def test_turn_consumption_only_counts_events_after_baseline(
        workdir, monkeypatch):
    session = _layout(workdir, {"session.jsonl": _jsonl(
        _usage_event(1, 10), _usage_event(2, 20))})
    _point_home(monkeypatch, workdir)
    assert session_log.turn_consumption(SESSION_ID, 2) == (None, 2)

    with (session / "session.jsonl").open("a", encoding="utf-8") as f:
        f.write(_jsonl(_usage_event(3, 30)))
    consumption, count = session_log.turn_consumption(SESSION_ID, 2)
    assert count == 3 and consumption is not None
    assert (consumption.input_other, consumption.output) == (3, 30)
    assert consumption.turn_id == "sid:3"


def test_turn_consumption_zero_total_advances_without_payload(
        workdir, monkeypatch):
    _layout(workdir, {"session.jsonl": _jsonl(
        _usage_event(1, 10), _usage_event(0, 0, cache_read=0))})
    _point_home(monkeypatch, workdir)
    consumption, count = session_log.turn_consumption(SESSION_ID, 1)
    assert consumption is None and count == 2


def test_turn_consumption_failure_paths_keep_baseline(workdir, monkeypatch):
    _point_home(monkeypatch, workdir)
    assert session_log.turn_consumption(None, 7) == (None, 7)
    assert session_log.turn_consumption(SESSION_ID, 7) == (None, 7)

    _layout(workdir, {"session.jsonl": "not-json\n"})
    assert session_log.turn_consumption(SESSION_ID, 7) == (None, 0), \
        "坏日志读到空事件集时按 0 条推进（与迁移前一致）"


def test_turn_consumption_selects_latest_generation(workdir, monkeypatch):
    _layout(workdir, {
        "session.v1.jsonl": _jsonl(_usage_event(1, 10)),
        "session.v2.jsonl": _jsonl(_usage_event(2, 20), _usage_event(3, 30)),
    })
    _point_home(monkeypatch, workdir)
    consumption, count = session_log.turn_consumption(SESSION_ID, 0)
    assert count == 2 and consumption is not None
    assert (consumption.input_other, consumption.output) == (5, 50)
    assert consumption.turn_id == "sid:2"


def test_turn_consumption_zstd_missing_skips_without_raising(
        workdir, monkeypatch, caplog):
    _layout(workdir, {"session.v1.jsonl.zstd": b"compressed"})
    _point_home(monkeypatch, workdir)
    monkeypatch.setattr(session_log.shutil, "which", lambda _name: None)
    with caplog.at_level(logging.WARNING, logger=session_log.__name__):
        assert session_log.turn_consumption(SESSION_ID, 4) == (None, 4)
    assert "系统未安装，跳过采集" in caplog.text
