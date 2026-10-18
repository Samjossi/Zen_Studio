"""DeepSeek Harness（dsh）ACP provider：长驻 `dsh acp` 子进程 + JSON-RPC（ndjson 帧）对接。

以 local_acp.py 为底本同构改写（计划 2026-1018-0732）：会话生命周期、
审批回环、流式映射全部复用泛化连接层 AcpConnection。协议真值来源：
`参考代码/deepseek-harness/packages/acp/acp/README.md`（标准 ACP v1，
自动化专用面）；实测证据：`文档/帧存档/dsh_spike_20261018_074413.json`
（scripts/spike_dsh_acp.py 产出）。

与 local_acp 的差异（逐条可核对）：
1. `_find_bin()` 四级范式：PATH → `$DSH_HOME/bin/dsh` → `~/.dsh/bin/dsh`
   → npx 缓存兜底（`~/.npm/_npx/*/node_modules/.bin/dsh` 取最新——开发机
   经 `npx @deepseek-ai/dsh` 运行的实指路径；缓存清理即失效，仅作兜底，
   正式安装（npm 全局/官方渠道）走前三级）。DSH_HOME 不设不碰：凭据归
   dsh 自管（`~/.dsh/.credentials.yaml` / `DEEPSEEK_API_KEY`），宿主
   代码库零密钥。
2. 强度 configId 为 `reasoning_effort`（snake_case，spike 实测；
   kimi 为 thinking、dream/local 为 effort，各家拼写互不相同）。
3. 模型选项值是**不透明 JSON 对串**（`["deepseek-official","deepseek-v4-flash"]`
   = 路由 + 型号），原样透传不解析不拼接（D6 红线 2 同款语义）；
   dsh 无 `dsh models` 枚举命令，模型清单只有 session/new 配置项与
   Web 设置页两个来源，初版用 spike 实测静态表（drift 风险见计划 §4）。
4. 能力面（initialize 实测）：terminal 不支持、promptCapabilities.image
   = false（acp profile 默认未挂附件存储）→ supports_images=False、
   _TERMINAL_CAPABILITY=False。
5. usage_update 推送通道有效（spike 轮次内实测 2 帧），用量徽章走
   公共映射，本文件零特殊处理。

session/update 映射统一走公共实现 map_session_update。
"""
import atexit
import logging
import os
import shutil
import threading
from pathlib import Path
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

DSH_BIN = "dsh"

#: ACP terminal/* 反向能力支持矩阵：dsh 不支持 terminals（协议 README
#: 明示 unsupported surfaces），保持 terminal: false
_TERMINAL_CAPABILITY = False

#: 模型静态表（2026-10-18 spike 实测值，见 dsh_spike_20261018_074413.json）：
#: session/new 配置项 model 的 options 全量（deepseek-official 路由组），
#: 值为不透明 JSON 对串（协议原值，原样透传）。dsh 无 CLI 枚举命令，
#: 静态表与服务端目录漂移时以 session/set_config_option 拒绝为信号
#: （失败降级保持当前模型，不崩 UI）
_DSH_MODELS: list[str] = [
    '["deepseek-official","deepseek-v4-flash"]',
    '["deepseek-official","deepseek-flash"]',
    '["deepseek-official","deepseek-v4-pro"]',
]

#: 模型显示名（spike 实测选项 name 原值；纯 UI 呈现，协议/持久化恒用对串）
_DSH_DISPLAY_NAMES: dict[str, str] = {
    '["deepseek-official","deepseek-v4-flash"]': "deepseek-v4-flash",
    '["deepseek-official","deepseek-flash"]': "DeepSeek-V41-Flash",
    '["deepseek-official","deepseek-v4-pro"]': "DeepSeek-V4-Pro",
}


