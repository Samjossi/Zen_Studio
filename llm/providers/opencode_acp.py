"""OpenCode ACP provider：长驻 `opencode acp` 子进程 + JSON-RPC（ndjson 帧）对接。

与 ReasonixAcpLLM 高度同构（计划 2026-0730-2128 §3.1）：会话生命周期、
审批回环、思维链映射全部复用泛化连接层 AcpConnection（llm/providers/acp.py）。
OpenCode 侧 ACP v1（protocolVersion: 1）与 kimi/reasonix 实现协议同构
（计划 §2 实测：initialize / session/new / session/set_config_option /
session/prompt / 流式 update 全链路通过），流式更新走 ACP 标准
`agent_message_chunk` / `agent_thought_chunk` / `tool_call`；
`available_commands_update` / `usage_update` 等忽略即可兼容（R1：不臆造协议）。

与 reasonix_acp 的差异（均写在本文件 docstring，逐条可核对）：
1. 模型目录来源：`list_opencode_models()` spawn `opencode models` 解析纯文本行
   （无 --json 旗标，计划 §2.2）；reasonix 自行解析 config.toml。原始目录
   本机实测 15 项，其中 7 项为 `opencode/` 前缀的 OpenCode Zen 官方模型
   （用户不使用），枚举时按 `GATEWAY_MODEL_PREFIX` 剔除，仅呈现已认证
   直连 provider（本机 8 项，计划 2026-0730-2318 §2.2）。边界：OpenCode
   agent 默认模型 `opencode/big-pickle` 即 Zen 模型，过滤后菜单不含它，
   但用户不选模型时 provider 沿用 agent 默认（configOptions currentValue），
   对话不受影响；已持久化的 Zen 别名经 set_model 原样透传仍生效——
   过滤仅作用于枚举呈现层（D6 红线 2 不破）。
2. bin 探测：两级链 PATH → `~/.opencode/bin/opencode`。OpenCode 无
   `OPENCODE_HOME` 类安装根环境变量（官方环境变量表只有 OPENCODE_CONFIG 等
   配置路径），故无 reasonix 的 $REASONIX_HOME/bin 中间级。
3. 未登录检测：session/new 与 session/prompt 认证类错误（含 auth/login/
   credential/unauthorized 关键词）宽松映射为「请先运行 opencode auth login」
   友好文案（D5：各 provider 自行翻译认证错误；reasonix 的 not configured
   关键词不搬——OpenCode 未配置模型时报错语义不同）。注：initialize 的
   `authMethods` 是静态能力声明（reasonix 实测结论同样适用于 OpenCode，
   计划 §2.3），不能作未登录信号。
4. mode 固定 build：OpenCode session/new 的 configOptions 多出 `mode`
   选项（build/plan，计划 §2.4），本 provider 不触碰——agent 默认即 build；
   plan 模式禁用编辑工具，与 IDE 对话场景不匹配，不暴露 UI。
5. 别名私有语义（D6 红线 2）：`provider/model` 全名（如 `kimi-for-coding/k3`）
   不透明透传，公共层不解析不拼接。

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
from llm.providers.acp import (
    AcpConnection,
    PermissionHandler,
    build_prompt_blocks,
    map_session_update,
)

OPENCODE_BIN = "opencode"

#: OpenCode Zen 官方模型前缀（计划 2026-0730-2318 D3）：用户不使用 Zen
#: 模型，list_opencode_models 枚举时剔除；仅影响菜单呈现，set_model
#: 不做前缀校验（别名不透明透传，D6 红线 2）
GATEWAY_MODEL_PREFIX = "opencode/"


def _find_bin() -> str | None:
    """解析 opencode 二进制路径：PATH → ~/.opencode/bin/opencode（两级链）。

    桌面启动 Zen Studio 时 PATH 可能不含 opencode 安装目录（curl 官方脚本
    装至 ~/.opencode/bin 并写 .bashrc，但非登录 shell/桌面会话不一定含此
    PATH），fallback 避免误判未安装——对齐 kimi/reasonix 探测范式；
    OpenCode 无安装根环境变量（官方仅 OPENCODE_CONFIG 等配置路径），
    故比 reasonix 少一级（计划 §2.1）。
    """
    if path := shutil.which(OPENCODE_BIN):
        return path
    candidate = Path.home() / ".opencode" / "bin" / OPENCODE_BIN
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate)
    return None


def opencode_available() -> bool:
    """检测 opencode CLI 是否可用（PATH 或默认安装位置存在）。"""
    return _find_bin() is not None


def list_opencode_models() -> list[str]:
    """spawn `opencode models` 枚举模型别名（`provider/model` 全名）；失败返回空列表。

    输出为纯文本、每行一个 `provider/model` 全名（无 --json 旗标，计划 §2.2
    实测），数据源是已认证 provider（models.dev 缓存），与 ACP session/new
    的 configOptions.model.options 同源。解析策略：逐行 strip、跳过空行，
    非 `provider/model` 形态行容错跳过，再剔除 `GATEWAY_MODEL_PREFIX`
    （`opencode/`）Zen 官方模型行——仅呈现已认证直连 provider（前缀
    黑名单而非白名单：用户日后新认证的直连 provider 自动出现，无需
    维护名单）；首次运行可能触网刷新 models.dev
    缓存，15s timeout 覆盖（与 kimi `provider list` 同值）。
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


