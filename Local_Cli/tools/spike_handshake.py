#!/usr/bin/env python3
"""Dream ACP 管道级 spike 断言脚本 —— 逐条验证《Dream ACP 接入协议 v1.0》。

以子进程管道直连 agent（argv `[bin, "acp"]`），不依赖任何测试框架与
第三方库（Python 3.10+ 标准库）。Dream 真实实现完成后，用
`--bin /path/to/local` 复用同一套断言做协议兼容冒烟。

断言覆盖（与协议文档逐条对应）：
 1. initialize 响应含 agentInfo（§1.3）
 2. session/new 接受绝对 cwd；拒相对路径（-32602，§1.4/A.2）
 3. 常规轮次：逐 chunk agent_message_chunk + agent_thought_chunk
    + usage_update（used/size 同帧）+ end_turn 收尾（§2）
 4. cancel 即停（stopReason=cancelled），停后同会话可续用（§1.6）
 5. 演示工具（「建文件」）：tool_call → session/request_permission
    （options 三态齐全）→ allow 实建文件；reject 放弃并追问（§3）
 6. 错误关键词「报错」→ stopReason="error"，非静默空回复（§2.4/A.3）
 7. set_config_option(configId="model") 两演示别名互切生效；
    未知别名报错不崩（§1.5）
 8. 真实工具循环（「协议工具」关键词，NORMAL 意图走 ToolLoopRunner）：
    审批通过→文件真实落盘→completed→end_turn；拒绝→failed→追问；
    allow_always 第二次免审批；tools_enabled=false 一键回退纯文本（§3.3/§3.5）

用法：
    python3 tools/spike_handshake.py [--bin .venv/bin/local-cli] [-v]
"""
import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading

#: spike 工作目录（会话 cwd）：本脚本同级 .spike_tmp/——自包含，搬走即跑
SPIKE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".spike_tmp")
DEFAULT_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", ".venv", "bin", "local-cli")

#: 真实工具循环回归的关键词与落盘文件（值与 local_cli/model/mock.py 的
#: TRIGGER_PROTOCOL_TOOL_KEYWORD / PROTOCOL_TOOL_FILE_NAME 保持一致；
#: spike 保持黑盒不 import 被测包，副本在此显式声明）
PROTOCOL_TOOL_KEYWORD = "协议工具"
PROTOCOL_TOOL_FILE_NAME = "protocol_tool_demo.txt"

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
    """最小 ACP 客户端：ndjson 帧收发 + id 配对。

    读帧走守护线程 + 队列（text 包装器的预读缓冲会让 select 误判
    「无数据」——缓冲行滞留 Python 层时 OS fd 已空，教训对齐
    Zen Studio acp.py 的 reader 线程设计）。
    """

    def __init__(self, bin_path: str, extra_config: str = "") -> None:
        # 回归必须自包含：用户真实配置（~/.local-cli/config.toml 可能指向
        # gguf 后端）不得渗入被测 agent，强制注入 hermetic mock 配置
        os.makedirs(SPIKE_DIR, exist_ok=True)
        config_path = os.path.join(SPIKE_DIR, "spike_config.toml")
        with open(config_path, "w", encoding="utf-8") as f:
            f.write('backend = "mock"\n' + extra_config)
        env = {**os.environ, "LOCAL_CLI_CONFIG": config_path}
        self._proc = subprocess.Popen(
            [bin_path, "acp"], cwd=SPIKE_DIR,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # 协议纪律：agent 日志走 stderr
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=env)
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

    def read_frame(self, timeout: float = 10.0) -> dict | None:
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def wait_turn(self, request_id: int, timeout: float = 15.0):
        """读取一轮：返回 (updates, reverse_requests, response)。

        updates: session/update 通知的 update 载荷列表；
        reverse_requests: agent 反向请求帧列表（调用方须自行 reply）；
        response: prompt 响应帧。
        """
        updates, reverses, response = [], [], None
        while response is None:
            frame = self.read_frame(timeout)
            if frame is None:
                raise TimeoutError(f"等待响应超时（id={request_id}）")
            if frame.get("method") == "session/update":
                updates.append((frame.get("params") or {}).get("update") or {})
            elif frame.get("method") and frame.get("id") is not None:
                reverses.append(frame)
                return updates, reverses, None  # 反向请求须先应答
            elif frame.get("id") == request_id:
                response = frame
        return updates, reverses, response

    def close(self) -> None:
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()


