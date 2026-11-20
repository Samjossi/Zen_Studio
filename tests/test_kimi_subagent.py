"""Kimi 子代理旁路：合成、目录发现、增量读取与线程生命周期。

不启动真实 ACP/agent：wire 记录按实测格式构造，旁路的确定性步骤直接
`_poll()` 驱动；只有生命周期与迟到帧用例启动线程，并用队列超时收敛。
临时目录一律落在项目 `.temp/` 下，避免系统临时目录。
"""
import json
import queue
import shutil
import threading
import uuid
from pathlib import Path

import pytest

from ide.llm.base import Chunk
from ide.llm.providers.kimi import subagent

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PARENT_TID = "0:tool_parent"
WIRE_THREAD_NAME = "kimi-wire-sidecar"


@pytest.fixture
def workdir():
    root = PROJECT_ROOT / ".temp" / f"pytest-s4-{uuid.uuid4().hex}"
    root.mkdir(parents=True)
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _session(workdir: Path) -> Path:
    session = workdir / "session"
    (session / "agents").mkdir(parents=True)
    return session


def _wire(session: Path, name: str = "agent-0") -> Path:
    agent = session / "agents" / name
    agent.mkdir(parents=True, exist_ok=True)
    return agent / "wire.jsonl"


def _loop_line(event: dict) -> str:
    return json.dumps(
        {"type": "context.append_loop_event", "event": event},
        ensure_ascii=False) + "\n"


def _tool_call(tid: str = "wire-1", name: str = "Bash",
               args: dict | None = None) -> dict:
    return {"type": "tool.call", "toolCallId": tid, "name": name,
            "args": {"command": "ls"} if args is None else args}


def _tool_result(tid: str = "wire-1", output: str = "README.md") -> dict:
    return {"type": "tool.result", "toolCallId": tid,
            "result": {"output": output}}


def _drain(out: "queue.Queue[dict]") -> list[dict]:
    updates = []
    while True:
        try:
            updates.append(out.get_nowait())
        except queue.Empty:
            return updates


def _alive_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == WIRE_THREAD_NAME]


# ----------------------------------------------------------------------
# 合成纯函数
# ----------------------------------------------------------------------
def test_wire_tool_kind_mapping():
    assert subagent.wire_tool_kind("Bash") == "execute"
    assert subagent.wire_tool_kind("Edit") == "edit"
    assert subagent.wire_tool_kind("Write") == "edit"
    assert subagent.wire_tool_kind("Grep") == "read"
    assert subagent.wire_tool_kind("WebFetch") == "fetch"
    assert subagent.wire_tool_kind("AskUserQuestion") == "other"


def test_synthesize_wire_call_builds_hierarchical_acp_update():
    update = subagent.synthesize_wire_call(
        {"name": "Read", "toolCallId": "w1", "args": {"path": "a.md"}},
        PARENT_TID)
    assert update == {
        "sessionUpdate": "tool_call",
        "toolCallId": f"{PARENT_TID}/w1",
        "title": "Read",
        "kind": "read",
        "status": "pending",
        "rawInput": {"path": "a.md"},
        "locations": [{"path": "a.md"}],
    }


def test_synthesize_wire_call_edit_adds_diff_content():
    update = subagent.synthesize_wire_call(
        {"name": "Edit", "toolCallId": "w2",
         "args": {"path": "a.py", "old_string": "old", "new_string": "new"}},
        PARENT_TID)
    assert update["content"] == [{
        "type": "diff", "path": "a.py", "oldText": "old", "newText": "new"}]
    # 只给一侧也合成 diff，另一侧补空串（与迁移前一致）
    one_side = subagent.synthesize_wire_call(
        {"name": "Edit", "toolCallId": "w3",
         "args": {"path": "a.py", "new_string": "new"}}, PARENT_TID)
    assert one_side["content"][0]["oldText"] == ""


def test_synthesize_wire_call_rejects_incomplete_events():
    assert subagent.synthesize_wire_call({}, PARENT_TID) is None
    assert subagent.synthesize_wire_call(
        {"name": "Read"}, PARENT_TID) is None
    assert subagent.synthesize_wire_call(
        {"name": "", "toolCallId": "w"}, PARENT_TID) is None
    assert subagent.synthesize_wire_call(
        {"name": "Read", "toolCallId": ""}, PARENT_TID) is None
    # args 非 dict 时按空参数处理，不抛异常
    update = subagent.synthesize_wire_call(
        {"name": "Read", "toolCallId": "w", "args": ["x"]}, PARENT_TID)
    assert update is not None and "rawInput" not in update


