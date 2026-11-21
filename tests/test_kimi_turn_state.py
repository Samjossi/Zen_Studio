"""TurnStateTracker 四态判定与容错口径测试。

夹具全部内联纯元数据（无正文/路径/工具载荷），与 13:53 事故 wire
的字段结构一致：turn.prompt / prompt.steered / turn.ended /
prompt.completed / task.started|terminated（taskId 在 info 子字典）/
context.append_loop_event(step.end)。
"""
import json

import pytest

from ide.llm.providers.kimi.turn_state import (
    PROMPT_GRACE_MS,
    TurnDesynchronized,
    TurnStateTracker,
)


@pytest.fixture
def session(tmp_path):
    d = tmp_path / "session_x" / "agents" / "main"
    d.mkdir(parents=True)
    return tmp_path / "session_x"


def write_wire(session, records, *, append=True):
    path = session / "agents" / "main" / "wire.jsonl"
    mode = "a" if append and path.exists() else "w"
    with path.open(mode, encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def turn_prompt(turn_id, prompt_id, *, kind="user", trigger="normal",
                notification_id=None):
    origin = {"kind": kind}
    if notification_id is not None:
        origin["notificationId"] = notification_id
    return {"type": "turn.prompt", "turnId": turn_id, "promptId": prompt_id,
            "origin": origin, "trigger": trigger, "time": 1000}


def step_end(turn_id, finish):
    return {"type": "context.append_loop_event", "time": 1500,
            "event": {"type": "step.end", "turnId": str(turn_id),
                      "finishReason": finish}}


def turn_ended(turn_id, reason="completed"):
    return {"type": "turn.ended", "turnId": turn_id, "reason": reason,
            "time": 2000}


def prompt_completed(prompt_id, reason="completed"):
    return {"type": "prompt.completed", "promptId": prompt_id,
            "reason": reason, "time": 2001}


def task_started(task_id):
    return {"type": "task.started", "time": 1200,
            "info": {"taskId": task_id, "status": "running", "detached": True}}


def task_terminated(task_id):
    return {"type": "task.terminated", "time": 1800,
            "info": {"taskId": task_id, "status": "completed"}}


def steered(*prompt_ids, message_id=None):
    r = {"type": "prompt.steered", "time": 1600, "promptIds": list(prompt_ids)}
    if message_id is not None:
        r["messageId"] = message_id
    return r


def make_tracker(session, records=()):
    # 先构造（空文件起点 offset=0）再写入：模拟新一轮从当前游标增量读
    t = TurnStateTracker(session)
    if records:
        write_wire(session, records, append=False)
    return t


def normal_user_turn():
    return [turn_prompt(0, "p0"), step_end(0, "end_turn"),
            turn_ended(0), prompt_completed("p0")]


# ---------------------------------------------------------------- 四态判定

def test_normal_completion(session):
    t = make_tracker(session, normal_user_turn())
    t.poll()
    snap = t.snapshot(response_received=True)
    assert snap.state == "normal"
    assert not snap.agent_active
    assert snap.user_prompt_id == "p0"


def test_notify_turn_active_drains(session):
    t = make_tracker(session, normal_user_turn() + [
        turn_prompt(1, "n1", kind="task", trigger="notify",
                    notification_id="task:x:completed"),
        step_end(1, "end_turn"), turn_ended(1), prompt_completed("n1"),
        turn_prompt(2, "n2", kind="task", trigger="notify",
                    notification_id="task:y:completed"),
    ])
    t.poll()
    snap = t.snapshot(response_received=True)
    assert snap.state == "draining"
    assert "n2" in snap.notification_ids


def test_tool_use_finish_is_desynchronized(session):
    records = [turn_prompt(0, "p0"), step_end(0, "tool_use"),
               turn_ended(0), prompt_completed("p0")]
    t = make_tracker(session, records)
    t.poll()
    snap = t.snapshot(response_received=True)
    assert snap.state == "desynchronized"
    assert "tool_use" in snap.reason


def test_notify_then_tool_use_turn_settles_desync(session):
    """事故序列：用户轮 tool_use 完成 + notify 轮活跃 → 先 drain，
    notify 落定后再看用户轮 finishReason → desync。"""
    records = [turn_prompt(0, "p0"), step_end(0, "tool_use"),
               turn_ended(0), prompt_completed("p0"),
               turn_prompt(1, "n1", kind="task", trigger="notify",
                           notification_id="task:x:completed")]
    t = make_tracker(session, records)
    t.poll()
    assert t.snapshot(response_received=True).state == "draining"
    write_wire(session, [step_end(1, "end_turn"), turn_ended(1),
                         prompt_completed("n1")])
    t.poll()
    snap = t.snapshot(response_received=True)
    assert snap.state == "desynchronized"


def test_active_task_drains(session):
    t = make_tracker(session, [
        turn_prompt(0, "p0"), task_started("bash-1"), step_end(0, "end_turn"),
        turn_ended(0), prompt_completed("p0"),
    ])
    t.poll()
    snap = t.snapshot(response_received=True)
    assert snap.state == "draining"
    assert snap.active_task_ids == ("bash-1",)


def test_terminated_task_does_not_drain(session):
    t = make_tracker(session, [
        turn_prompt(0, "p0"), task_started("bash-1"), task_terminated("bash-1"),
        step_end(0, "end_turn"), turn_ended(0), prompt_completed("p0"),
    ])
    t.poll()
    assert t.snapshot(response_received=True).state == "normal"


def test_missing_user_prompt_within_grace_drains(session):
    write_wire(session, [], append=False)  # wire 已创建但无内容
    t = TurnStateTracker(session)
    t.poll()
    snap = t.snapshot(response_received=True, prompt_grace_expired=False)
    assert snap.state == "draining"
    assert "落盘" in snap.reason


def test_missing_user_prompt_after_grace_desync(session):
    write_wire(session, [], append=False)
    t = TurnStateTracker(session)
    t.poll()
    snap = t.snapshot(response_received=True, prompt_grace_expired=True)
    assert snap.state == "desynchronized"


def test_missing_finish_reason_drains_not_unknown(session):
    """D9：finishReason 缺失不单独判 unknown、也不误判 normal。"""
    t = make_tracker(session, [turn_prompt(0, "p0"),
                               turn_ended(0), prompt_completed("p0")])
    t.poll()
    snap = t.snapshot(response_received=True)
    assert snap.state == "draining"
    assert snap.last_finish_reason is None


def test_unfinished_user_turn_drains(session):
    t = make_tracker(session, [turn_prompt(0, "p0"), step_end(0, "end_turn"),
                               turn_ended(0)])  # 缺 prompt.completed
    t.poll()
    assert t.snapshot(response_received=True).state == "draining"


def test_non_completed_end_reason_desync(session):
    t = make_tracker(session, [turn_prompt(0, "p0"), step_end(0, "end_turn"),
                               turn_ended(0, "aborted"),
                               prompt_completed("p0")])
    t.poll()
    assert t.snapshot(response_received=True).state == "desynchronized"


# ---------------------------------------------------------------- wire 容错

def test_wire_absent_no_evidence_fails_open(session):
    t = TurnStateTracker(session)  # 文件不存在
    t.poll()
    snap = t.snapshot(response_received=True)
    assert snap.state == "normal"


def test_broken_json_with_risk_is_unknown(session):
    t = TurnStateTracker(session)  # 文件不存在：尚无证据，fail-open
    t.poll()
    assert t.snapshot(response_received=True).state == "normal"
    write_wire(session, [turn_prompt(0, "p0")])
    with (session / "agents" / "main" / "wire.jsonl").open(
            "a", encoding="utf-8") as f:
        f.write("{not json}\n")  # 原始坏行
    t.poll()
    snap = t.snapshot(response_received=True)
    assert snap.state == "unknown"
    assert t.broken


def test_truncated_file_with_complete_evidence_is_normal(session):
    """坏行/截断后已缓存证据完整（核心+增强）→ 仍可 normal。"""
    records = normal_user_turn()
    t = make_tracker(session, records)
    t.poll()
    assert t.snapshot(response_received=True).state == "normal"
    # 模拟日志轮转：文件被更短的文件替换（size < offset）
    write_wire(session, records[:-1], append=False)
    t.poll()
    assert t.broken
    assert t.snapshot(response_received=True).state == "normal"


def test_missing_file_with_risk_is_unknown(session):
    t = make_tracker(session, [turn_prompt(0, "p0"), step_end(0, "end_turn")])
    t.poll()
    (session / "agents" / "main" / "wire.jsonl").unlink()
    t.poll()
    assert t.missing
    assert t.snapshot(response_received=True).state == "unknown"


def test_half_line_waits_for_completion(session):
    path = session / "agents" / "main" / "wire.jsonl"
    t = make_tracker(session)
    full = json.dumps(turn_prompt(0, "p0"), ensure_ascii=False)
    path.write_text(full[:30], encoding="utf-8")  # 无换行：残行
    t.poll()
    assert not t._turns
    with path.open("a", encoding="utf-8") as f:
        f.write(full[30:] + "\n" + json.dumps(step_end(0, "end_turn")) + "\n")
    t.poll()
    assert t.snapshot(response_received=True).state == "draining"  # 未 completed


def test_unknown_event_types_are_ignored(session):
    t = make_tracker(session, [
        {"type": "llm.request", "turnStep": "x", "modelAlias": "m"},
        {"type": "usage.record", "usageScope": "turn",
         "usage": {"output": 5}},
        {"type": "brand.new.event", "whatever": 1},
        *normal_user_turn(),
    ])
    t.poll()
    assert not t.broken
    assert t.snapshot(response_received=True).state == "normal"


def test_steered_without_prompt_ids_not_broken(session):
    t = make_tracker(session, [
        {"type": "prompt.steered", "time": 1},  # 载荷漂移：无 promptIds
        *normal_user_turn(),
    ])
    t.poll()
    assert not t.broken
    assert t.snapshot(response_received=True).state == "normal"


# ---------------------------------------------------------------- 其他口径

def test_prompt_grace_constant_is_bounded():
    assert 0 < PROMPT_GRACE_MS <= 5000


def test_response_not_received_reports_agent_active_only(session):
    records = [turn_prompt(0, "p0"), step_end(0, "end_turn"), turn_ended(0)]
    t = make_tracker(session, records)
    t.poll()
    snap = t.snapshot(response_received=False)
    assert snap.state == "draining"  # 用户轮未完成 → 待排空
    assert snap.agent_active


def test_turn_desynchronized_message_mentions_side_effects():
    exc = TurnDesynchronized("用户轮次在 tool_use 后提前完成")
    assert "工具副作用" in str(exc)
    assert "不会自动重发" in str(exc)


def test_turn_ids_across_int_and_str(session):
    """wire 中 turnId 时而 int 时而 str（step.end 用 str），映射须归一。"""
    t = make_tracker(session, [turn_prompt(0, "p0"),
                               step_end("0", "end_turn"),
                               turn_ended("0"), prompt_completed("p0")])
    t.poll()
    assert t.snapshot(response_received=True).state == "normal"


def test_notify_turn_completed_then_normal(session):
    """连续通知由同一个 tracker 吸收：第一个 notify 落定后仍要看到它已结束。"""
    records = [
        turn_prompt(0, "p0"), step_end(0, "end_turn"),
        turn_ended(0), prompt_completed("p0"),
        turn_prompt(1, "n1", kind="task", trigger="notify",
                    notification_id="task:x:completed"),
        step_end(1, "end_turn"), turn_ended(1), prompt_completed("n1"),
    ]
    t = make_tracker(session, records)
    t.poll()
    snap = t.snapshot(response_received=True)
    assert snap.state == "normal"
    assert snap.notification_ids == ("n1",)
