# LLM 包说明

> **状态**：已实施
> **范围**：`ide/llm/` 包 — LLM 调用薄层（本机 agent CLI 后端，代码库零密钥）
> **时间**：2026-07-18 17:21（UTC+8，创建）/ 2026-11-20（T5 公共出口与依赖守卫修订）/ 2026-11-21（S6 ACP 诊断与 terminal 再分）/ 2026-11-22（Kimi 极简链路与只读轮内用量修订）/ 2026-11-23（Kimi 唯一 KAP 协议修订）

---

## 1. 概述

`ide/llm/` 是 Zen Studio 的 LLM 调用层，与 `ide/gui/` 平级。职责单一：把"多轮消息 → 流式文本"抽象为统一接口 `LanguageModel`。多标签改造（2026-07-22，文档/修改记录/2026-0722-0756）后 provider 由每个 `ChatPanel` 自持实例（标签间完全隔离可并行），注册表仍是唯一的装配来源；依赖方向为"前端 → 后端"，包内不 import 任何 GUI 代码。

**注册表装配**（2026-07-30，文档/修改记录/2026-0730-0150）：后端发现/可用性探测/模型枚举/实例工厂收敛为单一注册表（`REGISTRY`），`ChatPanel` 经注册表工厂**懒实例化** provider（首轮对话前创建，切换接口时旧实例关闭丢弃，防多标签×多后台长驻进程膨胀）；新增 CLI 后台只动 `ide/llm/providers/` 与注册表，公共出口 `ide/llm/__init__.py` 不再需要追加具体 provider 类。**S5 收口**（2026-11-21）：`BackendSpec`/`resolve_efforts` 迁入中性 `llm/spec.py`，三路模型缓存迁入 `llm/cache.py`，各厂商静态元数据迁入 `providers/<vendor>/spec.py`；`registry.py` 只剩组合、`DEFAULT_BACKEND`、查询 API 与缓存失效委托。**S6 收口**（2026-11-21）：ACP 连接的脱敏/stderr ring/诊断日志迁入 `acp/diagnostics.py`，`terminal/*` 分发与异步 wait 簿记迁入 `acp/terminal.py`，`connection.py` 只保留帧收发、请求配对、反向请求总入口与轮次原语。

**统一后端策略**：对话统一经本机 agent CLI（Kimi / DSH / Local）完成，代码库**不存放、不读取、不输入任何 API KEY**——凭证由各 CLI 自行管理（如 kimi 的 OAuth）。DeepSeek API KEY 直连已于 2026-07-18 移除（见 `2026-0718-1455_移除APIKEY直连统一CLI后端实施计划.md`）。

设计蓝本：theia-zen `LanguageModel` Protocol（原注册表模式已随多标签改造移除），选型依据见 `2026-0718-1215_对话栏AI聊天面板选型报告.md`。

## 2. 文件结构

