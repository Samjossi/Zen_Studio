"""Kimi ACP provider：长驻 `kimi acp` 子进程 + JSON-RPC（ndjson 帧）对接。

kimi 唯一传输层（CLI `-p` 模式已移除，见 2026-0731-0036 计划）：长驻单进程、
token 级流式（`agent_message_chunk`）、思维链可见（`agent_thought_chunk`）、
会话原生管理（`session/new`）；历史由 agent 侧会话管理，仅取末条 user 消息作
prompt。凭证由 CLI 自管（OAuth），代码库零密钥。

连接层已泛化抽出（计划 2026-0730-0150 §4-D5）：帧收发/反向请求/死讯注入
见 llm/providers/acp.py 的 AcpConnection（agent_name 参数化）；本文件仅留
kimi 专有装配（initialize 载荷、-32000 authRequired 文案映射），二进制路径
解析复用 llm/providers/kimi_common.py 的 _find_bin。
行为与抽离前逐行等价。

上下文用量（1454 计划 T5，2026-07-31 实证落地）：kimi ACP 出口不推
usage_update（1412 T5 实测），轮次收尾改读 agent 会话落盘记录
`~/.kimi-code/session_index.jsonl` → sessionDir → agents/main/wire.jsonl
的末条 usage.record（API 真值，source="transcript"）；size 取末条
llm.request.maxTokens。usage.record 于 response 后约 1~3s 异步写盘，
轮次开始记基线条数、收尾短轮询（≤3s）只接受新记录，超时/残缺静默降级。

session/update 映射（1602 计划 T3）：私有 _map_update 已删除，统一改调
llm/providers/acp.py 的公共实现 map_session_update（D4 上收——原四份
逐行一致副本同一改动改四处必然漂移）。
"""
import atexit
import json
import threading
import time
from pathlib import Path
from typing import Iterator

from core.paths import PROJECT_ROOT  # agent 工作目录限定于项目根
from core.version import APP_VERSION
from llm.base import Chunk, LanguageModel, Message, UsageStats
# PermissionOption/ToolCallInfo 仅 re-export（llm/permission_policy 从此处取型）
from llm.providers.acp import (
    AcpConnection,
    PermissionHandler,
    PermissionOption,
    PermissionParams,
    ToolCallInfo,
    build_prompt_blocks,
    map_session_update,
)
from llm.providers.kimi_common import _find_bin

#: wire.jsonl usage.record 异步写盘等待上限（2026-07-31 实测：response 到达
#: 时 llm.request 已写但 usage.record 尚未落盘，约 1~3s 后异步写入）
_WIRE_USAGE_WAIT_S = 3.0
_WIRE_USAGE_POLL_S = 0.2

#: 轮次内轮询（0117 计划 T2）只读文件尾部的字节窗口：wire.jsonl 随会话增长
#: 可达 MB 级，每 2s 全量 read_text 不可接受；64KB 足够覆盖末多条
#: llm.request / usage.record（单行实测数百字节至数千字节）
_WIRE_TAIL_BYTES = 64 * 1024


def _session_dir_of(session_id: str) -> Path | None:
    """session_index.jsonl 按 sessionId 反查会话落盘目录；失败返回 None。

    2026-07-31 实证：ACP `session/new` 返回的 sessionId（`session_<uuid>`）
    与 `~/.kimi-code/session_index.jsonl` 的 sessionId 字段完全一致，索引行
    直接给出 sessionDir（wire.jsonl 在其 agents/main/ 下）。倒序扫描取最后
    匹配（索引按创建追加，同 sessionId 理论上唯一，倒序防御性取新）。
    """
    index = Path.home() / ".kimi-code" / "session_index.jsonl"
    try:
        lines = index.read_text(encoding="utf-8").strip().splitlines()
        for line in reversed(lines):
            entry = json.loads(line)
            if entry.get("sessionId") == session_id and entry.get("sessionDir"):
                return Path(entry["sessionDir"])
    except (OSError, json.JSONDecodeError):
        return None
    return None


