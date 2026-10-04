"""Local ACP provider：长驻 `local-cli acp` 子进程 + JSON-RPC（ndjson 帧）对接。

以 dream_acp.py 为底本逐行同构改写：会话生命周期、审批回环、流式映射
全部复用泛化连接层 AcpConnection（llm/providers/acp.py）。协议面以
dream-acp/protocol/dream-acp-v1.md 为真值来源（Local CLI 与 Dream CLI
共用同一套线协议；Local_Cli/ 子项目即由 Dream_Cli 复制改名而来）。

与 dream_acp 的差异（均写在本文件 docstring，逐条可核对）：
1. `_find_bin()` 三级范式：PATH → `$LOCAL_HOME/bin/local-cli` →
   `~/.local-cli/bin/local-cli`（同 dream 的 DREAM_HOME 范式换名）。
2. `list_local_models()` 走 `local-cli models` 子进程动态枚举（opencode
   同款范式）：Local CLI 服务端扫描配置的 GGUF 目录输出别名，IDE 不持
   静态表。dream 的静态表 `dream-creator` 是收敛期产物，不适用。
3. 错误文案指向 Local CLI 自有配置体系（~/.local-cli/）。
4. 思维链通道：llama-server 的 SSE 流携带独立 reasoning_content 字段，
   由 Local_Cli 服务端映射为 agent_thought_chunk，客户端经公共
   map_session_update 直接上屏，本文件零特殊处理。

session/update 映射统一走公共实现 map_session_update。
"""
import atexit
import logging
import os
import shutil
import subprocess
import sys
import threading
from typing import Iterator

from core.paths import PROJECT_ROOT  # agent 工作目录限定于项目根
from core.version import APP_VERSION
from llm.base import Chunk, LanguageModel, Message
from llm.providers.acp import (
    AcpConnection,
    PermissionHandler,
    TerminalHandler,
    build_client_capabilities,
    build_prompt_blocks,
    map_session_update,
)

logger = logging.getLogger(__name__)

#: 二进制名取 local-cli 而非 local：local 是 bash/POSIX shell 内建保留字，
#: 用户在终端敲 `local models` 会被 shell 拦下（只能在函数中使用）
LOCAL_BIN = "local-cli"

#: ACP terminal/* 反向能力支持矩阵（2026-0817-1522 五 agent 实测报告）：
#: kimi/reasonix 支持；本 agent 不支持时保持 terminal: false（行为与现状等价）
_TERMINAL_CAPABILITY = False


def _find_bin() -> str | None:
    """解析 local-cli 二进制路径：PATH → $LOCAL_HOME/bin/local-cli → ~/.local-cli/bin/local-cli。

    桌面启动 Zen Studio 时 PATH 可能不含 local-cli 安装目录（如 ~/.local/bin
    未入桌面会话 PATH），fallback 避免误判未安装——对齐 dream/reasonix
    _find_bin 三级范式，安装根目录环境变量为 LOCAL_HOME。
    """
    if path := shutil.which(LOCAL_BIN):
        return path
    candidates: list = []
    if home := os.environ.get("LOCAL_HOME"):
        candidates.append(os.path.join(home, "bin", LOCAL_BIN))
    candidates.append(os.path.join(os.path.expanduser("~"), ".local-cli", "bin", LOCAL_BIN))
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def local_available() -> bool:
    """检测 local CLI 是否可用（PATH 或默认安装位置存在）。"""
    return _find_bin() is not None


