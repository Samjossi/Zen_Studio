#!/usr/bin/env python3
"""GGUF 后端冒烟脚本 —— 真实 llama-server 全链路验证（不经 spike 断言体系）。

自包含：自动在 Local_Cli/.temp/ 写测试配置（backend=gguf，model_dir 指向
用户模型目录），经 `$LOCAL_CLI_CONFIG` 注入被测 agent，不影响用户态配置。

断言覆盖：
 1. `local models` 列出 GGUF 别名（stdout 纯别名行）
 2. initialize / session/new 正常
 3. set_config_option 切到目标模型别名成功，未知别名报错不崩
 4. prompt 收到 agent_thought_chunk（reasoning_content）与
    agent_message_chunk（content）双通道流式，usage_update 真实统计，end_turn 收尾
 5. cancel 即停（stopReason=cancelled）
 6. 真实模型工具轮：prompt 明确要求 write_file 写 smoke_poem.md——硬断言
    轮次完整收尾、连接不崩、正文通道无围栏起始标记（流式抑制，服务端行为
    可控）；软断言文件真实落盘（模型是否遵守 tool_call 围栏格式不在服务端
    掌控，落盘失败只警告不计挂）
 7. 结束后无遗留 llama-server 进程（atexit 回收生效）

用法：
    .venv/bin/python tools/smoke_gguf.py [--alias Qwen3.5-9B-UD-Q4_K_XL] [-v]
    模型目录非 ~/models 时用 LOCAL_CLI_MODEL_DIR=/path/to/dir 指定。
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

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMP_DIR = os.path.join(PROJECT_ROOT, ".temp")
SMOKE_DIR = os.path.join(TEMP_DIR, "smoke_gguf")
#: 真实模型工具轮的会话 cwd（独立目录：写盘副作用与常规轮次隔离，跑完自清）
TOOL_ROUND_DIR = os.path.join(TEMP_DIR, "smoke_tool_round")
CONFIG_PATH = os.path.join(TEMP_DIR, "test_config.toml")
DEFAULT_BIN = os.path.join(PROJECT_ROOT, ".venv", "bin", "local-cli")

#: 工具轮要求模型写入的文件名（prompt 与软断言共用同一名字，避免散写副本）
TOOL_ROUND_FILE_NAME = "smoke_poem.md"

#: 模型目录：`LOCAL_CLI_MODEL_DIR` 环境变量优先，缺省 ~/models——真实绝对路径
#: 属本机隐私，只存在环境/用户配置里，不落代码库（隐私门禁红线，见 D5）
MODEL_DIR = os.environ.get("LOCAL_CLI_MODEL_DIR", os.path.expanduser("~/models"))
DEFAULT_ALIAS = "Qwen3.5-9B-UD-Q4_K_XL"

_PASS = 0
_FAIL = 0
#: 软断言单独计数：模型指令遵循失败不该染红服务端回归（计划 §4.1）
_SOFT_PASS = 0
_SOFT_WARN = 0
_VERBOSE = False


def check(name: str, ok: bool, detail: str = "") -> None:
    global _PASS, _FAIL
    if ok:
        _PASS += 1
        print(f"  PASS  {name}")
    else:
        _FAIL += 1
        print(f"  FAIL  {name}" + (f"  —— {detail}" if detail else ""))


def soft_check(name: str, ok: bool, detail: str = "") -> None:
    """软断言：失败只警告不计挂（模型行为不在服务端掌控内）。"""
    global _SOFT_PASS, _SOFT_WARN
    if ok:
        _SOFT_PASS += 1
        print(f"  PASS(软)  {name}")
    else:
        _SOFT_WARN += 1
        print(f"  ⚠️ WARN(软)  {name}（模型未遵循约定，非服务端故障）"
              + (f"  —— {detail}" if detail else ""))


def _info(msg: str) -> None:
    if _VERBOSE:
        print(f"        {msg}")


def _llama_server_pids() -> set[int]:
    """当前 llama-server 进程集合（前后对比，验证无孤儿遗留）。"""
    result = subprocess.run(["pgrep", "-f", "llama-server --model"],
                            capture_output=True, text=True)
    return {int(line) for line in result.stdout.split() if line.strip()}


class AgentPipe:
    """最小 ACP 客户端（与 spike 同思路：读帧守护线程 + 队列；stderr 收集日志）。"""

    def __init__(self, bin_path: str, env: dict[str, str]) -> None:
        self._proc = subprocess.Popen(
            [bin_path, "acp"], cwd=SMOKE_DIR, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1)
        self._next = 0
        self._frames: queue.Queue[dict | None] = queue.Queue()
        #: stderr 日志行（模型加载进度等信息量排障用）
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
                self._frames.put(json.loads(line))
            except json.JSONDecodeError:
                _info(f"stdout 非 JSON 行: {line[:120]!r}")
        self._frames.put(None)

    def _read_stderr(self) -> None:
        assert self._proc.stderr is not None
        for line in self._proc.stderr:
            line = line.strip()
            if line:
                self.stderr_lines.append(line)
                _info(f"stderr: {line[:120]}")

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
        """应答 agent 反向请求（§3：审批回执）。"""
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": request_id, "result": result},
            ensure_ascii=False) + "\n")
        self._proc.stdin.flush()

    def read_frame(self, timeout: float = 30.0) -> dict | None:
        try:
            return self._frames.get(timeout=timeout)
        except queue.Empty:
            return None

    def wait_turn(self, request_id: int, timeout: float = 600.0):
        """读一轮：(updates, response, 首 chunk 耗时秒)。模型加载同步阻塞在此期间。"""
        updates, response = [], None
        first_chunk_seconds = None
        started = time.monotonic()
        deadline = started + timeout
        while response is None:
            frame = self.read_frame(max(1.0, deadline - time.monotonic()))
            if frame is None:
                raise TimeoutError(f"等待响应超时（id={request_id}）")
            if frame.get("method") == "session/update":
                update = (frame.get("params") or {}).get("update") or {}
                updates.append(update)
                if first_chunk_seconds is None and update.get("sessionUpdate") in (
                        "agent_message_chunk", "agent_thought_chunk"):
                    first_chunk_seconds = time.monotonic() - started
            elif frame.get("id") == request_id and "method" not in frame:
                response = frame
        return updates, response, first_chunk_seconds

    def wait_tool_turn(self, request_id: int, timeout: float = 600.0):
        """工具轮专用读一轮：审批反向请求自动应答 allow_once，否则 agent 干等超时。

        返回 (updates, response, 审批往返次数, toolCallId 列表)——观测数据
        原样带出，迭代次数与审批回环由调用方打印/断言。
        """
        updates, response = [], None
        permission_roundtrips = 0
        tool_call_ids: list[str] = []
        deadline = time.monotonic() + timeout
        while response is None:
            frame = self.read_frame(max(1.0, deadline - time.monotonic()))
            if frame is None:
                raise TimeoutError(f"等待响应超时（id={request_id}）")
            if frame.get("method") == "session/update":
                update = (frame.get("params") or {}).get("update") or {}
                updates.append(update)
                if update.get("sessionUpdate") == "tool_call":
                    tool_call_ids.append(str(update.get("toolCallId")))
            elif frame.get("method") == "session/request_permission":
                permission_roundtrips += 1
                options = (frame.get("params") or {}).get("options") or []
                allow_id = next((o["optionId"] for o in options
                                 if o.get("kind") == "allow_once"), None)
                _info(f"审批请求 {frame.get('id')} → 应答 {allow_id}")
                self.reply(frame["id"], {
                    "outcome": {"outcome": "selected", "optionId": allow_id}})
            elif frame.get("id") == request_id and "method" not in frame:
                response = frame
        return updates, response, permission_roundtrips, tool_call_ids

    def close(self) -> None:
        self._proc.terminate()
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()


def _text_of(updates: list[dict], kind: str) -> str:
    return "".join((u.get("content") or {}).get("text") or ""
                   for u in updates if u.get("sessionUpdate") == kind)


def _write_test_config() -> None:
    os.makedirs(TEMP_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as config_file:
        config_file.write(
            'backend = "gguf"\n'
            f'model_dir = "{MODEL_DIR}"\n'
            # ctx 不能太小：thinking 模型的思维链占 token 预算，
            # 2048 会被思考耗尽导致正文零输出（实测踩坑）
            "ctx_size = 4096\n"
            "threads = 8\n")


def main() -> int:
    global _VERBOSE
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bin", default=DEFAULT_BIN, help="被测 agent 二进制路径")
    parser.add_argument("--alias", default=DEFAULT_ALIAS, help="目标模型别名")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    _VERBOSE = args.verbose

    bin_path = os.path.abspath(args.bin)
    if not os.path.isfile(bin_path):
        print(f"agent 不存在：{bin_path}")
        return 2
    _write_test_config()
    shutil.rmtree(SMOKE_DIR, ignore_errors=True)
    os.makedirs(SMOKE_DIR, exist_ok=True)
    shutil.rmtree(TOOL_ROUND_DIR, ignore_errors=True)
    os.makedirs(TOOL_ROUND_DIR, exist_ok=True)
    env = dict(os.environ, LOCAL_CLI_CONFIG=CONFIG_PATH)

    print(f"== GGUF 冒烟（被测：{bin_path}，模型：{args.alias}）==")
    pids_before = _llama_server_pids()
    try:
        # -- 1. local models 枚举 ------------------------------------------
        print("[1] local models 枚举")
        listing = subprocess.run([bin_path, "models"], env=env,
                                 capture_output=True, text=True)
        aliases = [line.strip() for line in listing.stdout.splitlines() if line.strip()]
        check("models 退出码为 0", listing.returncode == 0,
              f"stderr={listing.stderr.strip()}")
        check("枚举出目标别名", args.alias in aliases, f"aliases={aliases}")

        # -- 2. 握手建会话 --------------------------------------------------
        print("[2] initialize + session/new")
        agent = AgentPipe(bin_path, env)
        try:
            rid = agent.send("initialize", {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False},
                "clientInfo": {"name": "local-gguf-smoke", "version": "1.0"}})
            resp = agent.read_frame()
            check("initialize 成功", "result" in (resp or {}), f"收到: {resp}")
            rid = agent.send("session/new", {"cwd": SMOKE_DIR, "mcpServers": []})
            resp = agent.read_frame()
            session_id = ((resp or {}).get("result") or {}).get("sessionId")
            check("建会话成功", isinstance(session_id, str), f"收到: {resp}")

            # -- 3. 模型切换白名单 -------------------------------------------
            print("[3] set_config_option 模型切换")
            rid = agent.send("session/set_config_option", {
                "sessionId": session_id, "configId": "model",
                "value": args.alias})
            resp = agent.read_frame()
            check("切换目标别名成功", "error" not in (resp or {}), f"resp={resp}")
            rid = agent.send("session/set_config_option", {
                "sessionId": session_id, "configId": "model",
                "value": "local/nonexistent"})
            resp = agent.read_frame()
            check("未知别名显式报错（连接不崩）", "error" in (resp or {}),
                  f"resp={resp}")

            # -- 4. 真实流式轮次（含冷启动加载） ------------------------------
            print("[4] 流式轮次（首轮含模型加载，耗时较长属预期）")
            rid = agent.send("session/prompt", {
                "sessionId": session_id,
                # 轻量 prompt 控耗时：thinking 模型的话痨思维链按 token 计费时间
                "prompt": [{"type": "text", "text": "你好"}]})
            updates, resp, first_chunk = agent.wait_turn(rid)
            thought_text = _text_of(updates, "agent_thought_chunk")
            message_text = _text_of(updates, "agent_message_chunk")
            check("收到思维链通道（reasoning_content）", len(thought_text) > 0)
            check("收到正文通道（content）", len(message_text) > 0)
            usage = next((u for u in updates
                          if u.get("sessionUpdate") == "usage_update"), None)
            check("usage_update 真实统计（used/size 同帧）",
                  isinstance(usage, dict) and isinstance(usage.get("used"), int)
                  and isinstance(usage.get("size"), int) and usage["size"] > 0,
                  f"usage={usage}")
            check("end_turn 收尾",
                  ((resp or {}).get("result") or {}).get("stopReason") == "end_turn",
                  f"resp={resp}")
            if first_chunk is not None:
                print(f"        首 chunk 耗时（含模型加载）：{first_chunk:.1f} 秒")
            if _VERBOSE:
                print(f"        思维链: {thought_text[:200]!r}")
                print(f"        正文: {message_text[:200]!r}")

            # -- 5. cancel 即停 ----------------------------------------------
            print("[5] cancel")
            rid = agent.send("session/prompt", {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": "讲一个很长的故事。"}]})
            agent.read_frame(300)  # 首帧到达后 cancel，保轮次进行中
            agent.send("session/cancel", {"sessionId": session_id}, with_id=False)
            updates, resp, _ = agent.wait_turn(rid)
            stop = ((resp or {}).get("result") or {}).get("stopReason")
            check("cancel 即停（stopReason=cancelled）", stop == "cancelled",
                  f"stopReason={stop}")

            # -- 6. 真实模型工具轮 ---------------------------------------------
            # 硬断言：轮次完整收尾 + 连接不崩；软断言：文件真实落盘——模型是否
            # 遵循 tool_call 围栏格式不在服务端掌控（计划 §4.1：软断言失败只警告）
            print("[6] 真实模型工具轮（软断言失败不计挂）")
            rid = agent.send("session/new",
                             {"cwd": TOOL_ROUND_DIR, "mcpServers": []})
            resp = agent.read_frame()
            tool_session = ((resp or {}).get("result") or {}).get("sessionId")
            check("工具轮建会话成功（cwd=smoke_tool_round）",
                  isinstance(tool_session, str), f"收到: {resp}")
            # 显式切到同一别名：会话默认别名是字典序首个，不切会误加载 35B 实例
            rid = agent.send("session/set_config_option", {
                "sessionId": tool_session, "configId": "model",
                "value": args.alias})
            resp = agent.read_frame()
            check("工具轮切换目标别名成功（复用已加载实例）",
                  "error" not in (resp or {}), f"resp={resp}")
            rid = agent.send("session/prompt", {
                "sessionId": tool_session,
                "prompt": [{"type": "text", "text": (
                    f"请使用 write_file 工具把一首四行中文短诗写入文件 "
                    f"{TOOL_ROUND_FILE_NAME}（就在当前工作目录，用相对路径）。"
                    "写完后用一句话告诉我结果。")}]})
            updates, resp, roundtrips, tool_call_ids = agent.wait_tool_turn(rid)
            stop = ((resp or {}).get("result") or {}).get("stopReason")
            check("工具轮完整收尾（stopReason 协议三态之一，连接不崩）",
                  stop in ("end_turn", "cancelled", "error"), f"stopReason={stop}")
            iterations = [int(tid.rsplit("-", 1)[-1])
                          for tid in tool_call_ids if tid.rsplit("-", 1)[-1].isdigit()]
            print(f"        观测：审批往返 {roundtrips} 次，"
                  f"tool_call 帧 {len(tool_call_ids)} 个（迭代序号 {iterations}），"
                  f"stopReason={stop}")
            if _VERBOSE:
                print(f"        正文: {_text_of(updates, 'agent_message_chunk')[:300]!r}")
            # 硬断言：服务端流式过滤行为可控，正文通道不得出现围栏起始标记
            check("工具轮正文通道无围栏起始标记（明文抑制生效）",
                  "```tool_call" not in _text_of(updates, "agent_message_chunk"),
                  f"正文: {_text_of(updates, 'agent_message_chunk')[:200]!r}")
            poem_path = os.path.join(TOOL_ROUND_DIR, TOOL_ROUND_FILE_NAME)
            poem_exists = os.path.isfile(poem_path) \
                and os.path.getsize(poem_path) > 0
            soft_check(f"文件 {TOOL_ROUND_FILE_NAME} 真实落盘且内容非空",
                       poem_exists,
                       "模型未按 §3.2 围栏格式输出 tool_call（指令遵循问题）")
            if poem_exists:
                with open(poem_path, encoding="utf-8") as poem_file:
                    print(f"        落盘内容: {poem_file.read()[:200]!r}")
            # 连接活性复检：同别名切换零成本（_ensure_server 同名即返回）
            rid = agent.send("session/set_config_option", {
                "sessionId": tool_session, "configId": "model",
                "value": args.alias})
            resp = agent.read_frame()
            check("工具轮后连接仍可复用", "error" not in (resp or {}),
                  f"resp={resp}")
        finally:
            agent.close()

        # -- 7. 无孤儿进程 ---------------------------------------------------
        print("[7] 进程回收")
        time.sleep(2)  # atexit 回收留一点宽限
        leaked = _llama_server_pids() - pids_before
        check("无遗留 llama-server 进程", not leaked, f"遗留 pid={leaked}")
        if leaked:
            for pid in leaked:
                os.kill(pid, 9)
    finally:
        shutil.rmtree(SMOKE_DIR, ignore_errors=True)
        shutil.rmtree(TOOL_ROUND_DIR, ignore_errors=True)

    print(f"\n== 结果：{_PASS} 过 / {_FAIL} 挂"
          f"（软断言 {_SOFT_PASS} 过 / {_SOFT_WARN} 警告）==")
    return 1 if _FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