def _read_wire_usage(session_dir: Path) -> tuple[int, UsageStats | None]:
    """读 wire.jsonl → (usage.record 条数, 末条用量定型)；文件缺失/损坏返回 (0, None)。

    末条口径（1454 计划 T5，E2 实证字段结构）：
    - used = inputOther + inputCacheRead（对齐 kilocode 推送口径 input+cache.read，
      不含 output 与 inputCacheCreation）；
    - size = 末条 `llm.request.maxTokens`（kimi-code 以模型窗口为请求上限，
      实测 262144 与 kilocode 推送的 size 交叉验证一致）；
    - source="transcript"（agent 落盘的 API 真值，非协议推送）。
    """
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    try:
        records = [json.loads(line)
                   for line in wire.read_text(encoding="utf-8").strip().splitlines()
                   if line.strip()]
    except (OSError, json.JSONDecodeError):
        return 0, None
    usage_records = [r for r in records
                     if r.get("type") == "usage.record" and r.get("usageScope") == "turn"]
    if not usage_records:
        return 0, None
    usage = usage_records[-1].get("usage") or {}
    used_other = usage.get("inputOther")
    used_cache = usage.get("inputCacheRead")
    requests = [r for r in records if r.get("type") == "llm.request"]
    max_tokens = requests[-1].get("maxTokens") if requests else None
    if (not isinstance(used_other, int) or used_other < 0
            or not isinstance(used_cache, int) or used_cache < 0
            or not isinstance(max_tokens, int) or max_tokens <= 0):
        return len(usage_records), None
    return len(usage_records), UsageStats(
        used=used_other + used_cache, size=max_tokens, cost=None, source="transcript")


def _read_wire_usage_tail(session_dir: Path) -> UsageStats | None:
    """轮次内轮询（0117 计划 T2/D2）：只读 wire.jsonl 尾部窗口，取末条用量定型。

    与收尾 `_poll_wire_usage` 语义相反——不卡基线条数，就要最新一条（轮次内
    任何子步骤的新记录皆可接受）；与 `_read_wire_usage` 全量读法区分——尾部
    seek 回溯 `_WIRE_TAIL_BYTES`，防 MB 级文件每 2s 全量读（红线）。

    尾部窗口内须同时找到末条 usage.record（used）与末条 llm.request
    （maxTokens 作 size）；窗口未覆盖 llm.request（理论罕见）或字段残缺、
    文件缺失/正被写半行（json 损坏行跳过）→ None，静默降级不抛出。
    """
    wire = session_dir / "agents" / "main" / "wire.jsonl"
    try:
        with wire.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - _WIRE_TAIL_BYTES))
            tail = f.read()
    except OSError:
        return None
    lines = tail.split(b"\n")
    if size > _WIRE_TAIL_BYTES and lines:
        lines = lines[1:]  # 窗口起点落在行中：丢弃首条残行
    used_other: int | None = None
    used_cache: int | None = None
    max_tokens: int | None = None
    for raw in reversed(lines):  # 倒序取末条：usage.record 与 llm.request 各取一
        if not raw.strip():
            continue
        try:
            r = json.loads(raw)
        except json.JSONDecodeError:
            continue  # 写盘半行/损坏行跳过
        rtype = r.get("type")
        if used_other is None and rtype == "usage.record" and r.get("usageScope") == "turn":
            usage = r.get("usage") or {}
            used_other = usage.get("inputOther")
            used_cache = usage.get("inputCacheRead")
        elif max_tokens is None and rtype == "llm.request":
            max_tokens = r.get("maxTokens")
        if used_other is not None and max_tokens is not None:
            break
    if (not isinstance(used_other, int) or used_other < 0
            or not isinstance(used_cache, int) or used_cache < 0
            or not isinstance(max_tokens, int) or max_tokens <= 0):
        return None
    return UsageStats(
        used=used_other + used_cache, size=max_tokens, cost=None, source="transcript")


