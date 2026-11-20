# LLM 包说明

> **状态**：已实施
> **范围**：`ide/llm/` 包 — LLM 调用薄层（本机 agent CLI 后端，代码库零密钥）
> **时间**：2026-07-18 17:21（UTC+8，创建）/ 2026-11-20（修订）

---

## 1. 概述

`ide/llm/` 是 Zen Studio 的 LLM 调用层，与 `ide/gui/` 平级。职责单一：把"多轮消息 → 流式文本"抽象为统一接口 `LanguageModel`。多标签改造（2026-07-22，文档/修改记录/2026-0722-0756）后注册表模式移除：provider 由每个 `ChatPanel` 自持实例（标签间完全隔离可并行），依赖方向为"前端 → 后端"，包内不 import 任何 GUI 代码。

**注册表装配**（2026-07-30，文档/修改记录/2026-0730-0150）：后端发现/可用性探测/模型枚举/实例工厂收敛为 `ide/llm/registry.py` 单一注册表（`BackendSpec` + `REGISTRY`），`ChatPanel` 经注册表工厂**懒实例化** provider（首轮对话前创建，切换接口时旧实例关闭丢弃，防多标签×多后台长驻进程膨胀）；新增 CLI 后台只动 `ide/llm/providers/` 与注册表。

**统一后端策略**：对话统一经本机 agent CLI（Kimi / DSH / Local）完成，代码库**不存放、不读取、不输入任何 API KEY**——凭证由各 CLI 自行管理（如 kimi 的 OAuth）。DeepSeek API KEY 直连已于 2026-07-18 移除（见 `2026-0718-1455_移除APIKEY直连统一CLI后端实施计划.md`）。

设计蓝本：theia-zen `LanguageModel` Protocol（原注册表模式已随多标签改造移除），选型依据见 `2026-0718-1215_对话栏AI聊天面板选型报告.md`。

## 2. 文件结构