def list_local_models() -> list[str]:
    """spawn `local-cli models` 枚举本地 GGUF 模型别名；失败返回空列表。

    输出契约：纯文本、每行一个模型别名（Local_Cli 服务端扫描 model_dir
    下的 *.gguf 产出）。解析策略与 opencode 同款：逐行 strip、跳过空行；
    15s timeout 与 kimi/opencode 枚举同值。失败/超时/空输出 → 空列表
    （R2 兜底纪律：空列表 = 用 agent 默认模型，不崩 UI）。
    """
    bin_path = _find_bin()
    if not bin_path:
        return []
    try:
        proc = subprocess.run(
            [bin_path, "models"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        if proc.returncode != 0:
            return []
        return [line.strip() for line in proc.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        logger.exception("local models 执行失败")
        return []


class LocalAcpLLM(LanguageModel):
    """Local ACP 后端（长驻子进程 + ndjson JSON-RPC，token 级流式 + 思维链）。"""

    def __init__(self, model: str | None = None, workspace_root: str | None = None) -> None:
        """
        :param model: 模型别名（不透明字符串；None = agent 默认模型）
        :param workspace_root: agent 工作目录（None = 项目根；多开模式由启动参数注入）。
            归一化为绝对路径——`session/new` 硬校验 cwd 必须绝对
            （-32602，reasonix 2026-07-30 实测先例，协议 §1.4 沿用），
            abspath 对已绝对输入幂等
        """
        self._model = model
        #: 推理强度预选值（白名单唯一合法值 auto；
        #: None = 未定制，agent 默认强度生效）
        self._effort: str | None = None
        self._cwd = os.path.abspath(workspace_root or str(PROJECT_ROOT))
        self._conn: AcpConnection | None = None
        self._session_id: str | None = None
        self._turn_lock = threading.Lock()
        #: 关闭标志（标签销毁）：close() 置位后 _ensure_session 拒绝新建连接，
        #: spawn 前后双检——与清理线程 close() 的竞态窗口内迟到的连接即建即杀
        self._closed = False
        #: 审批处理器（由 GUI 注入）：session/request_permission params → optionId | None
        self._permission_handler: PermissionHandler | None = None
        #: 终端处理器（GUI 桥经 set_terminal_handler 注入）；None = 不声明
        #: terminal 能力（无 GUI 的 headless 场景自动退化 false）
        self._terminal_handler: TerminalHandler | None = None
        atexit.register(self.close)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def set_model(self, alias: str) -> None:
        """切换模型别名（会话存在则即时生效，失败则降级为下次新会话生效）。

        生效机制自持（D6 红线 3）：`session/set_config_option(configId="model")`，
        别名原样透传，不解析不拼接（D6 红线 2）。
        """
        self._model = alias
        if self._conn and self._conn.is_alive and self._session_id:
            try:
                self._conn.request("session/set_config_option", {
                    "sessionId": self._session_id, "configId": "model", "value": alias}, timeout=10)
            except RuntimeError:
                logger.exception("切换模型失败，降级为下轮重建会话")
                self._session_id = None  # 降级：下轮重建会话并应用模型

    def set_effort(self, value: str) -> None:
        """切换推理强度（会话存在则即时生效）。

        生效机制同 set_model（D6 红线 3）：session/set_config_option
        （configId="effort"，白名单唯一合法值 auto），强度值原样透传不解析
        不校验（D6 红线 2 同款不透明字符串语义）。失败不丢弃会话（与
        set_model 的差异）：强度是辅助控制轴，拒绝不该陪葬会话上下文——
        `_effort` 已记，下个新会话生效。
        """
        self._effort = value
        if self._conn and self._conn.is_alive and self._session_id:
            try:
                self._conn.request("session/set_config_option", {
                    "sessionId": self._session_id, "configId": "effort",
                    "value": value}, timeout=10)
            except RuntimeError:
                logger.exception("切换推理强度失败，降级为新会话生效")
                pass  # 降级：保持会话与当前强度，新会话时应用 _effort

    def reset_session(self) -> None:
        """清空会话，下次请求 `session/new` 开新会话（进程保留）。"""
        self._session_id = None

    def set_workspace_root(self, root: str) -> None:
        """切换 agent 工作目录：丢弃旧 session（长驻进程保留，下次 session/new 用新 cwd）。

        当前无调用方（多开模型下工作区根进程级固定），按计划 2026-0722-0756 预留。
        """
        self._cwd = os.path.abspath(root)  # session/new 硬校验绝对路径（-32602 实测）
        self.reset_session()

    def set_permission_handler(self, handler: PermissionHandler | None) -> None:
        """注入审批处理器：params → optionId（None 视为拒绝）；None 恢复自动允许。"""
        self._permission_handler = handler
        if self._conn:
            self._conn.set_permission_handler(handler)

    def set_terminal_handler(self, handler: TerminalHandler | None) -> None:
        """注入终端处理器（terminal/* 反向请求路由）；None 恢复 terminal: false。"""
        self._terminal_handler = handler
        if self._conn:
            self._conn.set_terminal_handler(handler)

    def close(self) -> None:
        """终止 agent 子进程并注销 atexit 钩（多标签：实例随标签关闭销毁，
        不注销则绑定方法把已死实例钉在 atexit 注册表至进程退出）。

        `_closed` 先置位：`_ensure_session` 在 spawn 前后双检该标志，
        "close 之后新建的连接"语义上不可能存活（与 ReasonixAcpLLM 同一竞态防护）。
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
        if self._closed:  # 标签已销毁：拒绝新建连接
            raise RuntimeError("local acp 后端已关闭（标签已销毁）")
        bin_path = _find_bin()
        if not bin_path:
            raise RuntimeError(
                "local-cli 不可用：PATH、$LOCAL_HOME/bin 与 ~/.local-cli/bin 均未找到 local-cli")
        if self._conn is None or not self._conn.is_alive:
            if self._conn is not None:
                self._conn.terminate()
            self._conn = AcpConnection(bin_path, self._cwd, "local acp")
            if self._closed:  # spawn 与 close 竞态：迟到的连接即建即杀
                self._conn.terminate()
                self._conn = None
                raise RuntimeError("local acp 后端已关闭（标签已销毁）")
            self._conn.set_permission_handler(self._permission_handler)
            self._conn.set_terminal_handler(self._terminal_handler)
            self._session_id = None
            init_result = self._conn.request("initialize", {
                "protocolVersion": 1,
                "clientCapabilities": build_client_capabilities(
                    terminal=_TERMINAL_CAPABILITY and self._terminal_handler is not None),
                "clientInfo": {"name": "zen-studio", "title": "Zen Studio", "version": APP_VERSION},
            })
            self._log_agent_info(init_result)
        if self._session_id is None:
            try:
                result = self._conn.request(
                    "session/new", {"cwd": self._cwd, "mcpServers": []}, timeout=60)
            except RuntimeError as e:
                self._raise_config_hint_if_auth(e)
                raise
            self._session_id = result["sessionId"]
            if self._model:  # 新会话应用预选模型
                try:
                    self._conn.request("session/set_config_option", {
                        "sessionId": self._session_id, "configId": "model",
                        "value": self._model}, timeout=10)
                except RuntimeError:
                    logger.exception("新会话应用预选模型失败")
                    pass  # 保持 agent 默认模型，不阻断对话
            if self._effort:  # 新会话应用预选推理强度
                try:
                    self._conn.request("session/set_config_option", {
                        "sessionId": self._session_id, "configId": "effort",
                        "value": self._effort}, timeout=10)
                except RuntimeError:
                    logger.exception("新会话应用预选推理强度失败")
                    pass  # 保持 agent 默认强度，不阻断对话
        return self._conn

    @staticmethod
    def _log_agent_info(init_result: dict) -> None:
        """记录 agent 版本信息（诊断用 stderr 日志）。

        2026-07-30 实测修正沿用（R1 差异对齐）：`authMethods` 是**静态能力
        声明**，不能作未配置信号；真正的模型未加载/配置缺失信号在
        `session/new`（-32603 类错误），归 `_raise_config_hint_if_auth`
        映射。initialize 响应仅留版本日志，不做拦截。
        """
        agent = init_result.get("agentInfo") or {}
        print(f"[local acp] agent {agent.get('name', '?')} {agent.get('version', '?')}",
              file=sys.stderr)

    @staticmethod
    def _raise_config_hint_if_auth(error: RuntimeError) -> None:
        """session/new 认证/配置类错误 → 「本地模型未加载/配置缺失」引导文案。

        宽松判定（D5：各 provider 自行翻译认证错误）：错误消息含
        auth/unauthorized/login/config/not configured/未加载 任一关键词
        （不区分大小写）即视为认证/配置类失败。引导指向 Local CLI 自有
        配置体系（~/.local-cli/）。宽松而非精确匹配的理由：宁可多映射
        少数误伤（文案仍指向正确动作），不可漏映射让用户面对裸协议错误。
        """
        message = str(error).lower()
        if any(keyword in message
               for keyword in ("auth", "unauthorized", "login", "config",
                               "not configured", "未加载")):
            raise RuntimeError(
                "本地模型未加载或配置缺失，请检查 Local CLI 配置"
                "（~/.local-cli/config.toml 的 model_dir）并确认 GGUF 模型就绪") from None

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------
    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        # 历史由 agent 会话管理，仅取末条 user 消息作 prompt（可携带图片附件，
        # 0340 方案 B：text+image 多块经 build_prompt_blocks 构造；当前 GGUF
        # 均为文本模型，能力位 False 由 GUI 消费，协议通道不受阻）
        message = next((m for m in reversed(messages) if m["role"] == "user"), None)
        if message is None or (not message["content"] and not message.get("images")):
            return
        with self._turn_lock:  # 串行化轮次，防 inbox 串抢
            conn = self._ensure_session()
            conn.purge_updates()
            conn.begin_turn("session/prompt", {
                "sessionId": self._session_id,
                "prompt": build_prompt_blocks(message, workspace_root=self._cwd),
            })
            try:
                yield from self._iter_turn_chunks(conn)
            finally:
                conn.end_turn()

    def _iter_turn_chunks(self, conn: AcpConnection) -> Iterator[Chunk]:
        """轮次内消息消费循环：update → Chunk；response/dead 收尾本轮。

        usage_update 是协议正式通道，经 map_session_update 直接产出 usage
        Chunk 上屏；轮次收尾不做 transcript 估算。Local_Cli 侧暂无真实
        用量数据时不发帧，徽章保持隐藏（不臆造上限）。
        """
        while True:
            kind, obj = conn.next_update()
            if kind == "dead":
                self._session_id = None
                raise RuntimeError(f"local acp 进程意外退出（退出码 {obj}）")
            if kind == "response":
                self._raise_on_turn_error(obj)
                return
            chunk = map_session_update(obj)
            if chunk:
                yield chunk

    @staticmethod
    def _raise_on_turn_error(response: dict) -> None:
        """prompt 响应的错误识别：error 帧抛错；`stopReason="error"` 同样抛错。

        2026-07-30 实测修正沿用（R1 差异对齐）：轮次失败严禁「正常响应 +
        end_turn + 零 update」——不拦截则用户看到空回复零提示。协议
        §2.4 允许 error 帧与 stopReason=error 二选一，客户端两路同识别。
        """
        if "error" in response:
            err = response["error"]
            raise RuntimeError(f"local acp 对话失败 {err.get('code')}：{err.get('message')}")
        if (response.get("result") or {}).get("stopReason") == "error":
            raise RuntimeError(
                "Local 本轮响应失败（stopReason=error，多见于模型未加载或配置问题，"
                "请检查 Local CLI 侧日志与 ~/.local-cli/ 配置）")
