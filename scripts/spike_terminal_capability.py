#!/usr/bin/env python3
"""ACP terminal/* 反向能力 spike —— 实测各 agent 是否会用客户端终端跑命令。

以子进程管道直连 agent（argv `[bin, acp]`），握手时声明
`clientCapabilities.terminal: true`，随后下发一条必触发 Bash 的 prompt，
观察 agent 是否发 `terminal/create` 反向请求。为给出真实结论，脚本对
terminal/* 请求做**忠实客户端模拟**：收到 create 即真实执行命令（subprocess，
无 PTY），output/wait_for_exit 回真实输出与退出码——agent 走完全程才算
「支持且可用」。

判定口径：
- 全程未见 terminal/create → 该 agent 不使用客户端终端（命令在其内部进程跑）；
- 见到 terminal/create 且轮次正常收尾 → 支持；
- 见到 terminal/create 但轮次异常（error/超时）→ 支持但集成有风险，看日志。

用法：
    .venv/bin/python scripts/spike_terminal_capability.py [--bin /path/to/agent] [-v]
"""
import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time

#: spike 工作目录（会话 cwd）：项目根 .temp/ 下按 被测agent+pid 唯一化
#:（AGENTS.md：临时文件禁出项目；并发跑多个 agent 时共享目录会互删——实证踩坑）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SPIKE_BASE = os.path.join(PROJECT_ROOT, ".temp")

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
    """最小 ACP 客户端：ndjson 帧收发 + id 配对（对齐 dream-acp spike 模板）。"""

    def __init__(self, bin_path: str, acp_arg: str, spike_dir: str) -> None:
        self._proc = subprocess.Popen(
            [bin_path, acp_arg], cwd=spike_dir,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # 协议纪律：agent 日志走 stderr
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        self._next = 0
        self._frames: queue.Queue[dict | None] = queue.Queue()
        threading.Thread(target=self._reader, daemon=True).start()

    def _reader(self) -> None:
        assert self._proc.stdout is not None
        for line in self._proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._frames.put(json.loads(line))
            except json.JSONDecodeError:
                _info(f"非 JSON 帧: {line[:120]!r}")  # 丢弃污染行
        self._frames.put(None)  # EOF

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

    def reply(self, request_id, result: dict) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "result": result},
            ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def reply_error(self, request_id, code: int, message: str) -> None:
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": request_id,
             "error": {"code": code, "message": message}},
            ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def read_frame(self, timeout: float = 10.0) -> dict | None:
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def close(self) -> None:
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()


class FakeTerminalBank:
    """terminal/* 反向请求的忠实客户端模拟：真实执行命令并记账。

    无 PTY（spike 只验证协议走向，不验证渲染）；每 terminalId 记
    (进程结果, 是否已 release)。命令经 subprocess.run 同步执行——
    spike 命令都是秒级短命令，可接受。
    """

    def __init__(self, spike_dir: str) -> None:
        self._spike_dir = spike_dir
        self._serial = 0
        self._terms: dict[str, subprocess.CompletedProcess | None] = {}
        self.created_commands: list[str] = []

    def handle(self, agent: AgentPipe, frame: dict) -> None:
        method = frame["method"]
        params = frame.get("params") or {}
        rid = frame["id"]
        if method == "terminal/create":
            self._serial += 1
            term_id = f"spike-term-{self._serial}"
            command = params.get("command") or ""
            cmd_args = list(params.get("args") or [])
            cwd = params.get("cwd") or self._spike_dir
            self.created_commands.append(" ".join([command] + cmd_args))
            _info(f"terminal/create: {[command] + cmd_args!r} cwd={cwd}")
            try:
                # args 为空时 command 多为整行 shell 串（reasonix 实证），走 shell 执行
                if cmd_args:
                    result = subprocess.run(
                        [command] + cmd_args, cwd=cwd,
                        capture_output=True, text=True, timeout=60)
                else:
                    result = subprocess.run(
                        command, cwd=cwd, shell=True,
                        capture_output=True, text=True, timeout=60)
                self._terms[term_id] = result
            except (OSError, subprocess.TimeoutExpired) as e:
                _info(f"命令执行失败: {e}")
                self._terms[term_id] = None
            agent.reply(rid, {"terminalId": term_id})
        elif method == "terminal/output":
            term = self._terms.get(params.get("terminalId") or "")
            output = ""
            exit_status = None
            if term is not None:
                output = (term.stdout or "") + (term.stderr or "")
                exit_status = {"exitCode": term.returncode, "signal": None}
            result: dict = {"output": output, "truncated": False}
            if exit_status is not None:
                result["exitStatus"] = exit_status
            agent.reply(rid, result)
        elif method == "terminal/wait_for_exit":
            term = self._terms.get(params.get("terminalId") or "")
            agent.reply(rid, {"exitCode": term.returncode if term is not None else 1,
                              "signal": None})
        elif method in ("terminal/kill", "terminal/release"):
            agent.reply(rid, {})
        else:
            agent.reply_error(rid, -32601, "method not found")


def _allow_permission(agent: AgentPipe, frame: dict) -> None:
    """审批反向请求自动放行（取 allow_once，无则取首个选项）。"""
    options = ((frame.get("params") or {}).get("options")) or []
    chosen = next((o for o in options if o.get("kind") == "allow_once"), None)
    chosen = chosen or (options[0] if options else None)
    if chosen is None:
        agent.reply(frame["id"], {"outcome": {"outcome": "cancelled"}})
        return
    agent.reply(frame["id"], {
        "outcome": {"outcome": "selected", "optionId": chosen["optionId"]}})


