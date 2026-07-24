# LLM 包说明

> **状态**：已实施
> **范围**：`llm/` 包 — LLM 调用薄层（本机 agent CLI 后端，代码库零密钥）
> **时间**：2026-07-18 17:21（UTC+8）

---

## 1. 概述

`llm/` 是 Zen Studio 的 LLM 调用层，与 `gui/` 平级。职责单一：把"多轮消息 → 流式文本"抽象为统一接口 `LanguageModel`。多标签改造（2026-07-22，work plans/2026-0722-0756）后注册表模式移除：provider 由每个 `ChatPanel` 自持实例（`ChatPanel._build_providers` 为装配单点，标签间完全隔离可并行），依赖方向为"前端 → 后端"，包内不 import 任何 GUI 代码。

**统一后端策略**：对话统一经本机 agent CLI（当前为 Kimi Code CLI）完成，代码库**不存放、不读取、不输入任何 API KEY**——凭证由各 CLI 自行管理（如 kimi 的 OAuth）。DeepSeek API KEY 直连已于 2026-07-18 移除（见 `2026-0718-1455_移除APIKEY直连统一CLI后端实施计划.md`）。

设计蓝本：theia-zen `LanguageModel` Protocol（原注册表模式已随多标签改造移除），选型依据见 `2026-0718-1215_对话栏AI聊天面板选型报告.md`。

## 2. 文件结构

| 文件 | 说明 |
|:---|:---|
| `llm/__init__.py` | 包初始化：导出统一接口、后端常量（`BACKEND_KIMI_CLI`/`BACKEND_KIMI_ACP`/`BACKEND_LABELS`）与 provider 类 |
| `llm/base.py` | `LanguageModel` Protocol + `Message` 类型别名 + `Chunk` 流式块 |
| `llm/providers/__init__.py` | provider 子包标记（每家厂商一个文件） |
| `llm/providers/kimi_cli.py` | `KimiCliLLM`：本机 Kimi Code CLI 后端（spawn `kimi -p --output-format stream-json` 子进程 + session_id 续接；二进制检测链 PATH → `$KIMI_CODE_HOME/bin` → `~/.kimi-code/bin`） |
| `llm/providers/kimi_acp.py` | `KimiAcpLLM`：Kimi ACP 后端（长驻 `kimi acp` 子进程 + ndjson JSON-RPC；token 级流式、思维链可见、审批反向请求路由） |

## 3. 接口设计

```python
# 消息格式（OpenAI 格式）
Message = dict[str, str]  # {"role": "system"|"user"|"assistant", "content": str}

@dataclass(frozen=True)
class Chunk:
    kind: Literal["text", "reasoning"]  # 正文 / 过程信息（思维链或工具调用摘要）
    text: str

class LanguageModel(Protocol):
    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        """发送多轮消息，流式产出文本/过程块。"""
        ...
```

