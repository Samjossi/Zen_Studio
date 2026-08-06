# Dream ACP 接入协议 v1.0

> **文档版本**：v1.0
> **生效日期**：2026-08-03
> **线协议**：ACP `protocolVersion: 1`（initialize 协商值；与本文档版本是两个层面，见「变更纪律」）
> **真值来源**：Zen Studio `llm/providers/acp.py` 客户端实现 + Kimi / Reasonix / OpenCode / Kilo Code 四后台实测。协议真值来源是代码与实测，不是猜想。

---

## 变更纪律

- 本文档版本号（v1.0 起）**独立演进**，与 ACP 线协议 `protocolVersion: 1`
  是两个层面：后者是 initialize 握手的协商值，前者是 Dream 接入规约的文档版本。
- 任何协议面变更（新增方法 / 字段语义变化 / 行为约束调整）**必须升文档版本**，
  并在文尾「变更记录」追加一条（版本、日期、条目、理由）。
- 文档跟随客户端实测演进，不臆造协议：新增规约必须有对应的客户端实现或
  实测证据；`_meta` 厂商扩展默认被客户端忽略（返回 None 不崩），Dream
  需要时必须升版本逐个备案。

---

## 1. 传输与生命周期

### 1.1 启动方式

- 客户端以 **`[bin_path, "acp"]`** 启动 agent 长驻子进程（argv 恒定为两个元素，
  无其他参数）。Dream CLI 必须支持 `dream acp` 子命令进入 ACP 模式。
- 子进程 `cwd` 由客户端指定（项目工作区根目录）。
- 同一时刻仅一个活跃对话轮次。

### 1.2 帧格式

- **stdio ndjson JSON-RPC 2.0**：每行一个完整 JSON 对象，UTF-8 编码，`\n` 分隔。
- 请求/响应按 `id` 配对；`id` 为客户端自增整数。
- **stdout 只写协议帧**。任何日志、诊断、进度输出**一律走 stderr**
  （客户端将 stderr 重定向到 DEVNULL/日志，stdout 混入非 JSON 行会被
  丢弃并污染诊断）。
- 三类帧：
  1. **请求/响应**：客户端 → agent（`initialize`、`session/new`、
     `session/prompt`、`session/set_config_option`）。
  2. **通知**：agent → client（`session/update`，无 `id`，无需回应）；
     客户端 → agent（`session/cancel`）。
  3. **反向请求**：agent → client（`session/request_permission`，
     带 `id`，客户端必须应答——见 §3）。

### 1.3 initialize

客户端首帧：

```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{
  "protocolVersion": 1,
  "clientCapabilities": {
    "fs": {"readTextFile": false, "writeTextFile": false},
    "terminal": false
  },
  "clientInfo": {"name": "zen-studio", "title": "Zen Studio", "version": "..."}
}}
```

要点：

- `fs.readTextFile/writeTextFile: false`、`terminal: false`——**客户端不代办
  文件读写与终端执行**。Dream 的工具（读文件/写文件/执行命令）必须 agent
  自行执行，不要向客户端发 `fs/read_text_file` 等反向请求（客户端一律回
  `-32601 method not found`）。
- agent 响应必须含 `agentInfo`（`name`、`version`），客户端记录到诊断日志：

```json
{"jsonrpc":"2.0","id":1,"result":{
  "protocolVersion": 1,
  "agentInfo": {"name": "dream", "version": "0.1.0"}
}}
```

- **`authMethods` 是静态能力声明，不能当「未配置」信号**（附录 A.1）：
  无论是否返回 `authMethods`，客户端都不作拦截。模型未加载/配置缺失的
  信号一律归 `session/new` 错误（§1.4）。

### 1.4 session/new

```json
{"jsonrpc":"2.0","id":2,"method":"session/new","params":{
  "cwd": "/绝对/路径",
  "mcpServers": []
}}
```

- 超时 60s。响应：`{"sessionId": "..."}`。
- **`cwd` 硬校验必须为绝对路径**；相对路径必须拒绝，返回 **`-32602`
  invalid params**（附录 A.2，Reasonix 实测先例）。
- 模型未加载/配置缺失在**这里**报错（不在 initialize）：返回 error 帧，
  message 含可读原因（客户端会映射为用户引导文案，见附录 A.5）。
- `mcpServers` 恒为空数组（客户端不注入 MCP）。