def main() -> int:
    global _VERBOSE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin", default=None,
                        help="被测 agent 二进制路径（缺省自动探测 kimi）")
    parser.add_argument("--acp-arg", default="acp", help="acp 子命令名（默认 acp）")
    parser.add_argument("--turn-timeout", type=float, default=300.0,
                        help="整轮超时秒数（LLM 延迟计入，默认 300）")
    parser.add_argument("--model", default=None,
                        help="建会话后先 set_config_option 选模型（如 opencode 默认模型可能挂起）")
    parser.add_argument("--prompt", default=(
        "请用 Bash 工具执行 echo hello-from-terminal，"
        "然后把输出原样告诉我。不要解释，直接执行。"),
        help="触发工具调用的 prompt（默认促 Bash）")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    _VERBOSE = args.verbose

    bin_path = args.bin
    if bin_path is None:
        bin_path = shutil.which("kimi")
        if bin_path is None:
            candidate = os.path.expanduser("~/.kimi-code/bin/kimi")
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                bin_path = candidate
    if not bin_path or not os.path.isfile(bin_path):
        print(f"agent 不存在：{bin_path or '（未探测到 kimi，请 --bin 指定）'}")
        return 2
    bin_path = os.path.abspath(bin_path)
    agent_tag = os.path.basename(bin_path)
    spike_dir = os.path.join(SPIKE_BASE, f"spike_terminal_{agent_tag}_{os.getpid()}")
    shutil.rmtree(spike_dir, ignore_errors=True)
    os.makedirs(spike_dir, exist_ok=True)

    print(f"== ACP terminal/* 能力 spike（被测：{bin_path}）==")
    agent = AgentPipe(bin_path, args.acp_arg, spike_dir)
    bank = FakeTerminalBank(spike_dir)
    try:
        # -- 1. initialize：声明 terminal 能力 ----------------------------
        print("[1] initialize（terminal: true）")
        agent.send("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": True},
            "clientInfo": {"name": "zen-studio-terminal-spike", "version": "1.0"}})
        resp = agent.read_frame(30)
        check("initialize 握手成功", bool(resp and "result" in resp),
              f"收到: {resp}")

        # -- 2. session/new ------------------------------------------------
        print("[2] session/new")
        agent.send("session/new", {"cwd": spike_dir, "mcpServers": []})
        resp = agent.read_frame(60)
        session_id = ((resp or {}).get("result") or {}).get("sessionId")
        check("建会话成功", isinstance(session_id, str), f"收到: {resp}")
        if not isinstance(session_id, str):
            print("\n== 会话建立失败，终止 ==")
            return 1

        # -- 2.5 可选：选模型（默认模型可能未配置/挂起，opencode 实证）------
        if args.model:
            print(f"[2.5] set_config_option model={args.model}")
            agent.send("session/set_config_option", {
                "sessionId": session_id, "configId": "model", "value": args.model})
            resp = agent.read_frame(30)
            check("选模型成功", bool(resp and "error" not in resp), f"收到: {resp}")

        # -- 3. 必触发 Bash 的轮次：观察反向请求走向 ------------------------
        print("[3] 触发 Bash 轮次（观察 terminal/* 反向请求）")
        rid = agent.send("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": args.prompt}]})
        deadline = time.monotonic() + args.turn_timeout
        prompt_resp = None
        tool_titles: list[str] = []
        while prompt_resp is None and time.monotonic() < deadline:
            frame = agent.read_frame(max(1.0, deadline - time.monotonic()))
            if frame is None:
                break
            method = frame.get("method")
            if method == "session/update":
                update = (frame.get("params") or {}).get("update") or {}
                if update.get("sessionUpdate") == "tool_call":
                    tool_titles.append(str(update.get("title") or ""))
                    _info(f"tool_call: {update.get('title')!r} kind={update.get('kind')!r}")
            elif method and frame.get("id") is not None:
                _info(f"反向请求: {method}")
                if method == "session/request_permission":
                    _allow_permission(agent, frame)
                elif method.startswith("terminal/") or method.startswith("fs/"):
                    bank.handle(agent, frame)
                else:
                    agent.reply_error(frame["id"], -32601, "method not found")
            elif frame.get("id") == rid:
                prompt_resp = frame

        check("轮次在超时内收尾", prompt_resp is not None,
              f"turn-timeout={args.turn_timeout}s")
        if prompt_resp is not None:
            stop = ((prompt_resp.get("result") or {}).get("stopReason"))
            check("轮次正常收尾（stopReason=end_turn）", stop == "end_turn",
                  f"stopReason={stop} resp={prompt_resp}")

        # -- 4. 判定 --------------------------------------------------------
        print("[4] 判定")
        check("agent 发起 terminal/create（使用客户端终端）",
              len(bank.created_commands) > 0,
              f"工具调用标题={tool_titles}")
        if bank.created_commands:
            check("命令真实执行且输出含 hello-from-terminal",
                  any("hello-from-terminal"
                      in (t.stdout or "") if t else False
                      for t in bank._terms.values()),
                  f"create 命令={bank.created_commands}")
    finally:
        agent.close()
        shutil.rmtree(spike_dir, ignore_errors=True)

    print(f"\n== 结果：{_PASS} 过 / {_FAIL} 挂 ==")
    if bank.created_commands:
        print("== 结论：该 agent 支持 ACP terminal/* 反向能力 ==")
    else:
        print("== 结论：该 agent 未使用客户端终端（命令在其内部进程执行）==")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
