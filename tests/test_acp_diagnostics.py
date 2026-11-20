"""S6：ACP 连接诊断的脱敏、有界 stderr ring 与诊断日志格式。

这些用例锁定搬家前的行为：脱敏规则、40 行/8KB 双限、单行截断标记、
空 stderr 不出尾部日志。ring 是每连接实例状态，不共享全局缓冲。
"""
import logging

import pytest

from ide.llm.providers.acp import diagnostics
from ide.llm.providers.acp.diagnostics import (
    StderrRing,
    log_connection_diagnostics,
    redact_diagnostic,
)


# ----------------------------------------------------------------------
# 脱敏
# ----------------------------------------------------------------------
@pytest.mark.parametrize("secret", [
    "sk-live-abc123",
    "token-value-123",
    "correct-horse-battery-staple",
])
def test_redacts_bearer_and_sensitive_values(secret):
    text = redact_diagnostic(f"Authorization: Bearer {secret}")
    assert secret not in text
    assert "<redacted>" in text


def test_redacts_key_value_credentials_case_insensitively():
    text = redact_diagnostic(
        'API_KEY="sk-secret" Cookie: session=abc123 Password=hunter2')
    assert "sk-secret" not in text
    assert "abc123" not in text
    assert "hunter2" not in text
    assert text.count("<redacted>") >= 3


def test_redacts_jwt_without_touching_normal_text():
    jwt = ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0."
           "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U")
    text = redact_diagnostic(f"frame jwt={jwt} done")
    assert jwt not in text
    assert "jwt=<redacted> done" in text
    assert redact_diagnostic("plain stderr line") == "plain stderr line"


def test_ring_redacts_before_buffering():
    ring = StderrRing()
    ring.append("Authorization: Bearer sk-hidden\n")
    assert "sk-hidden" not in ring.tail()
    assert "<redacted>" in ring.tail()


# ----------------------------------------------------------------------
# 有界 ring
# ----------------------------------------------------------------------
def test_empty_ring_has_empty_tail():
    assert StderrRing().tail() == ""


def test_ring_strips_line_endings_and_keeps_order():
    ring = StderrRing()
    ring.append("first\n")
    ring.append("second\r\n")
    assert ring.tail() == "first\nsecond"


def test_ring_keeps_latest_lines_when_line_capacity_exceeded():
    ring = StderrRing()
    lines = [f"line-{i:02d}" for i in range(45)]
    for line in lines:
        ring.append(line)
    tail = ring.tail()
    content = tail.split("\n", 1)[1]
    assert tail.startswith("[stderr 已截断]\n")
    assert content.splitlines() == lines[-40:]


def test_ring_keeps_latest_lines_when_byte_capacity_exceeded():
    ring = StderrRing()
    for _ in range(40):
        ring.append("x" * 300)
    tail = ring.tail()
    content = tail.split("\n", 1)[1]
    kept = content.splitlines()
    # 每行 300 字节 + 换行；8KB 上限只能留下 27 行（27 × 301 = 8127）
    assert len(kept) == 27
    assert all(line == "x" * 300 for line in kept)


def test_ring_truncates_single_overlong_line():
    ring = StderrRing()
    ring.append("x" * 1200)
    tail = ring.tail()
    content = tail.split("\n", 1)[1]
    assert tail.startswith("[stderr 已截断]\n")
    assert content == "x" * 1000 + "…[单行截断]"


def test_ring_clears_buffer_when_single_line_exceeds_byte_cap(monkeypatch):
    monkeypatch.setattr(diagnostics, "STDERR_LINE_MAX", 10_000)
    monkeypatch.setattr(diagnostics, "STDERR_MAX_BYTES", 50)
    ring = StderrRing()
    ring.append("x" * 60)
    assert ring.tail() == ""
    ring.append("ok")
    assert ring.tail() == "[stderr 已截断]\nok"


def test_ring_counts_utf8_bytes_not_characters(monkeypatch):
    monkeypatch.setattr(diagnostics, "STDERR_MAX_BYTES", 14)
    ring = StderrRing()
    ring.append("中文日志")  # 12 字节 + 换行 = 13，恰好可留
    ring.append("中文")      # 6 字节 + 换行 = 7，总 20 超限 → 丢最旧
    assert ring.tail() == "[stderr 已截断]\n中文"


def test_ring_tolerates_replacement_characters():
    ring = StderrRing()
    ring.append("bad \ufffd byte")
    assert ring.tail() == "bad \ufffd byte"


# ----------------------------------------------------------------------
# 诊断日志
# ----------------------------------------------------------------------
def test_log_diagnostics_keeps_event_format_and_redacted_tail(caplog):
    ring = StderrRing()
    ring.append("Authorization: Bearer sk-hidden")
    with caplog.at_level(
            logging.WARNING, logger="ide.llm.providers.acp.diagnostics"):
        log_connection_diagnostics(
            agent_name="kimi acp",
            event="connection_dead",
            generation=7,
            pid=1234,
            method="session/prompt",
            request_id=9,
            reason="eof",
            exit_code=1,
            stderr_tail=ring.tail(),
        )
    messages = [record.getMessage() for record in caplog.records]
    assert messages[0] == (
        "[kimi acp] 诊断 event=connection_dead generation=7 pid=1234 "
        "method=session/prompt requestId=9 reason=eof exitCode=1")
    assert len(messages) == 2
    assert messages[1].startswith("[kimi acp] stderr 尾部 generation=7")
    assert "sk-hidden" not in messages[1]
    assert "<redacted>" in messages[1]


def test_connection_module_no_longer_owns_diagnostics_implementation():
    from ide.llm.providers.acp import connection

    assert not hasattr(connection, "_StderrRing")
    assert not hasattr(connection, "_redact_diagnostic")


def test_log_diagnostics_skips_tail_line_when_stderr_empty(caplog):
    with caplog.at_level(
            logging.WARNING, logger="ide.llm.providers.acp.diagnostics"):
        log_connection_diagnostics(
            agent_name="test", event="request_timeout", generation=1,
            pid=None, method=None, request_id=None, reason=None,
            exit_code=None, stderr_tail="",
        )
    assert len(caplog.records) == 1
    assert "event=request_timeout" in caplog.records[0].getMessage()
