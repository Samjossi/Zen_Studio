#!/usr/bin/env python3
"""Dream ACP 补充验证脚本 —— 断言 spike_handshake.py 未覆盖的协议项。

与 spike 互补（spike 覆盖：initialize、cwd 校验、常规流式、cancel、
审批 allow/reject、stopReason=error、模型别名切换）。本脚本断言：

 1. 未知方法（foo/bar）回 -32601（协议 §5 错误码表）
 2. session/prompt 未知 sessionId 回 -32602
 3. session/set_config_option 未知 sessionId 回 -32602
 4. session/set_config_option 未知 configId（非 "model"）回 -32602
 5. 空 text 块 prompt 不崩轮次、正常 end_turn（附录 A.4）
 6. stdout 无协议外输出（每行均为合法 JSON 帧，非 JSON 行显式计数；
    同时确认 stderr 确实收到日志——§1.2/A.6 日志纪律）

零第三方依赖（Python 3.10+ 标准库），复用 spike 的 AgentPipe 思路：
读帧守护线程 + 队列。stderr 单独起守护线程收集，用于断言日志通道。

用法：
    .venv/bin/python tools/verify_extras.py [--bin .venv/bin/local-cli] [-v]
"""
import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading

#: 验证工作目录（会话 cwd）：本脚本同级 .verify_tmp/——自包含，搬走即跑
VERIFY_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".verify_tmp")
DEFAULT_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", ".venv", "bin", "local-cli")

_PASS = 0
_FAIL = 0
_VERBOSE = False


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}" + (f"  —— {detail}" if detail else ""))


def _info(msg: str) -> None:
    if _VERBOSE:
        print(f"        {msg}")


class AgentPipe:
    """最小 ACP 客户端：ndjson 帧收发 + id 配对 + stdout 纯度显式记录。

    与 spike.AgentPipe 的两点差异：
    - stdout 非 JSON 行不静默丢弃，而是记入 polluted_lines 供最终断言
      （§1.2/A.6：stdout 混入任何非协议输出即污染协议流）；
    - stderr 以守护线程逐行收集（spike 直接 DEVNULL），用于断言日志
      确实走了 stderr 通道。
    """

    def __init__(self, bin_path: str) -> None:
        # 回归必须自包含：用户真实配置（~/.local-cli/config.toml 可能指向
        # gguf 后端）不得渗入被测 agent，强制注入 hermetic mock 配置
        os.makedirs(VERIFY_DIR, exist_ok=True)
        config_path = os.path.join(VERIFY_DIR, "verify_config.toml")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write('backend = "mock"\n')
        env = {**os.environ, "LOCAL_CLI_CONFIG": config_path}
        self._proc = subprocess.Popen(
            [bin_path, "acp"], cwd=VERIFY_DIR,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=env)
        self._next = 0
        self._frames: queue.Queue[dict | None] = queue.Queue()
        #: stdout 上的非 JSON 行（协议污染）；正常应恒为空
        self.polluted_lines: list[str] = []
        #: stderr 收到的日志行（验证日志通道存在）
        self.stderr_lines: list[str] = []
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                frame = json.loads(line)
            except json.JSONDecodeError:
                self.polluted_lines.append(line)
                _info(f"stdout 非 JSON 行: {line[:120]!r}")
                continue
            if not isinstance(frame, dict):
                # 合法 JSON 但不是对象帧（如裸字符串/数字），同样视为污染
                self.polluted_lines.append(line)
                continue
            self._frames.put(frame)
        self._frames.put(None)  # EOF

    def _read_stderr(self) -> None:
        assert self._proc.stderr is not None
        for line in self._proc.stderr:
            line = line.strip()
            if line:
                self.stderr_lines.append(line)

    def send(self, method: str, params: dict, with_id: bool = True) -> int | None:
        self._next += 1
        msg = {"jsonrpc": "2.0", "method": method, "params": params}
        request_id = None
        if with_id:
            request_id = self._next
            msg["id"] = request_id
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self._proc.stdin.flush()
        return request_id

    def read_frame(self, timeout: float = 10.0) -> dict | None:
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def wait_turn(self, request_id: int, timeout: float = 15.0):
        """读取一轮：返回 (updates, response)。反向请求不在本脚本覆盖范围。"""
        updates, response = [], None
        while response is None:
            frame = self.read_frame(timeout)
            if frame is None:
                raise TimeoutError(f"等待响应超时（id={request_id}）")
            if frame.get("method") == "session/update":
                updates.append((frame.get("params") or {}).get("update") or {})
            elif frame.get("id") == request_id and "method" not in frame:
                response = frame
        return updates, response

    def close(self) -> None:
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()


