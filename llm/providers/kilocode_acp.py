"""Kilo Code ACP provider：长驻 `kilo acp` 子进程 + JSON-RPC（ndjson 帧）对接。

与 OpenCodeAcpLLM 高度同构（计划 2026-0730-2240 §3.1；Kilo CLI 官方自述为
OpenCode fork，协议级兼容）：会话生命周期、审批回环、思维链映射全部复用
泛化连接层 AcpConnection（llm/providers/acp.py）。Kilo 侧 ACP v1
（protocolVersion: 1）与 kimi/reasonix/opencode 实现协议同构（计划 §2.3-§2.5
实测：initialize / session/new / session/set_config_option / session/prompt /
流式 update 全链路通过），流式更新走 ACP 标准 `agent_message_chunk` /
`agent_thought_chunk` / `tool_call`；`usage_update` 等忽略即可兼容
（R1：不臆造协议）。

与 opencode_acp 的差异（均写在本文件 docstring，逐条可核对）：
1. bin 探测：两级链 PATH（`kilocode` → `kilo` 双名顺序探测——双名 symlink
   同指同一二进制，`kilocode` 更长更不易撞名优先，`kilo` 为官方主推名兜底）
   → `~/.local/bin/kilocode` 兜底（npm 全局 bin 通常在桌面会话 PATH 中但不
   保证，计划 §2.1）。Kilo CLI 无安装根环境变量（官方仅 KILO_CONFIG 等配置
   路径），故探测链为两级。
2. 模型目录规模与网关过滤：`list_kilocode_models()` spawn `kilo models`
   解析纯文本行（无 --json 旗标，计划 2026-0730-2240 §2.2）；原始目录本机
   实测 294 项，其中 280 项为 `kilo/` 前缀的 Kilo Gateway 聚合目录（用户
   不使用），枚举时按 `GATEWAY_MODEL_PREFIX` 剔除，仅呈现已认证直连
   provider（本机 14 项，计划 2026-0730-2318 §2.1）。`~` 前缀别名行全部
   位于 `kilo/` 下，随前缀过滤一并剔除，无需特判。过滤仅作用于枚举呈现
   层：set_model 不做前缀校验，已持久化的网关别名仍原样透传生效
   （D6 红线 2 不破）。
3. 未登录检测：session/new 与 session/prompt 认证类错误（含 auth/login/
   credential/unauthorized 关键词）宽松映射为「请先运行 kilo auth login」
   友好文案（D5：各 provider 自行翻译认证错误）。注：initialize 的
   `authMethods` 是静态能力声明（计划 §2.3），不能作未登录信号。
4. mode/effort 不触碰（D5）：Kilo session/new 的 configOptions 多出 `mode`
   （ask/code/debug/orchestrator/plan，默认 ask 纯问答、禁用编辑工具——恰好
   匹配 IDE 对话场景，比 opencode 默认 build 更贴合更安全）与 `effort`
   （high/max，默认 high 推理力度）；本 provider 均不使用
   `session/set_config_option` 触碰，不暴露 UI（计划 §2.4）。
5. 别名私有语义（D6 红线 2）：`provider/model` 全名（含 `~` 别名）不透明
   透传，公共层不解析不拼接。

session/update 映射（1602 计划 T3）：私有 _map_update 已删除，统一改调
llm/providers/acp.py 的公共实现 map_session_update（D4 上收——原四份
逐行一致副本同一改动改四处必然漂移）。
"""
import atexit
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Iterator

from core.paths import PROJECT_ROOT  # agent 工作目录限定于项目根
from core.version import APP_VERSION
from llm.base import Chunk, LanguageModel, Message
from llm.providers.acp import AcpConnection, PermissionHandler, map_session_update

#: PATH 探测的候选二进制名（双名 symlink 同指同一二进制，计划 §2.1）
KILOCODE_BIN_NAMES = ("kilocode", "kilo")

#: Kilo Gateway 聚合目录前缀（计划 2026-0730-2318 D3）：用户不使用网关
#: 模型，list_kilocode_models 枚举时剔除；仅影响菜单呈现，set_model
#: 不做前缀校验（别名不透明透传，D6 红线 2）
GATEWAY_MODEL_PREFIX = "kilo/"


def _find_bin() -> str | None:
    """解析 kilocode 二进制路径：PATH（kilocode → kilo 顺序）→ ~/.local/bin/kilocode。

    npm 全局安装（`npm install -g @kilocode/cli`）落在 npm 全局 bin，通常在
    桌面会话 PATH 中但不保证（用户自定义 npm prefix 时路径漂移），fallback 到
    默认 npm 全局 bin `~/.local/bin` 避免误判未安装——对齐 kimi
    `~/.kimi-code/bin`、opencode `~/.opencode/bin` 的兜底范式；Kilo CLI 无
    安装根环境变量（官方仅 KILO_CONFIG 等配置路径），故探测链为两级
    （计划 §2.1）。
    """
    for name in KILOCODE_BIN_NAMES:
        if path := shutil.which(name):
            return path
    candidate = Path.home() / ".local" / "bin" / KILOCODE_BIN_NAMES[0]
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


def kilocode_available() -> bool:
    """检测 kilocode CLI 是否可用（PATH 或默认 npm 全局 bin 存在）。"""
    return _find_bin() is not None