| 文件 | 说明 |
|:---|:---|
| `ide/llm/__init__.py` | 包初始化：导出统一接口、后端常量（`BACKEND_KIMI_ACP`/`BACKEND_LABELS`，由 REGISTRY 派生 re-export）与 provider 类 |
| `ide/llm/base.py` | `LanguageModel` Protocol + `Message` 类型别名 + `Chunk` 流式块（kind：text/reasoning/usage/turn_sync；`speed` 可选字段携轮次输出速度，2026-10-23，work plans/2026-1023-1632）+ `UsageStats` 用量定型（source 三级 push/transcript/estimate，2026-07-31，文档/修改记录/2026-0731-1454）+ `TurnSpeed` 输出速度定型（output_tokens/gen_seconds，仅模型生成时间计分母）+ `TurnConsumption` 轮次真实消耗定型（输入缓存读写/输出四项 + turn_id 去重键，随轮末 usage chunk 的 `consumption` 字段携带，仅 kimi / DSH 产出，2026-1114-1452 计划）+ `poll_usage()` 可选轮询方法（默认 None，2026-08-02，文档/修改记录/2026-0802-0117） |
| `ide/llm/registry.py` | 后端注册表：`BackendSpec`（name/label/vendor/available/list_models/factory + 可选 `dev_note` 开发中告知文案，2026-08-14，文档/修改记录/2026-0814-0603 计划）+ `REGISTRY` 单点 + 查询 API（`spec_of`/`vendor_of`/`vendor_groups`）；模型列表进程级缓存（`_cached_list_models` 包装，锁覆盖查拉写防并发重复拉取）+ `refresh_models()` 唯一失效口（2026-07-30，文档/修改记录/2026-0730-2338）；import 无副作用，探测均惰性 |
| `ide/llm/providers/__init__.py` | provider 子包标记（每家厂商一个文件） |
| `ide/llm/providers/acp.py` | 泛化 ACP 连接层 `AcpConnection`（ndjson JSON-RPC 帧收发/id 配对/反向请求分发/统一幂等失效；agent 名参数化；连接代次、请求 ID、预期/意外关闭与有界脱敏 stderr 尾部诊断）+ 审批协议定型类型（`PermissionParams`/`PermissionHandler`）+ terminal/* 反向能力（2026-0817-1554 计划：`TerminalHandler` 契约 + `set_terminal_handler` 鸭子类型注入 + `build_client_capabilities` 声明收口；create/output/kill/release 同步应答、wait_for_exit 异步应答不占 reader 线程、无 handler 维持 -32601 兜底；GUI 桥实现见 `ide/gui/panels/terminal/agent_bridge.py`）+ session/update 公共映射 `map_session_update`（2026-0731-1602 计划 T2：四 provider 私有 `_map_update` 上收，message/thought/usage 原三分支 + tool_call 结构化/tool_call_update/plan 新三分支；2026-0808-0627 计划 T1：todowrite→todo 首帧特判撤销，rawInput.todos 提取为 `todos` 载荷 + 跨调用 diff changed 标记，kind="todo" 仅余 plan 通道；2026-0813-1919 计划 T1：层级 toolCallId `父/子` 拆出 `parent_tool_call_id` 父指针——reasonix 子代理内部活动帧嵌套显示锚点，无层级 ID 后端零影响）与 `map_usage_update` |
| `ide/llm/providers/kimi_common.py` | kimi 二进制公共探测与模型目录：`kimi provider list --json` 同源派生模型别名、强度档位、显示名及 `maxContextSize` 上下文窗口（`context_sizes_from_catalog`），目录失败不臆造静态上限 |
| `ide/llm/providers/kimi_acp.py` | `KimiAcpLLM`：Kimi ACP 后端（长驻 `kimi acp` 子进程，复用 `AcpConnection`；token 级流式、思维链可见、审批反向请求路由；轮末 ACP `usage_update` 为主，response 后在同一 ≤3s 窗口继续收集，并由轮后短宽限/下轮开始前回收迟到帧，source="push"；wire 用于轮内增量轮询与 ACP 缺失兜底，source="transcript"，优先 token_counting.measured 水位、旧 usage.record 四项求和兜底，窗口读模型目录而非 llm.request.maxTokens；仅带 turnStep 的请求参与计数与速度配对，超时保留有效子集；轮次输出速度随收尾 usage chunk 携带；子代理 wire 旁路 `_WireSidecar` 格式假设见 `维护手册/后端CLI工具维护说明手册.md`） |
| `ide/llm/providers/kimi_turn_consistency.py` | Kimi 主 wire 的纯元数据跟踪器：按字节游标与残行缓冲读取用户/通知轮次的 promptId、turnId、origin/trigger、finishReason 和完成边界；response 后区分 `normal` / `draining` / `desynchronized` / `unknown`，不保存或输出正文、工具参数与工具输出；`TurnDesynchronized` 为失配专用失败异常 |
| `ide/llm/providers/dsh_acp.py` | `DshAcpLLM`：DeepSeek Harness（dsh）ACP 后端（长驻 `dsh acp` 子进程，以 local_acp.py 为底本同构，计划 2026-1018-0732；探测链 PATH → `$DSH_HOME/bin` → `~/.dsh/bin` → npx 缓存兜底；强度 configId 为 `reasoning_effort`；模型值为不透明 JSON 对串，静态表仅列现行 `deepseek-flash` / `deepseek-v4-pro`，旧别名 `deepseek-v4-flash` 不再作为新会话候选、持久化旧值不静默改写；terminals 与图片不支持（spike 实测，帧存档 dsh_spike_20261018_074413.json）；凭据归 dsh 自管 `~/.dsh/`，developer preview 状态经注册表 `dev_note` 如实告知；协议真值来源 `参考代码/deepseek-harness/packages/acp/acp/README.md`） |
| `ide/llm/providers/local_acp.py` | `LocalAcpLLM`：本地模型 ACP 后端（厂商分组 Local；探测链 PATH → `$LOCAL_HOME/bin/local-cli` → `~/.local-cli/bin/local-cli`；模型枚举走 `local-cli models` 子进程动态扫描 GGUF 目录，无静态表；服务端为独立仓库 Local CLI（llama-server orchestrator），协议参考保留在 `dream-acp/protocol/dream-acp-v1.md`，该目录不再是 Dream 后台注册入口） |

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
| `Chunk` | 流式块：`kind="text"` 为正文增量；`kind="reasoning"` 为过程信息（思维链或工具调用摘要），仅当次显示、不回传；`kind="turn_sync"` 为 Kimi 轮次同步提示，同样只上屏、不入历史 |
| `KimiAcpLLM` | kimi 后台唯一接口（`"kimi-acp"`，出厂默认）：长驻 `kimi acp` 子进程经 ndjson JSON-RPC 对接（[ACP 协议](https://agentclientprotocol.com)，Zed/JetBrains 同款集成方式）；**token 级流式**（`agent_message_chunk`）、思维链可见（`agent_thought_chunk` → reasoning 通道）、`session/new` 原生会话、`session/set_config_option` 会话内切模型；工具审批经 `set_permission_handler()` 注入的回调路由（GUI 模态框），无回调时自动允许，回调返回 None/异常按拒绝兜底；轮次终态按 `stopReason` 分类：`cancelled` → `TurnCancelled` 停止通道，`max_tokens` / `max_turn_requests` / `refusal` → 失败通道（带原始值），缺失/未知 fail-open；断管或看门狗停滞后，下一次手动发送重建连接并以 `session/resume` 恢复原会话，绝不自动重放中断 prompt |
| `DshAcpLLM` | provider（`"dsh-acp"`，后台 DSH，developer preview）：长驻 `dsh acp` 子进程，同构；模型/强度切换走 `session/set_config_option`（configId `model` / `reasoning_effort`，spike 实测），模型值为不透明 JSON 对串、静态表枚举；凭据类错误映射「配置 ~/.dsh/.credentials.yaml 或 DEEPSEEK_API_KEY」引导；轮次失败 `stopReason=error` 转可读报错 |
| `LocalAcpLLM` | provider（`"local-acp"`，后台 Local）：长驻 `local-cli acp` 子进程，同构；模型目录经 `local-cli models` 纯文本枚举（Local CLI 扫描配置 `model_dir` 下的 `*.gguf`）；思维链由 llama-server SSE 的 `reasoning_content` 字段经服务端映射进 thought 通道；模型加载/配置类错误映射「检查 ~/.local-cli/config.toml」引导 |

### 3.1 上下文用量徽章：数据时机与各后端限制

徽章刷新分两通道，瓶颈均不在 GUI 而在**用量数据的产生时机**：

| 通道 | 机制 | 覆盖后端 |
|:---|:---|:---|
| 轮末推送/收尾读盘 | agent 推 `usage_update`；kimi 在 response 后异步发送，provider 于同一 ≤3s 窗口继续消费并优先采用（`source="push"`），ACP 缺失才回退 wire（`source="transcript"`）；Kimi 另有最长 10 秒非 busy 宽限，并在下轮 purge 前回收迟到 usage | 全部 |
| 轮次内轮询 | GUI 侧 QTimer（2s）调 `provider.poll_usage()`；kimi 首次以 64KB 尾部建立 wire.jsonl 游标，之后仅读新增字节并保留最后有效采样，其余后端默认 `None` 空转 | **仅 kimi-acp** |

限制与红线：

- **推送型后端轮次内不刷新**（DSH / Local）：数据在 agent 进程内，IDE 无中间数据可挖；不插值、不估算、不按文本长度臆造百分比。
- `poll_usage()` 实现约束：廉价（增量读与只读缓存，禁反复全量读 MB 级 wire.jsonl、禁 GUI 线程起目录子进程）、幂等、线程安全，失败静默 `None`，不得臆造数值。空闲回收必须非阻塞取得轮次锁，只提取 usage，其他帧维持原清理语义。
- Kimi wire 优先以 `token_counting.measured.tokens` 为实测上下文水位；缺失时旧 `usage.record` 的 used 为 `inputOther + inputCacheRead + inputCacheCreation + output`。上下文窗口取模型目录 `maxContextSize`，`llm.request.maxTokens` 是输出预算，禁止用作窗口。只有带 `turnStep` 的请求参与轮次计数与速度配对，超时保留有效子集。
- 会话目录仅缓存成功定位，失败按 2/5/15 秒有界退避；空模型目录不置 loaded，下一轮 worker 准备阶段重试。文件截断、会话/模型切换会重置对应游标，空轮询与无效帧不清除最后有效值。
- ACP `used` 是 agent 对外报告的上下文占用，可含官方策略的估算尾部，与会员额度分开理解；未收到真实用量时不做文本长度估算。
- 未发送时徽章空白；发送后无真值显示 `—` 并在 tooltip 说明等待/不可用，绝不伪造 `0%`。已有有效值不被空轮询清除，轮末官方 push 在空闲期保持最终优先级。

## 4. 使用方式

```python
from ide.llm import KimiAcpLLM

llm = KimiAcpLLM()  # GUI 侧由 ChatPanel 经注册表工厂自持装配
llm.set_model("kimi-code/k3-256k")  # 可选，默认 CLI default_model
for chunk in llm.chat([{"role": "user", "content": "你好"}]):
    if chunk.kind == "reasoning":
        print(chunk.text, end="", flush=True)  # 过程信息（思维链/工具摘要）：仅显示
    else:
        print(chunk.text, end="", flush=True)  # 正文
```

> ⚠️ `chat()` 是阻塞式 generator（长驻子进程 stdio 读取）。GUI 中必须放后台线程消费，经信号逐块上屏，避免冻结主线程（参考实现：`ide/gui/panels/chat/worker.py`）。

多轮对话由 agent 侧会话管理（`session/new` 原生会话，ACP 长驻连接内续轮）；调用方无需回传历史（panel 传入的消息列表中仅末条 user 消息被用作 prompt）。

### Kimi 连接失效与恢复状态机

- 正常手动停止只发送 `session/cancel` 并本地解除等待，连接与原会话保留；看门狗停滞经 `mark_stalled()` 标记下轮必须恢复。
- 轮次终态以 `session/prompt` 响应的 `stopReason` 为准（Kimi）：`cancelled` 抛 `TurnCancelled` 走停止收尾（保留屏幕输出、回滚 history、不播报成功），`max_tokens` / `max_turn_requests` / `refusal` 抛带原始值的 `RuntimeError` 走失败收尾；两者都在轮末用量收尾之后抛，避免丢 usage/速度/消耗。缺失或未知值 fail-open 并按正常完成收尾，未知值记 warning 保留原始值。
- EOF、进程退出或任意写路径失败统一标记连接死亡，唤醒控制请求、轮次消费与 terminal wait；会话 ID、创建工作区和 MCP 参数不随连接销毁。
- 下一次手动发送在同一 `_turn_lock` 内执行 `initialize → session/resume → session/set_config_option`；`resume` 不回放历史，恢复后只发送本次新指令。恢复期间到达的旧连接事件不参与新连接状态。
- CLI 未声明 `agentCapabilities.sessionCapabilities.resume`，或 initialize/resume/模型/thinking 配置失败时，恢复 fail closed：不发送 prompt、不降级 `session/new`，用户可重试或显式清空会话。
- stderr 由独立 daemon reader 持续排空，内存仅保留最多 40 行、8 KiB 的脱敏尾部；正常对话不逐行落日志，异常退出、写失败、请求超时和初始化/恢复失败才输出诊断尾部。结构化日志记录 PID、agent 版本、连接代次、会话 ID、请求 ID、失败方法、退出码与恢复结果，不记录 prompt 正文或 ACP stdout 原文。

**Kimi 后台通知轮次一致性**：`KimiTurnTracker` 只解析当前 IDE 轮次之后新增的私有 wire 元数据。正常无风险轮次不因 wire 缺失而失败；已出现通知或轮次矛盾时，`end_turn` 不再直接等于成功。通知活跃进入 drain，沿用原 worker 消费正文、工具、sidecar 与 usage；用户轮次缺失、在 `tool_use` 后完成或风险核对信息损坏时 fail closed。只有确认已知活动结束后才设置恢复标记，下一轮手动发送沿用原 `sessionId` 执行 `session/resume`，绝不自动重放原请求。私有格式判定不扩散到公共 ACP、DSH 或 Local。

**kimi 二进制探测与模型枚举**（`ide/llm/providers/kimi_common.py`）：

- 二进制检测链：`PATH` → `$KIMI_CODE_HOME/bin/kimi` → `~/.kimi-code/bin/kimi`（桌面启动 PATH 不含安装目录时仍可发现）
- 可用模型别名由 `list_kimi_models()`（`kimi provider list --json`）**动态解析**，随 CLI 重登录刷新的服务端目录自动增减，IDE 侧零硬编码（2026-07-25 起目录含 `kimi-code/k3-256k`：K3 的 256K 版，同模式消耗约为 `k3`（1M）一半，**仅图片输入不支持视频**，effort `low`/`high`/`max`；IDE 持久化默认版本与 CLI `default_model` 均已切换至它，见 文档/修改记录/2026-0725-0205。2026-10-24 起目录含 `kimi-code/k3.1-pro` / `kimi-code/k3.1-pro-highspeed`：K3.1 Pro 系列，3T 级、1M 上下文（高档位会员）、仅 thinking（`always_thinking`）、effort `low`/`high`/`max` 默认 `max`、支持视频输入；目录字段齐全，IDE 零改动接入，见 `work plans/2026-1024-0403_K3.1Pro新模型接入落地计划.md`）
- 上下文窗口由 `context_sizes_from_catalog()` 同源解析 `maxContextSize`，先合并 `overrides`；provider 在 worker 会话准备阶段加载缓存，GUI 轮询不起目录子进程，缺失不做静态兜底。

## 5. 安全模型（零密钥）

- 代码库**零密钥字面量、零密钥读取路径**：凭证由各 CLI 自行管理（kimi：`kimi login` OAuth，存于 `~/.kimi-code/`）
- `.gitignore` 保留 `api_key/` 条目，防未来误存密钥
- agent 权限：ACP 工具审批四态（允许一次/始终允许/拒绝 + 设置中心默认档），agent 可在**项目目录**读写文件与执行命令（CLI 静态 deny 规则生效）；工作目录限定为项目根

## 6. 新增 provider

1. 在 `ide/llm/providers/` 下新建 `<名称>.py`，实现 `LanguageModel` 协议（`chat()` 为流式 generator；历史策略由各实现自决，CLI 类推荐"末条 user 消息 + CLI 侧会话"）；ACP 系后端直接复用 `ide/llm/providers/acp.py` 的 `AcpConnection`（参照 `local_acp.py` 与 `kimi_acp.py` 同构范式）
2. 在 `ide/llm/registry.py` 的 `REGISTRY` 注册 `BackendSpec`（`available`/`list_models`/`factory` 均惰性可调用，import 无副作用）——注册即全链路生效（ModelBar 三级下拉、设置中心、ChatPanel 懒实例化工厂），**无需改任何 GUI 代码**
3. 模型范围按接口级隔离（D6 红线）：模型目录挂各 `BackendSpec.list_models()`，别名是各后台私有语义的不透明字符串，禁止跨后台共享模型表、禁止在公共代码解析别名
4. 凭证管理：CLI 类凭证由 CLI 自管，代码库不出现密钥；API key 类凭证放项目根 `api_key/<名称>`（已 gitignore）