def test_synthesize_wire_result_maps_output():
    update = subagent.synthesize_wire_result(
        {"toolCallId": "w1", "result": {"output": "done"}},
        PARENT_TID, "execute")
    assert update == {
        "sessionUpdate": "tool_call_update",
        "toolCallId": f"{PARENT_TID}/w1",
        "status": "completed",
        "kind": "execute",
        "rawOutput": {"output": "done"},
    }


def test_synthesize_wire_result_degrades_without_output():
    update = subagent.synthesize_wire_result(
        {"toolCallId": "w1"}, PARENT_TID, "other")
    assert update is not None and "rawOutput" not in update
    assert subagent.synthesize_wire_result({}, PARENT_TID, "read") is None
    non_text = subagent.synthesize_wire_result(
        {"toolCallId": "w1", "result": {"output": 42}}, PARENT_TID, "read")
    assert "rawOutput" not in non_text


# ----------------------------------------------------------------------
# ACP 帧反哺与目录发现
# ----------------------------------------------------------------------
def test_observe_chunk_registers_agent_and_task_titles(workdir):
    session = _session(workdir)
    sidecar = subagent.WireSidecar(session, queue.Queue())
    subagent.observe_chunk(
        sidecar, Chunk("text", "不是工具调用"))
    assert sidecar._parent_tid is None

    subagent.observe_chunk(sidecar, Chunk(
        "tool_call", "", payload={"title": "Agent", "tool_call_id": "t1"}))
    assert sidecar._parent_tid == "t1"

    # 关联建立前，后续登记会覆盖；建立后忽略（首在途优先）
    subagent.observe_chunk(sidecar, Chunk(
        "tool_call", "", payload={"title": "task", "tool_call_id": "t2"}))
    assert sidecar._parent_tid == "t2"
    sidecar._wire_path = Path("wire.jsonl")
    subagent.observe_chunk(sidecar, Chunk(
        "tool_call", "", payload={"title": "Agent", "tool_call_id": "t3"}))
    assert sidecar._parent_tid == "t2"


def test_observe_chunk_ignores_malformed_payloads(workdir):
    session = _session(workdir)
    sidecar = subagent.WireSidecar(session, queue.Queue())
    for payload in (None, {}, {"title": "Agent"}, {"tool_call_id": "t1"},
                    {"title": 1, "tool_call_id": "t1"}):
        subagent.observe_chunk(sidecar, Chunk("tool_call", "", payload=payload))
    assert sidecar._parent_tid is None


def test_sidecar_discovers_new_agent_and_synthesizes_events(workdir):
    session = _session(workdir)
    preexisting = _wire(session, "agent-old")
    preexisting.write_text(_loop_line(_tool_call("old")), encoding="utf-8")

    out: "queue.Queue[dict]" = queue.Queue()
    sidecar = subagent.WireSidecar(session, out)
    sidecar.note_agent_call(PARENT_TID)
    sidecar._poll()
    assert _drain(out) == [], "构造时已在场的子目录不属本轮新增"

    wire = _wire(session)
    wire.write_text(
        _loop_line(_tool_call("w1", "Bash", {"command": "ls"}))
        + _loop_line(_tool_result("w1", "a.md"))
        + _loop_line({"type": "content.part", "text": "思维链跳过"})
        + json.dumps({"type": "llm.request", "turnStep": "x"}) + "\n",
        encoding="utf-8")
    sidecar._poll()

    updates = _drain(out)
    assert [u["sessionUpdate"] for u in updates] == [
        "tool_call", "tool_call_update"]
    assert updates[0]["toolCallId"] == f"{PARENT_TID}/w1"
    assert updates[0]["kind"] == "execute"
    assert updates[1]["kind"] == "execute"
    assert updates[1]["rawOutput"] == {"output": "a.md"}


def test_sidecar_waits_for_parent_before_discovery(workdir):
    session = _session(workdir)
    out: "queue.Queue[dict]" = queue.Queue()
    sidecar = subagent.WireSidecar(session, out)
    _wire(session).write_text(_loop_line(_tool_call()), encoding="utf-8")
    sidecar._poll()
    assert sidecar._wire_path is None and _drain(out) == []

    sidecar.note_agent_call(PARENT_TID)
    sidecar._poll()
    assert sidecar._wire_path is not None
    assert len(_drain(out)) == 1


def test_sidecar_survives_agents_dir_disappearing(workdir):
    session = _session(workdir)
    out: "queue.Queue[dict]" = queue.Queue()
    sidecar = subagent.WireSidecar(session, out)
    _wire(session).write_text(_loop_line(_tool_call()), encoding="utf-8")
    sidecar.note_agent_call(PARENT_TID)
    sidecar._poll()
    assert len(_drain(out)) == 1

    shutil.rmtree(session)
    sidecar._poll()  # 文件瞬逝：silent retry，不抛异常
    assert _drain(out) == []


