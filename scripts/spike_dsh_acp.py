#!/usr/bin/env python3
"""dsh（DeepSeek Harness）ACP 接入 spike：握手 / 配置项 / 对话 / 审批实测。

接入落地前的协议实证（计划 2026-1018-0732 任务 7），逐项验证：

1. initialize 握手：agentInfo / 能力声明（terminal 预期不支持）；
2. session/new：返回的 configuration-option 状态（model 清单、
   reasoning_effort 档位、configId 实际拼写）；
3. session/set_config_option：模型与推理强度切换是否被接受；
4. 单轮对话：触发 shell 工具的 prompt，验证流式帧、工具帧与
   session/request_permission 审批回环（无 handler 自动 allow_once）；
5. session/cancel：第二轮 prompt 发出后即刻取消，验证取消语义。

全程录制双向原始帧（ndjson）落盘 .temp/dsh_acp_spike/frames.jsonl，
浓缩证据归档 文档/帧存档/dsh_spike_<时间戳>.json。

用法（项目根目录下，强制 .venv）：
    .venv/bin/python scripts/spike_dsh_acp.py [--bin <dsh 二进制路径>]
"""
import argparse
import json
import queue
import sys
import threading
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from core.version import APP_VERSION  # noqa: E402
from llm.providers.acp import AcpConnection  # noqa: E402

#: 测试工作区（agent 子进程 cwd，限定项目内）
TEST_CWD = str(PROJECT_ROOT / "测试文件夹")

#: 默认二进制：本机 npx 缓存中的 dsh（`npx @deepseek-ai/dsh` 的实指）
DEFAULT_BIN = str(Path.home() / ".npm" / "_npx" / "1e7f6d9597241db0"
                  / "node_modules" / ".bin" / "dsh")

TURN_TIMEOUT_S = 300  # 单轮次整体超时


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


def _run_turn(conn: AcpConnection, session_id: str, prompt_text: str,
              updates: list[dict], cancel_after_s: float | None = None) -> dict:
    """发一轮 prompt 并消费至收尾；cancel_after_s 非空时到时发 session/cancel。"""
    conn.begin_turn("session/prompt", {
        "sessionId": session_id,
        "prompt": [{"type": "text", "text": prompt_text}],
    })
    t0 = time.monotonic()
    cancelled = False
    type_counts: dict[str, int] = {}
    try:
        while True:
            try:
                kind, obj = conn.next_update(timeout=TURN_TIMEOUT_S)
            except queue.Empty:
                return {"stop_reason": "<timeout>", "seconds": round(time.monotonic() - t0, 1)}
            if (cancel_after_s is not None and not cancelled
                    and time.monotonic() - t0 >= cancel_after_s):
                cancelled = conn.cancel_turn(session_id)
            if kind == "dead":
                return {"stop_reason": f"<process dead: exit={obj}>",
                        "seconds": round(time.monotonic() - t0, 1)}
            if kind == "response":
                err = obj.get("error")
                stop = (f"<error {err.get('code')}: {err.get('message')}>"
                        if err else (obj.get("result") or {}).get("stopReason", "<unknown>"))
                return {"stop_reason": stop, "seconds": round(time.monotonic() - t0, 1),
                        "cancel_sent": cancelled}
            updates.append(obj)
            update_kind = ((obj.get("params") or {}).get("update") or {}).get(
                "sessionUpdate", "<missing>")
            type_counts[update_kind] = type_counts.get(update_kind, 0) + 1
    finally:
        conn.end_turn()


def _config_options_of(session_new_result: dict) -> dict:
    """从 session/new 响应提取 configuration-option 状态（结构探测，宽容解析）。"""
    for key in ("configOptions", "config_options", "configurationOptions"):
        if isinstance(session_new_result.get(key), list):
            return {key: session_new_result[key]}
    return {"_note": "session/new 响应未见配置项清单",
            "_keys": sorted(session_new_result.keys())}