def list_kilocode_models() -> list[str]:
    """spawn `kilo models` 枚举模型别名（`provider/model` 全名）；失败返回空列表。

    输出为纯文本、每行一个 `provider/model` 全名（无 --json 旗标，计划 §2.2
    实测），数据源是已认证 provider（models.dev 缓存），与 ACP session/new
    的 configOptions.model.options 同源。解析策略：逐行 strip、跳过空行，
    含 `/` 的行保留，再剔除 `GATEWAY_MODEL_PREFIX`（`kilo/`）网关聚合行
    ——仅呈现已认证直连 provider（前缀黑名单而非白名单：用户日后新认证
    的直连 provider 自动出现，无需维护名单）；首次运行可能触网刷新
    models.dev 缓存，15s timeout 覆盖（与 kimi `provider list` 同值）。
    失败/超时/空输出 → 空列表（R2：输出格式无契约、随版本漂移，全 try
    兜底；空列表时 provider 用 agent 默认模型，对话功能不受影响）。
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
        return [line.strip() for line in proc.stdout.splitlines()
                if "/" in line.strip()
                and not line.strip().startswith(GATEWAY_MODEL_PREFIX)]
    except (OSError, subprocess.SubprocessError):
        return []


class KiloCodeAcpLLM(LanguageModel):
    """Kilo Code ACP 后端（长驻子进程 + ndjson JSON-RPC，token 级流式 + 思维链）。"""

    def __init__(self, model: str | None = None, workspace_root: str | None = None) -> None:
        """
        :param model: 模型别名（`provider/model` 全名，含 `~` 别名；None = agent
            默认模型 configOptions currentValue）
        :param workspace_root: agent 工作目录（None = 项目根；多开模式由启动参数注入）。
            归一化为绝对路径（对齐 reasonix/opencode 稳妥做法，abspath 对已绝对输入幂等）
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
        别名原样透传，不解析不拼接（D6 红线 2）。Kilo 实测同会话切换即时生效
        （计划 §2.4）。
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
        self._cwd = os.path.abspath(root)
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
        "close 之后新建的连接"语义上不可能存活（与 KimiAcpLLM 同一竞态防护）。
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
            raise RuntimeError("kilocode acp 后端已关闭（标签已销毁）")
        bin_path = _find_bin()
        if not bin_path:
            raise RuntimeError(
                "kilocode CLI 不可用：PATH 与 ~/.local/bin 均未找到 kilocode/kilo")
        if self._conn is None or not self._conn.is_alive:
            if self._conn is not None:
                self._conn.terminate()
            self._conn = AcpConnection(bin_path, self._cwd, "kilocode acp")
            if self._closed:  # spawn 与 close 竞态：迟到的连接即建即杀
                self._conn.terminate()
                self._conn = None
                raise RuntimeError("kilocode acp 后端已关闭（标签已销毁）")
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
                self._raise_login_hint_if_auth(e)
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

        不拦截 `authMethods`：它是**静态能力声明**（恒为 terminal 型
        `kilo-login`，计划 §2.3 实测响应；reasonix/opencode 实测结论同样适用），
        已登录与未登录响应相同；真正的未登录信号在 session/new 与
        session/prompt 错误，归 `_raise_login_hint_if_auth`。
        """
        agent = init_result.get("agentInfo") or {}
        print(f"[kilocode acp] agent {agent.get('name', '?')} {agent.get('version', '?')}",
              file=sys.stderr)

    @staticmethod
    def _raise_login_hint_if_auth(error: RuntimeError) -> None:
        """认证类错误 → 「请先运行 kilo auth login」友好文案（D5 各自翻译）。

        宽松判定：错误消息含 auth/login/credential/unauthorized 任一关键词
        （不区分大小写）即视为认证/凭证类失败。宽松而非精确匹配的理由：
        Kilo 认证失败的错误码语义未定型（R1），宁可多映射少数误伤
        （文案仍指向正确动作），不可漏映射让用户面对裸协议错误。
        """
        message = str(error).lower()
        if any(keyword in message
               for keyword in ("auth", "login", "credential", "unauthorized")):
            raise RuntimeError(
                "kilocode 尚未登录或凭证失效，请先在终端运行 kilo auth login") from None

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------
    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        # 历史由 agent 会话管理，仅取最后一条 user 消息作 prompt
        prompt = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
        if not prompt:
            return
        with self._turn_lock:  # 串行化轮次，防 inbox 串抢
            conn = self._ensure_session()
            conn.purge_updates()
            conn.begin_turn("session/prompt", {
                "sessionId": self._session_id,
                "prompt": [{"type": "text", "text": prompt}],
            })
            try:
                yield from self._iter_turn_chunks(conn)
            finally:
                conn.end_turn()

    def _iter_turn_chunks(self, conn: AcpConnection) -> Iterator[Chunk]:
        """轮次内消息消费循环：update → Chunk；response/dead 收尾本轮。"""
        while True:
            kind, obj = conn.next_update()
            if kind == "dead":
                self._session_id = None
                raise RuntimeError(f"kilocode acp 进程意外退出（退出码 {obj}）")
            if kind == "response":
                self._raise_on_turn_error(obj)
                return
            chunk = map_session_update(obj)
            if chunk:
                yield chunk

    def _raise_on_turn_error(self, response: dict) -> None:
        """prompt 响应的错误识别：error 帧抛错（认证类先转友好文案）。

        未登录/凭证失效的真实信号在 session/prompt 错误帧（计划 §2.3），
        归 `_raise_login_hint_if_auth` 翻译后再抛裸协议错误兜底。
        """
        if "error" in response:
            err = response["error"]
            error = RuntimeError(f"kilocode acp 对话失败 {err.get('code')}：{err.get('message')}")
            self._raise_login_hint_if_auth(error)
            raise error