| 文件 | 说明 |
|:---|:---|
| `ide/llm/__init__.py` | 公共出口：只导出稳定契约（`LanguageModel`/`Chunk` 等）、ACP 公共类型（`PermissionRequest` 等）与注册表 API（`DEFAULT_BACKEND`/`REGISTRY`/`spec_of`/`BACKEND_LABELS` 等）；**不再导出具体 provider 类或探测函数**，具体类由各厂商包公开、由 `registry.py` 装配 |
| `ide/llm/base.py` | `LanguageModel` Protocol + `Message` 类型别名 + `Chunk` 流式块（kind：text/reasoning/usage/tool_call/tool_call_update/todo；工具/计划 `payload` 对通用层不透明，schema 归各厂商 `models.py`；`speed` 可选字段携轮次输出速度，2026-10-23，work plans/2026-1023-1632）+ `UsageStats` 用量定型（source 三级 push/transcript/estimate，2026-07-31，文档/修改记录/2026-0731-1454）+ `TurnSpeed` 输出速度定型（output_tokens/gen_seconds，仅模型生成时间计分母）+ `TurnConsumption` 轮次真实消耗定型（输入缓存读写/输出四项 + turn_id 去重键，随轮末 usage chunk 的 `consumption` 字段携带，仅具备逐次真值的后台产出，2026-1114-1452 计划）+ `BackendCapabilities` 不可变能力快照（图片输入/轮后宽限/看门狗/恢复待定）与 `capabilities_of()` 中立读取 helper、`apply_vendor_settings()` 厂商设置入口（默认 no-op，`apply_vendor_settings_to()` 兼容未实现对象） + `poll_usage()` 可选轮询方法（默认 None，2026-08-02，文档/修改记录/2026-0802-0117） |
| `ide/llm/spec.py` | 中性注册类型：`BackendSpec`（接口名/显示名/后台名/可用性/模型与强度工厂/静态能力/`dev_note`/`vendor_settings`）+ `resolve_efforts()` 强度解析链；不出现厂商名、私有路径、工具名或设置键，不 import registry/厂商包/GUI |
| `ide/llm/cache.py` | 进程级三路模型缓存：模型别名、模型级强度、原始目录载荷；锁覆盖查拉写，键恒为接口实现名；`clear(name=None)` 是唯一失效实现，`registry.refresh_models()` 只委托它 |
| `ide/llm/registry.py` | 后端组合根：按固定顺序组合 `providers/<vendor>/spec.py` 的 `SPEC`（Kimi 唯一 KAP，DSH/Local 各一 ACP）为 `REGISTRY`，`DEFAULT_BACKEND="kimi-kap"`；保留查询 API（`spec_of`/`vendor_of`/`vendor_groups`）、派生显示映射与 `refresh_models()` 委托；不再持有厂商静态数据或缓存闭包；import 无副作用，探测均惰性 |
| `ide/llm/providers/<vendor>/spec.py` | 厂商注册声明：显示名、厂商标签、可用性、目录/强度/显示名工厂、静态能力、默认强度、`dev_note`、`vendor_settings` 与实例工厂；只 import 中性 `llm.spec`/`llm.cache`、`llm.base` 与本厂商模块，不 import registry/GUI/其他厂商 |
| `ide/llm/providers/__init__.py` | provider 子包标记（稳定 ACP 公共层 + Kimi/DSH/Local 厂商目录）；刻意不 re-export 任何具体 provider |
| `ide/llm/providers/acp/__init__.py` | 稳定 ACP 公共设施出口：连接、协议定型、prompt 构造、usage 解析；不导出过渡期 update 映射 |
| `ide/llm/providers/acp/connection.py` | `AcpConnection`：ndjson JSON-RPC 帧收发/id 配对/反向请求总分发/统一幂等失效；构造接收**完整 argv**（启动命令由各厂商 provider 组装）；连接代次、请求 ID、预期/意外关闭与轮次原语；诊断与 terminal 细节委托 `diagnostics.py`/`terminal.py` |
| `ide/llm/providers/acp/diagnostics.py` | 连接诊断：凭证/JWT/Cookie 脱敏、有界 stderr ring（40 行/8KB 双限、单行 1000 字符截断）与统一诊断日志 helper；ring 按连接实例持有，无全局状态 |
| `ide/llm/providers/acp/terminal.py` | `terminal/*` 反向请求分发：create/output/wait_for_exit/kill/release、env 解析、异步 wait 应答簿记与连接死讯清理；处理函数接收连接层写帧原语，不反向持有连接对象 |
| `ide/llm/providers/acp/protocol.py` | ACP 协议定型：`PermissionOption`/`PermissionParams`/`ToolCallInfo`/`PermissionHandler`/`TerminalHandler`/`build_client_capabilities`/`map_usage_update` |
| `ide/llm/providers/acp/prompt.py` | `build_prompt_blocks`：标准 text/image ContentBlock 构造与附图路径透传 |
| `ide/llm/providers/acp/content.py` | 标准 ACP content/diff/文本截断纯函数（ANSI 净化、content 块分发、unified diff、行数截断）；不含厂商词表 |
| `ide/llm/providers/kimi/__init__.py` | Kimi 厂商包公开入口：`KimiKapLLM`、KAP 可用性探测与模型目录函数 |
| `ide/llm/providers/kimi/models.py` | Kimi 工具/计划活动载荷类型（tool_call/update、todo、提问、图片字段），只服务 Kimi 卡片，不是通用契约 |
| `ide/llm/providers/kimi/updates.py` | `KimiUpdateMapper`：显式 kind 信任、Kimi 工具名/rawInput 词表、write diff 合成、回执模板、`<image path>`、task 成果、todo `title`/`done`、层级父指针；账本按实例与 `(sessionId, toolCallId)` 隔离，由 KAP 事件适配层复用 |
| `ide/llm/providers/kimi/catalog.py` | kimi 二进制公共探测与模型目录：`kimi provider list --json` 同源派生模型别名、强度档位、显示名及 `maxContextSize` 上下文窗口（`context_sizes_from_catalog`），目录失败不臆造静态上限；`kimi_kap_available` 校验 CLI ≥ 2.7.0 |
| `ide/llm/providers/kimi/spec.py` | Kimi 唯一注册声明：`SPEC`（kimi-kap，出厂默认）；显示名/厂商标签、KAP 可用性、目录三路派生（共享原始载荷缓存）、图片能力、静态强度与默认档及实例工厂 |
| `ide/llm/providers/kimi/kap/transport.py` | KAP 传输：REST 信封/错误码、应用层 JSON ping-pong 的 WS 客户端、Desktop/`kimi web` 实例发现（回环校验、desktop 优先）与私有服务启动/回收 |
| `ide/llm/providers/kimi/kap/events.py` | KAP `item.*` → ACP `session/update` 形状适配，复用 `KimiUpdateMapper` 生成现有 Kimi 卡片载荷；文本 delta/补尾、tool 快照、todo、context 用量、turn usage |
| `ide/llm/providers/kimi/kap/provider.py` | `KimiKapLLM`：常驻服务多会话；prompt→`user_message` 精确绑定、通知轮隔离、排队/取消、审批交互、`context`/`turn.usage` 用量、`turn.ended` 终态与 `:reload` 恢复；WS 由独立 pump 消费，空闲期也更新用量并应答交互 |
| `ide/llm/providers/dsh/provider.py` | `DshAcpLLM`：DeepSeek Harness（dsh）ACP 后端（长驻 `dsh acp` 子进程，以 local/provider.py 为底本同构，计划 2026-1018-0732；探测链 PATH → `$DSH_HOME/bin` → `~/.dsh/bin` → npx 缓存兜底；强度 configId 为 `reasoning_effort`；模型值为不透明 JSON 对串，静态表与 developer preview `dev_note` 归 `dsh/spec.py`，旧别名 `deepseek-v4-flash` 不再作为新会话候选、持久化旧值不静默改写；terminals 不支持、图片能力由 initialize 的 `promptCapabilities.image` 实测（未建连时快照为 None，GUI 回退注册表静态位）；凭据归 dsh 自管 `~/.dsh/`；协议真值来源 `参考代码/deepseek-harness/packages/acp/acp/README.md`） |
| `ide/llm/providers/dsh/spec.py` | DSH 注册声明：静态模型/显示名表、静态强度、`dev_note`、可用性、图片兜底位与实例工厂；import 不探测 CLI |
| `ide/llm/providers/dsh/session_log.py` | generation 定位、zstd 解压、usage 事件提取与轮次消耗差量；纯文件计算，不 import provider/其他厂商/GUI |
| `ide/llm/providers/dsh/models.py` | DSH 工具/计划活动载荷类型，只服务 DSH 卡片，不复用 Kimi schema |
| `ide/llm/providers/dsh/updates.py` | `DshUpdateMapper`：kind 缺失/`other` 时按 DSH 工具名兜底、snake_case 词表、`read_image`、`todo_write`、DSH 图片输入与输出形态；推导账本按实例与 `(sessionId, toolCallId)` 隔离 |
| `ide/llm/providers/local/provider.py` | `LocalAcpLLM`：本地模型 ACP 后端（厂商分组 Local；探测链 PATH → `$LOCAL_HOME/bin/local-cli` → `~/.local-cli/bin/local-cli`；模型枚举走 `local-cli models` 子进程动态扫描 GGUF 目录，无静态表；服务端为独立仓库 Local CLI（llama-server orchestrator），协议参考保留在 `dream-acp/protocol/dream-acp-v1.md`，该目录不再是 Dream 后台注册入口；能力快照图片输入恒 False） |
| `ide/llm/providers/local/spec.py` | Local 注册声明：模型枚举缓存包装、静态单档强度、图片能力与实例工厂 |
| `ide/llm/providers/local/models.py` | Local 工具/计划活动载荷类型，不从 Kimi/DSH 导入 |
| `ide/llm/providers/local/updates.py` | `LocalUpdateMapper`：信任显式 kind、标准 ACP content 与自身 rawInput 词表；不借用 DSH 工具名兜底或 Kimi 私有标记 |