class OpenCodeAcpLLM(LanguageModel):
    """OpenCode ACP 后端（长驻子进程 + ndjson JSON-RPC，token 级流式 + 思维链）。"""

    def __init__(self, model: str | None = None, workspace_root: str | None = None) -> None:
        """
        :param model: 模型别名（`provider/model` 全名；None = agent 默认模型
            configOptions currentValue）
        :param workspace_root: agent 工作目录（None = 项目根；多开模式由启动参数注入）。
            归一化为绝对路径（对齐 reasonix 稳妥做法，abspath 对已绝对输入幂等）
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
        别名原样透传，不解析不拼接（D6 红线 2）。OpenCode 实测同会话切换
        即时生效（计划 §2.4）。
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
            raise RuntimeError("opencode acp 后端已关闭（标签已销毁）")
        bin_path = _find_bin()
        if not bin_path:
            raise RuntimeError(
                "opencode CLI 不可用：PATH 与 ~/.opencode/bin 均未找到 opencode")
        if self._conn is None or not self._conn.is_alive:
            if self._conn is not None:
                self._conn.terminate()
            self._conn = AcpConnection(bin_path, self._cwd, "opencode acp")
            if self._closed:  # spawn 与 close 竞态：迟到的连接即建即杀
                self._conn.terminate()
                self._conn = None
                raise RuntimeError("opencode acp 后端已关闭（标签已销毁）")
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
        `opencode-login`，计划 §2.3 实测响应；reasonix 侧 2026-07-30 实测
        结论同样适用），已登录与未登录响应相同；真正的未登录信号在
        session/new 与 session/prompt 错误，归 `_raise_login_hint_if_auth`。
        """
        agent = init_result.get("agentInfo") or {}
        print(f"[opencode acp] agent {agent.get('name', '?')} {agent.get('version', '?')}",
              file=sys.stderr)

    @staticmethod
    def _raise_login_hint_if_auth(error: RuntimeError) -> None:
        """认证类错误 → 「请先运行 opencode auth login」友好文案（D5 各自翻译）。

        宽松判定：错误消息含 auth/login/credential/unauthorized 任一关键词
        （不区分大小写）即视为认证/凭证类失败。宽松而非精确匹配的理由：
        OpenCode 认证失败的错误码语义未定型（R1），宁可多映射少数误伤
        （文案仍指向正确动作），不可漏映射让用户面对裸协议错误。
        """
        message = str(error).lower()
        if any(keyword in message
               for keyword in ("auth", "login", "credential", "unauthorized")):
            raise RuntimeError(
                "opencode 尚未登录或凭证失效，请先在终端运行 opencode auth login") from None

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
                "prompt": build_prompt_blocks(message, workspace_root=self._cwd),
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
                raise RuntimeError(f"opencode acp 进程意外退出（退出码 {obj}）")
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
            error = RuntimeError(f"opencode acp 对话失败 {err.get('code')}：{err.get('message')}")
            self._raise_login_hint_if_auth(error)
            raise error
