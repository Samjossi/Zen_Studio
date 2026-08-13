#!/usr/bin/env python3
"""子代理内部事件探针：实测各 ACP 后端是否下发子代理内部活动事件。

背景：GUI 上调用子代理（Agent/task 工具）只显示一张卡片，看不到子代理
内部在干什么。已知 kilocode 侧子会话事件在 ACP 出口被静默丢弃（F2 协议
永久边界），本脚本对 kimi / reasonix / kilocode 逐个实测验证：

1. 以真实 ACP 客户端身份（复用 llm/providers/acp.py 的 AcpConnection）
   建立会话，发送「强制使用子代理」的 prompt；
2. 全程录制双向原始帧（ndjson），落盘 .temp/subagent_acp_test/<agent>.jsonl；
3. 轮次结束后分析：子代理工具调用存活期间，是否收到任何属于其他
   toolCallId 的嵌套 tool_call / tool_call_update / 思考流帧；
4. 产出浓缩证据到 文档/帧存档/subagent_probe_<agent>_<时间戳>.json。

用法（项目根目录下，强制 .venv）：
    .venv/bin/python scripts/probe_subagent_frames.py kimi
    .venv/bin/python scripts/probe_subagent_frames.py reasonix
    .venv/bin/python scripts/probe_subagent_frames.py kilocode
"""
import argparse
import json
import queue
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.version import APP_VERSION  # noqa: E402
from llm.providers.acp import AcpConnection  # noqa: E402

#: 测试工作区（agent 子进程 cwd，限定项目内）
TEST_CWD = str(PROJECT_ROOT / "测试文件夹")

#: 各后端二进制与「强制触发子代理」的 prompt
AGENTS = {
    "kimi": {
        "bin": str(Path.home() / ".kimi-code" / "bin" / "kimi"),
        "prompt": (
            "请使用 Agent 工具委派一个子代理完成这个小任务："
            "列出当前目录下的文件名，并用一句话总结。你自己不要直接列目录，"
            "必须通过子代理完成，完成后汇报子代理的结果。"
        ),
    },
    "reasonix": {
        "bin": "reasonix",
        "prompt": (
            "如果你有子代理委派工具（例如 Agent 或 task 工具），请用它委派一个"
            "子代理完成：列出当前目录下的文件名并用一句话总结。"
            "如果你没有这类工具，请直接回答「无子代理工具」，不要调用其他工具。"
        ),
    },
    "kilocode": {
        "bin": "kilocode",
        "prompt": (
            "请使用 task 工具委派一个子任务给子代理：列出当前目录下的文件名，"
            "并用一句话总结。你自己不要直接执行，必须通过 task 子代理完成。"
        ),
    },
}

TURN_TIMEOUT_S = 360  # 单轮次整体超时（子代理执行可能长达数分钟）


class FrameRecorder:
    """录制 AcpConnection 双向原始帧（猴子补丁 _send / _dispatch_line）。"""

    def __init__(self, path: Path) -> None:
        self._lock = threading.Lock()
        self._fh = path.open("w", encoding="utf-8")
        self._orig_send = AcpConnection._send
        self._orig_dispatch = AcpConnection._dispatch_line
        recorder = self

        def send_patched(conn_self: AcpConnection, msg: dict) -> None:
            recorder._write("out", msg)
            recorder._orig_send(conn_self, msg)

        def dispatch_patched(conn_self: AcpConnection, line: str) -> None:
            line_stripped = line.strip()
            if line_stripped:
                try:
                    recorder._write("in", json.loads(line_stripped))
                except json.JSONDecodeError:
                    recorder._write("in", {"_non_json": line_stripped[:500]})
            recorder._orig_dispatch(conn_self, line)

        AcpConnection._send = send_patched
        AcpConnection._dispatch_line = dispatch_patched

    def _write(self, direction: str, obj: dict) -> None:
        with self._lock:
            self._fh.write(json.dumps(
                {"ts": round(time.time(), 3), "dir": direction, "frame": obj},
                ensure_ascii=False) + "\n")
            self._fh.flush()

    def restore(self) -> None:
        AcpConnection._send = self._orig_send
        AcpConnection._dispatch_line = self._orig_dispatch
        self._fh.close()