> 公共出口边界（T5/S5）：`ide.llm` 只导出稳定契约、ACP 公共类型与注册表 API；`BackendSpec`/`resolve_efforts` 由中性 `llm/spec.py` 提供并再导出，缓存 helper 不进公共出口。具体 provider 类由各厂商包公开、由厂商 `spec.py` 声明、由 `registry.py` 装配。公共 `acp/` 包不包含 session/update 映射、工具名表、厂商回执模板或厂商私有落盘路径；`llm.base` 只保留稳定流式信封，`Chunk.payload` 对通用层不透明。依赖方向与公共层词表由 `tests/test_provider_boundaries.py` 守卫。

## 3. 接口设计

```python
# 消息格式（OpenAI 格式）
Message = dict[str, str]  # {"role": "system"|"user"|"assistant", "content": str}

@dataclass(frozen=True)
class Chunk:
    kind: Literal[
        "text", "reasoning", "usage", "tool_call", "tool_call_update", "todo",
        "status",
    ]  # 正文 / 过程信息 / 用量 / 不透明工具与计划事件 / 后台同步状态提示
    text: str
    payload: dict | None = None  # 工具/计划私有载荷：通用层只路由不读字段

class LanguageModel(Protocol):
    def chat(self, messages: list[Message]) -> Iterator[Chunk]:
        """发送多轮消息，流式产出文本/过程块。"""
        ...
```