def _error_code_of(frame: dict | None) -> int | None:
    return (((frame or {}).get("error")) or {}).get("code")


def main() -> int:
    global _VERBOSE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin", default=DEFAULT_BIN, help="被测 agent 二进制路径")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    _VERBOSE = args.verbose

    bin_path = os.path.abspath(args.bin)
    if not os.path.isfile(bin_path):
        print(f"agent 不存在：{bin_path}")
        return 2
    shutil.rmtree(VERIFY_DIR, ignore_errors=True)
    os.makedirs(VERIFY_DIR, exist_ok=True)

    print(f"== Local ACP 补充验证（被测：{bin_path}）==")
    agent = AgentPipe(bin_path)
    try:
        # -- 0. 前置：initialize + 建会话 ----------------------------------
        print("[0] 前置握手")
        rid = agent.send("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False},
            "clientInfo": {"name": "local-acp-verify-extras", "version": "1.0"}})
        resp = agent.read_frame()
        check("initialize 成功", "result" in (resp or {}), f"收到: {resp}")
        rid = agent.send("session/new", {"cwd": VERIFY_DIR, "mcpServers": []})
        resp = agent.read_frame()
        session_id = ((resp or {}).get("result") or {}).get("sessionId")
        check("建会话成功", isinstance(session_id, str), f"收到: {resp}")

        # -- 1. 未知方法 → -32601 -------------------------------------------
        print("[1] 未知方法")
        rid = agent.send("foo/bar", {})
        resp = agent.read_frame()
        check("未知方法 foo/bar 回 -32601",
              _error_code_of(resp) == -32601, f"收到: {resp}")

        # -- 2. 未知 sessionId → -32602 -------------------------------------
        print("[2] 未知 sessionId")
        rid = agent.send("session/prompt", {
            "sessionId": "local-session-999",
            "prompt": [{"type": "text", "text": "你好"}]})
        resp = agent.read_frame()
        check("session/prompt 未知 sessionId 回 -32602",
              _error_code_of(resp) == -32602, f"收到: {resp}")
        rid = agent.send("session/set_config_option", {
            "sessionId": "local-session-999", "configId": "model",
            "value": "local/demo-fast"})
        resp = agent.read_frame()
        check("session/set_config_option 未知 sessionId 回 -32602",
              _error_code_of(resp) == -32602, f"收到: {resp}")

        # -- 3. 未知 configId → -32602 --------------------------------------
        print("[3] 未知 configId")
        rid = agent.send("session/set_config_option", {
            "sessionId": session_id, "configId": "theme", "value": "dark"})
        resp = agent.read_frame()
        check("未知 configId（非 model）回 -32602",
              _error_code_of(resp) == -32602, f"收到: {resp}")

        # -- 4. 空 text 块 prompt（A.4） -------------------------------------
        print("[4] 空 text 块")
        rid = agent.send("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": ""}]})
        updates, resp = agent.wait_turn(rid)
        stop = ((resp or {}).get("result") or {}).get("stopReason")
        check("空 text 块不崩轮次，正常 end_turn",
              stop == "end_turn", f"stopReason={stop}")
        message_text = "".join((u.get("content") or {}).get("text") or ""
                               for u in updates
                               if u.get("sessionUpdate") == "agent_message_chunk")
        check("空 text 块仍有流式正文（非静默空回复）", len(message_text) > 0)

        # -- 5. stdout 纯度 + stderr 日志通道 --------------------------------
        print("[5] 输出通道纪律")
        # 先补一轮正常对话，让 stderr 日志与 stdout 帧都充分产生
        rid = agent.send("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "通道纪律验证"}]})
        agent.wait_turn(rid)
        check("stdout 无协议外输出（每行均为合法 JSON 帧）",
              not agent.polluted_lines,
              f"污染行: {agent.polluted_lines[:3]}")
        check("stderr 收到 agent 日志（日志未混入 stdout）",
              len(agent.stderr_lines) > 0,
              "stderr 为空")
    finally:
        agent.close()
        shutil.rmtree(VERIFY_DIR, ignore_errors=True)

    print(f"\n== 结果：{_PASS} 过 / {_FAIL} 挂 ==")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
