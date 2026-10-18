"""ACP 工具调用 kind 兜底推导与回填测试。

覆盖：
- 兜底推导表：DSH 风格帧（kind 缺失或敷衍上报 "other"）按工具名
  归一化推导 execute/edit/read/search/think；未知工具名保持 "other"；
- 归一化兼容：连字符（fs-search）与下划线（fs_search）双形态同命中；
- update 帧回填：首帧推导入账本后，同 toolCallId 缺 kind 的 update
  帧按推导结果走（execute 迟到 command、write diff 合成），终态帧
  惰性清理账本；
- 显式上报零干扰（Kimi 回归红线）：kind 明确的帧推导不生效；Kimi 的
  Agent/TodoList/AskUserQuestion 帧（kind="other" 且工具名不入表）
  tool_kind 保持 "other"、不派生 is_subagent、账本不留痕；帧存档
  实抓 Kimi 帧整流回放逐帧核对 tool_kind 不变；
- 卡片分派：todo_write 落 TodoListCard、推导 think 落 SubagentCard、
  未知工具落 McpCard、DSH 风格 bash 帧端到端落 BashCard。

全部帧级打桩，不起真实子进程；模块级账本（_derived_tool_kinds /
_input_detail_seen）逐用例清空防串扰。
"""
import json
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

from llm.providers import acp
from llm.providers.acp import map_session_update
from gui.panels.chat.cards import (
    BashCard,
    CardColors,
    McpCard,
    OpenStateMap,
    SubagentCard,
    TodoListCard,
    make_tool_card,
)

FRAME_ARCHIVE = Path(__file__).resolve().parents[1] / "文档" / "帧存档"


@pytest.fixture(autouse=True)
def _clean_ledgers():
    acp._derived_tool_kinds.clear()
    acp._input_detail_seen.clear()
    yield
    acp._derived_tool_kinds.clear()
    acp._input_detail_seen.clear()


def _map(update: dict):
    return map_session_update({"params": {"update": update, "sessionId": "test"}})


def _app():
    return QApplication.instance() or QApplication([])


def _colors() -> CardColors:
    return CardColors("#888888", "#777777", "#ff0000", "#eeeeee",
                      "#dddddd", "#0000ff", "#00aa00", "#aa0000")


# ----------------------------------------------------------------------
# 兜底推导表（首帧）
# ----------------------------------------------------------------------

def test_bash_kind_missing_derives_execute():
    chunk = _map({"sessionUpdate": "tool_call", "toolCallId": "d1",
                  "title": "bash", "status": "pending",
                  "rawInput": {"command": "ls -la"}})
    payload = chunk.payload or {}
    assert payload.get("tool_kind") == "execute", "kind 缺失按工具名推导 execute"
    assert payload.get("command") == "ls -la", "推导后 execute 通道提取 command"
    assert acp._derived_tool_kinds.get("d1") == "execute", "推导命中入账本"


def test_bash_kind_other_derives_execute():
    chunk = _map({"sessionUpdate": "tool_call", "toolCallId": "d2",
                  "title": "bash", "kind": "other", "status": "pending",
                  "rawInput": {"command": "pwd"}})
    assert (chunk.payload or {}).get("tool_kind") == "execute", \
        "kind 敷衍上报 other 同样触发推导"


def test_write_edit_read_derivation():
    write = _map({"sessionUpdate": "tool_call", "toolCallId": "d3",
                  "title": "write", "kind": "other", "status": "pending",
                  "rawInput": {"path": "a.md", "content": "一行\n两行\n"}})
    payload = write.payload or {}
    assert payload.get("tool_kind") == "edit", "write 推导 edit"
    assert payload.get("diff_stat") == "+2 −0", "write 首帧合成 diff（kind 注入后生效）"

    edit = _map({"sessionUpdate": "tool_call", "toolCallId": "d4",
                 "title": "edit", "kind": "other", "status": "pending"})
    assert (edit.payload or {}).get("tool_kind") == "edit", "edit 推导 edit"

    read = _map({"sessionUpdate": "tool_call", "toolCallId": "d5",
                 "title": "read", "kind": "other", "status": "pending",
                 "rawInput": {"path": "a.md"}})
    payload = read.payload or {}
    assert payload.get("tool_kind") == "read", "read 推导 read"
    assert payload.get("summary") == "a.md", "推导后 read 通道取路径摘要"