def _text_of(updates: list[dict], kind: str) -> str:
    return "".join((u.get("content") or {}).get("text") or ""
                   for u in updates if u.get("sessionUpdate") == kind)


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
    shutil.rmtree(SPIKE_DIR, ignore_errors=True)
    os.makedirs(SPIKE_DIR, exist_ok=True)

    print(f"== Local ACP spike（被测：{bin_path}）==")
    agent = AgentPipe(bin_path)
    try:
        # -- 1. initialize ------------------------------------------------
        print("[1] initialize")
        rid = agent.send("initialize", {
            "protocolVersion": 1,
            "clientCapabilities": {
                "fs": {"readTextFile": False, "writeTextFile": False},
                "terminal": False},
            "clientInfo": {"name": "local-acp-spike", "version": "1.0"}})
        resp = agent.read_frame()
        agent_info = (resp or {}).get("result", {}).get("agentInfo") or {}
        check("initialize 响应含 agentInfo(name/version)",
              bool(agent_info.get("name") and agent_info.get("version")),
              f"收到: {resp}")

        # -- 2. session/new：绝对 cwd 接受 / 相对拒收 ----------------------
        print("[2] session/new cwd 校验")
        rid = agent.send("session/new", {"cwd": "relative/path", "mcpServers": []})
        resp = agent.read_frame()
        err = (resp or {}).get("error") or {}
        check("相对 cwd 拒收 -32602", err.get("code") == -32602, f"收到: {resp}")

        rid = agent.send("session/new", {"cwd": SPIKE_DIR, "mcpServers": []})
        resp = agent.read_frame()
        session_id = ((resp or {}).get("result") or {}).get("sessionId")
        check("绝对 cwd 建会话成功", isinstance(session_id, str), f"收到: {resp}")

        # -- 3. 常规轮次：流式 + 思维链 + usage + end_turn -----------------
        print("[3] 常规轮次流式")
        rid = agent.send("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "你好"}]})
        updates, _, resp = agent.wait_turn(rid)
        kinds = [u.get("sessionUpdate") for u in updates]
        msg_text = _text_of(updates, "agent_message_chunk")
        check("收到逐 chunk 正文（≥2 个 agent_message_chunk）",
              kinds.count("agent_message_chunk") >= 2 and len(msg_text) > 0,
              f"chunks={kinds.count('agent_message_chunk')}")
        check("收到 agent_thought_chunk 思维链段",
              "agent_thought_chunk" in kinds
              and len(_text_of(updates, "agent_thought_chunk")) > 0)
        usage = next((u for u in updates
                      if u.get("sessionUpdate") == "usage_update"), None)
        check("usage_update 携带 used/size 同帧",
              isinstance(usage, dict) and isinstance(usage.get("used"), int)
              and isinstance(usage.get("size"), int) and usage["size"] > 0,
              f"usage={usage}")
        check("end_turn 收尾",
              ((resp or {}).get("result") or {}).get("stopReason") == "end_turn",
              f"resp={resp}")

        # -- 4. cancel 即停 + 停后续用 -------------------------------------
        print("[4] cancel")
        rid = agent.send("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "再来一轮"}]})
        agent.read_frame(5)  # 首帧（thought/chunk）到达后发 cancel，保轮次进行中
        agent.send("session/cancel", {"sessionId": session_id}, with_id=False)
        updates, _, resp = agent.wait_turn(rid)
        stop = ((resp or {}).get("result") or {}).get("stopReason")
        check("cancel 即停（stopReason=cancelled）", stop == "cancelled",
              f"stopReason={stop}")
        rid = agent.send("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "停后续用验证"}]})
        updates, _, resp = agent.wait_turn(rid)
        check("cancel 后同会话可续用",
              ((resp or {}).get("result") or {}).get("stopReason") == "end_turn"
              and len(_text_of(updates, "agent_message_chunk")) > 0)

        # -- 5. 工具审批：allow 实建 / reject 放弃追问 ----------------------
        print("[5] 审批回环")
        demo_file = os.path.join(SPIKE_DIR, "local_demo.txt")
        rid = agent.send("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "帮我建文件"}]})
        updates, reverses, _ = agent.wait_turn(rid)
        check("tool_call 通知先行",
              any(u.get("sessionUpdate") == "tool_call" for u in updates))
        check("收到 session/request_permission 反向请求",
              len(reverses) == 1
              and reverses[0].get("method") == "session/request_permission",
              f"reverses={reverses}")
        options = ((reverses[0].get("params") or {}).get("options") or []) \
            if reverses else []
        kinds_opt = {o.get("kind") for o in options}
        check("options 三态齐全（allow_once/allow_always/reject_once）",
              {"allow_once", "allow_always", "reject_once"} <= kinds_opt,
              f"kinds={kinds_opt}")
        allow_id = next((o["optionId"] for o in options
                         if o.get("kind") == "allow_once"), None)
        agent.reply(reverses[0]["id"], {
            "outcome": {"outcome": "selected", "optionId": allow_id}})
        updates2, _, resp = agent.wait_turn(rid)
        check("allow → tool_call_update completed",
              any(u.get("sessionUpdate") == "tool_call_update"
                  and u.get("status") == "completed" for u in updates2),
              f"updates={[(u.get('sessionUpdate'), u.get('status')) for u in updates2]}")
        check("allow → 演示文件实建", os.path.isfile(demo_file))
        check("allow → end_turn 收尾",
              ((resp or {}).get("result") or {}).get("stopReason") == "end_turn")

        if os.path.isfile(demo_file):
            os.remove(demo_file)
        rid = agent.send("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "再建文件一次"}]})
        updates, reverses, _ = agent.wait_turn(rid)
        reject_id = next((o["optionId"] for o in
                          ((reverses[0].get("params") or {}).get("options") or [])
                          if o.get("kind") == "reject_once"), None) \
            if reverses else None
        check("reject 路径同样收到反向请求", reject_id is not None)
        agent.reply(reverses[0]["id"], {
            "outcome": {"outcome": "selected", "optionId": reject_id}})
        updates2, _, resp = agent.wait_turn(rid)
        check("reject → tool_call_update failed",
              any(u.get("sessionUpdate") == "tool_call_update"
                  and u.get("status") == "failed" for u in updates2))
        check("reject → 文件未建", not os.path.isfile(demo_file))
        check("reject → agent 放弃并向用户追问",
              len(_text_of(updates2, "agent_message_chunk")) > 0)
        check("reject → end_turn 收尾",
              ((resp or {}).get("result") or {}).get("stopReason") == "end_turn")

        # -- 6. 错误路径：stopReason="error" --------------------------------
        print("[6] 错误路径")
        rid = agent.send("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "演示报错"}]})
        updates, _, resp = agent.wait_turn(rid)
        check("错误关键词 → stopReason=\"error\"（非静默空回复）",
              ((resp or {}).get("result") or {}).get("stopReason") == "error",
              f"resp={resp}")

        # -- 7. 模型切换 ----------------------------------------------------
        print("[7] set_config_option 模型切换")
        rid = agent.send("session/set_config_option", {
            "sessionId": session_id, "configId": "model",
            "value": "local/demo-smart"})
        resp = agent.read_frame()
        check("切换 local/demo-smart 成功", "error" not in (resp or {}),
              f"resp={resp}")
        rid = agent.send("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": "现在用哪个模型？"}]})
        updates, _, resp = agent.wait_turn(rid)
        check("切换后正文反映新别名",
              "demo-smart" in _text_of(updates, "agent_message_chunk"))
        rid = agent.send("session/set_config_option", {
            "sessionId": session_id, "configId": "model",
            "value": "local/nonexistent"})
        resp = agent.read_frame()
        check("未知别名显式报错（连接不崩）", "error" in (resp or {}),
              f"resp={resp}")
        rid = agent.send("session/set_config_option", {
            "sessionId": session_id, "configId": "model",
            "value": "local/demo-fast"})
        resp = agent.read_frame()
        check("报错后切回 demo-fast 仍可用（连接复用）",
              "error" not in (resp or {}))

        # -- 8. 真实工具循环（Mock 协议工具关键词驱动 ToolLoopRunner） ----------
        print("[8] 工具循环（协议工具）")
        tool_file = os.path.join(SPIKE_DIR, PROTOCOL_TOOL_FILE_NAME)

        def _find_option(reverse_frame, kind):
            return next((o["optionId"] for o in
                         ((reverse_frame.get("params") or {}).get("options") or [])
                         if o.get("kind") == kind), None)

        # 8a. 审批通过 → 文件真实落盘 → completed → end_turn
        rid = agent.send("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": f"演示{PROTOCOL_TOOL_KEYWORD}"}]})
        updates, reverses, _ = agent.wait_turn(rid)
        tool_call_update = next(
            (u for u in updates if u.get("sessionUpdate") == "tool_call"), None)
        check("真循环 tool_call 先行（id 为 tool-<session>-<迭代> 格式）",
              isinstance(tool_call_update, dict)
              and str(tool_call_update.get("toolCallId") or "").startswith("tool-"),
              f"tool_call={tool_call_update}")
        check("真循环收到 request_permission",
              len(reverses) == 1
              and reverses[0].get("method") == "session/request_permission",
              f"reverses={reverses}")
        agent.reply(reverses[0]["id"], {
            "outcome": {"outcome": "selected",
                        "optionId": _find_option(reverses[0], "allow_once")}})
        updates2, _, resp = agent.wait_turn(rid)
        check("真循环 allow → completed",
              any(u.get("sessionUpdate") == "tool_call_update"
                  and u.get("status") == "completed" for u in updates2),
              f"updates={[(u.get('sessionUpdate'), u.get('status')) for u in updates2]}")
        check("真循环 allow → 文件真实落盘且内容正确",
              os.path.isfile(tool_file)
              and open(tool_file, encoding="utf-8").read().strip() != "")
        check("真循环 allow → end_turn 收尾",
              ((resp or {}).get("result") or {}).get("stopReason") == "end_turn")
        # 上屏抑制判定线：正文通道全程无围栏起始标记（IDE 只渲染工具卡片）
        streamed_text = _text_of(updates, "agent_message_chunk") \
            + _text_of(updates2, "agent_message_chunk")
        check("真循环消息流不含围栏起始标记（明文抑制生效）",
              "```tool_call" not in streamed_text,
              f"streamed={streamed_text[:120]!r}")

        # 8b. 拒绝 → failed → 追问文案 → 文件未建 → end_turn
        if os.path.isfile(tool_file):
            os.remove(tool_file)
        rid = agent.send("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": f"再演示{PROTOCOL_TOOL_KEYWORD}"}]})
        updates, reverses, _ = agent.wait_turn(rid)
        check("真循环 reject 仍收到反向请求（allow_once 不留记忆）",
              len(reverses) == 1, f"reverses={reverses}")
        agent.reply(reverses[0]["id"], {
            "outcome": {"outcome": "selected",
                        "optionId": _find_option(reverses[0], "reject_once")}})
        updates2, _, resp = agent.wait_turn(rid)
        check("真循环 reject → tool_call_update failed",
              any(u.get("sessionUpdate") == "tool_call_update"
                  and u.get("status") == "failed" for u in updates2))
        check("真循环 reject → 放弃执行并向用户追问",
              len(_text_of(updates2, "agent_message_chunk")) > 0)
        check("真循环 reject → 文件未建",
              not os.path.isfile(tool_file))
        check("真循环 reject → end_turn 收尾",
              ((resp or {}).get("result") or {}).get("stopReason") == "end_turn")

        # 8c. allow_always：同会话第二次同工具免审批（§3.3 会话级记忆）
        rid = agent.send("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": f"演示{PROTOCOL_TOOL_KEYWORD}"}]})
        updates, reverses, _ = agent.wait_turn(rid)
        check("allow_always 首次仍弹审批",
              len(reverses) == 1, f"reverses={reverses}")
        agent.reply(reverses[0]["id"], {
            "outcome": {"outcome": "selected",
                        "optionId": _find_option(reverses[0], "allow_always")}})
        updates2, _, resp = agent.wait_turn(rid)
        check("allow_always 首次执行落盘",
              any(u.get("sessionUpdate") == "tool_call_update"
                  and u.get("status") == "completed" for u in updates2)
              and os.path.isfile(tool_file))
        if os.path.isfile(tool_file):
            os.remove(tool_file)
        rid = agent.send("session/prompt", {
            "sessionId": session_id,
            "prompt": [{"type": "text", "text": f"再演示{PROTOCOL_TOOL_KEYWORD}"}]})
        updates, reverses, resp = agent.wait_turn(rid)
        check("allow_always 第二次免审批（无反向请求，轮次直接收尾）",
              not reverses and resp is not None,
              f"reverses={reverses}")
        check("allow_always 第二次仍真实执行（文件重建 + completed）",
              os.path.isfile(tool_file)
              and any(u.get("sessionUpdate") == "tool_call_update"
                      and u.get("status") == "completed" for u in updates))
        check("allow_always 第二次 end_turn 收尾",
              ((resp or {}).get("result") or {}).get("stopReason") == "end_turn")

        # 8d. tools_enabled=false 一键回退：围栏块按纯文本流式，无工具帧无审批
        print("[8d] tools_enabled=false 回退")
        agent_no_tools = AgentPipe(bin_path, extra_config="tools_enabled = false\n")
        try:
            rid = agent_no_tools.send("initialize", {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False},
                "clientInfo": {"name": "local-acp-spike", "version": "1.0"}})
            agent_no_tools.read_frame()
            rid = agent_no_tools.send("session/new",
                                      {"cwd": SPIKE_DIR, "mcpServers": []})
            resp = agent_no_tools.read_frame()
            session_no_tools = ((resp or {}).get("result") or {}).get("sessionId")
            if os.path.isfile(tool_file):
                os.remove(tool_file)
            rid = agent_no_tools.send("session/prompt", {
                "sessionId": session_no_tools,
                "prompt": [{"type": "text", "text": f"演示{PROTOCOL_TOOL_KEYWORD}"}]})
            updates, reverses, resp = agent_no_tools.wait_turn(rid)
            check("回退模式无 tool_call 帧且无审批请求",
                  not reverses and not any(
                      u.get("sessionUpdate") in ("tool_call", "tool_call_update")
                      for u in updates),
                  f"reverses={reverses}")
            check("回退模式围栏块按纯文本上屏且 end_turn、文件未建",
                  "```tool_call" in _text_of(updates, "agent_message_chunk")
                  and ((resp or {}).get("result") or {}).get("stopReason") == "end_turn"
                  and not os.path.isfile(tool_file))
        finally:
            agent_no_tools.close()
    finally:
        agent.close()
        shutil.rmtree(SPIKE_DIR, ignore_errors=True)

    print(f"\n== 结果：{_PASS} 过 / {_FAIL} 挂 ==")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