### 1.5 session/set_config_option（模型切换）

```json
{"jsonrpc":"2.0","id":3,"method":"session/set_config_option","params":{
  "sessionId": "...",
  "configId": "model",
  "value": "dream/demo-smart"
}}
```

- 超时 10s。用于**会话内即时切换模型**。
- `value` 是**不透明字符串**：原样接受，不解析、不拼接、不校验格式
  （模型别名是各后台私有语义）。未知别名可报错，也可忽略保持当前模型，
  但不得崩溃。
- 新会话建立后，若用户预选了模型，客户端会立即补发一次本请求。

### 1.6 session/cancel

```json
{"jsonrpc":"2.0","method":"session/cancel","params":{"sessionId":"..."}}
```

- **通知**（无 `id`），无需响应。
- 收到后**当前轮次立即停止**：尽快结束流式输出并回 prompt 响应
  （`stopReason` 用 `cancelled` 或 `end_turn` 均可，客户端均按轮次结束处理）。
- **cancel 之后连接与会话必须可复用**：同一会话可继续 `session/prompt`
  新一轮，不得要求重建会话或重启进程。

---

## 2. 轮次与流式

### 2.1 session/prompt

```json
{"jsonrpc":"2.0","id":4,"method":"session/prompt","params":{
  "sessionId": "...",
  "prompt": [
    {"type": "text", "text": "用户消息……"},
    {"type": "image", "data": "<base64>", "mimeType": "image/png"}
  ]
}}
```

- `prompt` 为 ContentBlock 数组，**恒以 text 块打头**；纯图片场景 text 为
  占位文案「请查看附图。」（客户端已兜底）。agent 亦应容忍空 text 块。
- image 块：`data` 为 base64，`mimeType` 如 `image/png`。无视觉能力时
  可忽略 image 块（不报错）。
- **历史由 agent 会话自管理**：客户端每轮只发末条 user 消息；工具结果
  回灌、上下文拼装都在 agent 内部闭环。

### 2.2 流式通知 session/update

轮次内 agent 持续发通知（无 `id`）：

```json
{"jsonrpc":"2.0","method":"session/update","params":{
  "sessionId": "...",
  "update": {"sessionUpdate": "agent_message_chunk",
             "content": {"type": "text", "text": "正文增量"}}
}}
```

| `sessionUpdate` | 语义 | 要点 |
|:---|:---|:---|
| `agent_message_chunk` | 正文流式增量 | `content.text` 非空才上屏 |
| `agent_thought_chunk` | 思维链增量（客户端灰字显示） | 同上 |
| `tool_call` | 工具调用开始 | 带 `toolCallId`、`title`、`kind`、`rawInput`、`locations`；`rawInput.todos` 检出即转 todo 清单卡 |
| `tool_call_update` | 工具状态流转 | `status`: `in_progress`/`completed`/`failed`；failed 带错误详情。首帧空壳时 `rawInput` 可在 `in_progress` 帧补发（客户端回填入参区，首帧优先不覆盖）；`rawInput.questions` 同频回填（问答卡数据源） |
| `usage_update` | 上下文用量 | 见 §2.3 |
| `plan` | todo 清单（可选） | `entries`: `[{content, status, priority}]` |

工具出参的图片通道（0806 修订）：`rawOutput.output` 为字符串形态的
content 数组（如 `[{"type":"text",...},{"type":"image_url",...}]`）或
`content` 帧内 `image`（`data`+`mimeType`）/ `image_url`（`imageUrl.url`
为 `data:`/`file:` 开头）/ `resource`（`blob` 且 mime 为 `image/*`）
块时，客户端提取为图片内嵌渲染，不再以文本裸露；`http(s)` 图片链接
不联网拉取，仅留占位文本。字符串出参为合法 JSON 时客户端 pretty 化
展示。系统指令性回执文本（如 "Ensure that you continue to use the
todo list..."）不应作为出参发送——客户端有白名单过滤，会被剔除。

问答卡选项结构载荷（0807-0148 修订）：`rawInput.questions` 每项为
`{question, header?, options: [{label, description?}], multi_select}`
（kimi 实证：多选字段名为 `multi_select` 蛇形；`header` 为可选短标题）。
客户端协议层提取为 `question_options` 载荷字段（与 `questions` 文本
列表并存），供问答卡交互侧渲染选项；多选题经 ACP `request_permission`
降级为单选语义（响应模型只能回一个 optionId）。