| 组件 | 说明 |
|:---|:---|
| `LanguageModel` | 统一接口，返回 `Iterator[Chunk]` 逐块产出，与 UI 解耦 |
| `Chunk` | 流式块：`kind="text"` 为正文增量；`kind="reasoning"` 为过程信息（思维链或工具调用摘要），仅当次显示、不回传；`usage` 与工具/计划块只更新展示，不决定轮次终态 |
| `KimiKapLLM` | Kimi 唯一后台接口（`"kimi-kap"`，出厂默认）：优先复用本机 Desktop/`kimi web`，否则拉起私有 `kimi web --port 0 --json`；REST 提交 prompt、WS 收 `turn.*`/`item.*`。以 `user_message_id`/`prompt_id` 绑定本轮，通知轮（`notification` item）只观察不收尾；追问在通知轮活跃期返回 `status=queued`，等通知轮结束后的用户轮。`turn.ended` 为终态（completed/interrupted/failed）；停止走 `:cancel`；空闲 pump 持续更新 `context` 用量，轮后宽限 1s 让终值上屏；服务重启后保留 session 并 `:reload`。KAP 不可用时明确报错，不回退 ACP |
| `DshAcpLLM` | provider（`"dsh-acp"`，后台 DSH，developer preview）：长驻 `dsh acp` 子进程，同构；模型/强度切换走 `session/set_config_option`（configId `model` / `reasoning_effort`，spike 实测），模型值为不透明 JSON 对串、静态表枚举；凭据类错误映射「配置 ~/.dsh/.credentials.yaml 或 DEEPSEEK_API_KEY」引导；轮次失败 `stopReason=error` 转可读报错 |
| `LocalAcpLLM` | provider（`"local-acp"`，后台 Local）：长驻 `local-cli acp` 子进程，同构；模型目录经 `local-cli models` 纯文本枚举（Local CLI 扫描配置 `model_dir` 下的 `*.gguf`）；思维链由 llama-server SSE 的 `reasoning_content` 字段经服务端映射进 thought 通道；模型加载/配置类错误映射「检查 ~/.local-cli/config.toml」引导 |

### 3.1 上下文用量徽章：数据时机与各后端限制

