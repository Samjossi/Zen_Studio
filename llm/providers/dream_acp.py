"""Dream ACP provider：长驻 `dream acp` 子进程 + JSON-RPC（ndjson 帧）对接。

与 ReasonixAcpLLM 逐行同构（计划 2026-0803-0041 §5.2，本文件即逐行 diff
核对底本）：会话生命周期、审批回环、流式映射全部复用泛化连接层
AcpConnection（llm/providers/acp.py）。协议面以 dream-acp/protocol/
dream-acp-v1.md 为真值来源（其上游即 acp.py 与四后台实测）。

与 reasonix_acp 的差异（均写在本文件 docstring，逐条可核对）：
1. `_find_bin()` 三级范式：PATH → `$DREAM_HOME/bin/dream` →
   `~/.dream/bin/dream`（桌面会话 PATH 可能缺用户级目录；计划 §3.4）。
2. `list_dream_models()` 模型目录来源自持：示例期为静态表（与
   dream-acp/example/dream 的 DEMO_MODELS 同步——示例 agent 提供
   dream/demo-fast 与 dream/demo-smart 两个演示别名验证切换链路）；
   Dream 真实实现落地后形态由其自定（config 自解析或静态表均可），
   兜底空列表纪律沿用（空列表 = 用 agent 默认模型，不崩 UI）。
3. 错误文案：Dream 无 setup 概念，`_raise_setup_hint_if_auth` 改为
   「模型未加载/配置缺失」类引导（对齐实测修正 #5 的报错引导价值——
   宁可多映射，不可让用户面对裸协议错误）。
4. 用量通道：reasonix 无 usage_update（E1 实证）需 transcript 文本估算；
   Dream 协议（dream-acp-v1.md §2.3）定义 usage_update 为正式通道，
   示例 agent 即发演示格式帧——经公共 map_session_update 直接上屏，
   轮次收尾不再做 transcript 估算（estimate_usage_from_transcript 不搬）。
   无真实数据时 agent 不发帧，徽章保持隐藏（D4 不臆造上限）。

session/update 映射统一走公共实现 map_session_update（1602 计划 D4 上收）。
"""
import atexit
import os
import shutil
import sys
import threading
from typing import Iterator

from core.paths import PROJECT_ROOT  # agent 工作目录限定于项目根
from core.version import APP_VERSION
from llm.base import Chunk, LanguageModel, Message
from llm.providers.acp import (
    AcpConnection,
    PermissionHandler,
    build_prompt_blocks,
    map_session_update,
)

DREAM_BIN = "dream"

#: 示例期静态模型表（与 dream-acp/example/dream 的 DEMO_MODELS 同步维护；
#: 默认模型提首——菜单回退落点依赖枚举序，红线 4）
_DEMO_MODELS = ["dream/demo-fast", "dream/demo-smart"]


def _find_bin() -> str | None:
    """解析 dream 二进制路径：PATH → $DREAM_HOME/bin/dream → ~/.dream/bin/dream。

    桌面启动 Zen Studio 时 PATH 可能不含 dream 安装目录（如 ~/.local/bin
    未入桌面会话 PATH），fallback 避免误判未安装——对齐 reasonix
    _find_bin 三级范式，安装根目录环境变量为 DREAM_HOME（计划 §3.4）。
    """
    if path := shutil.which(DREAM_BIN):
        return path
    candidates: list = []
    if home := os.environ.get("DREAM_HOME"):
        candidates.append(os.path.join(home, "bin", DREAM_BIN))
    candidates.append(os.path.join(os.path.expanduser("~"), ".dream", "bin", DREAM_BIN))
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None


def dream_available() -> bool:
    """检测 dream CLI 是否可用（PATH 或默认安装位置存在）。"""
    return _find_bin() is not None


def list_dream_models() -> list[str]:
    """枚举 Dream 模型别名；示例期为静态表，任何异常兜底空列表。

    示例期：返回与示例 agent 同步的演示别名表（默认提首）。Dream 真实
    实现落地后形态由其自定（config 文件自解析或静态表）；缺文件/解析
    失败全 try 兜底空列表（空列表 = 用 agent 默认模型，不崩 UI，R2 纪律）。
    别名为不透明字符串（D6 红线 2）：不解析、不拼接、不校验格式。
    """
    try:
        return list(_DEMO_MODELS)
    except Exception:  # noqa: BLE001 — R2：全 try 兜底空列表
        return []