def analyze(updates: list[dict]) -> dict:
    """分析轮次内 session/update 帧序列，判定有无子代理内部活动。"""
    type_counts: dict[str, int] = {}
    tool_calls: list[dict] = []       # 出现过的 tool_call（开始帧）
    tool_updates: list[dict] = []     # tool_call_update 摘要
    unknown_frames: list[dict] = []
    subagent_keywords = ("agent", "task", "subagent", "delegate")

    for obj in updates:
        params = obj.get("params") or {}
        update = params.get("update") or {}
        kind = update.get("sessionUpdate", "<missing>")
        type_counts[kind] = type_counts.get(kind, 0) + 1
        if kind == "tool_call":
            tool_calls.append({
                "toolCallId": update.get("toolCallId"),
                "title": update.get("title"),
                "kind": update.get("kind"),
                "status": update.get("status"),
                "has_parentId": "parentToolCallId" in update or "parentId" in update,
                "all_keys": sorted(update.keys()),
            })
        elif kind == "tool_call_update":
            tool_updates.append({
                "toolCallId": update.get("toolCallId"),
                "status": update.get("status"),
                "title": update.get("title"),
            })
        elif kind not in ("agent_message_chunk", "agent_thought_chunk",
                          "plan", "usage_update", "available_commands_update"):
            unknown_frames.append(update)

    # 子代理工具调用 = title/kind 含子代理语义的开始帧
    def _is_subagent_call(tc: dict) -> bool:
        haystack = f"{tc.get('title') or ''} {tc.get('kind') or ''}".lower()
        return any(kw in haystack for kw in subagent_keywords)

    subagent_calls = [tc for tc in tool_calls if _is_subagent_call(tc)]
    subagent_ids = {tc["toolCallId"] for tc in subagent_calls}

    # 嵌套活动信号：出现了不属于子代理卡本人的其他 toolCallId 帧，
    # 或任何帧携带 parent 指针
    nested_signals = []
    if len({tc["toolCallId"] for tc in tool_calls}) > len(subagent_ids) and subagent_ids:
        nested_signals.append("子代理之外出现了其他 tool_call 开始帧（可能为主代理自身活动，需人工复核时序）")
    for tc in tool_calls:
        if tc["has_parentId"]:
            nested_signals.append(f"tool_call {tc['toolCallId']} 携带 parent 指针")

    return {
        "session_update_type_counts": type_counts,
        "tool_call_count": len(tool_calls),
        "tool_calls": tool_calls,
        "tool_update_count": len(tool_updates),
        "tool_updates": tool_updates,
        "subagent_like_calls": subagent_calls,
        "nested_activity_signals": nested_signals,
        "unknown_frames_sample": unknown_frames[:5],
        "conclusion": ("发现疑似子代理内部活动帧" if nested_signals
                       else ("收到子代理工具帧，但无内部活动帧（仅起止）"
                             if subagent_calls else "未触发子代理工具调用")),
    }


def run_probe(agent: str) -> int:
    spec = AGENTS[agent]
    bin_path = spec["bin"]
    if agent == "kimi" and not Path(bin_path).exists():
        print(f"[{agent}] 二进制不存在：{bin_path}", file=sys.stderr)
        return 2

    out_dir = PROJECT_ROOT / ".temp" / "subagent_acp_test"
    out_dir.mkdir(parents=True, exist_ok=True)
    raw_path = out_dir / f"{agent}_frames.jsonl"
    recorder = FrameRecorder(raw_path)

    updates: list[dict] = []
    turn_result: dict = {"agent": agent, "bin": bin_path, "prompt": spec["prompt"]}
    conn: AcpConnection | None = None
    try:
        conn = AcpConnection(bin_path, TEST_CWD, f"{agent} acp")
        init_result = conn.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
            },
            "clientInfo": {"name": "zen-studio", "title": "Zen Studio",
                           "version": APP_VERSION},
        })
        turn_result["initialize_result"] = init_result
        session = conn.request("session/new", {"cwd": TEST_CWD, "mcpServers": []},
                               timeout=60)
        session_id = session["sessionId"]
        turn_result["sessionId"] = session_id

        t0 = time.monotonic()
        conn.begin_turn("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": spec["prompt"]}],
        })
        stop_reason = "<timeout>"
        while True:
            try:
                kind, obj = conn._updates.get(timeout=TURN_TIMEOUT_S)
            except queue.Empty:
                break
            if kind == "dead":
                stop_reason = f"<process dead: exit={obj}>"
                break
            if kind == "response":
                err = obj.get("error")
                stop_reason = (f"<error {err.get('code')}: {err.get('message')}>"
                               if err else
                               (obj.get("result") or {}).get("stopReason", "<unknown>"))
                break
            updates.append(obj)
        conn.end_turn()
        turn_result["turn_seconds"] = round(time.monotonic() - t0, 1)
        turn_result["stop_reason"] = stop_reason
        turn_result["update_frame_count"] = len(updates)
        turn_result["analysis"] = analyze(updates)
    except Exception as e:  # noqa: BLE001 — 探针须完整记录失败形态
        turn_result["fatal_error"] = str(e)
    finally:
        if conn is not None:
            conn.terminate()
        recorder.restore()

    ts = time.strftime("%Y%m%d_%H%M%S")
    evidence_path = PROJECT_ROOT / "文档" / "帧存档" / f"subagent_probe_{agent}_{ts}.json"
    evidence_path.write_text(json.dumps(turn_result, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    print(f"[{agent}] 原始帧 → {raw_path}")
    print(f"[{agent}] 证据摘要 → {evidence_path}")
    print(json.dumps({k: turn_result.get(k) for k in
                      ("stop_reason", "turn_seconds", "update_frame_count",
                       "fatal_error", "sessionId")},
                     ensure_ascii=False, indent=2))
    analysis = turn_result.get("analysis")
    if analysis:
        print(f"[{agent}] 结论：{analysis['conclusion']}")
        print(f"[{agent}] 帧类型统计：{analysis['session_update_type_counts']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent", choices=sorted(AGENTS))
    args = parser.parse_args()
    return run_probe(args.agent)


if __name__ == "__main__":
    sys.exit(main())
