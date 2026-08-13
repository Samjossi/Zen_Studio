"""子代理活动嵌套显示单元测试（0813-1919 计划 T1/T4 验证）。

覆盖：
- T1 层级 toolCallId 解析：reasonix 实测帧（.temp/subagent_acp_test/
  reasonix_frames.jsonl 蓝本）驱动 map_session_update，断言
  parent_tool_call_id 拆出与无层级 ID 后端的零影响；
- T4 kimi wire 旁路合成：实测 wire 裁剪样本（.temp/subagent_wire_sample.jsonl）
  驱动 _synthesize_wire_call/_synthesize_wire_result + _WireSidecar 增量
  解析（目录发现/残行缓冲/熔断），断言合成 Chunk 序列形态。

用法：
    .venv/bin/python scripts/test_subagent_nested.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llm.providers.acp import map_session_update, _split_hierarchical_tid
from llm.providers.kimi_acp import (
    _SIDECAR_PARSE_FAIL_LIMIT,
    _WireSidecar,
    _synthesize_wire_call,
    _synthesize_wire_result,
    _wire_tool_kind,
)

ROOT = Path(__file__).resolve().parents[1]
REASONIX_FRAMES = ROOT / ".temp" / "subagent_acp_test" / "reasonix_frames.jsonl"
WIRE_SAMPLE = ROOT / ".temp" / "subagent_wire_sample.jsonl"
SIDECAR_DIR = ROOT / ".temp" / "sidecar_test"

PARENT_TID = "call_00_P14w9pZLRhdaZCaoAny63153"
CHILD_TID = f"{PARENT_TID}/call_00_twRhcebsPqDidwYhwNcc6784"


def check(name: str, cond: bool) -> None:
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}")
    if not cond:
        raise SystemExit(f"断言失败：{name}")


def _map(update: dict) -> object:
    return map_session_update({"params": {"update": update, "sessionId": "test"}})


# ----------------------------------------------------------------------
# 1. T1 层级 ID 解析
# ----------------------------------------------------------------------
print("T1 层级 toolCallId 解析")
check("首段即父 ID", _split_hierarchical_tid(CHILD_TID) == PARENT_TID)
check("无 `/` 返回 None", _split_hierarchical_tid("call_00_abc") is None)
check("kimi `0:tool_` 形态无层级", _split_hierarchical_tid("0:tool_abc") is None)
check("空首段不拆", _split_hierarchical_tid("/child") is None)

# reasonix 实测帧蓝本：父 task 帧 + 子 ls/read_file 帧（帧存档
# subagent_probe_reasonix_20260813_190102.json 同源 .temp 原始帧）
parent_call = _map({"sessionUpdate": "tool_call", "toolCallId": PARENT_TID,
                    "title": "task", "kind": "other", "status": "pending",
                    "rawInput": {"description": "探针子代理", "prompt": "读文件"}})
check("父帧无父指针", "parent_tool_call_id" not in (parent_call.payload or {}))

child_call = _map({"sessionUpdate": "tool_call", "toolCallId": CHILD_TID,
                   "title": "ls", "kind": "read", "status": "pending",
                   "rawInput": {"path": "docs"}})
check("子帧拆出父指针", (child_call.payload or {}).get("parent_tool_call_id") == PARENT_TID)
check("子帧 tid 保留全串", (child_call.payload or {}).get("tool_call_id") == CHILD_TID)

child_update = _map({"sessionUpdate": "tool_call_update", "toolCallId": CHILD_TID,
                     "status": "completed",
                     "rawOutput": {"output": "a.md\nb.md"}})
check("子 update 帧拆出父指针",
      (child_update.payload or {}).get("parent_tool_call_id") == PARENT_TID)
check("子 update 输出正文进载荷",
      (child_update.payload or {}).get("output") == "a.md\nb.md")

kimi_agent = _map({"sessionUpdate": "tool_call",
                   "toolCallId": "0:tool_sbYPlYDEPKcsL9XI6fidkBfY",
                   "title": "Agent", "kind": "other", "status": "pending"})
check("kimi Agent 帧无父指针", "parent_tool_call_id" not in (kimi_agent.payload or {}))

# reasonix 实测原始帧整流回归：全量 session/update 帧过映射不抛异常
if REASONIX_FRAMES.is_file():
    nested = 0
    for line in REASONIX_FRAMES.read_text(encoding="utf-8").strip().splitlines():
        frame = (json.loads(line).get("frame") or {})
        if frame.get("method") != "session/update":
            continue
        chunk = map_session_update({"params": frame.get("params") or {}})
        if chunk and chunk.kind in ("tool_call", "tool_call_update") \
                and (chunk.payload or {}).get("parent_tool_call_id"):
            nested += 1
    check("实测帧整流：嵌套帧 12 张（6 call + 6 update）",
          nested == 12)
else:
    print("  [SKIP] reasonix 原始帧缺失（.temp 已清理），整流回归跳过")

# ----------------------------------------------------------------------
# 2. T4 kimi wire 旁路合成
# ----------------------------------------------------------------------
print("T4 kimi wire 旁路合成")
check("kind 映射：Bash→execute", _wire_tool_kind("Bash") == "execute")
check("kind 映射：Edit→edit", _wire_tool_kind("Edit") == "edit")
check("kind 映射：Grep→read", _wire_tool_kind("Grep") == "read")
check("kind 映射：未收录→other", _wire_tool_kind("AskUserQuestion") == "other")

kimi_parent = "0:tool_sbYPlYDEPKcsL9XI6fidkBfY"
sample_events = []
for line in WIRE_SAMPLE.read_text(encoding="utf-8").strip().splitlines():
    record = json.loads(line)
    if record.get("type") == "context.append_loop_event":
        sample_events.append(record.get("event") or {})
check("样本 12 条事件", len(sample_events) == 12)

kinds: dict[str, str] = {}
chunks = []
for event in sample_events:
    if event.get("type") == "tool.call":
        update = _synthesize_wire_call(event, kimi_parent)
        kinds[event["toolCallId"]] = update["kind"]
    else:
        update = _synthesize_wire_result(
            event, kimi_parent, kinds.get(event.get("toolCallId") or "", "other"))
    chunks.append(_map(update))

check("合成 12 帧全部映射出 Chunk", all(c is not None for c in chunks))
check("合成帧父指针一致", all(
    (c.payload or {}).get("parent_tool_call_id") == kimi_parent for c in chunks))
check("合成 tid 为 `父/子` 全串", all(
    (c.payload or {}).get("tool_call_id", "").startswith(kimi_parent + "/")
    for c in chunks))

by_name: dict[str, list] = {}
for event, chunk in zip(sample_events[::2], chunks[::2]):  # call 帧
    by_name.setdefault(event.get("name"), []).append(chunk)
check("Bash 子帧 execute + command",
      by_name["Bash"][0].payload.get("tool_kind") == "execute"
      and by_name["Bash"][0].payload.get("command"))
check("Read 子帧 read", by_name["Read"][0].payload.get("tool_kind") == "read")
check("Grep 子帧 read", by_name["Grep"][0].payload.get("tool_kind") == "read")
check("Edit 子帧合成 diff 项", bool(by_name["Edit"][0].payload.get("diff_hunks")))
check("Write 子帧合成 write diff", bool(by_name["Write"][0].payload.get("diff_hunks")))
check("TodoList 子帧 todos 载荷", bool(by_name["TodoList"][0].payload.get("todos")))

result_chunks = chunks[1::2]  # result 帧
check("result 帧全 completed", all(
    (c.payload or {}).get("status") == "completed" for c in result_chunks))
check("Bash result 输出正文", bool(result_chunks[1].payload.get("output")))

check("残缺事件合成 None（缺 name）",
      _synthesize_wire_call({"toolCallId": "x"}, kimi_parent) is None)
check("残缺事件合成 None（缺 toolCallId）",
      _synthesize_wire_call({"name": "Read"}, kimi_parent) is None)
check("残缺 result 合成 None", _synthesize_wire_result({}, kimi_parent, "read") is None)

# ----------------------------------------------------------------------
# 3. _WireSidecar 目录发现 + 增量解析 + 熔断
# ----------------------------------------------------------------------
print("_WireSidecar 增量解析")
import queue as _queue
import shutil

session_dir = SIDECAR_DIR / "session_x"
shutil.rmtree(SIDECAR_DIR, ignore_errors=True)
(session_dir / "agents" / "main").mkdir(parents=True)

q: _queue.Queue[dict] = _queue.Queue()
sidecar = _WireSidecar(session_dir, q)
sidecar.note_agent_call(kimi_parent)
sidecar._poll()  # 无新增目录：无输出
check("无新增目录无输出", q.empty())

agent_dir = session_dir / "agents" / "agent-0"
agent_dir.mkdir()
sidecar._poll()  # 目录发现（wire 尚未创建：静默）
check("目录发现后 wire 缺失静默", q.empty() and sidecar._wire_path is not None)

wire = agent_dir / "wire.jsonl"
sample_lines = WIRE_SAMPLE.read_text(encoding="utf-8").strip().splitlines()
wire.write_text("\n".join(sample_lines[:6]) + "\n", encoding="utf-8")
sidecar._poll()  # 首段 6 条（3 对 call/result）
check("首段解析出 6 帧", q.qsize() == 6)

# 增量追加（含末段无换行的残行：先写半行，下轮补全）
with wire.open("a", encoding="utf-8") as f:
    for line in sample_lines[6:10]:
        f.write(line + "\n")
    f.write(sample_lines[10][:80])  # 残行（无换行）
sidecar._poll()
check("增量解析累计 10 帧", q.qsize() == 10)

with wire.open("a", encoding="utf-8") as f:
    f.write(sample_lines[10][80:] + "\n" + sample_lines[11] + "\n")
sidecar._poll()
check("残行重拼后 12 帧齐", q.qsize() == 12)

first = q.get_nowait()
check("旁路输出为 ACP 同构 update dict",
      first.get("sessionUpdate") == "tool_call"
      and str(first.get("toolCallId", "")).startswith(kimi_parent + "/"))

# 熔断：连续坏行累计超阈值 → _broken 置位静默收束
for _ in range(_SIDECAR_PARSE_FAIL_LIMIT):
    with wire.open("a", encoding="utf-8") as f:
        f.write("{not json!!!\n")
    sidecar._poll()
check("解析失败累计熔断", sidecar._broken)

sidecar.stop()
print("\n全部断言通过")