徽章刷新分两通道，瓶颈均不在 GUI 而在**用量数据的产生时机**：

| 通道 | 机制 | 覆盖后端 |
|:---|:---|:---|
| 官方推送 | DSH/Local 在正常 ACP 帧循环中接收 `usage_update`；Kimi KAP 的常驻 WS pump 接收 `context` 与 `turn.usage`，空闲期也持续更新 | 以 agent 实际推送为准 |
| 轮次内轮询 | GUI 侧 QTimer（2s）调 `provider.poll_usage()`；Kimi 返回最近一次官方 `context` 快照，轮后宽限 1s 接收可能晚于 `turn.ended` 的终值，其余后端默认 `None` | **kimi-kap** |

限制与红线：

- **推送型后端轮次内不刷新**（DSH / Local）：数据在 agent 进程内，IDE 无中间数据可挖；不插值、不估算、不按文本长度臆造百分比。
- `poll_usage()` 实现约束：廉价、幂等、线程安全，失败静默 `None`，不得臆造数值，禁 GUI 线程起目录子进程。Kimi 只读内存快照，不接收连接、不取轮次锁，也绝不读取、排空或清理事件队列。
- Kimi 上下文窗口取模型目录 `maxContextSize` 或 KAP `/api/v1/models` 的 `max_context_size`，目录在 worker 会话准备阶段加载；缺失时不做静态上限兜底。
- `context` 终值可能晚于 `turn.ended`，pump 必须常驻到轮后；1 秒宽限只用于让终值上屏，不延迟轮次终态。
- 未发送时徽章空白；发送后无真值显示 `—` 并在 tooltip 说明等待/不可用，绝不伪造 `0%`。已有有效值不被空轮询清除。

### 3.2 能力快照与厂商设置

通用 GUI 不读取 provider 私有能力属性，统一经 `capabilities_of(provider)` 取 `BackendCapabilities`（不可变快照）；未实现 `capabilities()` 的测试桩/旧实现回退默认快照。provider 每次调用按当前实例状态重建快照，不缓存到跨轮次失效。

| 字段 | 默认 | 语义 |
|:---|:---:|:---|
| `image_input` | `None` | 图片输入能力；`None` = 未实测，GUI 回退 `BackendSpec.supports_images` |
| `post_turn_usage_grace_s` | `0.0` | 轮次收尾后继续接收迟到用量帧的宽限秒数 |
| `stall_watchdog` | `True` | 本后台是否适用轮次停滞看门狗 |
| `recovery_pending` | `False` | 当前是否处于待恢复态（GUI 据此取消自动待发等） |

- **刷新时点**：provider 创建后、initialize 完成后、轮次收尾后、切换后台后；快照只读状态，不承担事件通知，也不替代动作接口。
- **厂商设置**：`BackendSpec.vendor_settings` 只声明键名；面板按声明过滤公共设置后经 `apply_vendor_settings_to()` 整体交给 provider，键名不进入通用 GUI。当前 Kimi/DSH/Local 均未声明额外厂商设置键。
- **测试桩**：不继承 `LanguageModel` 的假 provider 可能没有 `capabilities()`；一律走 helper，不在 GUI 直接 `getattr` 私有属性。

## 4. 使用方式

```python
from ide.llm import DEFAULT_BACKEND, spec_of

spec = spec_of(DEFAULT_BACKEND)  # 实际 GUI 按 ModelBar 当前后台名查表
assert spec is not None
llm = spec.factory()  # GUI 侧由 ChatPanel 懒实例化并自持
llm.set_model("kimi-code/k3-256k")  # 可选，默认 CLI default_model
for chunk in llm.chat([{"role": "user", "content": "你好"}]):
    if chunk.kind == "reasoning":
        print(chunk.text, end="", flush=True)  # 过程信息（思维链/工具摘要）：仅显示
    else:
        print(chunk.text, end="", flush=True)  # 正文
```

> ⚠️ `chat()` 是阻塞式 generator（ACP stdio 或 KAP REST/WS 读取）。GUI 中必须放后台线程消费，经信号逐块上屏，避免冻结主线程（参考实现：`ide/gui/panels/chat/worker.py`）。

