# 后端CLI工具维护说明手册

> 本文档只保留团队常用后端 CLI 的安装、运行、更新与验证命令。
> 升级后需重启 Zen Studio，再验证版本与实际会话。
> 最后更新：2026-11-23

---

## 1. Kimi Code（原 Kimi CLI）

Kimi 官方 AI 编程代理，Zen Studio 仅通过 `kimi web` 提供的 KAP 1.0.0（REST + WebSocket）接入。`kimi-kap` 是 Kimi 厂商唯一且出厂默认的接口，不提供 ACP 回退；DSH、Local 仍分别使用各自 ACP 链路。

| 资源 | 地址 |
| --- | --- |
| 主文档站 | https://www.kimi.com/code/docs/ |
| CLI 快速开始 | https://www.kimi.com/code/docs/kimi-code-cli/guides/getting-started.html |
| GitHub 仓库 | https://github.com/MoonshotAI/kimi-code |

### 安装

```bash
curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash
```

### 运行

交互式：

```bash
kimi
```

KAP 服务（Zen Studio 使用；无现成实例时会自动拉起，无需手动执行）：

```bash
kimi web --port 0 --json --no-open
```

### 更新

```bash
kimi upgrade -y
```

### 验证

```bash
kimi --version
```

KAP 真实验收环境输出 `2.7.2`。

### KAP 链路（Kimi 唯一接口）

Kimi Code 的本地 `kimi web` 服务提供 KAP 1.0.0（REST + WebSocket）。Zen Studio 只注册 **Kimi KAP**，并以其作为出厂默认后台；KAP 不可用时会明确显示不可用，不启动 `kimi acp`，也不静默切换其他厂商。

**前置门槛**：CLI ≥ `2.7.0`、Desktop ≥ `1.4.0`。CLI 缺失或低于门槛时 `kimi-kap` 入口显示「未检测到」并禁用，不会回退 ACP。

**加载与缓存**：可用性仍执行真实 `kimi --version` 版本检查，并未退化为仅查二进制。工作区首个 `ChatPanel` 只需一次版本探测和一次 `kimi provider list --json` 目录拉取；同进程的多标签、重复模型控件与设置中心复用结果。缓存按接口隔离且无 TTL，运行中安装/升级 CLI 后须显式刷新 `registry.refresh_models("kimi-kap")` 或重启 Zen Studio；定向失效会同时清除该接口的可用性、模型、强度与原始目录载荷。

```bash
kimi --version
# 手动起一个临时服务（Zen 也会在无现成实例时自动拉起私有服务）
kimi web --port 0 --json --no-open
# stdout 首行:
# {"origin":"http://127.0.0.1:<port>","ws_url":"ws://.../api/v1/ws","token":"...","kimi_agent_protocol_version":"1.0.0"}
```

**服务发现顺序**：优先复用已运行的 Desktop / `kimi web` 实例（读 `$KIMI_CODE_HOME/server/instances/*.json` 与 `server.token`，desktop 优先、其余按 `started_at` 新→旧，逐个 `/api/v1/meta` 鉴权探活）；没有可用实例才拉起私有 `kimi web --port 0 --json`，私有进程在标签关闭时回收，复用的用户服务不杀。

**验证入口**：

```bash
# 需要 token；不要把 token 写进日志/文档/截图
curl -sf -H "Authorization: Bearer $TOKEN" "$ORIGIN/api/v1/meta"
curl -sf -H "Authorization: Bearer $TOKEN" "$ORIGIN/api/v1/kap.md" | head
```

- 正常轮：WS 应依次出现 `turn.started` → `user_message`/`reasoning`/`tool_call`/`assistant_message` item → `turn.ended(outcome=completed)`。
- 后台通知轮：显式出现 `turn.ended` 之前的真实 `agent` turn，首个 item 为 `notification`（`notification_type=task.completed`）；通知轮活跃期提交的追问由服务端返回 `status=queued`，通知轮结束后才生成新的用户轮，**不应**出现「追问立即空 `end_turn`」或「end_turn 后隐藏执行」。
- 用量：`context` 事件携带 `context_tokens`/`max_context_tokens`，轮末终值可能晚于 `turn.ended` 约 250ms；KAP 能力声明 `post_turn_usage_grace_s=1.0`，GUI 在轮后宽限内继续 `poll_usage()`，不要为抢终值在 WS reader 线程阻塞。
- 停止：`POST /api/v1/sessions/{id}:cancel`（`include_background=false`），轮次以 `interrupted` 收尾；不自动 `:takeover`，`40931` 会话占用需用户在原客户端处理。

