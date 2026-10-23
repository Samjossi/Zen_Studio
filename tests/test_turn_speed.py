"""kimi wire.jsonl 轮次输出速度：`_read_wire_turn_speed` 纯函数用例。

合成 wire.jsonl（tmp_path，不落真实会话目录），覆盖配对/求和/降级
（2026-1023-1632 计划）：usage.output 单次调用值直取不差分、生成耗时
= 配对 usage.record.time − llm.request.time、基线截断、残缺条目跳过。
"""
import json

from llm.providers.kimi_acp import _read_wire_turn_speed


def _write_wire(tmp_path, records: list[dict]):
    session_dir = tmp_path / "session_x"
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    wire.parent.mkdir(parents=True)
    wire.write_text(
        "".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return session_dir


def _request(t: int) -> dict:
    return {"type": "llm.request", "maxTokens": 262144, "time": t}


def _record(t: int, output: int) -> dict:
    return {
        "type": "usage.record",
        "usageScope": "turn",
        "usage": {"inputOther": 100, "output": output,
                  "inputCacheRead": 200, "inputCacheCreation": 0},
        "time": t,
    }


def test_single_call_pairing(tmp_path):
    session_dir = _write_wire(tmp_path, [_request(1000), _record(6000, 277)])
    speed = _read_wire_turn_speed(session_dir, 0)
    assert speed is not None
    assert speed.output_tokens == 277
    assert speed.gen_seconds == 5.0


def test_multi_call_sums_without_diffing(tmp_path):
    # output 非单调（单次调用值）：直取求和 277+201+326，禁止差分
    session_dir = _write_wire(tmp_path, [
        _request(1000), _record(6000, 277),
        _request(7000), _record(15000, 201),
        _request(16000), _record(29000, 326),
    ])
    speed = _read_wire_turn_speed(session_dir, 0)
    assert speed is not None
    assert speed.output_tokens == 804
    assert speed.gen_seconds == 5.0 + 8.0 + 13.0


def test_tool_gap_excluded(tmp_path):
    # 工具执行/审批等待在两次 request 之间：record(6000)→request(20000)
    # 的 14s 缝隙不得计入生成时间
    session_dir = _write_wire(tmp_path, [
        _request(1000), _record(6000, 100),
        _request(20000), _record(25000, 100),
    ])
    speed = _read_wire_turn_speed(session_dir, 0)
    assert speed is not None
    assert speed.gen_seconds == 10.0


def test_baseline_excludes_prior_turn(tmp_path):
    session_dir = _write_wire(tmp_path, [
        _request(1000), _record(6000, 999),   # 上一轮（基线内）
        _request(7000), _record(10000, 50),   # 本轮
    ])
    speed = _read_wire_turn_speed(session_dir, 1)
    assert speed is not None
    assert speed.output_tokens == 50
    assert speed.gen_seconds == 3.0


def test_missing_time_or_output_skipped(tmp_path):
    session_dir = _write_wire(tmp_path, [
        _request(1000),
        {"type": "usage.record", "usageScope": "turn",
         "usage": {"output": 100}},                      # 缺 time
        _request(2000),
        {"type": "usage.record", "usageScope": "turn",
         "usage": {"inputOther": 1}, "time": 5000},      # 缺 output
        _request(6000), _record(9000, 42),               # 正常一对
    ])
    speed = _read_wire_turn_speed(session_dir, 0)
    assert speed is not None
    assert speed.output_tokens == 42
    assert speed.gen_seconds == 3.0


def test_orphan_record_without_request_skipped(tmp_path):
    session_dir = _write_wire(tmp_path, [
        _record(5000, 100),                   # 无前置 llm.request
        _request(6000), _record(8000, 30),
    ])
    speed = _read_wire_turn_speed(session_dir, 0)
    assert speed is not None
    assert speed.output_tokens == 30
    assert speed.gen_seconds == 2.0


def test_non_positive_duration_skipped(tmp_path):
    session_dir = _write_wire(tmp_path, [
        _request(5000), _record(5000, 100),   # 耗时为 0
        _request(6000), _record(4000, 100),   # 耗时为负（时钟漂移）
    ])
    assert _read_wire_turn_speed(session_dir, 0) is None


def test_all_invalid_returns_none(tmp_path):
    session_dir = _write_wire(tmp_path, [
        {"type": "usage.record", "usageScope": "turn", "usage": {}},
    ])
    assert _read_wire_turn_speed(session_dir, 0) is None


def test_missing_file_returns_none(tmp_path):
    assert _read_wire_turn_speed(tmp_path / "no_such_dir", 0) is None


def test_baseline_beyond_records_returns_none(tmp_path):
    session_dir = _write_wire(tmp_path, [_request(1000), _record(6000, 100)])
    assert _read_wire_turn_speed(session_dir, 5) is None
