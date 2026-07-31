"""Reasonix ACP provider：长驻 `reasonix acp` 子进程 + JSON-RPC（ndjson 帧）对接。

与 KimiAcpLLM 高度同构（计划 2026-0730-0150 §5 阶段三 T8/T9）：会话生命周期、
审批回环、思维链映射全部复用泛化连接层 AcpConnection（llm/providers/acp.py）。
reasonix 侧 ACP v1（protocolVersion: 1）与 kimi 实现协议同构（计划 §3 调研结论），
流式更新走 ACP 标准 `agent_message_chunk` / `agent_thought_chunk` / `tool_call`；
`_reasonix.io/*` 等 `_meta` 厂商扩展忽略即可兼容（R1：不臆造协议）。

与 kimi_acp 的差异（均写在本文件 docstring，逐条可核对）：
1. 模型目录来源：reasonix 无 CLI 枚举命令，`list_reasonix_models()` 自行解析
   config.toml（标准库 tomllib，零新依赖）；kimi 走 `kimi provider list --json`。
2. 未 setup 检测：`session/new` 认证/未配置类错误（含 `not configured`，实测
   错误码 -32603）宽松映射为「请先运行 reasonix setup」友好文案；kimi 侧是
   `-32000 authRequired → kimi login` 文案映射，不搬（D5：各 provider 自行
   翻译认证错误）。注：initialize 的 `authMethods` 是静态能力声明（已/未
   setup 相同），不能作未配置信号——2026-07-30 实测推翻初版拦截设计，
   详见 `_log_agent_info` docstring。
3. 别名私有语义（D6 红线 2）：reasonix 别名是 `provider/model` 全名
   （如 `deepseek/deepseek-v4-flash`），与 kimi 的 `kimi-code/k3-256k` 各自为政，
   公共层一律不透明透传。
4. 上下文用量估算（1454 计划 T4，2026-07-31 实证落地）：reasonix ACP 出口
   无 usage_update、transcript 亦无 token 字段（E1 实证），但 prompt 响应帧
   带 `result.transcriptPath`（官方持久化转录，含 system/工具结果全量快照）。
   轮次收尾读快照做 chars/4 文本估算（source="estimate"）；size 取
   config.toml `[[providers]].context_window`（llm/context_limits.py），
   缺项/解析失败静默降级（D4 隐藏徽章，不臆造上限）。

session/update 映射（1602 计划 T3）：私有 _map_update 已删除，统一改调
llm/providers/acp.py 的公共实现 map_session_update（D4 上收——原四份
逐行一致副本同一改动改四处必然漂移）。
"""
import atexit
import json
import os
import shutil
import sys
import threading
import tomllib
from pathlib import Path
from typing import Iterator

from core.paths import PROJECT_ROOT  # agent 工作目录限定于项目根
from core.version import APP_VERSION
from llm.base import Chunk, LanguageModel, Message, UsageStats
from llm.context_limits import reasonix_config_path, reasonix_context_window
from llm.providers.acp import (
    AcpConnection,
    PermissionHandler,
    build_prompt_blocks,
    map_session_update,
)

REASONIX_BIN = "reasonix"

# config.toml 路径解析主定义已上收 llm/context_limits.py（T2：上限查询与
# 模型枚举共用同一路径知识）；本别名保持既有调用点与语义不变。
_config_path = reasonix_config_path

#: transcript 文本估算系数（chars/token）：英文 ~4、中文 ~1.5，reasonix
#: system prompt 英文为主，取 4 偏保守（估算偏低，GUI 以 ~ 前缀标注口径）
_ESTIMATE_CHARS_PER_TOKEN = 4


def estimate_usage_from_transcript(
    transcript_path: str | None, alias: str | None
) -> UsageStats | None:
    """transcript jsonl 消息文本 → 估算 UsageStats；任何环节失败返回 None。

    1454 计划 T4（E1 实证定稿）：reasonix 转录无 token usage 字段（rg 全目录
    无命中），但含完整消息快照（system/user/assistant 全量历史，含 agent
    注入的 system prompt 与工具结果——比 IDE 侧盲估已发内容准一个量级）。
    估算口径：Σ 各消息 content 字符数 ÷ 4（reasoning_content 不计——
    DeepSeek 约束推理不回传，不占后续上下文）。

    size 取自 config.toml `[[providers]].context_window`（官方配置字段，
    见 llm/context_limits.py）；缺项返回 None（D4：不臆造上限，徽章隐藏）。
    文件缺失/JSON 行损坏/路径漂移 → None（格式漂移静默降级，D2 兜底）。
    """
    if not transcript_path:
        return None
    size = reasonix_context_window(alias)
    if size is None:
        return None
    try:
        total_chars = 0
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                content = entry.get("content")
                if isinstance(content, str):
                    total_chars += len(content)
        used = total_chars // _ESTIMATE_CHARS_PER_TOKEN
        if used <= 0:
            return None
        return UsageStats(used=used, size=size, cost=None, source="estimate")
    except (OSError, json.JSONDecodeError, AttributeError):
        return None