**诊断与降级**：

- 日志检索 `[kimi kap]`；只记录事件类型、`code`、`request_id`、会话/轮次 id 与数值，不记录 token、prompt 正文、完整工具载荷或 diff。
- 错误码：`40901` 会话忙、`40931` 被其他进程占用、`40932` 已被其他客户端更新、`401/403` 凭证失效（需 `kimi login`）、`40401` 会话不存在（下一轮自动 `:reload` 或新建）。
- WS 心跳是**应用层 JSON** `{"type":"ping"}` → `{"type":"pong"}`，不是 WS 控制帧；不回会被服务端断开订阅。`KapWebSocket` 内部已处理，排障时不要把它当普通控制帧。
- 契约以运行时 `GET /openapi.json`、`GET /asyncapi.json` 与 `GET /api/v1/kap.md` 为准；CLI 升级后先复核 schema，未知事件类型忽略、解析失败降级，不阻断聊天。
- 工具事件按 Kimi 现有卡片载荷适配；映射不了的未知工具降级为通用卡片，不伪造字段。图片走 KAP `image` content 的本地 `path` source。

### 旧配置与会话存档边界

- 读取设置时，旧 `kimi-acp` 后台 ID、模型记忆和强度记忆会归一到 `kimi-kap`；已有 KAP 记忆优先，读取本身不写回用户文件。
- 旧 ACP 会话存档只回放历史文字，下一次发送建立新的 KAP 会话；不得把 ACP session id 当作 KAP 会话续接，也不得宣称恢复了原 ACP 服务端上下文。

---

## 2. DeepSeek Harness（DSH）

DeepSeek 官方开源的 Agent Harness，CLI 命令为 `dsh`；Zen Studio 通过 `dsh acp` 接入。

| 资源 | 地址 |
| --- | --- |
| GitHub 仓库 | https://github.com/deepseek-ai/deepseek-harness |
| 官方文档站 | https://deepseek-harness.github.io/deepseek-harness/ |
| CLI 说明 | https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/README.zh.md |
| 模型配置指南 | https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/providers.zh.md |

### 安装

```bash
npm i -g @deepseek-ai/dsh@0.2.2-rc.5
```

### 运行

Web UI：

```bash
dsh web
```

ACP（Zen Studio 使用）：

```bash
dsh acp
```

### 更新

```bash
npm i -g @deepseek-ai/dsh@latest
```

### 验证

```bash
dsh --version
```

当前应输出 `0.2.2-rc.5`；Node.js 要求 `>=24.9.0`。

---

## 3. Local CLI

Local CLI 是独立仓库维护的本地模型 agent，Zen Studio 通过 `local-cli acp` 接入。模型目录由服务端扫描 `~/.local-cli/config.toml` 中 `model_dir` 下的 `*.gguf` 生成；协议参考保留在本仓库 `dream-acp/protocol/dream-acp-v1.md`，该目录不再是 Dream 后台注册入口。

### 安装

本仓库未记录可核实的 Local CLI 现行安装命令，待独立仓库资料补充；不要套用 Dream 示例 agent 作为安装方式。安装后可放在 `PATH`、`$LOCAL_HOME/bin/local-cli` 或 `~/.local-cli/bin/local-cli`。

### 运行

模型枚举：

```bash
local-cli models
```

ACP（Zen Studio 使用）：

```bash
local-cli acp
```

### 更新

本仓库未记录可核实的 Local CLI 现行更新命令，待独立仓库资料补充。

### 验证

```bash
local-cli models
```

正常输出为每行一个 GGUF 模型别名；空输出、非零退出或二进制缺失时，IDE 模型列表回退为空，不伪造可用模型。未安装时界面标注「未检测到」，这与后台封存无关。完整对话验证还需在 `~/.local-cli/config.toml` 配置有效 `model_dir` 并准备 GGUF 模型。

---

## 4. 源码目录、架构守卫与扩展流程

本节记录现行依赖边界；CLI 安装、更新和真机验证仍以第 1–3 节为准。

### 4.1 目录职责

