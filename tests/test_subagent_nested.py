"""子代理活动嵌套显示单元测试（收编自 scripts/test_subagent_nested.py；
0813-1919 计划 T1/T4 验证）。

覆盖：
- T1 层级 toolCallId 解析：reasonix 实测帧（tests/fixtures/reasonix_frames.jsonl
  蓝本，帧存档 subagent_probe_reasonix_20260813_190102.json 同源）驱动
  map_session_update，断言 parent_tool_call_id 拆出与无层级 ID 后端的零影响；
- T4 kimi wire 旁路合成：实测 wire 裁剪样本（tests/fixtures/
  subagent_wire_sample.jsonl）驱动 _synthesize_wire_call/_synthesize_wire_result
  + _WireSidecar 增量解析（目录发现/残行缓冲/熔断），断言合成 Chunk 序列形态。
"""
from __future__ import annotations

import json
import queue
from pathlib import Path

from llm.providers.acp import _split_hierarchical_tid, map_session_update
from llm.providers.kimi_acp import (
    _SIDECAR_PARSE_FAIL_LIMIT,
    _WireSidecar,
    _synthesize_wire_call,
    _synthesize_wire_result,
    _wire_tool_kind,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
REASONIX_FRAMES = FIXTURES / "reasonix_frames.jsonl"
WIRE_SAMPLE = FIXTURES / "subagent_wire_sample.jsonl"

PARENT_TID = "call_00_P14w9pZLRhdaZCaoAny63153"
CHILD_TID = f"{PARENT_TID}/call_00_twRhcebsPqDidwYhwNcc6784"
KIMI_PARENT = "0:tool_sbYPlYDEPKcsL9XI6fidkBfY"


def _map(update: dict) -> object:
    return map_session_update({"params": {"update": update, "sessionId": "test"}})


# ----------------------------------------------------------------------
# T1 层级 ID 解析
# ----------------------------------------------------------------------

def test_split_hierarchical_tid():
    assert _split_hierarchical_tid(CHILD_TID) == PARENT_TID, "首段即父 ID"
    assert _split_hierarchical_tid("call_00_abc") is None, "无 `/` 返回 None"
    assert _split_hierarchical_tid("0:tool_abc") is None, "kimi `0:tool_` 形态无层级"
    assert _split_hierarchical_tid("/child") is None, "空首段不拆"


def test_parent_child_frames():
    parent_call = _map({"sessionUpdate": "tool_call", "toolCallId": PARENT_TID,
                        "title": "task", "kind": "other", "status": "pending",
                        "rawInput": {"description": "探针子代理", "prompt": "读文件"}})
    assert "parent_tool_call_id" not in (parent_call.payload or {}), "父帧无父指针"

    child_call = _map({"sessionUpdate": "tool_call", "toolCallId": CHILD_TID,
                       "title": "ls", "kind": "read", "status": "pending",
                       "rawInput": {"path": "docs"}})
    assert (child_call.payload or {}).get("parent_tool_call_id") == PARENT_TID, "子帧拆出父指针"
    assert (child_call.payload or {}).get("tool_call_id") == CHILD_TID, "子帧 tid 保留全串"

    child_update = _map({"sessionUpdate": "tool_call_update", "toolCallId": CHILD_TID,
                         "status": "completed",
                         "rawOutput": {"output": "a.md\nb.md"}})
    assert (child_update.payload or {}).get("parent_tool_call_id") == PARENT_TID, "子 update 帧拆出父指针"
    assert (child_update.payload or {}).get("output") == "a.md\nb.md", "子 update 输出正文进载荷"

    kimi_agent = _map({"sessionUpdate": "tool_call",
                       "toolCallId": "0:tool_sbYPlYDEPKcsL9XI6fidkBfY",
                       "title": "Agent", "kind": "other", "status": "pending"})
    assert "parent_tool_call_id" not in (kimi_agent.payload or {}), "kimi Agent 帧无父指针"


def test_reasonix_frames_replay():
    """reasonix 实测原始帧整流回归：全量 session/update 帧过映射不抛异常。"""
    nested = 0
    for line in REASONIX_FRAMES.read_text(encoding="utf-8").strip().splitlines():
        frame = (json.loads(line).get("frame") or {})
        if frame.get("method") != "session/update":
            continue
        chunk = map_session_update({"params": frame.get("params") or {}})
        if chunk and chunk.kind in ("tool_call", "tool_call_update") \
                and (chunk.payload or {}).get("parent_tool_call_id"):
            nested += 1
    assert nested == 12, "实测帧整流：嵌套帧 12 张（6 call + 6 update）"


# ----------------------------------------------------------------------
# T4 kimi wire 旁路合成
# ----------------------------------------------------------------------

def test_wire_tool_kind_mapping():
    assert _wire_tool_kind("Bash") == "execute", "kind 映射：Bash→execute"
    assert _wire_tool_kind("Edit") == "edit", "kind 映射：Edit→edit"
    assert _wire_tool_kind("Grep") == "read", "kind 映射：Grep→read"
    assert _wire_tool_kind("AskUserQuestion") == "other", "kind 映射：未收录→other"


def _load_sample_events() -> list[dict]:
    events = []
    for line in WIRE_SAMPLE.read_text(encoding="utf-8").strip().splitlines():
        record = json.loads(line)
        if record.get("type") == "context.append_loop_event":
            events.append(record.get("event") or {})
    return events


def test_wire_synthesis_sequence():
    sample_events = _load_sample_events()
    assert len(sample_events) == 12, "样本 12 条事件"

    kinds: dict[str, str] = {}
    chunks = []
    for event in sample_events:
        if event.get("type") == "tool.call":
            update = _synthesize_wire_call(event, KIMI_PARENT)
            kinds[event["toolCallId"]] = update["kind"]
        else:
            update = _synthesize_wire_result(
                event, KIMI_PARENT, kinds.get(event.get("toolCallId") or "", "other"))
        chunks.append(_map(update))

    assert all(c is not None for c in chunks), "合成 12 帧全部映射出 Chunk"
    assert all((c.payload or {}).get("parent_tool_call_id") == KIMI_PARENT for c in chunks), "合成帧父指针一致"
    assert all((c.payload or {}).get("tool_call_id", "").startswith(KIMI_PARENT + "/")
               for c in chunks), "合成 tid 为 `父/子` 全串"

    by_name: dict[str, list] = {}
    for event, chunk in zip(sample_events[::2], chunks[::2]):  # call 帧
        by_name.setdefault(event.get("name"), []).append(chunk)
    assert by_name["Bash"][0].payload.get("tool_kind") == "execute" \
        and by_name["Bash"][0].payload.get("command"), "Bash 子帧 execute + command"
    assert by_name["Read"][0].payload.get("tool_kind") == "read", "Read 子帧 read"
    assert by_name["Grep"][0].payload.get("tool_kind") == "read", "Grep 子帧 read"
    assert by_name["Edit"][0].payload.get("diff_hunks"), "Edit 子帧合成 diff 项"
    assert by_name["Write"][0].payload.get("diff_hunks"), "Write 子帧合成 write diff"
    assert by_name["TodoList"][0].payload.get("todos"), "TodoList 子帧 todos 载荷"

    result_chunks = chunks[1::2]  # result 帧
    assert all((c.payload or {}).get("status") == "completed" for c in result_chunks), "result 帧全 completed"
    assert result_chunks[1].payload.get("output"), "Bash result 输出正文"


def test_wire_synthesis_incomplete_events():
    assert _synthesize_wire_call({"toolCallId": "x"}, KIMI_PARENT) is None, "残缺事件合成 None（缺 name）"
    assert _synthesize_wire_call({"name": "Read"}, KIMI_PARENT) is None, "残缺事件合成 None（缺 toolCallId）"
    assert _synthesize_wire_result({}, KIMI_PARENT, "read") is None, "残缺 result 合成 None"


# ----------------------------------------------------------------------
# _WireSidecar 目录发现 + 增量解析 + 熔断
# ----------------------------------------------------------------------

def test_wire_sidecar_incremental_parsing(tmp_path):
    session_dir = tmp_path / "session_x"
    (session_dir / "agents" / "main").mkdir(parents=True)

    q: queue.Queue[dict] = queue.Queue()
    sidecar = _WireSidecar(session_dir, q)
    sidecar.note_agent_call(KIMI_PARENT)
    sidecar._poll()  # 无新增目录：无输出
    assert q.empty(), "无新增目录无输出"

    agent_dir = session_dir / "agents" / "agent-0"
    agent_dir.mkdir()
    sidecar._poll()  # 目录发现（wire 尚未创建：静默）
    assert q.empty() and sidecar._wire_path is not None, "目录发现后 wire 缺失静默"

    wire = agent_dir / "wire.jsonl"
    sample_lines = WIRE_SAMPLE.read_text(encoding="utf-8").strip().splitlines()
    wire.write_text("\n".join(sample_lines[:6]) + "\n", encoding="utf-8")
    sidecar._poll()  # 首段 6 条（3 对 call/result）
    assert q.qsize() == 6, "首段解析出 6 帧"

    # 增量追加（含末段无换行的残行：先写半行，下轮补全）
    with wire.open("a", encoding="utf-8") as f:
        for line in sample_lines[6:10]:
            f.write(line + "\n")
        f.write(sample_lines[10][:80])  # 残行（无换行）
    sidecar._poll()
    assert q.qsize() == 10, "增量解析累计 10 帧"

    with wire.open("a", encoding="utf-8") as f:
        f.write(sample_lines[10][80:] + "\n" + sample_lines[11] + "\n")
    sidecar._poll()
    assert q.qsize() == 12, "残行重拼后 12 帧齐"

    first = q.get_nowait()
    assert first.get("sessionUpdate") == "tool_call" \
        and str(first.get("toolCallId", "")).startswith(KIMI_PARENT + "/"), \
        "旁路输出为 ACP 同构 update dict"

    # 熔断：连续坏行累计超阈值 → _broken 置位静默收束
    for _ in range(_SIDECAR_PARSE_FAIL_LIMIT):
        with wire.open("a", encoding="utf-8") as f:
            f.write("{not json!!!\n")
        sidecar._poll()
    assert sidecar._broken, "解析失败累计熔断"

    sidecar.stop()