# ----------------------------------------------------------------------
# 增量读取、残行与熔断
# ----------------------------------------------------------------------
def test_sidecar_incremental_read_keeps_partial_line(workdir):
    session = _session(workdir)
    out: "queue.Queue[dict]" = queue.Queue()
    sidecar = subagent.WireSidecar(session, out)
    wire = _wire(session)
    wire.write_text(_loop_line(_tool_call("w1")), encoding="utf-8")
    sidecar.note_agent_call(PARENT_TID)
    sidecar._poll()
    assert len(_drain(out)) == 1

    second = _loop_line(_tool_call("w2", "Read", {"path": "b.md"}))
    with wire.open("ab") as f:
        f.write(second[: len(second) // 2].encode("utf-8"))
    sidecar._poll()
    assert _drain(out) == [], "未写完的半行必须留缓冲，不能当残帧消费"

    with wire.open("ab") as f:
        f.write(second[len(second) // 2:].encode("utf-8"))
    sidecar._poll()
    updates = _drain(out)
    assert len(updates) == 1
    assert updates[0]["toolCallId"] == f"{PARENT_TID}/w2"
    assert updates[0]["kind"] == "read"


def test_sidecar_truncated_json_counts_toward_breaker(workdir):
    session = _session(workdir)
    out: "queue.Queue[dict]" = queue.Queue()
    sidecar = subagent.WireSidecar(session, out)
    wire = _wire(session)
    wire.write_text("{not-json\n", encoding="utf-8")
    sidecar.note_agent_call(PARENT_TID)
    sidecar._poll()
    assert sidecar._parse_failures == 1 and sidecar._broken is False

    for _ in range(subagent.SIDECAR_PARSE_FAIL_LIMIT - 1):
        with wire.open("a", encoding="utf-8") as f:
            f.write("{not-json\n")
        sidecar._poll()
    assert sidecar._parse_failures == subagent.SIDECAR_PARSE_FAIL_LIMIT
    assert sidecar._broken is True
    assert _drain(out) == []


def test_sidecar_partial_line_does_not_count_as_parse_failure(workdir):
    session = _session(workdir)
    out: "queue.Queue[dict]" = queue.Queue()
    sidecar = subagent.WireSidecar(session, out)
    wire = _wire(session)
    record = _loop_line(_tool_call())
    wire.write_text(record[:10], encoding="utf-8")
    sidecar.note_agent_call(PARENT_TID)
    sidecar._poll()
    assert sidecar._parse_failures == 0

    with wire.open("a", encoding="utf-8") as f:
        f.write(record[10:])
    sidecar._poll()
    assert sidecar._parse_failures == 0
    assert len(_drain(out)) == 1


# ----------------------------------------------------------------------
# 线程生命周期
# ----------------------------------------------------------------------
def test_sidecar_start_stop_idempotent_and_no_thread_leak(workdir):
    session = _session(workdir)
    out: "queue.Queue[dict]" = queue.Queue()
    sidecar = subagent.WireSidecar(session, out)
    assert not sidecar.is_alive()

    sidecar.start()
    sidecar.start()  # 重复 start 不再开线程
    assert sidecar.is_alive()
    assert len(_alive_threads()) == 1

    sidecar.stop()
    sidecar.stop()  # 重复 stop 安全
    assert not sidecar.is_alive()
    assert _alive_threads() == []
    sidecar.start()  # 单轮生命周期：stop 后不再重启
    assert not sidecar.is_alive()


def test_sidecar_repeated_instances_leave_no_threads(workdir):
    session = _session(workdir)
    for _ in range(10):
        sidecar = subagent.WireSidecar(session, queue.Queue())
        sidecar.start()
        sidecar.stop()
        assert not sidecar.is_alive()
    assert _alive_threads() == []


def test_sidecar_thread_consumes_late_wire_frames(workdir):
    session = _session(workdir)
    out: "queue.Queue[dict]" = queue.Queue()
    sidecar = subagent.WireSidecar(session, out)
    sidecar.start()
    try:
        sidecar.note_agent_call(PARENT_TID)
        wire = _wire(session)  # 线程启动后才出现的子目录
        wire.write_text(_loop_line(_tool_call("late")), encoding="utf-8")
        update = out.get(timeout=3)
    finally:
        sidecar.stop()
    assert update["toolCallId"] == f"{PARENT_TID}/late"
    assert not sidecar.is_alive()
    assert _alive_threads() == []


def test_sidecar_thread_exits_cleanly_on_poll_error(workdir):
    session = _session(workdir)
    sidecar = subagent.WireSidecar(session, queue.Queue())

    def boom() -> None:
        raise RuntimeError("模拟旁路异常")

    sidecar._poll = boom  # type: ignore[method-assign]
    sidecar.start()
    sidecar.join(2)
    assert not sidecar.is_alive()
    sidecar.stop()
    assert _alive_threads() == []