| 组件 | 说明 |
|:---|:---|
| `LanguageModel` | 统一接口，返回 `Iterator[Chunk]` 逐块产出，与 UI 解耦 |
| `Chunk` | 流式块：`kind="text"` 为正文增量；`kind="reasoning"` 为过程信息（思维链或工具调用摘要），仅当次显示、不回传 |
| `KimiCliLLM` | provider 之一（`"kimi-cli"`，默认）：本机 Kimi Code CLI（OAuth 自管凭证）；spawn 子进程逐行解析 JSONL，assistant 正文为**消息粒度**（非 token 流式），`tool_calls` 复用 reasoning 通道灰字展示；历史由 CLI 会话管理（meta 行 `session_id` 续接），`set_model(alias)` 切换模型、`reset_session()` 开新会话；⚠️ `-p` 固定 auto 权限，agent 可在项目目录读写文件与执行命令 |
| `KimiAcpLLM` | provider 之二（`"kimi-acp"`）：长驻 `kimi acp` 子进程经 ndjson JSON-RPC 对接（[ACP 协议](https://agentclientprotocol.com)，Zed/JetBrains 同款集成方式）；**token 级流式**（`agent_message_chunk`）、思维链可见（`agent_thought_chunk` → reasoning 通道）、`session/new` 原生会话、`session/set_config_option` 会话内切模型；工具审批经 `set_permission_handler()` 注入的回调路由（GUI 模态框），无回调时自动允许（等价 `-p` auto），回调返回 None/异常按拒绝兜底；进程崩溃下轮自动重启并开新会话 |

## 4. 使用方式

```python
from llm import KimiAcpLLM, KimiCliLLM

llm = KimiCliLLM()  # 或 KimiAcpLLM()（token 流式 + 思维链）；GUI 侧由 ChatPanel 自持装配
llm.set_model("kimi-code/kimi-for-coding-highspeed")  # 可选，默认 CLI default_model
for chunk in llm.chat([{"role": "user", "content": "你好"}]):
    if chunk.kind == "reasoning":
        print(chunk.text, end="", flush=True)  # 过程信息（思维链/工具摘要）：仅显示
    else:
        print(chunk.text, end="", flush=True)  # 正文
```

> ⚠️ `chat()` 是阻塞式 generator（CLI 后端为子进程 stdout 读取）。GUI 中必须放后台线程消费，经信号逐块上屏，避免冻结主线程（参考实现：`gui/panels/chat/worker.py`）。

多轮对话由 CLI 侧会话管理（首轮后 meta 行回传 `session_id`，后续请求经 `-S` 续接）；调用方无需回传历史（panel 传入的消息列表中仅末条 user 消息被用作 prompt）。子进程非零退出抛 `RuntimeError`（消息附 stderr 尾部诊断），由调用方捕获后上屏，不崩溃。

**Kimi Code CLI 版本要求与行为说明**（依据 0.27.0 实测，详见 `2026-0718-1545_KimiCLI新版兼容验证与最小修补实施计划.md`）：

- 版本要求 **≥ 0.2.0**（该版起 stream-json 输出 `session.resume_hint` meta 行，为多轮续接前提；TypeScript 重写版均满足）
- 二进制检测链：`PATH` → `$KIMI_CODE_HOME/bin/kimi` → `~/.kimi-code/bin/kimi`（桌面启动 PATH 不含安装目录时仍可发现）
- `-p` 模式默认等后台任务/subagent 完成才退出（0.24.x 起），长耗时单轮属 CLI 预期行为而非卡死
- stream-json 中可能出现重试等新事件类型（0.23.5 起），解析器按字段名容错跳过，向前兼容
- 可用模型别名由 `list_kimi_models()`（`kimi provider list --json`）**动态解析**，随 CLI 重登录刷新的服务端目录自动增减，IDE 侧零硬编码（2026-07-25 起目录含 `kimi-code/k3-256k`：K3 的 256K 版，同模式消耗约为 `k3`（1M）一半，**仅图片输入不支持视频**，effort `low`/`high`/`max`；IDE 持久化默认版本与 CLI `default_model` 均已切换至它，见 work plans/2026-0725-0205）

## 5. 安全模型（零密钥）

- 代码库**零密钥字面量、零密钥读取路径**：凭证由各 CLI 自行管理（kimi：`kimi login` OAuth，存于 `~/.kimi-code/`）
- `.gitignore` 保留 `api_key/` 条目，防未来误存密钥
- agent 权限：CLI `-p` 模式固定 auto 权限，agent 可在**项目目录**读写文件与执行命令（CLI 静态 deny 规则生效）；工作目录限定为项目根

## 6. 新增 provider

1. 在 `llm/providers/` 下新建 `<名称>.py`，实现 `LanguageModel` 协议（`chat()` 为流式 generator；历史策略由各实现自决，CLI 类推荐"末条 user 消息 + CLI 侧会话"）
2. 在 `llm/__init__.py` 中导入并加入 `__all__` 导出；GUI 消费侧在 `ChatPanel._build_providers` 装配（外部 CLI 类 provider 先 `shutil.which` 检测可用性再实例化）
3. 凭证管理：CLI 类凭证由 CLI 自管，代码库不出现密钥；API key 类凭证放项目根 `api_key/<名称>`（已 gitignore）
