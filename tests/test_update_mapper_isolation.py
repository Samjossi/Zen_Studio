"""update mapper 实例账本隔离、清理与厂商边界。

覆盖 T2 的状态约束：同 toolCallId 在不同 provider / 不同会话不互相命中；
reset/close 清空本实例账本；厂商包之间无横向 import。
"""
import ast
from pathlib import Path

import pytest

from ide.llm.providers.dsh.provider import DshAcpLLM
from ide.llm.providers.dsh.updates import DshUpdateMapper
from ide.llm.providers.kimi.provider import KimiAcpLLM
from ide.llm.providers.kimi.updates import KimiUpdateMapper
from ide.llm.providers.local.provider import LocalAcpLLM
from ide.llm.providers.local.updates import LocalUpdateMapper

SESSION_ID = "isolation-session"
OTHER_SESSION_ID = "isolation-session-other"


def frame(update: dict, session_id: str = SESSION_ID) -> dict:
    return {
        "method": "session/update",
        "params": {"sessionId": session_id, "update": update},
    }


def tool_call(update: dict, session_id: str = SESSION_ID) -> dict:
    return frame({"sessionUpdate": "tool_call", **update}, session_id)


def tool_update(update: dict, session_id: str = SESSION_ID) -> dict:
    return frame({"sessionUpdate": "tool_call_update", **update}, session_id)


def test_same_tool_call_id_across_providers_does_not_share_ledger():
    tid = "shared-tid"
    kimi = KimiUpdateMapper()
    dsh = DshUpdateMapper()

    kimi_first = kimi.map_session_update(tool_call({
        "toolCallId": tid, "title": "Read", "kind": "read",
        "status": "pending", "rawInput": {"path": "kimi.py"},
    }))
    assert kimi_first.payload["input_detail"] == "path: kimi.py"

    dsh_update = dsh.map_session_update(tool_update({
        "toolCallId": tid, "status": "in_progress",
        "rawInput": {"file_path": "dsh.py"},
    }))
    assert dsh_update.payload["input_detail"] == "file_path: dsh.py"

    # Kimi 自己的账本仍然只对 Kimi 生效：首帧已登记，迟到帧不重复回填。
    kimi_update = kimi.map_session_update(tool_update({
        "toolCallId": tid, "status": "in_progress",
        "rawInput": {"path": "kimi-later.py"},
    }))
    assert "input_detail" not in kimi_update.payload


def test_same_tool_call_id_across_mapper_instances_does_not_share_ledger():
    tid = "shared-instance-tid"
    first_mapper = KimiUpdateMapper()
    second_mapper = KimiUpdateMapper()

    first_mapper.map_session_update(tool_call({
        "toolCallId": tid, "title": "Read", "kind": "read",
        "status": "pending", "rawInput": {"path": "first.py"},
    }))

    second_update = second_mapper.map_session_update(tool_update({
        "toolCallId": tid, "status": "in_progress",
        "rawInput": {"path": "second.py"},
    }))
    assert second_update.payload["input_detail"] == "path: second.py"

    first_update = first_mapper.map_session_update(tool_update({
        "toolCallId": tid, "status": "in_progress",
        "rawInput": {"path": "first-later.py"},
    }))
    assert "input_detail" not in first_update.payload