def test_search_hyphen_underscore_forms():
    hyphen = _map({"sessionUpdate": "tool_call", "toolCallId": "d6",
                   "title": "fs-search", "kind": "other", "status": "pending"})
    snake = _map({"sessionUpdate": "tool_call", "toolCallId": "d7",
                  "title": "fs_search", "kind": "other", "status": "pending"})
    assert (hyphen.payload or {}).get("tool_kind") == "search", "连字符形态命中"
    assert (snake.payload or {}).get("tool_kind") == "search", "下划线形态命中"


def test_subagent_derives_think():
    chunk = _map({"sessionUpdate": "tool_call", "toolCallId": "d8",
                  "title": "subagent", "kind": "other", "status": "pending"})
    payload = chunk.payload or {}
    assert payload.get("tool_kind") == "think", "subagent 借 think 通道出子代理卡"
    assert payload.get("is_subagent") is True, "think 派生 is_subagent 标记"


def test_todo_write_stays_other():
    chunk = _map({"sessionUpdate": "tool_call", "toolCallId": "d9",
                  "title": "todo_write", "kind": "other", "status": "pending"})
    payload = chunk.payload or {}
    assert payload.get("tool_kind") == "other", "todo_write 不借 kind 通道"
    assert "is_subagent" not in payload
    assert not acp._derived_tool_kinds, "推导 other 不入账本"


def test_unknown_tool_stays_other():
    chunk = _map({"sessionUpdate": "tool_call", "toolCallId": "d10",
                  "title": "workflow", "kind": "other", "status": "pending"})
    assert (chunk.payload or {}).get("tool_kind") == "other", \
        "未收录工具名保持 other 落通用卡"
    assert not acp._derived_tool_kinds


# ----------------------------------------------------------------------
# update 帧回填（账本同构机制）
# ----------------------------------------------------------------------

def test_update_backfill_execute_command():
    _map({"sessionUpdate": "tool_call", "toolCallId": "u1",
          "title": "bash", "kind": "other", "status": "pending"})
    update = _map({"sessionUpdate": "tool_call_update", "toolCallId": "u1",
                   "status": "in_progress",
                   "rawInput": {"command": "make build"}})
    assert (update.payload or {}).get("command") == "make build", \
        "缺 kind 的 update 帧回填 execute：迟到 command 提取生效"


def test_update_backfill_write_diff():
    _map({"sessionUpdate": "tool_call", "toolCallId": "u2",
          "title": "write", "kind": "other", "status": "pending"})
    update = _map({"sessionUpdate": "tool_call_update", "toolCallId": "u2",
                   "status": "in_progress",
                   "rawInput": {"path": "b.md", "content": "新文\n"}})
    assert (update.payload or {}).get("diff_stat") == "+1 −0", \
        "缺 kind 的 update 帧回填 edit：write diff 合成生效"


def test_update_backfill_think_result_summary():
    _map({"sessionUpdate": "tool_call", "toolCallId": "u3",
          "title": "subagent", "kind": "other", "status": "pending"})
    update = _map({"sessionUpdate": "tool_call_update", "toolCallId": "u3",
                   "status": "completed",
                   "rawOutput": {"output": "<task_result>成果</task_result>"}})
    assert (update.payload or {}).get("result_summary") == "成果", \
        "completed 帧回填 think：成果摘要提取生效"
    assert "u3" not in acp._derived_tool_kinds, "终态帧惰性清理账本"


def test_update_without_ledger_untouched():
    update = _map({"sessionUpdate": "tool_call_update", "toolCallId": "ux",
                   "status": "completed",
                   "rawOutput": {"output": "<task_result>不应提取</task_result>"}})
    assert "result_summary" not in (update.payload or {}), \
        "账本未登记的 tid 不回填（首帧缺失保守兜底）"


# ----------------------------------------------------------------------
# Kimi 回归红线：显式上报零干扰 + 一级名分派帧不变
# ----------------------------------------------------------------------

def test_explicit_kind_never_overridden():
    execute = _map({"sessionUpdate": "tool_call", "toolCallId": "k1",
                    "title": "Bash", "kind": "execute", "status": "pending",
                    "rawInput": {"command": "echo hi"}})
    assert (execute.payload or {}).get("tool_kind") == "execute"

    # 表内工具名 + 显式非 other kind：推导不得覆盖（即使与表值冲突）
    mismatched = _map({"sessionUpdate": "tool_call", "toolCallId": "k2",
                       "title": "bash", "kind": "read", "status": "pending"})
    assert (mismatched.payload or {}).get("tool_kind") == "read", \
        "显式上报 kind 原样透传，推导不生效"
    assert not acp._derived_tool_kinds, "显式上报帧不入账本"


