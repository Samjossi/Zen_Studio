"""KAP 事件映射：文本补尾、工具卡适配、TODO、context 与 turn usage。"""
from ide.llm.providers.kimi.kap.events import KapEventMapper


def _tool(item_id="tool-1", name="Bash", **extra):
    item = {
        "type": "tool_call",
        "item_id": item_id,
        "status": "running",
        "name": name,
        "input": {"command": "printf hi"},
        "display": {"kind": "command", "command": "printf hi"},
    }
    item.update(extra)
    return item


def test_text_delta_then_ended_only_emits_missing_tail():
    mapper = KapEventMapper()
    assert mapper.map_item_started(
        {"type": "assistant_message", "item_id": "a", "text": ""}) == []
    chunks = mapper.map_delta(
        {"item_id": "a", "field": "text", "delta": "你"})
    assert [chunk.kind for chunk in chunks] == ["text"]
    assert chunks[0].text == "你"
    # 结束帧带全文：只补「好」，不重发「你」
    chunks = mapper.map_item_ended(
        {"type": "assistant_message", "item_id": "a", "text": "你好"})
    assert [chunk.text for chunk in chunks] == ["好"]
    assert mapper.map_item_ended(
        {"type": "assistant_message", "item_id": "a", "text": "你好"}) == []


def test_reasoning_delta_is_separate_kind():
    mapper = KapEventMapper()
    mapper.map_item_started({"type": "reasoning", "item_id": "r", "text": ""})
    chunks = mapper.map_delta({"item_id": "r", "field": "text", "delta": "想"})
    assert [(chunk.kind, chunk.text) for chunk in chunks] == [("reasoning", "想")]


def test_tool_start_and_end_use_existing_kimi_card_payload():
    mapper = KapEventMapper()
    start = mapper.map_item_started(_tool())
    assert len(start) == 1
    payload = start[0].payload
    assert start[0].kind == "tool_call"
    assert payload["tool_call_id"] == "tool-1"
    assert payload["title"] == "Bash"
    assert payload["tool_kind"] == "execute"
    assert payload["command"] == "printf hi"

    ended = mapper.map_item_ended(_tool(
        status="ended", outcome="completed", output="hi\n"))
    assert len(ended) == 1
    update = ended[0].payload
    assert ended[0].kind == "tool_call_update"
    assert update["status"] == "completed"
    assert update["output"].strip() == "hi"
    assert update["output_total_lines"] == 1


def test_tool_updated_merges_partial_set_into_snapshot():
    mapper = KapEventMapper()
    mapper.map_item_started(_tool())
    chunks = mapper.map_item_updated({
        "item_id": "tool-1",
        "set": {"status": "running", "output": "第一段"},
    })
    assert chunks[0].payload["output"] == "第一段"
    chunks = mapper.map_item_updated({
        "item_id": "tool-1",
        "set": {"status": "ended", "outcome": "completed", "output": "第一段完成"},
    })
    assert chunks[0].payload["status"] == "completed"
    assert chunks[0].payload["output"] == "第一段完成"


def test_tool_output_delta_is_not_streamed_as_card_updates():
    mapper = KapEventMapper()
    mapper.map_item_started(_tool())
    assert mapper.map_delta({
        "item_id": "tool-1", "field": "output", "delta": "x"}) == []
    # 快照仍能拿到累计输出（由后续 item.updated/ended 发送）
    chunks = mapper.map_item_ended(_tool(
        status="ended", outcome="completed", output="x"))
    assert chunks[0].payload["output"] == "x"


def test_display_old_new_builds_diff_hunks():
    mapper = KapEventMapper()
    item = {
        "type": "tool_call",
        "item_id": "tool-diff",
        "status": "ended",
        "outcome": "completed",
        "name": "Edit",
        "input": {"path": "a.txt"},
        "display": {"kind": "diff", "oldText": "a\n", "newText": "b\n"},
    }
    chunks = mapper.map_item_started(item)
    update = chunks[-1].payload
    assert update["tool_kind"] == "edit"
    assert update["diff_stat"].startswith("+")


def test_todo_normalizes_done_and_marks_changes():
    mapper = KapEventMapper()
    first = mapper.map_todo({"items": [
        {"title": "A", "status": "pending"},
        {"title": "B", "status": "done"},
    ]})
    assert first.kind == "todo"
    assert [entry["content"] for entry in first.payload["entries"]] == ["A", "B"]
    assert first.payload["entries"][1]["status"] == "completed"
    assert all(entry.get("changed") for entry in first.payload["entries"])

    second = mapper.map_todo({"items": [
        {"title": "A", "status": "in_progress"},
        {"title": "B", "status": "done"},
    ]})
    assert second.payload["entries"][0].get("changed") is True
    assert "changed" not in second.payload["entries"][1]


def test_context_maps_to_usage_and_requires_size():
    mapper = KapEventMapper()
    chunk = mapper.map_context(
        {"context_tokens": 12, "max_context_tokens": 1000})
    assert chunk.usage.used == 12
    assert chunk.usage.size == 1000
    assert mapper.map_context({"context_tokens": 12}) is None
    assert mapper.map_context({"context_tokens": 12}, fallback_size=500).usage.size == 500


def test_turn_consumption_from_turn_usage():
    turn = {
        "turn_id": "t1",
        "usage": {"input_other": 3, "input_cache_read": 4,
                  "input_cache_creation": 5, "output": 6},
    }
    consumption = KapEventMapper.turn_consumption(turn, "s1")
    assert consumption.total == 18
    assert consumption.turn_id == "s1:t1"
    assert KapEventMapper.turn_consumption({"usage": {"output": 1}}, "s1") is None


def test_reset_clears_text_ledger_so_reused_id_restarts():
    mapper = KapEventMapper()
    mapper.map_item_started({"type": "assistant_message", "item_id": "x", "text": ""})
    mapper.map_delta({"item_id": "x", "field": "text", "delta": "旧"})
    mapper.end_turn()
    chunks = mapper.map_item_ended(
        {"type": "assistant_message", "item_id": "x", "text": "新"})
    assert [chunk.text for chunk in chunks] == ["新"]
