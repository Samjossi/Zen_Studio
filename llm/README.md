# LLM 包说明

> **状态**：已实施
> **范围**：`llm/` 包 — LLM 调用薄层（本机 agent CLI 后端，代码库零密钥）
> **时间**：2026-07-18 17:21（UTC+8，创建）/ 2026-07-31 01:30（修订）

---

## 1. 概述

`llm/` 是 Zen Studio 的 LLM 调用层，与 `gui/` 平级。职责单一：把"多轮消息 → 流式文本"抽象为统一接口 `LanguageModel`。多标签改造（2026-07-22，文档/修改记录/2026-0722-0756）后注册表模式移除：provider 由每个 `ChatPanel` 自持实例（标签间完全隔离可并行），依赖方向为"前端 → 后端"，包内不 import 任何 GUI 代码。

**注册表装配**（2026-07-30，文档/修改记录/2026-0730-0150）：后端发现/可用性探测/模型枚举/实例工厂收敛为 `llm/registry.py` 单一注册表（`BackendSpec` + `REGISTRY`），`ChatPanel` 经注册表工厂**懒实例化** provider（首轮对话前创建，切换接口时旧实例关闭丢弃，防多标签×多后台长驻进程膨胀）；新增 CLI 后台只动 `llm/providers/` 一处。

**统一后端策略**：对话统一经本机 agent CLI（当前为 Kimi Code CLI）完成，代码库**不存放、不读取、不输入任何 API KEY**——凭证由各 CLI 自行管理（如 kimi 的 OAuth）。DeepSeek API KEY 直连已于 2026-07-18 移除（见 `2026-0718-1455_移除APIKEY直连统一CLI后端实施计划.md`）。

设计蓝本：theia-zen `LanguageModel` Protocol（原注册表模式已随多标签改造移除），选型依据见 `2026-0718-1215_对话栏AI聊天面板选型报告.md`。

## 2. 文件结构

