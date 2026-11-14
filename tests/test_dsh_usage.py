"""DSH 轮次真实消耗：会话落盘日志差量求和回归测试（2026-1114-1452 计划）。

fixture 格式依据 `参考代码/deepseek-harness/snapshots/sdk/` 快照：
assistant/message 事件 data.usage 携带 inputTokens/outputTokens/
cacheReadTokens/reasoningTokens（输出子集，不重复计入）。
"""
import json
import shutil
import subprocess

import pytest

from ide.llm.providers import dsh_acp
from ide.llm.providers.dsh_acp import _session_log_path, _turn_consumption

SID = "sess-1"


def _event(input_tokens=10, output_tokens=4, cache_read=2, reasoning=1):
    usage = {"inputTokens": input_tokens, "outputTokens": output_tokens,
             "cacheReadTokens": cache_read, "reasoningTokens": reasoning}
    return {"type": "assistant/message",
            "data": {"turn": 1, "step": 1, "usage": usage}}


def _write_session(tmp_path, records, session_id=SID, filename="session.v4.jsonl"):
    log_dir = tmp_path / "sessions" / "--proj--" / session_id
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / filename
    path.write_text(
        "".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _fake_home(monkeypatch, tmp_path):
    monkeypatch.setattr(dsh_acp, "_dsh_home", lambda: tmp_path)


def test_turn_consumption_sums_all_fields(tmp_path):
    _write_session(tmp_path, [_event(), _event(20, 8, 4, 2)])
    consumption, count = _turn_consumption(SID, 0)
    assert count == 2
    assert consumption is not None
    assert consumption.input_other == 30
    assert consumption.input_cache_read == 6
    assert consumption.input_cache_creation == 0
    assert consumption.output == 12  # reasoningTokens 不重复计入
    assert consumption.turn_id == f"{SID}:2"


def test_baseline_counts_only_new_events(tmp_path):
    _write_session(tmp_path, [_event(), _event(20, 8, 4, 2)])
    consumption, count = _turn_consumption(SID, 1)
    assert count == 2
    assert consumption is not None
    assert consumption.input_other == 20
    assert consumption.output == 8


def test_no_new_events_returns_none(tmp_path):
    _write_session(tmp_path, [_event()])
    consumption, count = _turn_consumption(SID, 1)
    assert consumption is None
    assert count == 1


def test_missing_session_is_silent(tmp_path):
    assert _turn_consumption("no-such-session", 0) == (None, 0)
    assert _turn_consumption(None, 3) == (None, 3)


def test_non_usage_events_and_bad_lines_ignored(tmp_path):
    path = _write_session(tmp_path, [
        {"type": "user/message", "data": {}},
        {"type": "assistant/message", "data": {"turn": 1}},  # 无 usage
        _event(),
    ])
    with open(path, "a", encoding="utf-8") as f:
        f.write("{torn tail\n")
    consumption, count = _turn_consumption(SID, 0)
    assert count == 1
    assert consumption is not None and consumption.total == 16


def test_malformed_usage_event_skipped(tmp_path):
    bad = _event()
    bad["data"]["usage"]["inputTokens"] = "10"
    _write_session(tmp_path, [bad, _event()])
    consumption, count = _turn_consumption(SID, 0)
    assert count == 1
    assert consumption is not None and consumption.input_other == 10


def test_highest_generation_and_raw_preferred(tmp_path):
    _write_session(tmp_path, [_event(1, 1, 0)], filename="session.v3.jsonl")
    _write_session(tmp_path, [_event(5, 5, 0)], filename="session.v4.jsonl")
    # 同代 .zstd 与裸文本并存：优先裸文本（内容故意不同以区分）
    _write_session(tmp_path, [_event(9, 9, 9)], filename="session.v4.jsonl.zstd")
    path = _session_log_path(SID)
    assert path is not None and path.name == "session.v4.jsonl"
    consumption, _ = _turn_consumption(SID, 0)
    assert consumption is not None and consumption.input_other == 5


def test_v0_plain_filename_recognized(tmp_path):
    _write_session(tmp_path, [_event()], filename="session.jsonl")
    assert _session_log_path(SID) is not None


@pytest.mark.skipif(shutil.which("zstd") is None, reason="系统无 zstd 命令")
def test_zstd_log_read_via_system_cli(tmp_path):
    raw = _write_session(tmp_path, [_event()], filename="session.v4.jsonl")
    # 显式指定输出名：zstd 默认后缀是 .zst，DSH 的规范后缀是 .zstd
    subprocess.run(["zstd", "-q", "-f", str(raw), "-o",
                    str(raw) + ".zstd"], check=True)
    raw.unlink()  # 只留 session.v4.jsonl.zstd
    consumption, count = _turn_consumption(SID, 0)
    assert count == 1
    assert consumption is not None and consumption.total == 16