def _find_bin() -> str | None:
    """解析 dsh 二进制路径：PATH → $DSH_HOME/bin → ~/.dsh/bin → npx 缓存兜底。

    桌面启动 Zen Studio 时 PATH 可能不含 dsh 安装目录，fallback 避免误判
    未安装——对齐 kimi/local _find_bin 范式，安装根目录环境变量为
    DSH_HOME（dsh 原生默认 ~/.dsh）。npx 缓存级（`npx @deepseek-ai/dsh`
    的实指）取 mtime 最新者：缓存目录名为 npm 哈希、随包版本缓存清理
    而漂移，仅作开发机兜底，不作分发依赖。
    """
    if path := shutil.which(DSH_BIN):
        return path
    candidates: list[Path] = []
    if home := os.environ.get("DSH_HOME"):
        candidates.append(Path(home) / "bin" / DSH_BIN)
    candidates.append(Path.home() / ".dsh" / "bin" / DSH_BIN)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    npx_bins = sorted(
        Path.home().glob(".npm/_npx/*/node_modules/.bin/dsh"),
        key=lambda p: p.stat().st_mtime if p.exists() else 0,
        reverse=True)
    for candidate in npx_bins:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def dsh_available() -> bool:
    """检测 dsh CLI 是否可用（PATH、安装目录或 npx 缓存存在可执行 dsh）。"""
    return _find_bin() is not None


def list_dsh_models() -> list[str]:
    """模型别名枚举：静态表（dsh 无 CLI 枚举命令，见模块 docstring 差异 3）。"""
    return list(_DSH_MODELS)


def list_dsh_display_names() -> dict[str, str]:
    """模型显示名枚举：静态表（别名 → 显示名；查无该别名 UI 回退别名原文）。"""
    return dict(_DSH_DISPLAY_NAMES)