class DreamAcpLLM(LanguageModel):
    """Dream ACP 后端（长驻子进程 + ndjson JSON-RPC，token 级流式 + 思维链）。"""

    def __init__(self, model: str | None = None, workspace_root: str | None = None) -> None:
        """
        :param model: 模型别名（不透明字符串；None = agent 默认模型）
        :param workspace_root: agent 工作目录（None = 项目根；多开模式由启动参数注入）。
            归一化为绝对路径——`session/new` 硬校验 cwd 必须绝对
            （-32602，reasonix 2026-07-30 实测先例，协议 §1.4 沿用），
            abspath 对已绝对输入幂等
        """
        self._model = model
        self._cwd = os.path.abspath(workspace_root or str(PROJECT_ROOT))
        self._conn: AcpConnection | None = None
        self._session_id: str | None = None
        self._turn_lock = threading.Lock()
        #: 关闭标志（标签销毁）：close() 置位后 _ensure_session 拒绝新建连接，
        #: spawn 前后双检——与清理线程 close() 的竞态窗口内迟到的连接即建即杀
        self._closed = False
        #: 审批处理器（由 GUI 注入）：session/request_permission params → optionId | None
        self._permission_handler: PermissionHandler | None = None
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
                self._session_id = None  # 降级：下轮重建会话并应用模型

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
            raise RuntimeError("dream acp 后端已关闭（标签已销毁）")
        bin_path = _find_bin()
        if not bin_path:
            raise RuntimeError(
                "dream CLI 不可用：PATH、$DREAM_HOME/bin 与 ~/.dream/bin 均未找到 dream")
        if self._conn is None or not self._conn.is_alive:
            if self._conn is not None:
                self._conn.terminate()
            self._conn = AcpConnection(bin_path, self._cwd, "dream acp")
            if self._closed:  # spawn 与 close 竞态：迟到的连接即建即杀
                self._conn.terminate()
                self._conn = None
                raise RuntimeError("dream acp 后端已关闭（标签已销毁）")
            self._conn.set_permission_handler(self._permission_handler)
            self._session_id = None
            init_result = self._conn.request("initialize", {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": False,
                },
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
                    pass  # 保持 agent 默认模型，不阻断对话
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
        print(f"[dream acp] agent {agent.get('name', '?')} {agent.get('version', '?')}",
              file=sys.stderr)

    @staticmethod
    def _raise_config_hint_if_auth(error: RuntimeError) -> None:
        """session/new 认证/配置类错误 → 「Dream 模型未加载/配置缺失」引导文案。

        宽松判定（D5：各 provider 自行翻译认证错误）：错误消息含
        auth/unauthorized/login/config/not configured/未加载 任一关键词
        （不区分大小写）即视为认证/配置类失败。Dream 无 setup 概念（与
        reasonix 差异 ③），引导指向 Dream 自有配置体系（~/.dream/）。
        宽松而非精确匹配的理由：宁可多映射少数误伤（文案仍指向正确动作），
        不可漏映射让用户面对裸协议错误。
        """
        message = str(error).lower()
        if any(keyword in message
               for keyword in ("auth", "unauthorized", "login", "config",
                               "not configured", "未加载")):
            raise RuntimeError(
                "Dream 模型未加载或配置缺失，请检查 Dream 配置（~/.dream/）"
                "并确认模型已就绪") from None

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------
    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        # 历史由 agent 会话管理，仅取末条 user 消息作 prompt（可携带图片附件，
        # 0340 方案 B：text+image 多块经 build_prompt_blocks 构造；示例 agent
        # 无视觉能力，能力位 False 由 GUI 消费，协议通道不受阻——计划 §5.3）
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
        """轮次内消息消费循环：update → Chunk；response/dead 收尾本轮。

        与 reasonix 差异 ④：usage_update 是 Dream 协议正式通道（示例 agent
        即发演示格式帧），经 map_session_update 直接产出 usage Chunk 上屏；
        轮次收尾不做 transcript 估算。
        """
        while True:
            kind, obj = conn.next_update()
            if kind == "dead":
                self._session_id = None
                raise RuntimeError(f"dream acp 进程意外退出（退出码 {obj}）")
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
        end_turn + 零 update」——不拦截则用户看到空回复零提示。Dream 协议
        §2.4 允许 error 帧与 stopReason=error 二选一，客户端两路同识别。
        """
        if "error" in response:
            err = response["error"]
            raise RuntimeError(f"dream acp 对话失败 {err.get('code')}：{err.get('message')}")
        if (response.get("result") or {}).get("stopReason") == "error":
            raise RuntimeError(
                "Dream 本轮响应失败（stopReason=error，多见于模型未加载或配置问题，"
                "请检查 Dream 侧日志与 ~/.dream/ 配置）")
