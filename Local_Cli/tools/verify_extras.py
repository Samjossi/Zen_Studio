#!/usr/bin/env python3
"""Dream ACP 补充验证脚本 —— 断言 spike_handshake.py 未覆盖的协议项。

与 spike 互补（spike 覆盖：initialize、cwd 校验、常规流式、cancel、
审批 allow/reject、stopReason=error、模型别名切换、真工具循环）。本脚本断言：

 1. 未知方法（foo/bar）回 -32601（协议 §5 错误码表）
 2. session/prompt 未知 sessionId 回 -32602
 3. session/set_config_option 未知 sessionId 回 -32602
 4. session/set_config_option 未知 configId（非 "model"）回 -32602
 5. 空 text 块 prompt 不崩轮次、正常 end_turn（附录 A.4）
 6. stdout 无协议外输出（每行均为合法 JSON 帧，非 JSON 行显式计数；
    同时确认 stderr 确实收到日志——§1.2/A.6 日志纪律）
 7. ToolCallParser 单元级：合法块/坏 JSON/未知工具/缺必填参数/多块取末（§3.2）
 8. ToolExecutor 单元级：写读回环/逃逸三态（..、绝对路径、软链）拒绝/
    不存在文件/空目录列举（§3.2 沙盒纪律）
 9. config 装载：tools_enabled/tool_max_iterations 缺省/显式/非法三态（§3.5）
10. ToolCallFenceFilter 单元级：纯文本直通/围栏抑制/标记跨 chunk 切开/
    普通代码块不误伤/围栏后正文保留/空流与仅围栏流（上屏抑制计划 §3）

零第三方依赖（Python 3.10+ 标准库），复用 spike 的 AgentPipe 思路：
读帧守护线程 + 队列。stderr 单独起守护线程收集，用于断言日志通道。
7-10 为进程内单元断言（local_cli 以 editable 安装，直接 import）。

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
from pathlib import Path

from local_cli import config as config_module
from local_cli.toolkit.definitions import TOOL_CALL_FENCE_START_MARKER
from local_cli.toolkit.executor import ToolExecutor
from local_cli.toolkit.fence_filter import ToolCallFenceFilter
from local_cli.toolkit.parser import (
    ToolCallParseFailure,
    ToolCallParser,
    ToolCallRequest,
)

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


def _check_parser_units() -> None:
    """[7] ToolCallParser：§3.2 围栏格式的合规/不合规各态。"""
    print("[7] ToolCallParser 单元")
    parser = ToolCallParser()
    legal = parser.parse(
        "前言\n```tool_call\n"
        '{"tool": "write_file", "args": {"path": "a/b.md", "content": "hi"}}\n'
        "```\n尾")
    check("合法围栏块解析出 ToolCallRequest（工具名与参数正确）",
          isinstance(legal, ToolCallRequest)
          and legal.tool_name == "write_file"
          and legal.arguments == {"path": "a/b.md", "content": "hi"},
          f"结果: {legal}")
    bad_json = parser.parse("```tool_call\n{bad json}\n```")
    check("坏 JSON 产出结构化错误而非异常",
          isinstance(bad_json, ToolCallParseFailure) and "JSON" in bad_json.error_message,
          f"结果: {bad_json}")
    unknown = parser.parse('```tool_call\n{"tool": "rm", "args": {}}\n```')
    check("未知工具名被拒（错误文案列出可用工具）",
          isinstance(unknown, ToolCallParseFailure)
          and "write_file" in unknown.error_message,
          f"结果: {unknown}")
    missing = parser.parse('```tool_call\n{"tool": "write_file", "args": {"path": "a"}}\n```')
    check("缺必填参数被拒（文案点名 content）",
          isinstance(missing, ToolCallParseFailure) and "content" in missing.error_message,
          f"结果: {missing}")
    multi = parser.parse(
        '```tool_call\n{"tool": "read_file", "args": {"path": "x"}}\n```\n'
        '中间\n```tool_call\n{"tool": "list_dir", "args": {"path": "."}}\n```')
    check("多块取末个（末块是最新意图）",
          isinstance(multi, ToolCallRequest) and multi.tool_name == "list_dir",
          f"结果: {multi}")
    check("无围栏块的回复返回 None（正常文字轮次）",
          parser.parse("纯文字回复，没有工具调用") is None)


def _check_executor_units() -> None:
    """[8] ToolExecutor：cwd 沙盒纪律与三工具行为。"""
    print("[8] ToolExecutor 单元")
    sandbox = Path(VERIFY_DIR) / "unit_sandbox"
    sandbox.mkdir(parents=True, exist_ok=True)
    executor = ToolExecutor(sandbox)

    write = executor.execute(ToolCallRequest(
        tool_name="write_file",
        arguments={"path": "sub/poem.md", "content": "诗一行\n二行\n"}))
    read_back = executor.execute(ToolCallRequest(
        tool_name="read_file", arguments={"path": "sub/poem.md"}))
    check("写读回环（自动建父目录，UTF-8 内容一致）",
          write.is_success and read_back.is_success
          and read_back.output_text == "诗一行\n二行\n",
          f"写: {write} 读: {read_back}")

    dotdot = executor.execute(ToolCallRequest(
        tool_name="read_file", arguments={"path": "../escape.txt"}))
    check(".. 逃逸返回结构化错误（不抛异常）",
          not dotdot.is_success and "拒绝" in dotdot.output_text,
          f"结果: {dotdot}")
    absolute = executor.execute(ToolCallRequest(
        tool_name="write_file",
        arguments={"path": "/etc/zen_studio_probe.txt", "content": "x"}))
    check("绝对路径返回结构化错误",
          not absolute.is_success and "绝对路径" in absolute.output_text,
          f"结果: {absolute}")
    outside = Path(VERIFY_DIR) / "outside_secret.txt"
    outside.write_text("机密", encoding="utf-8")
    link = sandbox / "link.txt"
    if link.exists() or link.is_symlink():
        link.unlink()
    link.symlink_to("../outside_secret.txt")
    symlink = executor.execute(ToolCallRequest(
        tool_name="read_file", arguments={"path": "link.txt"}))
    check("软链逃逸返回结构化错误（resolve 后判定）",
          not symlink.is_success and "拒绝" in symlink.output_text,
          f"结果: {symlink}")

    nonexistent = executor.execute(ToolCallRequest(
        tool_name="read_file", arguments={"path": "nope.txt"}))
    check("不存在文件返回结构化错误",
          not nonexistent.is_success and "不存在" in nonexistent.output_text,
          f"结果: {nonexistent}")
    empty = sandbox / "empty_dir"
    empty.mkdir(exist_ok=True)
    listing = executor.execute(ToolCallRequest(
        tool_name="list_dir", arguments={"path": "empty_dir"}))
    check("空目录列举成功且文案可读",
          listing.is_success and "空目录" in listing.output_text,
          f"结果: {listing}")


def _check_config_units() -> None:
    """[9] config.py：tools_enabled / tool_max_iterations 三态装载（§3.5）。"""
    print("[9] config 工具键装载")

    def _load_with(config_text: str) -> config_module.LocalCliConfig:
        config_file = os.path.join(VERIFY_DIR, "unit_config.toml")
        with open(config_file, "w", encoding="utf-8") as f:
            f.write(config_text)
        previous = os.environ.get(config_module.ENV_CONFIG_PATH)
        os.environ[config_module.ENV_CONFIG_PATH] = config_file
        try:
            return config_module.load_config()
        finally:
            if previous is None:
                os.environ.pop(config_module.ENV_CONFIG_PATH, None)
            else:
                os.environ[config_module.ENV_CONFIG_PATH] = previous

    defaults = _load_with('backend = "mock"\n')
    check("缺省：tools_enabled=true / tool_max_iterations=5",
          defaults.tools_enabled is True and defaults.tool_max_iterations == 5,
          f"收到: {defaults.tools_enabled}/{defaults.tool_max_iterations}")
    explicit = _load_with('tools_enabled = false\ntool_max_iterations = 3\n')
    check("显式：false 与 3 原样生效",
          explicit.tools_enabled is False and explicit.tool_max_iterations == 3,
          f"收到: {explicit.tools_enabled}/{explicit.tool_max_iterations}")
    invalid = _load_with('tools_enabled = "yes"\ntool_max_iterations = 0\n')
    check("非法值：静默回退默认（配置层不许崩进程）",
          invalid.tools_enabled is True and invalid.tool_max_iterations == 5,
          f"收到: {invalid.tools_enabled}/{invalid.tool_max_iterations}")


def _check_fence_filter_units() -> None:
    """[10] ToolCallFenceFilter：两态状态机 + 尾部扣留的六类用例（计划 §3）。"""
    print("[10] ToolCallFenceFilter 单元")

    def _run(chunks: list[str]) -> tuple[str, str, bool]:
        """喂完返回 (上屏拼接, flush 扣留, 是否曾扣留)。"""
        fence_filter = ToolCallFenceFilter()
        visible = "".join(fence_filter.feed(chunk) for chunk in chunks)
        return visible, fence_filter.flush(), fence_filter.is_suspended

    plain = "纯文本回复，没有任何代码块，逐字节直通过滤器。"
    visible, held, suspended = _run([plain[i:i + 7] for i in range(0, len(plain), 7)])
    check("纯文本直通：上屏拼接与原文逐字节一致",
          visible + held == plain and not suspended,
          f"visible={visible!r} held={held!r}")

    fenced = ("先说一句话。\n```tool_call\n"
              '{"tool": "write_file", "args": {"path": "a.md", "content": "x"}}\n```\n')
    visible, held, suspended = _run([fenced[i:i + 5] for i in range(0, len(fenced), 5)])
    check("围栏抑制：上屏不含起始标记，扣留自标记起全文",
          TOOL_CALL_FENCE_START_MARKER not in visible
          and held.startswith(TOOL_CALL_FENCE_START_MARKER) and suspended
          and visible == "先说一句话。\n",
          f"visible={visible!r} held 头={held[:20]!r}")

    split_chunks = ["前文", "`", "``", "tool_ca", "ll\n{}", "\n```", "块后"]
    visible, held, suspended = _run(split_chunks)
    check("标记被 chunk 任意切开不错检：上屏无标记残片",
          TOOL_CALL_FENCE_START_MARKER not in visible
          and all(piece not in visible for piece in ("```to", "```tool_c"))
          and suspended and held.startswith(TOOL_CALL_FENCE_START_MARKER),
          f"visible={visible!r} held={held!r}")

    json_block = "看代码：\n```json\n{\"a\": 1}\n```\n还有 ```python\npass\n``` 完"
    visible, held, suspended = _run([json_block[i:i + 6] for i in range(0, len(json_block), 6)])
    check("普通代码块（```json/```python）零误伤、逐字节直通",
          visible + held == json_block and not suspended,
          f"visible={visible!r} held={held!r}")

    trailing = "前缀\n```tool_call\n{}\n```\n已成功写入文件。"
    visible, held, suspended = _run([trailing])
    check("围栏后正文保留在扣留原文里（待裁决后补发）",
          suspended and held.endswith("已成功写入文件。")
          and visible == "前缀\n",
          f"visible={visible!r} held 尾={held[-20:]!r}")

    empty_visible, empty_held, empty_suspended = _run([])
    only_visible, only_held, only_suspended = _run(["```tool_call\n{}\n```"])
    check("空流与仅围栏流：空流零产出；仅围栏流全量扣留",
          empty_visible == "" and empty_held == "" and not empty_suspended
          and only_visible == "" and only_held == "```tool_call\n{}\n```"
          and only_suspended,
          f"空流=({empty_visible!r},{empty_held!r}) 仅围栏=({only_visible!r},{only_held!r})")


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

        # -- 7-9. 进程内单元断言（parser/executor/config） -------------------
        _check_parser_units()
        _check_executor_units()
        _check_config_units()
        _check_fence_filter_units()
    finally:
        agent.close()
        shutil.rmtree(VERIFY_DIR, ignore_errors=True)

    print(f"\n== 结果：{_PASS} 过 / {_FAIL} 挂 ==")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