class DshAcpLLM(LanguageModel):
    """dsh ACP 后端（长驻子进程 + ndjson JSON-RPC，token 级流式 + 思维链）。"""

    def __init__(self, model: str | None = None, workspace_root: str | None = None) -> None:
        """
        :param model: 模型选项值（不透明 JSON 对串；None = agent 默认模型
            configOptions currentValue）
        :param workspace_root: agent 工作目录（None = 项目根；多开模式由启动参数注入）。
            归一化为绝对路径——`session/new` 硬校验 cwd 必须绝对
            （-32602，reasonix 2026-07-30 实测先例，协议沿用）
        """
        self._model = model
        #: 推理强度预选值（None = 未定制，agent 默认强度生效）
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
        #: 终端处理器（GUI 桥经 set_terminal_handler 注入）；dsh 不支持
        #: terminals，恒不声明 terminal 能力（参数保留仅为鸭子类型对齐）
        self._terminal_handler: TerminalHandler | None = None
        atexit.register(self.close)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def set_model(self, alias: str) -> None:
        """切换模型（会话存在则即时生效，失败则降级为下次新会话生效）。

        生效机制自持（D6 红线 3）：`session/set_config_option(configId="model")`，
        选项值（不透明 JSON 对串）原样透传，不解析不拼接（D6 红线 2）。
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
        （configId="reasoning_effort"，spike 实测 snake_case 拼写），强度值
        原样透传不解析不校验。失败不丢弃会话（与 set_model 的差异）：强度是
        辅助控制轴，拒绝不该陪葬会话上下文——`_effort` 已记，下个新会话生效。
        """
        self._effort = value
        if self._conn and self._conn.is_alive and self._session_id:
            try:
                self._conn.request("session/set_config_option", {
                    "sessionId": self._session_id, "configId": "reasoning_effort",
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
        """注入终端处理器（鸭子类型对齐；dsh 不支持 terminals，始终不声明能力）。"""
        self._terminal_handler = handler
        if self._conn:
            self._conn.set_terminal_handler(handler)

    def close(self) -> None:
        """终止 agent 子进程并注销 atexit 钩（多标签：实例随标签关闭销毁，
        不注销则绑定方法把已死实例钉在 atexit 注册表至进程退出）。

        `_closed` 先置位：`_ensure_session` 在 spawn 前后双检该标志，
        "close 之后新建的连接"语义上不可能存活（与其他 ACP provider 同一竞态防护）。
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
            raise RuntimeError("dsh acp 后端已关闭（标签已销毁）")
        bin_path = _find_bin()
        if not bin_path:
            raise RuntimeError(
                "dsh 不可用：PATH、$DSH_HOME/bin、~/.dsh/bin 与 npx 缓存均未找到 dsh"
                "（安装见 https://deepseek-harness.github.io/deepseek-harness/）")
        if self._conn is None or not self._conn.is_alive:
            if self._conn is not None:
                self._conn.terminate()
            self._conn = AcpConnection(bin_path, self._cwd, "dsh acp")
            if self._closed:  # spawn 与 close 竞态：迟到的连接即建即杀
                self._conn.terminate()
                self._conn = None
                raise RuntimeError("dsh acp 后端已关闭（标签已销毁）")
            self._conn.set_permission_handler(self._permission_handler)
            self._conn.set_terminal_handler(self._terminal_handler)
            self._session_id = None
            init_result = self._conn.request("initialize", {
                "protocolVersion": 1,
                "clientCapabilities": build_client_capabilities(terminal=False),
                "clientInfo": {"name": "zen-studio", "title": "Zen Studio", "version": APP_VERSION},
            }, timeout=60)  # 首次 boot 初始化 acp profile 可能耗时（计划 §4 风险项）
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
                        "sessionId": self._session_id, "configId": "reasoning_effort",
                        "value": self._effort}, timeout=10)
                except RuntimeError:
                    logger.exception("新会话应用预选推理强度失败")
                    pass  # 保持 agent 默认强度，不阻断对话
        return self._conn

    @staticmethod
    def _log_agent_info(init_result: dict) -> None:
        """记录 agent 版本信息（走 logging：冻结态桌面启动 stderr 是断管，
        直打 stderr 会抛 BrokenPipe 炸掉会话建立）。

        `authMethods` 是**静态能力声明**，不能作未配置信号（dsh 实测恒为空
        数组——authenticate 即刻成功、服务端不要求认证）；真正的凭据缺失
        信号在 session/new / prompt 的错误帧，归 `_raise_config_hint_if_auth`。
        """
        agent = init_result.get("agentInfo") or {}
        logger.info(f"[dsh acp] agent {agent.get('name', '?')} {agent.get('version', '?')}")

    @staticmethod
    def _raise_config_hint_if_auth(error: RuntimeError) -> None:
        """session/new 认证/凭据类错误 → 「dsh 凭据缺失」引导文案。

        宽松判定（D5：各 provider 自行翻译认证错误）：错误消息含
        auth/unauthorized/credential/api key/401 任一关键词（不区分大小写）
        即视为凭据类失败。引导指向 dsh 自有凭据体系（~/.dsh/.credentials.yaml
        / DEEPSEEK_API_KEY），宿主侧不做密钥管理。宽松而非精确匹配的理由：
        宁可多映射少数误伤（文案仍指向正确动作），不可漏映射让用户面对
        裸协议错误。
        """
        message = str(error).lower()
        if any(keyword in message
               for keyword in ("auth", "unauthorized", "credential",
                               "api key", "apikey", "401")):
            raise RuntimeError(
                "dsh 凭据缺失或已失效，请配置 ~/.dsh/.credentials.yaml"
                " 或环境变量 DEEPSEEK_API_KEY 后重试") from None

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------
    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        # 历史由 agent 会话管理，仅取末条 user 消息作 prompt（可携带图片附件，
        # 0340 方案 B：text+image 多块经 build_prompt_blocks 构造；当前 acp
        # profile 未挂附件存储、image 能力 false，能力位由 GUI 消费，
        # 协议通道不受阻）
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

        usage_update 是协议正式通道（spike 实测轮次内推送 2 帧），经公共
        map_session_update 直接产出 usage Chunk 上屏，轮次收尾不做
        transcript 估算。
        """
        while True:
            kind, obj = conn.next_update()
            if kind == "dead":
                self._session_id = None
                raise RuntimeError(f"dsh acp 进程意外退出（退出码 {obj}）")
            if kind == "response":
                self._raise_on_turn_error(obj)
                return
            chunk = map_session_update(obj)
            if chunk:
                yield chunk

    @staticmethod
    def _raise_on_turn_error(response: dict) -> None:
        """prompt 响应的错误识别：error 帧抛错；`stopReason="error"` 同样抛错。

        轮次失败严禁「正常响应 + end_turn + 零 update」——不拦截则用户看到
        空回复零提示。协议允许 error 帧与 stopReason=error 二选一，客户端
        两路同识别（local_acp 同款判别）。
        """
        if "error" in response:
            err = response["error"]
            raise RuntimeError(f"dsh acp 对话失败 {err.get('code')}：{err.get('message')}")
        if (response.get("result") or {}).get("stopReason") == "error":
            raise RuntimeError(
                "dsh 本轮响应失败（stopReason=error，多见于凭据缺失或模型路由"
                "配置问题，请检查 ~/.dsh/.credentials.yaml 与 dsh 侧日志）")