def test_same_tool_call_id_across_sessions_does_not_share_ledger():
    tid = "session-scoped-tid"
    mapper = KimiUpdateMapper()

    first = mapper.map_session_update(
        tool_call({
            "toolCallId": tid, "title": "Read", "kind": "read",
            "status": "pending", "rawInput": {"path": "a.py"},
        }, SESSION_ID))
    assert first.payload["input_detail"] == "path: a.py"

    other_session = mapper.map_session_update(
        tool_update({
            "toolCallId": tid, "status": "in_progress",
            "rawInput": {"path": "b.py"},
        }, OTHER_SESSION_ID))
    assert other_session.payload["input_detail"] == "path: b.py"

    # todo 快照也按会话隔离：另一会话同清单应重新全量高亮。
    one = mapper.map_session_update(
        tool_call({
            "toolCallId": "todo-1", "title": "TodoList", "kind": "other",
            "status": "pending", "rawInput": {"todos": [
                {"title": "甲", "status": "pending"},
                {"title": "乙", "status": "pending"},
            ]},
        }, SESSION_ID))
    two = mapper.map_session_update(
        tool_call({
            "toolCallId": "todo-2", "title": "TodoList", "kind": "other",
            "status": "pending", "rawInput": {"todos": [
                {"title": "甲", "status": "pending"},
                {"title": "乙", "status": "pending"},
            ]},
        }, OTHER_SESSION_ID))
    assert all(entry["changed"] for entry in one.payload["todos"])
    assert all(entry["changed"] for entry in two.payload["todos"])


def test_reset_clears_input_and_derived_ledgers():
    tid = "reset-tid"
    mapper = DshUpdateMapper()
    mapper.map_session_update(tool_call({
        "toolCallId": tid, "title": "bash", "kind": "other",
        "status": "pending", "content": [],
    }))
    before = mapper.map_session_update(tool_update({
        "toolCallId": tid, "status": "in_progress",
        "rawInput": {"command": "ls"},
    }))
    assert before.payload["command"] == "ls"  # 首帧推导 execute 后回填

    mapper.reset()
    after = mapper.map_session_update(tool_update({
        "toolCallId": tid, "status": "in_progress",
        "rawInput": {"command": "ls"},
    }))
    # 推导账本已清空：缺 kind 的 update 帧不再走 execute 迟到 command 分支。
    assert "command" not in after.payload
    assert after.payload["input_detail"] == "command: ls"


def test_reset_clears_todo_snapshots():
    mapper = LocalUpdateMapper()
    payload = {
        "toolCallId": "todo-tid", "title": "Plan", "kind": "other",
        "status": "pending",
        "rawInput": {"todos": [{"content": "甲", "status": "pending"}]},
    }
    first = mapper.map_session_update(tool_call(payload))
    second = mapper.map_session_update(tool_call(payload))
    assert all(entry.get("changed") for entry in first.payload["todos"])
    assert not any(entry.get("changed") for entry in second.payload["todos"])

    mapper.reset()
    third = mapper.map_session_update(tool_call(payload))
    assert all(entry.get("changed") for entry in third.payload["todos"])


@pytest.mark.parametrize("provider_class", [
    KimiAcpLLM, DshAcpLLM, LocalAcpLLM,
])
def test_provider_reset_and_close_clear_mapper_state(provider_class):
    provider = provider_class(workspace_root=".")
    provider._updates.map_session_update(tool_call({
        "toolCallId": "provider-tid", "title": "Read", "kind": "read",
        "status": "pending", "rawInput": {"path": "a.py"},
    }))
    assert provider._updates._input_detail_seen

    provider.reset_session()
    assert not provider._updates._input_detail_seen
    assert not provider._updates._last_todo_snapshots

    provider._updates.map_session_update(tool_call({
        "toolCallId": "provider-tid-2", "title": "Read", "kind": "read",
        "status": "pending", "rawInput": {"path": "b.py"},
    }))
    assert provider._updates._input_detail_seen
    provider.close()
    assert not provider._updates._input_detail_seen
    assert not provider._updates._last_todo_snapshots


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_vendor_packages_do_not_import_each_other():
    root = Path(__file__).resolve().parents[1] / "src" / "ide" / "llm" / "providers"
    vendors = ("kimi", "dsh", "local")
    for vendor in vendors:
        others = [f"ide.llm.providers.{other}" for other in vendors if other != vendor]
        for path in (root / vendor).glob("*.py"):
            modules = _imported_modules(path)
            for module in modules:
                assert not any(module == other or module.startswith(other + ".")
                               for other in others), \
                    f"{path} 横向导入了其他厂商：{module}"