多轮对话由 agent 侧会话管理（ACP 长驻连接或 KAP 常驻服务会话）；调用方无需回传历史（panel 传入的消息列表中仅末条 user 消息被用作 prompt）。

### Kimi 连接失效与恢复状态机

- 手动停止走 `POST /api/v1/sessions/{id}:cancel`，等待本轮 `turn.ended(outcome=interrupted)`；超时按本地取消解封，不自动接管其他客户端占用的会话。Kimi 不启动停滞看门狗。
- 轮次只由 `user_message_id`/`prompt_ids` 命中的用户轮收尾；通知轮是独立 `agent` turn，不得用它的 `turn.ended` 结束用户轮。`completed` 正常完成，`interrupted` 走 `TurnCancelled`，`failed` 转可读错误。
- WS 断开或服务进程退出会保留 `session_id` 并标记恢复待定；下一次手动发送重新发现/启动服务，原会话存在则 `:reload`，已不存在才新建。原 prompt 不自动重放。
- WS reader 只负责帧解析与应用层 JSON ping/pong，独立 pump 是唯一事件消费者；空闲期仍处理 `context` 用量和交互，轮后宽限不阻塞 reader。
- 服务发现优先复用 Desktop/普通 `kimi web`，私有服务由本 provider 回收；复用实例不杀。日志只记录事件类型、业务码、请求/会话/轮次 ID 与脱敏数值，不记录 token、prompt 正文或完整工具载荷。

**Kimi 后台通知边界**：通知轮活跃期提交的追问由服务端排队，通知轮结束后才生成新的用户轮；客户端严格按 prompt 绑定观察，不以私有落盘或本地状态机判断终态。

**kimi 二进制探测与模型枚举**（`ide/llm/providers/kimi/catalog.py`）：

- 二进制检测链：`PATH` → `$KIMI_CODE_HOME/bin/kimi` → `~/.kimi-code/bin/kimi`（桌面启动 PATH 不含安装目录时仍可发现）
- 可用模型别名由 `list_kimi_models()`（`kimi provider list --json`）**动态解析**，随 CLI 重登录刷新的服务端目录自动增减，IDE 侧零硬编码（2026-07-25 起目录含 `kimi-code/k3-256k`：K3 的 256K 版，同模式消耗约为 `k3`（1M）一半，**仅图片输入不支持视频**，effort `low`/`high`/`max`；IDE 持久化默认版本与 CLI `default_model` 均已切换至它，见 文档/修改记录/2026-0725-0205。2026-10-24 起目录含 `kimi-code/k3.1-pro` / `kimi-code/k3.1-pro-highspeed`：K3.1 Pro 系列，3T 级、1M 上下文（高档位会员）、仅 thinking（`always_thinking`）、effort `low`/`high`/`max` 默认 `max`、支持视频输入；目录字段齐全，IDE 零改动接入，见 `work plans/2026-1024-0403_K3.1Pro新模型接入落地计划.md`）
- 上下文窗口由 `context_sizes_from_catalog()` 同源解析 `maxContextSize`，先合并 `overrides`；provider 在 worker 会话准备阶段加载缓存，GUI 轮询不起目录子进程，缺失不做静态兜底。

## 5. 安全模型（零密钥）

- 代码库**零密钥字面量、零密钥读取路径**：凭证由各 CLI 自行管理（kimi：`kimi login` OAuth，存于 `~/.kimi-code/`）
- `.gitignore` 保留 `api_key/` 条目，防未来误存密钥
- agent 权限：Kimi KAP 与 DSH/Local ACP 共用工具审批四态（允许一次/始终允许/拒绝 + 设置中心默认档），agent 可在**项目目录**读写文件与执行命令（CLI 静态 deny 规则生效）；工作目录限定为项目根

## 6. 新增后台 provider