def _find_bin() -> str | None:
    """解析 reasonix 二进制路径：PATH → $REASONIX_HOME/bin/reasonix → ~/.reasonix/bin/reasonix。

    桌面启动 Zen Studio 时 PATH 可能不含 reasonix 安装目录（如 ~/.local/bin
    未入桌面会话 PATH），fallback 避免误判未安装——对齐 kimi _find_bin 三级范式，
    安装根目录环境变量改为 REASONIX_HOME（reasonix 官方约定，计划 §3）。
    """
    if path := shutil.which(REASONIX_BIN):
        return path
    candidates: list[Path] = []
    if home := os.environ.get("REASONIX_HOME"):
        candidates.append(Path(home) / "bin" / REASONIX_BIN)
    candidates.append(Path.home() / ".reasonix" / "bin" / REASONIX_BIN)
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def reasonix_available() -> bool:
    """检测 reasonix CLI 是否可用（PATH 或默认安装位置存在）。"""
    return _find_bin() is not None


def list_reasonix_models() -> list[str]:
    """解析 reasonix config.toml 枚举模型别名（`provider/model` 全名）；失败返回空列表。

    枚举规则（计划 T8 定案，D6 红线 2：别名是各后台私有语义，取全名）：
    - 遍历顶层 `[[providers]]` 段：每段 `name` 为 provider 名，模型表兼容三形态——
      `models` 字符串列表逐项产出、`models` 表数组取 `id` 字段、
      `model = "..."` 单值字段（2026-07-30 真实配置实测补入：setup 向导
      单模型 provider 写单值 `model` 而非 `models` 列表，漏掉则枚举少项）。
    - 默认模型排最前（该 provider 内）：`default` 标记兼容两形态——
      provider 段级 `default = "model名"`（官方文档形态）与
      模型条目级 `default = true`（表数组兼容形态），命中项提至段首。
    - 顶层 `default_model` 作全局提首指针：命中某个枚举别名时将其提至列表
      首位（2026-07-30 实测补强：setup 向导写的全局默认即用户期望的
      回退落点；未命中/缺失时保持 providers 配置序）。
    - 文件缺失 / TOML 解析失败 / 段缺失或形态异常 → 空列表
      （R2：配置结构随版本漂移，全 try 兜底；空列表时 provider 用 agent 默认模型）。
    """
    path = _config_path()
    if not path.is_file():
        return []
    try:
        with open(path, "rb") as f:
            config = tomllib.load(f)
        aliases: list[str] = []
        for provider in config.get("providers") or []:
            if not isinstance(provider, dict):
                continue
            provider_name = provider.get("name")
            if not provider_name:
                continue
            entries: list[tuple[str, bool]] = []  # (模型 id, 是否默认)
            for item in provider.get("models") or []:
                if isinstance(item, str):
                    entries.append((item, item == provider.get("default")))
                elif isinstance(item, dict) and (model_id := item.get("id")):
                    entries.append((model_id, bool(item.get("default"))
                                    or model_id == provider.get("default")))
            if not entries and isinstance(provider.get("model"), str):
                # 单值形态：setup 向导单模型 provider 写 `model = "..."`（实测）
                entries.append((provider["model"], True))
            # default 标记的模型排最前（该 provider 内），其余保持配置序
            entries.sort(key=lambda entry: not entry[1])
            aliases.extend(f"{provider_name}/{model_id}" for model_id, _ in entries)
        # 顶层 default_model 全局提首（用户 setup 选定的默认 = 回退落点）
        if (default := config.get("default_model")) and default in aliases:
            aliases.remove(default)
            aliases.insert(0, default)
        return aliases
    except (OSError, tomllib.TOMLDecodeError):
        return []