def test_kimi_other_frames_untouched():
    """Kimi 的 Agent/TodoList/AskUserQuestion kind 恒为 other，靠卡片侧
    一级工具名分派落专卡；工具名不入推导表，映射结果须逐字段不变。"""
    for tid, title in [("k3", "Agent"), ("k4", "TodoList"),
                       ("k5", "AskUserQuestion")]:
        chunk = _map({"sessionUpdate": "tool_call", "toolCallId": tid,
                      "title": title, "kind": "other", "status": "pending"})
        payload = chunk.payload or {}
        assert payload.get("tool_kind") == "other", f"{title} kind 保持 other"
        assert "is_subagent" not in payload, f"{title} 不派生 is_subagent"
        assert payload.get("summary") is None, f"{title} 不派生 think 摘要"
    assert not acp._derived_tool_kinds, "Kimi 帧账本零登记"


def test_kimi_archived_frames_replay():
    """帧存档实抓 Kimi 帧整流回放：每张 tool_call 的 tool_kind 与帧内
    kind 原值逐一相等（缺失归 other），证明 Kimi 路径行为逐字节不变。"""
    archives = ["execute_20260812_075201.json", "edit_20260812_075201.json",
                "write_20260812_075201.json", "todolist_20260812_075201.json",
                "askuser_20260807_023718.json"]
    seen = 0
    for name in archives:
        data = json.loads((FRAME_ARCHIVE / name).read_text(encoding="utf-8"))
        for frame in data.get("frames") or []:
            update = (frame.get("params") or {}).get("update") or {}
            if update.get("sessionUpdate") != "tool_call":
                continue
            chunk = _map(update)
            expected = update.get("kind") \
                if isinstance(update.get("kind"), str) else "other"
            assert (chunk.payload or {}).get("tool_kind") == expected, \
                f"{name}: tool_call kind 与实抓帧原值一致"
            seen += 1
    assert seen == len(archives), "每档存档各一张 tool_call 帧全数核对"
    assert not acp._derived_tool_kinds, "Kimi 整流回放账本零登记"


# ----------------------------------------------------------------------
# 卡片分派（端到端落位）
# ----------------------------------------------------------------------

def test_todo_write_dispatches_todo_list_card():
    _app()
    card = make_tool_card(_colors(), OpenStateMap(),
                          {"title": "todo_write", "tool_kind": "other",
                           "tool_call_id": "c1"})
    assert isinstance(card, TodoListCard), "DSH 下划线命名落 TodoListCard"


def test_derived_frames_end_to_end_cards():
    _app()
    cases = [
        ({"sessionUpdate": "tool_call", "toolCallId": "c2",
          "title": "bash", "kind": "other", "status": "pending"}, BashCard),
        ({"sessionUpdate": "tool_call", "toolCallId": "c3",
          "title": "subagent", "kind": "other", "status": "pending"}, SubagentCard),
        ({"sessionUpdate": "tool_call", "toolCallId": "c4",
          "title": "workflow", "kind": "other", "status": "pending"}, McpCard),
    ]
    for update, card_cls in cases:
        chunk = _map(update)
        card = make_tool_card(_colors(), OpenStateMap(), chunk.payload or {})
        assert isinstance(card, card_cls), \
            f"{update['title']} 端到端落 {card_cls.__name__}"


def test_kimi_agent_card_dispatch_unchanged():
    """Kimi Agent 帧（kind=other）经一级工具名分派落 SubagentCard——
    与兜底推导引入前同卡，推导不改变 Kimi 侧落位。"""
    _app()
    chunk = _map({"sessionUpdate": "tool_call",
                  "toolCallId": "0:tool_kimiagent",
                  "title": "Agent", "kind": "other", "status": "pending"})
    card = make_tool_card(_colors(), OpenStateMap(), chunk.payload or {})
    assert isinstance(card, SubagentCard), "Agent 一级名分派落子代理卡不变"
    assert (chunk.payload or {}).get("tool_kind") == "other", \
        "卡内 kind 键控表现（图标/默认开合）基准值不变"