| 路径 | 职责 |
|:---|:---|
| `src/ide/llm/__init__.py` | 公共出口：稳定契约、ACP 公共类型、注册表 API；不再导出具体 provider 类或探测函数 |
| `src/ide/llm/spec.py` | 中性注册类型：`BackendSpec` 与 `resolve_efforts()`；不含厂商名、私有路径、工具名或设置键 |
| `src/ide/llm/cache.py` | 进程级四路接口缓存（可用性/模型/强度/原始载荷）；按接口名隔离，`clear()` 是唯一失效实现 |
| `src/ide/llm/registry.py` | 后台组合根：组合三家厂商 `spec.py` 为 `REGISTRY`，保留 `DEFAULT_BACKEND`、查询 API 与 `refresh_models()` 委托；唯一允许 import 具体厂商后台的模块 |
| `src/ide/llm/providers/acp/` | 稳定 ACP 传输（连接/诊断/terminal 分发）、协议定型、prompt 与标准 content 纯函数；不含厂商工具名、私有配置键或落盘路径 |
| `src/ide/llm/providers/<vendor>/` | 各厂商 `spec.py`（注册声明）、provider、models、updates 与按需划分的协议子包；厂商之间禁止横向 import。Kimi 运行时位于 `kimi/kap/`，DSH/Local 保留各自 ACP provider |
| `src/ide/gui/panels/chat/card_base.py` | 无厂商语义的卡片生命周期、折叠/复制/图片等 UI 基础件 |
| `src/ide/gui/panels/chat/vendors/registry.py` | 卡片组合根：唯一允许 import 具体厂商卡片包的模块 |
| `src/ide/gui/panels/chat/vendors/<vendor>/` | 各厂商 `factory.py`/`cards.py`/`presenter.py`；互不借用，最多 import 本厂商 `models.py` 纯类型 |
| `src/ide/gui/panels/chat/panel.py` | 只选择当前厂商工厂、管理卡片生命周期和转发不透明事件 |

### 4.2 新增后台 provider

1. 在 `src/ide/llm/providers/<vendor>/` 建厂商包，`__init__.py` 只公开本厂商入口；ACP 系后端复用 `providers/acp/` 的 `AcpConnection`，provider 自行组装完整启动 argv。
2. 在同目录 `spec.py` 声明 `SPEC = BackendSpec(...)`：显示名/厂商标签、可用性、模型/强度/显示名工厂、静态能力、默认强度、可选 `dev_note` 与 `vendor_settings`；所有探测/枚举/工厂必须惰性，import 无副作用。需要跨调用缓存时只经 `ide/llm/cache.py`，键用接口实现名。
3. 在 `src/ide/llm/registry.py` 的组合元组追加本厂商 `SPEC`；`registry.py` 只做组合与查询，不再写厂商静态数据或缓存闭包。
4. 不要修改 `src/ide/llm/__init__.py` 追加具体类；GUI 通过注册表工厂消费，无需新增厂商分支。
5. 厂商 `spec.py` 只允许 import 中性 `ide.llm.spec`/`ide.llm.cache`、`ide.llm.base` 与本厂商模块；禁止 import registry、GUI 或其他厂商。
6. 厂商私有工具名、载荷字段、配置键、错误关键词和落盘路径只写入本厂商目录。

### 4.3 新增厂商卡片包

1. 在 `src/ide/gui/panels/chat/vendors/<vendor>/` 建 `factory.py`、`cards.py`、`presenter.py`、`__init__.py`；工厂只理解本厂商工具名与载荷，presenter 只维护本厂商账本。
2. 在 `src/ide/gui/panels/chat/vendors/registry.py` 注册工厂、presenter 与卡片模块；通用面板不得直接 import 厂商卡片包。
3. 具体卡片可先从现有厂商等价复制，复制后独立维护；禁止跨厂商共享具体卡片、公共 `card_kind` 或万能卡片基类。

### 4.4 架构守卫

- `tests/test_provider_boundaries.py` 用标准库 AST 扫描 `src/ide/**/*.py` 与 `main.py`，覆盖：厂商后台/卡片横向隔离、两个组合根例外、provider 不反向 import registry/GUI、公共层不含厂商工具分派表/私有载荷/configId/落盘路径、厂商 `spec.py` 只依赖中性 spec/cache 与本厂商模块、旧平铺模块路径。
- 守卫内置受控反例，反例只在测试内存构造；新增规则时必须同时提供「会失败」的反例，避免全绿只是没扫到目标路径。
- 提交前统一运行 `bash scripts/check.sh`（隐私扫描 + QLabel 守卫 + ruff + pytest）；架构守卫失败时不得通过删除测试或放宽断言绕过。