入参图片路径载荷（0807-0158 修订）：`rawInput.path`/`rawInput.filePath`
指向图片扩展名（`.png .jpg .jpeg .gif .bmp .webp`）时，客户端协议层
提取为 `media_path` 载荷字段（首帧空壳时随 `in_progress` 帧与入参
回填同频补发），供 MediaReadCard 渲染入参略缩图——**仅供人类查看，
不回传 AI、不改协议语义**；路径原样下传（相对路径不解析），渲染层
按工作区根解析，文件不存在或超 10MB 静默降级。

未识别的 `sessionUpdate` 类型与 `_meta` 厂商扩展会被客户端**静默忽略**
（不崩），但不得依赖此行为传递关键信息（见「变更纪律」）。

### 2.3 usage_update

```json
{"update": {"sessionUpdate": "usage_update",
            "used": 1234, "size": 262144,
            "cost": {"amount": 0.0, "currency": "USD"}}}
```

- `used`（已用 token）与 `size`（上下文上限）**同帧送达**；`cost` 可选。
- **`size` 缺失或为 0 的通知不上屏**（客户端算不出百分比直接丢弃）。
- **没有真实数据就不要发**——不臆造上限，无数据时客户端保持徽章隐藏，
  这是设计好的降级形态，不是缺陷。

### 2.4 轮次收尾

prompt 请求的响应帧收尾：

```json
{"jsonrpc":"2.0","id":4,"result":{"stopReason":"end_turn"}}
```

| stopReason | 语义 |
|:---|:---|
| `end_turn` | 正常完成 |
| `cancelled` | 被 session/cancel 中止 |
| `error` | **轮次失败必须显式标记**（附录 A.3） |

- **轮次失败两条路二选一并保持一致**：① error 帧
  （`{"error":{"code":...,"message":...}}`）；② 正常响应 +
  `stopReason="error"`。**严禁**「正常响应 + `end_turn` + 零 update」——
  客户端会把零输出的成功轮次显示为**空回复零提示**（附录 A.3，实测踩坑）。
- 响应帧可携带扩展字段（如 `transcriptPath`），客户端对未知字段宽容。

---

## 3. 审批回环

执行类工具（写文件/执行命令等）执行**前**，agent 向客户端发反向请求：

```json
{"jsonrpc":"2.0","id":100,"method":"session/request_permission","params":{
  "sessionId": "...",
  "toolCall": {"toolCallId": "...", "title": "创建文件 demo.txt",
               "kind": "edit", "rawInput": {...}},
  "options": [
    {"optionId": "allow_once",   "name": "允许一次",     "kind": "allow_once"},
    {"optionId": "allow_always", "name": "总是允许",     "kind": "allow_always"},
    {"optionId": "reject_once",  "name": "拒绝",         "kind": "reject_once"}
  ]
}}
```

客户端响应：

```json
{"jsonrpc":"2.0","id":100,"result":{
  "outcome": {"outcome": "selected", "optionId": "allow_once"}
}}
```

要点：

- `options` 每项含 `optionId` + `name` + `kind`；`kind` 取值
  `allow_once` / `allow_always` / `reject_once` / `reject_always`。
  **回应用 agent 提供的 `optionId` 原值**，客户端不臆造。
- 客户端按 kind 兜底选择：有审批处理器时交给用户决策；用户取消/超时/
  处理器异常 → 兜底选 `reject_once`；无处理器（非交互场景）→ 自动选
  `allow_once`。agent 未提供任何选项时客户端回 `outcome: cancelled`。
- **被拒绝后 agent 应放弃执行**，并向用户追问或说明（不静默跳过）；
  随后以 `tool_call_update`（status `failed` 或不发）收尾并 `end_turn`。
- 反向请求**必须能被及时应答**：审批阻塞期间 agent 不要占住 stdout 写锁
  做其他长操作（客户端应答在独立线程完成，协议层无额外约束，但 agent
  侧须保证读循环不阻塞）。

---

## 4. 本地部署侧（客户端发现 agent 的约定）

Dream CLI 在本机的发现遵循三级范式（与 Reasonix 等后台一致）：

1. **PATH**：`dream` 在 `$PATH` 中（`shutil.which`）。
2. **`$DREAM_HOME/bin/dream`**：安装根目录环境变量。
3. **`~/.dream/bin/dream`**：默认安装位置。