1. 在 `ide/llm/providers/<名称>/` 建厂商包：`__init__.py` 只导出本厂商公开入口，`provider.py` 实现 `LanguageModel` 协议（`chat()` 为流式 generator；历史策略由各实现自决，CLI 类推荐"末条 user 消息 + CLI 侧会话"）；按实例状态覆写 `capabilities()` 返回 `BackendCapabilities`，需要用户配置时覆写 `apply_vendor_settings()`；ACP 系后端直接复用 `ide/llm/providers/acp/` 的 `AcpConnection`，由 provider 组装完整启动 argv（如 `[bin_path, "acp"]`；参照任一现有厂商包）。
2. 在 `ide/llm/providers/<名称>/spec.py` 声明本厂商 `SPEC = BackendSpec(...)`：显示名/厂商标签、可用性、模型/强度/显示名工厂、静态能力、默认强度、可选 `dev_note` 与 `vendor_settings`；`available`/`list_models`/`factory` 均惰性可调用，import 无副作用。需要共享目录载荷或进程缓存时只经 `ide/llm/cache.py` 的 `cached_*` helper，缓存键用接口实现名。
3. 在 `ide/llm/registry.py` 的 `REGISTRY` 组合元组追加本厂商 `SPEC`——注册即全链路生效（ModelBar 三级下拉、设置中心、ChatPanel 懒实例化工厂），**无需改任何 GUI 代码**。`registry.py` 是唯一允许 import 具体厂商后台的模块；不要在 `ide/llm/__init__.py` 追加具体类或探测函数。
4. 厂商 `spec.py` 只允许 import 中性 `ide.llm.spec`/`ide.llm.cache`、`ide.llm.base` 与本厂商模块；禁止 import `ide.llm.registry`、GUI 或其他厂商。
5. 厂商包之间禁止横向 import；provider 不得 import `ide.llm.registry` 或 `ide.gui`。厂商私有工具名、载荷字段、配置键和落盘路径只留在本厂商目录。
6. 模型范围按接口级隔离（D6 红线）：模型目录挂各 `BackendSpec.list_models()`，别名是各后台私有语义的不透明字符串，禁止跨后台共享模型表、禁止在公共代码解析别名。
7. 凭证管理：CLI 类凭证由 CLI 自管，代码库不出现密钥；API key 类凭证放项目根 `api_key/<名称>`（已 gitignore）。

## 7. 新增厂商卡片包

1. 在 `ide/gui/panels/chat/vendors/<名称>/` 建 `factory.py`、`cards.py`、`presenter.py`、`__init__.py`：工厂只理解本厂商工具名与载荷，卡片只放本厂商具体格式，presenter 只维护本厂商迟到帧、标题/命令账本和更新逻辑。
2. 在 `ide/gui/panels/chat/vendors/registry.py` 的 `_FACTORIES`/`_PRESENTERS`/`_CARDS` 注册本厂商入口。该文件是唯一允许 import 具体厂商卡片包的模块；通用面板只经它取得工厂/presenter。
3. 厂商卡片包之间禁止横向 import；需要纯数据类型时最多 import 本厂商 `ide.llm.providers.<名称>.models`，不得 import provider 运行时、连接对象、registry 或另一家的卡片。
4. 具体卡片可以从现有厂商等价复制，但复制后归本厂商维护；禁止再抽出跨厂商共享的具体卡片或卡片基类。

## 8. 架构守卫

`tests/test_provider_boundaries.py` 用标准库 AST 扫描 `src/ide/**/*.py` 与 `main.py`（不含 `tests/`、`scripts/`、历史文档和帧存档），锁定：

- 后台三厂商、GUI 卡片三厂商互不横向 import；
- 只有 `ide/llm/registry.py` 可 import 具体后台包，只有 `vendors/registry.py` 可 import 具体卡片包；
- 厂商 `spec.py` 只 import 中性 `llm.spec`/`llm.cache`、`llm.base` 与本厂商模块；`llm.spec`/`llm.cache` 与 `registry.py` 按公共层扫描，不得含厂商显示名、工具名、私有路径或设置键；
- provider/公共 ACP 不 import registry 或 GUI，`llm.base`/`permission_policy` 不依赖具体厂商；
- 公共 LLM 层与通用聊天 GUI 不含厂商工具名分派表、私有载荷字段、私有 configId 或落盘路径；
- 生产代码不再 import 迁移前的平铺模块路径。

守卫自带受控反例，反例只在测试内存构造；提交前统一跑 `bash scripts/check.sh`。