| 文件 | 说明 |
|:---|:---|
| `llm/__init__.py` | 包初始化：导出统一接口、后端常量（`BACKEND_KIMI_ACP`/`BACKEND_LABELS`，由 REGISTRY 派生 re-export）与 provider 类 |
| `llm/base.py` | `LanguageModel` Protocol + `Message` 类型别名 + `Chunk` 流式块（kind：text/reasoning/usage）+ `UsageStats` 用量定型（source 三级 push/transcript/estimate，2026-07-31，文档/修改记录/2026-0731-1454）+ `poll_usage()` 可选轮询方法（默认 None，2026-08-02，文档/修改记录/2026-0802-0117） |
| `llm/context_limits.py` | 模型上下文窗口上限查询（2026-07-31，文档/修改记录/2026-0731-1454）：`reasonix_context_window()`（config.toml `[[providers]].context_window` 动态解析，缺项 None 不臆造）+ `reasonix_config_path()` 路径主定义（providers 反向复用，无环） |
| `llm/registry.py` | 后端注册表：`BackendSpec`（name/label/vendor/available/list_models/factory）+ `REGISTRY` 单点 + 查询 API（`spec_of`/`vendor_of`/`vendor_groups`）；模型列表进程级缓存（`_cached_list_models` 包装，锁覆盖查拉写防并发重复拉取）+ `refresh_models()` 唯一失效口（2026-07-30，文档/修改记录/2026-0730-2338）；import 无副作用，探测均惰性 |
| `llm/providers/__init__.py` | provider 子包标记（每家厂商一个文件） |
| `llm/providers/acp.py` | 泛化 ACP 连接层 `AcpConnection`（ndjson JSON-RPC 帧收发/id 配对/反向请求分发/死讯注入；agent 名参数化）+ 审批协议定型类型（`PermissionParams`/`PermissionHandler`）+ session/update 公共映射 `map_session_update`（2026-0731-1602 计划 T2：四 provider 私有 `_map_update` 上收，message/thought/usage 原三分支 + tool_call 结构化（含 todowrite→todo 特判）/tool_call_update/plan 新三分支）与 `map_usage_update` |
| `llm/providers/kimi_common.py` | kimi 二进制公共探测与模型枚举：`_find_bin`（PATH → `$KIMI_CODE_HOME/bin` → `~/.kimi-code/bin`）、`kimi_available`、`list_kimi_models`（CLI 传输层已于 2026-07-31 移除，见 文档/修改记录/2026-0731-0036） |
| `llm/providers/kimi_acp.py` | `KimiAcpLLM`：Kimi ACP 后端（长驻 `kimi acp` 子进程，复用 `AcpConnection`；token 级流式、思维链可见、审批反向请求路由；上下文用量走 wire.jsonl 落盘记录读取，source="transcript"，2026-0731-1454；轮次内 `poll_usage()` 尾部轮询实时刷新，2026-08-02，文档/修改记录/2026-0802-0117） |
| `llm/providers/reasonix_acp.py` | `ReasonixAcpLLM`：Reasonix ACP 后端（长驻 `reasonix acp`，与 KimiAcpLLM 同构；模型目录解析 `~/.reasonix/config.toml`；未 setup 经 session/new 错误映射引导「请先运行 reasonix setup」；轮次失败 `stopReason=error` 转可读报错；上下文用量走 transcript 快照文本估算，source="estimate"，2026-0731-1454） |
| `llm/providers/opencode_acp.py` | `OpenCodeAcpLLM`：OpenCode ACP 后端（同构；`opencode models` 纯文本枚举，Zen 官方 `opencode/` 前缀模型菜单层剔除） |
| `llm/providers/kilocode_acp.py` | `KiloCodeAcpLLM`：Kilo Code ACP 后端（同构；`kilo models` 纯文本枚举，网关聚合 `kilo/` 前缀模型菜单层剔除） |

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
| `KimiAcpLLM` | kimi 后台唯一接口（`"kimi-acp"`，出厂默认）：长驻 `kimi acp` 子进程经 ndjson JSON-RPC 对接（[ACP 协议](https://agentclientprotocol.com)，Zed/JetBrains 同款集成方式）；**token 级流式**（`agent_message_chunk`）、思维链可见（`agent_thought_chunk` → reasoning 通道）、`session/new` 原生会话、`session/set_config_option` 会话内切模型；工具审批经 `set_permission_handler()` 注入的回调路由（GUI 模态框），无回调时自动允许，回调返回 None/异常按拒绝兜底；进程崩溃下轮自动重启并开新会话 |
| `ReasonixAcpLLM` | provider（`"reasonix-acp"`，后台 Reasonix）：长驻 `reasonix acp` 子进程，协议层与 `KimiAcpLLM` 同构（ACP v1）；模型目录经 `list_reasonix_models()` 解析 `~/.reasonix/config.toml`（`$REASONIX_HOME` 可覆盖）`[[providers]]` 段，别名为 `provider/model` 全名（DeepSeek 系）；未 setup（`session/new` 报 `not configured` 类错误）时映射为「请先运行 reasonix setup」友好提示；轮次失败 `stopReason=error` 转可读报错 |
| `OpenCodeAcpLLM` | provider（`"opencode-acp"`，后台 OpenCode）：长驻 `opencode acp` 子进程，同构；模型目录经 `opencode models` 纯文本枚举，Zen 官方 `opencode/` 前缀模型在菜单层剔除 |
| `KiloCodeAcpLLM` | provider（`"kilocode-acp"`，后台 Kilo Code）：长驻 `kilo acp` 子进程，同构；模型目录经 `kilo models` 纯文本枚举，网关聚合 `kilo/` 前缀模型在菜单层剔除 |

### 3.1 上下文用量徽章：数据时机与各后端限制（2026-08-02，文档/修改记录/2026-0802-0117）

徽章刷新分两通道，瓶颈均不在 GUI 而在**用量数据的产生时机**：

| 通道 | 机制 | 覆盖后端 |
|:---|:---|:---|
| 轮末推送/收尾读盘 | agent 推 `usage_update`（kilocode/opencode/reasonix，**轮末一条**——kilocode 源码实证 `service.ts` prompt 阻塞至轮末才 sendUsageUpdate；协议无频率协商手段）或 IDE 收尾短轮询 wire.jsonl 基线增量（kimi，1454 计划） | 全部 |
| 轮次内轮询 | GUI 侧 QTimer（2s）调 `provider.poll_usage()`；kimi 实现为 wire.jsonl 尾部读法（T0 实证：轮次内每次 API 调用后 `usage.record` 增量写盘、数值单调爬升），其余后端默认 `None` 空转 | **仅 kimi-acp** |

限制与红线：

- **推送型后端轮次内不刷新**（kilocode/opencode/reasonix）：数据在 agent 进程内，IDE 无中间数据可挖；不插值、不估算、不按文本长度臆造百分比。上游补丁路径（kilocode `acp/event.ts` 订阅 step-finish 转 `usage_update`）可行但属 agent 侧改造，见计划 D4 补记
- `poll_usage()` 实现约束：廉价（只读文件尾部，禁全量读 MB 级 wire.jsonl）、幂等、线程安全（GUI 线程调用），失败静默 `None`，不得臆造数值
- reasonix 维持 transcript 文本估算（source="estimate"，轮末一条）

## 4. 使用方式

```python
from llm import KimiAcpLLM

llm = KimiAcpLLM()  # GUI 侧由 ChatPanel 经注册表工厂自持装配
llm.set_model("kimi-code/k3-256k")  # 可选，默认 CLI default_model
for chunk in llm.chat([{"role": "user", "content": "你好"}]):
    if chunk.kind == "reasoning":
        print(chunk.text, end="", flush=True)  # 过程信息（思维链/工具摘要）：仅显示
    else:
        print(chunk.text, end="", flush=True)  # 正文
```

> ⚠️ `chat()` 是阻塞式 generator（长驻子进程 stdio 读取）。GUI 中必须放后台线程消费，经信号逐块上屏，避免冻结主线程（参考实现：`gui/panels/chat/worker.py`）。

多轮对话由 agent 侧会话管理（`session/new` 原生会话，ACP 长驻连接内续轮）；调用方无需回传历史（panel 传入的消息列表中仅末条 user 消息被用作 prompt）。

**kimi 二进制探测与模型枚举**（`llm/providers/kimi_common.py`）：

- 二进制检测链：`PATH` → `$KIMI_CODE_HOME/bin/kimi` → `~/.kimi-code/bin/kimi`（桌面启动 PATH 不含安装目录时仍可发现）
- 可用模型别名由 `list_kimi_models()`（`kimi provider list --json`）**动态解析**，随 CLI 重登录刷新的服务端目录自动增减，IDE 侧零硬编码（2026-07-25 起目录含 `kimi-code/k3-256k`：K3 的 256K 版，同模式消耗约为 `k3`（1M）一半，**仅图片输入不支持视频**，effort `low`/`high`/`max`；IDE 持久化默认版本与 CLI `default_model` 均已切换至它，见 文档/修改记录/2026-0725-0205）

## 5. 安全模型（零密钥）

- 代码库**零密钥字面量、零密钥读取路径**：凭证由各 CLI 自行管理（kimi：`kimi login` OAuth，存于 `~/.kimi-code/`）
- `.gitignore` 保留 `api_key/` 条目，防未来误存密钥
- agent 权限：ACP 工具审批四态（允许一次/始终允许/拒绝 + 设置中心默认档），agent 可在**项目目录**读写文件与执行命令（CLI 静态 deny 规则生效）；工作目录限定为项目根

## 6. 新增 provider

1. 在 `llm/providers/` 下新建 `<名称>.py`，实现 `LanguageModel` 协议（`chat()` 为流式 generator；历史策略由各实现自决，CLI 类推荐"末条 user 消息 + CLI 侧会话"）；ACP 系后端直接复用 `llm/providers/acp.py` 的 `AcpConnection`（参照 `reasonix_acp.py` 与 `kimi_acp.py` 同构范式）
2. 在 `llm/registry.py` 的 `REGISTRY` 注册 `BackendSpec`（`available`/`list_models`/`factory` 均惰性可调用，import 无副作用）——注册即全链路生效（ModelBar 三级下拉、设置中心、ChatPanel 懒实例化工厂），**无需改任何 GUI 代码**
3. 模型范围按接口级隔离（D6 红线）：模型目录挂各 `BackendSpec.list_models()`，别名是各后台私有语义的不透明字符串，禁止跨后台共享模型表、禁止在公共代码解析别名
4. 凭证管理：CLI 类凭证由 CLI 自管，代码库不出现密钥；API key 类凭证放项目根 `api_key/<名称>`（已 gitignore）