class ReasonixAcpLLM(LanguageModel):
    """Reasonix ACP 后端（长驻子进程 + ndjson JSON-RPC，token 级流式 + 思维链）。"""

    def __init__(self, model: str | None = None, workspace_root: str | None = None) -> None:
        """
        :param model: 模型别名（`provider/model` 全名；None = agent 默认模型
            configOptions currentValue / config.toml default_model）
        :param workspace_root: agent 工作目录（None = 项目根；多开模式由启动参数注入）。
            归一化为绝对路径——reasonix `session/new` 硬校验 cwd 必须绝对
            （-32602，2026-07-30 实测；kimi 无此要求），abspath 对已绝对输入幂等
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
            raise RuntimeError("reasonix acp 后端已关闭（标签已销毁）")
        bin_path = _find_bin()
        if not bin_path:
            raise RuntimeError(
                "reasonix CLI 不可用：PATH、$REASONIX_HOME/bin 与 ~/.reasonix/bin 均未找到 reasonix")
        if self._conn is None or not self._conn.is_alive:
            if self._conn is not None:
                self._conn.terminate()
            self._conn = AcpConnection(bin_path, self._cwd, "reasonix acp")
            if self._closed:  # spawn 与 close 竞态：迟到的连接即建即杀
                self._conn.terminate()
                self._conn = None
                raise RuntimeError("reasonix acp 后端已关闭（标签已销毁）")
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
                self._raise_setup_hint_if_auth(e)
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

        2026-07-30 实测修正（R1 差异对齐）：曾在此拦截「authMethods 非空即
        未 setup」——实测推翻：reasonix 的 `authMethods` 是**静态能力声明**
        （恒为 terminal 型 `reasonix-setup`，args=["setup"]），已 setup 与
        未 setup 响应完全相同；真正的未配置信号在 `session/new`
        （-32603 `model "..." is not configured`），归 `_raise_setup_hint_if_auth`
        映射。initialize 响应仅留版本日志，不做拦截。
        """
        agent = init_result.get("agentInfo") or {}
        print(f"[reasonix acp] agent {agent.get('name', '?')} {agent.get('version', '?')}",
              file=sys.stderr)

    @staticmethod
    def _raise_setup_hint_if_auth(error: RuntimeError) -> None:
        """session/new 认证/未配置类错误 → 「请先运行 reasonix setup」友好文案。

        宽松判定（D5：各 provider 自行翻译认证错误；kimi 的 -32000 精确映射不搬）：
        错误消息含 auth/unauthorized/login/setup/not configured 任一关键词
        （不区分大小写）即视为认证/配置类失败。`not configured` 为 2026-07-30
        实测补入：未 setup（无 config.toml）时 session/new 报
        `-32603 model "deepseek-flash" is not configured`，无 auth 字样。
        宽松而非精确匹配的理由：reasonix 认证失败的错误码语义未定型（R1），
        宁可多映射少数误伤（文案仍指向正确动作），不可漏映射让用户面对裸协议错误。
        """
        message = str(error).lower()
        if any(keyword in message
               for keyword in ("auth", "unauthorized", "login", "setup", "not configured")):
            raise RuntimeError(
                "reasonix 尚未配置或凭证失效，请先在终端运行 reasonix setup") from None

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
        while True:
            kind, obj = conn.next_update()
            if kind == "dead":
                self._session_id = None
                raise RuntimeError(f"reasonix acp 进程意外退出（退出码 {obj}）")
            if kind == "response":
                self._raise_on_turn_error(obj)
                # 轮次收尾：reasonix 无 usage_update（E1/1412 T5 实证），
                # 从响应帧 transcriptPath 读转录快照做文本估算（1454 T4）
                stats = estimate_usage_from_transcript(
                    (obj.get("result") or {}).get("transcriptPath"), self._model)
                if stats is not None:
                    yield Chunk("usage", "", usage=stats)
                return
            chunk = map_session_update(obj)
            if chunk:
                yield chunk

    @staticmethod
    def _raise_on_turn_error(response: dict) -> None:
        """prompt 响应的错误识别：error 帧抛错；`stopReason="error"` 同样抛错。

        2026-07-30 实测补入（R1 差异对齐）：reasonix 轮次失败（如模型
        provider 配置错误、上游调用失败）不走 error 帧，而是正常响应
        `result.stopReason="error"` 且**零 update**——不拦截则用户看到
        空回复零提示。stopReason 无错误详情（transcript 仅存本地标记），
        报错文案引导查 agent 侧日志/配置。kimi 正常结束为 end_turn，
        不产生 error 态，此检查对 kimi 语义无影响（kimi_acp 不动）。
        """
        if "error" in response:
            err = response["error"]
            raise RuntimeError(f"reasonix acp 对话失败 {err.get('code')}：{err.get('message')}")
        if (response.get("result") or {}).get("stopReason") == "error":
            raise RuntimeError(
                "reasonix 本轮响应失败（stopReason=error，多见于模型 provider 配置问题，"
                "请检查 ~/.reasonix/config.toml 对应 provider 的 base_url/api_key）")