def run_spike(bin_path: str) -> int:
    if not Path(bin_path).exists():
        print(f"dsh 二进制不存在：{bin_path}", file=sys.stderr)
        return 2

    out_dir = PROJECT_ROOT / ".temp" / "dsh_acp_spike"
    out_dir.mkdir(parents=True, exist_ok=True)
    recorder = FrameRecorder(out_dir / "frames.jsonl")

    result: dict = {"bin": bin_path.replace(str(Path.home()), "~")}  # 脱敏：家目录以 ~ 代（隐私扫描约定）
    updates: list[dict] = []
    conn: AcpConnection | None = None
    try:
        conn = AcpConnection(bin_path, TEST_CWD, "dsh acp")
        # 1. 握手（dsh 不支持 terminals → terminal=False）
        init_result = conn.request("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False,
            },
            "clientInfo": {"name": "zen-studio", "title": "Zen Studio",
                           "version": APP_VERSION},
        }, timeout=60)
        result["initialize_result"] = init_result

        # 2. session/new → 配置项状态
        session = conn.request("session/new", {"cwd": TEST_CWD, "mcpServers": []},
                               timeout=120)
        session_id = session["sessionId"]
        result["sessionId"] = session_id
        config_options = _config_options_of(session)
        result["config_options"] = config_options

        # 3. set_config_option 实测：从配置项清单挑一个非当前值的模型/档位切换
        set_results: dict[str, object] = {}
        options_list = next(iter(config_options.values()), [])
        if isinstance(options_list, list):
            for option in options_list:
                if not isinstance(option, dict):
                    continue
                config_id = option.get("id") or option.get("configId")
                choices = option.get("options") or []
                current = option.get("currentValue") or option.get("value")
                values = [c.get("value") for c in choices
                          if isinstance(c, dict) and isinstance(c.get("value"), str)]
                target = next((v for v in values if v != current), None)
                if not isinstance(config_id, str) or target is None:
                    continue
                try:
                    resp = conn.request("session/set_config_option", {
                        "sessionId": session_id, "configId": config_id,
                        "value": target}, timeout=15)
                    set_results[config_id] = {
                        "ok": True, "from": current, "to": target,
                        "response_keys": sorted(resp.keys())}
                except RuntimeError as e:
                    set_results[config_id] = {"ok": False, "error": str(e)}
        result["set_config_option_trials"] = set_results

        # 4. 单轮对话（触发 shell 工具 → 审批回环；无 handler = 自动 allow_once）
        result["turn1"] = _run_turn(
            conn, session_id,
            "请用 shell 工具执行 `ls` 列出当前目录的文件名，然后用一句话总结。",
            updates)
        result["turn1_frame_types"] = {}
        for obj in updates:
            kind = ((obj.get("params") or {}).get("update") or {}).get(
                "sessionUpdate", "<missing>")
            result["turn1_frame_types"][kind] = \
                result["turn1_frame_types"].get(kind, 0) + 1
        # 审批请求出现在录制帧中（out 方向无 id 响应配对，从 in 帧统计）
        result["turn1_text_sample"] = "".join(
            (((o.get("params") or {}).get("update") or {}).get("content") or {}).get("text") or ""
            for o in updates
            if ((o.get("params") or {}).get("update") or {}).get("sessionUpdate")
            == "agent_message_chunk")[:300]

        # 5. 取消语义：第二轮发出 2s 后取消
        result["turn2_cancel"] = _run_turn(
            conn, session_id, "请慢慢数从 1 到 50，每个数字一行。", [],
            cancel_after_s=2.0)
    except Exception as e:  # noqa: BLE001 — spike 须完整记录失败形态
        result["fatal_error"] = str(e)
    finally:
        if conn is not None:
            conn.terminate()
        recorder.restore()

    ts = time.strftime("%Y%m%d_%H%M%S")
    evidence_path = PROJECT_ROOT / "文档" / "帧存档" / f"dsh_spike_{ts}.json"
    evidence_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                             encoding="utf-8")
    print(f"原始帧 → {out_dir / 'frames.jsonl'}")
    print(f"证据摘要 → {evidence_path}")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin", default=DEFAULT_BIN, help="dsh 二进制路径")
    args = parser.parse_args()
    return run_spike(args.bin)


if __name__ == "__main__":
    sys.exit(main())