桌面会话的 PATH 可能缺 `~/.local/bin` 等用户级目录，后两级 fallback 避免
「已安装但检测不到」的误判。客户端在未检测到时会禁用菜单项并标注
「（未检测到）」，不作回退落点。

---

## 5. 错误码表

| code | 场景 | 说明 |
|:---|:---|:---|
| `-32601` | method not found | 未知方法（客户端对 fs/terminal 反向请求也回此码） |
| `-32602` | invalid params | **cwd 非绝对路径**等参数校验失败 |
| `-32603` | internal error | 模型未加载/配置缺失等 agent 内部错误（session/new 未配置信号归此） |
| `-32099` | 进程意外退出 | **客户端注入**的死讯错误（agent 不会发出，仅作记录） |
| `-32000` | （kimi 专有 authRequired） | Dream 不沿用；认证/配置问题统一走 -32603 + 可读 message |

原则：错误 message 写**可读原因与建议动作**（客户端会把 session/new 的
认证/配置类错误映射为引导文案；宁可多映射，不可让用户面对裸协议错误）。

---

## 6. 典型时序

```
client                                agent
  │ ── initialize ───────────────────> │
  │ <──────────── result(agentInfo) ── │
  │ ── session/new {cwd} ────────────> │
  │ <──────────── result(sessionId) ── │
  │ ── set_config_option(model) ─────> │   （用户预选模型时）
  │ <──────────── result ──────────── │
  │ ── session/prompt ───────────────> │
  │ <── session/update (chunk ×N) ──── │   流式正文/思维链
  │ <── session/request_permission ──  │   执行类工具前（可选）
  │ ── result(outcome selected) ─────> │
  │ <── session/update (tool_call…) ── │
  │ <── session/update (usage_update)  │   （有真实数据时）
  │ <──────────── result(end_turn) ── │
  │ ── session/cancel (通知) ────────> │   用户按停止（轮次即停）
  │ <──────────── result(cancelled) ── │
  │ ── session/prompt ───────────────> │   同会话续用
```

---

## 附录 A. 实测教训（四后台踩坑记录，逐条均为已发生事件）

### A.1 authMethods 是静态能力声明（2026-07-30，Reasonix）

初版客户端设计曾以「initialize 响应的 `authMethods` 非空」为「未 setup」
拦截信号——实测推翻：Reasonix 的 `authMethods` 恒为 terminal 型静态声明，
已/未配置响应完全相同。**未配置信号归 session/new 错误**（-32603
`model "..." is not configured`）。Dream 不要在 authMethods 上玩花样。

### A.2 cwd 必须绝对路径（2026-07-30，Reasonix）

`session/new` 对相对路径 cwd 返回 `-32602` 硬拒绝。客户端侧已归一化为
绝对路径，agent 侧仍应硬校验兜底。

### A.3 轮次失败必须显式标记（2026-07-30，Reasonix 实测修正 #5）

Reasonix 轮次失败（模型 provider 配置错误等）曾走「正常响应 + 零 update」，
用户看到空回复零提示。修正后客户端同时识别 error 帧与
`stopReason="error"`。Dream 实现二选一并保持全路径一致。

### A.4 空 text 块被拒（2026-08-01，T0 spike）

Kimi（-32603）与 Reasonix（-32602）均拒绝空 text 块。客户端对纯图发送
已兜底占位文案「请查看附图。」；agent 侧应容忍空 text，不因此崩轮次。

### A.5 错误文案的引导价值（2026-07-30）

认证/配置类裸协议错误对用户无意义。客户端会把 session/new 错误消息中含
auth / not configured / 模型未加载 等关键词的错误映射为「请先配置…」
引导文案。**message 写清楚缺什么、怎么办**，宁可多映射不可漏映射。

### A.6 日志污染协议流（惯例）

stdout 混入任何非协议输出（横幅、进度条、调试打印）即污染协议流。
客户端会丢弃非 JSON 行并记 stderr 诊断，但流式 chunk 若被截断则表现为
正文丢失。**日志一律 stderr**。

---

## 变更记录

| 版本 | 日期 | 变更 |
|:---|:---|:---|
| v1.0 | 2026-08-03 | 初版：§1-§6 全条目 + 附录 A 六条实测教训（源自 Zen Studio 四后台接入实证） |