class KimiAcpLLM(LanguageModel):
    """Kimi ACP 后端（长驻子进程 + ndjson JSON-RPC，token 级流式 + 思维链）。"""

    def __init__(self, model: str | None = None, workspace_root: str | None = None) -> None:
        """
        :param model: 模型别名（None = agent 默认模型 configOptions currentValue）
        :param workspace_root: agent 工作目录（None = 项目根；多开模式由启动参数注入）
        """
        self._model = model
        #: 推理强度预选值（2026-0806 计划；None = 未定制，agent 默认强度生效）
        self._effort: str | None = None
        self._cwd = workspace_root or str(PROJECT_ROOT)
        self._conn: AcpConnection | None = None
        self._session_id: str | None = None
        self._turn_lock = threading.Lock()
        #: 关闭标志（标签销毁）：close() 置位后 _ensure_session 拒绝新建连接，
        #: spawn 前后双检——与清理线程 close() 的竞态窗口内迟到的连接即建即杀
        self._closed = False
        #: 审批处理器（由 GUI 注入）：session/request_permission params → optionId | None
        self._permission_handler: PermissionHandler | None = None
        #: poll_usage 的会话目录缓存（0117 计划 T2）：session_index.jsonl 随会话
        #: 增长，每 2s 轮询重读全索引不可接受；按 session_id 缓存反查结果，
        #: 会话切换（reset/重建）时随 _session_id 变化失效重查
        self._poll_dir_sid: str | None = None
        self._poll_dir: Path | None = None
        atexit.register(self.close)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def set_model(self, alias: str) -> None:
        """切换模型别名（会话存在则即时生效，失败则降级为下次新会话生效）。"""
        self._model = alias
        if self._conn and self._conn.is_alive and self._session_id:
            try:
                self._conn.request("session/set_config_option", {
                    "sessionId": self._session_id, "configId": "model", "value": alias}, timeout=10)
            except RuntimeError:
                self._session_id = None  # 降级：下轮重建会话并应用模型

    def set_effort(self, value: str) -> None:
        """切换推理强度（2026-0806 计划；会话存在则即时生效）。

        生效机制同 set_model（D6 红线 3）：session/set_config_option
        （configId="thinking"——kimi configOptions 的 thinking 选择器，
        2026-0718-1555 实测），强度值原样透传不解析不校验（D6 红线 2 同款
        不透明字符串语义）；值域随模型而异（k3-256k 为 low/high/max，
        2026-0725-0205 实测），当前模型不支持的档位由 agent 侧拒绝。
        失败不丢弃会话（与 set_model 的差异）：强度是辅助控制轴，拒绝
        不该陪葬会话上下文——`_effort` 已记，下个新会话生效。
        """
        self._effort = value
        if self._conn and self._conn.is_alive and self._session_id:
            try:
                self._conn.request("session/set_config_option", {
                    "sessionId": self._session_id, "configId": "thinking",
                    "value": value}, timeout=10)
            except RuntimeError:
                pass  # 降级：保持会话与当前强度，新会话时应用 _effort

    def reset_session(self) -> None:
        """清空会话，下次请求 `session/new` 开新会话（进程保留）。"""
        self._session_id = None

    def set_workspace_root(self, root: str) -> None:
        """切换 agent 工作目录：丢弃旧 session（长驻进程保留，下次 session/new 用新 cwd）。

        当前无调用方（多开模型下工作区根进程级固定），按计划 2026-0722-0756 预留。
        """
        self._cwd = root
        self.reset_session()

    def set_permission_handler(self, handler: PermissionHandler | None) -> None:
        """注入审批处理器：params → optionId（None 视为拒绝）；None 恢复自动允许。"""
        self._permission_handler = handler
        if self._conn:
            self._conn.set_permission_handler(handler)

    def close(self) -> None:
        """终止 agent 子进程并注销 atexit 钩（多标签：实例随标签关闭销毁，
        不注销则绑定方法把已死实例钉在 atexit 注册表至进程退出）。

        `_closed` 先置位：`_ensure_session` 在 spawn 前后双检该标志，
        "close 之后新建的连接"语义上不可能存活（评审 CRITICAL#1：
        清理线程 terminate 与 worker spawn 竞态曾致 acp 进程孤儿）。
        """
        self._closed = True
        atexit.unregister(self.close)
        if self._conn:
            self._conn.terminate()
            self._conn = None
            self._session_id = None

    def cancel(self) -> None:
        """取消当前轮次（协议级）：发 `session/cancel`；无轮次时 no-op。

        不持 `_turn_lock`（cancel 从 GUI 线程来，锁由 chat 所在 worker 持有）；
        不终止连接进程——长驻连接是会话资产，仅结束当前轮次。
        """
        conn = self._conn
        if conn is not None and self._session_id:
            conn.cancel_turn(self._session_id)

    def _ensure_session(self) -> AcpConnection:
        if self._closed:  # 标签已销毁：拒绝新建连接（评审 CRITICAL#1）
            raise RuntimeError("kimi acp 后端已关闭（标签已销毁）")
        bin_path = _find_bin()
        if not bin_path:
            raise RuntimeError("kimi CLI 不可用：PATH 与 ~/.kimi-code/bin 均未找到 kimi")
        if self._conn is None or not self._conn.is_alive:
            if self._conn is not None:
                self._conn.terminate()
            self._conn = AcpConnection(bin_path, self._cwd, "kimi acp")
            if self._closed:  # spawn 与 close 竞态：迟到的连接即建即杀
                self._conn.terminate()
                self._conn = None
                raise RuntimeError("kimi acp 后端已关闭（标签已销毁）")
            self._conn.set_permission_handler(self._permission_handler)
            self._session_id = None
            self._conn.request("initialize", {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
                "clientInfo": {"name": "zen-studio", "title": "Zen Studio", "version": APP_VERSION},
            })
        if self._session_id is None:
            try:
                result = self._conn.request(
                    "session/new", {"cwd": self._cwd, "mcpServers": []}, timeout=60)
            except RuntimeError as e:
                if "-32000" in str(e):  # authRequired：凭证失效，需用户重新登录
                    raise RuntimeError("kimi 未登录或凭证已过期，请在终端执行 `kimi login` 后重试") from None
                raise
            self._session_id = result["sessionId"]
            if self._model:  # 新会话应用预选模型
                try:
                    self._conn.request("session/set_config_option", {
                        "sessionId": self._session_id, "configId": "model",
                        "value": self._model}, timeout=10)
                except RuntimeError:
                    pass  # 保持 agent 默认模型，不阻断对话
            if self._effort:  # 新会话应用预选推理强度（2026-0806 计划）
                try:
                    self._conn.request("session/set_config_option", {
                        "sessionId": self._session_id, "configId": "thinking",
                        "value": self._effort}, timeout=10)
                except RuntimeError:
                    pass  # 保持 agent 默认强度，不阻断对话
        return self._conn

    def poll_usage(self) -> UsageStats | None:
        """轮次内主动取用量快照（0117 计划 T2）：读 wire.jsonl 尾部末条记录。

        T0 实证：轮次内每次 API 调用后 usage.record(scope=turn) 增量写盘，
        数值单调爬升，可作为轮次内徽章数据源。语义见 D2——不卡基线条数，
        就要最新一条；收尾真值仍由 `_poll_wire_usage` 基线轮询兜底。

        线程安全：从 GUI QTimer 调用，与 chat 所在 worker 并发——`_session_id`
        只读单值、目录缓存读写均为原子赋值（GIL），读文件失败静默 None。
        """
        session_id = self._session_id
        if not session_id:
            return None
        if session_id != self._poll_dir_sid:
            self._poll_dir = _session_dir_of(session_id)
            self._poll_dir_sid = session_id
        if self._poll_dir is None:
            return None
        return _read_wire_usage_tail(self._poll_dir)

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------
    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        # 历史由 agent 会话管理，仅取末条 user 消息作 prompt（可携带图片附件，
        # 0340 方案 B：text+image 多块经 build_prompt_blocks 构造）
        message = next((m for m in reversed(messages) if m["role"] == "user"), None)
        if message is None or (not message["content"] and not message.get("images")):
            return
        with self._turn_lock:  # 串行化轮次，防 inbox 串抢
            conn = self._ensure_session()
            conn.purge_updates()
            conn.begin_turn("session/prompt", {
                "sessionId": self._session_id,
                "prompt": build_prompt_blocks(message),
            })
            try:
                yield from self._iter_turn_chunks(conn)
            finally:
                conn.end_turn()

    def _iter_turn_chunks(self, conn: AcpConnection) -> Iterator[Chunk]:
        """轮次内消息消费循环：update → Chunk；response/dead 收尾本轮。"""
        # 用量基线（1454 T5）：kimi 无 usage_update（1412 T5 实证），轮次收尾
        # 改读会话落盘 wire.jsonl。usage.record 于 response 后约 1~3s 异步写盘，
        # 且须防误读上一轮记录——轮次开始时记下 (session_dir, 已有条数) 基线，
        # 收尾只接受条数增长后的新记录。
        session_dir = _session_dir_of(self._session_id) if self._session_id else None
        baseline_count = _read_wire_usage(session_dir)[0] if session_dir else 0
        while True:
            kind, obj = conn.next_update()
            if kind == "dead":
                self._session_id = None
                raise RuntimeError(f"kimi acp 进程意外退出（退出码 {obj}）")
            if kind == "response":
                self._raise_on_turn_error(obj)
                stats = self._poll_wire_usage(session_dir, baseline_count)
                if stats is not None:
                    yield Chunk("usage", "", usage=stats)
                return
            chunk = map_session_update(obj)
            if chunk:
                yield chunk

    @staticmethod
    def _poll_wire_usage(
        session_dir: Path | None, baseline_count: int
    ) -> UsageStats | None:
        """轮询 wire.jsonl 直至出现本轮新 usage.record（条数 > 基线）或超时。

        在 worker 线程内短轮询（最长 _WIRE_USAGE_WAIT_S），不阻塞 GUI；
        超时/数据残缺 → None（徽章隐藏降级，D4 语义）。session_dir 为 None
        （索引未收录）时直接放弃，不轮询。
        """
        if session_dir is None:
            return None
        deadline = time.monotonic() + _WIRE_USAGE_WAIT_S
        while True:
            count, stats = _read_wire_usage(session_dir)
            if count > baseline_count:
                return stats  # 新记录已落盘（数据残缺则 stats=None，同样收尾）
            if time.monotonic() >= deadline:
                return None
            time.sleep(_WIRE_USAGE_POLL_S)

    @staticmethod
    def _raise_on_turn_error(response: dict) -> None:
        """prompt 响应含 error 时抛错（stopReason 正常结束为静默返回）。"""
        if "error" in response:
            err = response["error"]
            raise RuntimeError(f"kimi acp 对话失败 {err.get('code')}：{err.get('message')}")
