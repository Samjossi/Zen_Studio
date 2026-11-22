# Kimi kap-server 链路增量接入落地计划

> **状态**：已实施（2026-11-23 00:01，工作树未提交）
> **范围**：`src/ide/llm/providers/kimi/`（新增 kap 子包）、`src/ide/gui/panels/chat/vendors/kimi/`（kap presenter）、`src/ide/llm/registry.py`、`src/ide/gui/panels/chat/vendors/registry.py`、`tests/`（对应新增用例）；不触碰 `src/ide/llm/providers/acp/` 公共传输层与 DSH、Local 链路
> **时间**：2026-11-22 23:08 (UTC+8)（设计）
> **优先级**：中
> **依据**：用户指示（2026-11-22 会话：以增量方式新增 kap-server 链路，不替换 ACP，先写计划待审阅）；用户整理 `work options/Kimi Code 四条集成路线对比.md`（KAP 定为首选 spike 路线）；`维护手册/后端CLI工具维护说明手册.md` 第 1、4 节；官方《服务 API》参考 https://www.kimi.com/code/docs/kimi-code-cli/reference/server-api.html

---

## 1. 背景与目标

Kimi 上游 ACP 链路存在轮次契约缺陷（2026-11-21 裸 ACP 复现：通知 steering 后提前 `end_turn`、追问空轮次，详见 `2026-1122-1250_KimiACP全裸基线通知轮交错提前end_turn复现审计报告.md`），官方在 API 与 TUI 链路已修复、ACP 链路未修复。

kap-server 即 `kimi web` 启动的本地服务，Kimi 官方 Desktop/Web UI 走的就是它，由 agent-core-v2 引擎直供（与已修复的 TUI/API 同引擎路径），不经过 ACP 适配层。因此增量接入 kap 链路有望**绕开上游 ACP 适配层缺陷**，而非在客户端继续止血。

目标：

1. 在 Kimi 厂商目录下并列新增 kap 链路（REST + WebSocket），与现有 ACP 链路并存，用户可选。
2. DSH、Local 及其他 ACP 后台零改动；`providers/acp/` 公共层零改动。
3. 以硬指标验证目标缺陷在 kap 链路是否消失，再决定扩大投入或止步于原型。

非目标：不替换、不废弃 Kimi ACP 链路；不做跨厂商协议抽象（kap 私有字段只留在 kimi 目录内）。

### 前置条件（硬性，用户手动完成）

KAP 1.0.0 硬性要求 **Kimi Code CLI ≥ v2.7.0、Desktop ≥ v1.4.0**。后端升级由用户手动执行（`kimi upgrade -y`），不属于本计划实施范围。T1 启动前先以 `kimi --version` 确认本机 CLI ≥ 2.7.0，未达标则暂停等待用户升级。

## 2. 方案拆解 / 结构映射

官方接口要点（以运行时 `GET /openapi.json`、`GET /asyncapi.json` 为最终契约）：

- 服务：`kimi web` 启动，默认 `http://127.0.0.1:58627`，端口占用自动后移；运行实例登记在 `~/.kimi-code/server/instances/`。
- 鉴权：bearer token；REST 用 `Authorization` 头，WebSocket 用子协议 `kimi-code.bearer.<token>`。
- REST：`POST /api/v1/sessions` 建会话（`metadata.cwd`）、提交 prompt、会话恢复/历史回读等；统一信封 `{code, msg, data, request_id}`，业务结果看 `code`。
- WebSocket：`/api/v1/ws` 订阅会话事件，`turns` 全量帧 + `item.started/appended/completed/delta/removed` 增量事件 + `notice`；事件可重复，按替换语义应用。
- 状态冲突：`40901` 会话忙、`40931` 会话被其他进程占用（`:takeover` 接管）、`40932` 已被其他客户端更新。

新增代码映射（全部位于 Kimi 厂商边界内）：

| 新增/修改 | 位置 | 职责 |
|:---|:---|:---|
| 新增 `kap/` 子包 | `src/ide/llm/providers/kimi/kap/` | HTTP 客户端、WS 客户端、服务进程生命周期（启动/发现/健康检查）、token 获取 |
| 新增事件映射 | 同子包内 events 模块 | `turns`/`item.*` → 本厂商更新模型；解析失败降级，不影响主流程 |
| 修改注册声明 | `src/ide/llm/providers/kimi/spec.py` | Kimi 下并列 kap 入口（或独立 BackendSpec，由 registry 组合） |
| 修改组合根 | `src/ide/llm/registry.py` | 仅追加组合，不写厂商静态数据 |
| 新增 kap presenter | `src/ide/gui/panels/chat/vendors/kimi/` | kap 事件 → 卡片；能映射现有卡片则复用本厂商卡片，映射不了降级纯文本 |
| 新增测试 | `tests/` 对应文件 | 传输/事件映射/降级用例；架构守卫同步覆盖新目录 |

## 3. 现状锚点

- `src/ide/llm/providers/kimi/`：`provider.py`、`spec.py`、`updates.py`、`usage_poll.py`、`models.py`、`catalog.py`、`permissions.py`——现有 ACP 链路全部保留。
- `src/ide/llm/providers/acp/`：公共 ACP 传输，本计划只读不改。
- `src/ide/gui/panels/chat/vendors/kimi/`：`factory.py`、`cards.py`、`presenter.py`。
- 扩展流程与隔离约束：`维护手册/后端CLI工具维护说明手册.md` §4.2–4.4；架构守卫 `tests/test_provider_boundaries.py`。
- 现有 wire 只读取径（`usage_poll.py`）是 kap 事件的参照系， kap 用量事件若覆盖 `token_counting`/turn 级 usage，可替代该私有读取；不覆盖则 ACP 链路照旧、kap 链路按官方事件实现。

## 4. 设计决策

1. **增量并存**：kap 与 ACP 在 Kimi 厂商下二选一（设置项），默认仍 ACP；不引入第二条自动停止/恢复通道掩盖问题。
2. **契约以在线 schema 为准**：客户端启动时抓 `/openapi.json`/`/asyncapi.json` 记录版本指纹；事件解析一律容错降级，缺失字段不伪造数据。
3. **原型先行、硬指标闸门**：T1 原型未证明目标缺陷消失，则 T2–T4 不启动。
4. **传输不复用 AcpConnection**：kap 是 REST/WS，新写传输，禁止塞进 `providers/acp/`。
5. **进程模型差异显式化**：kap 为常驻服务多会话（发现/接管/健康检查），与 ACP 每连接一进程不同，生命周期代码独立。
6. **隐私纪律沿用**：日志只记事件类型、脱敏数值、code、request_id；不落 prompt 正文、完整工具载荷、token。

## 5. 实施步骤

### T1 最小驱动原型（验证闸门，先行）

- 在 `.temp/` 下写一次性验证脚本（项目 `.venv` Python）：以 `kimi web --port 0 --json` 启动服务（stdout 首行返回 origin/ws_url/token；另取运行时 `GET /api/v1/kap.md` 规范存档）→ `POST /api/v1/sessions` 建会话 → WS 订阅 → 提交 prompt → 打印事件序列（仅类型与脱敏元数据）；用完 SIGTERM 回收服务进程。
- 复现审计报告中的通知 steering 场景，观察 kap 链路轮次收尾是否正常、是否出现提前结束/空轮次。
- 产出：事件类型清单、与 ACP 事件的对照、目标缺陷是否复现的明确结论，回写本文档"验证结果"。
- 闸门：缺陷仍复现 → 停止后续任务，计划标记放弃并记录结论；缺陷消失 → 进入 T2。

#### T1 验证结果（2026-11-22，闸门通过）

**环境**：Kimi Code CLI `2.7.2`（`~/.kimi-code/bin/kimi`），KAP `1.0.0`，server `2.7.2`；模型 `kimi-code/kimi-for-coding`；隔离 `KIMI_CODE_HOME`/`XDG_CACHE_HOME` 于 `.temp/kap-t1/`，未写入真实会话目录。

**运行时契约指纹**（`.temp/kap-t1/`）：

| 文件 | 大小 | SHA-256 |
|:---|--:|:---|
| `openapi.json` | 332172 | `bab53d5068a41b651f8cc92f3276c6d72a89618751ca83cb7ccbf4661d301f56` |
| `asyncapi.json` | 63481 | `dba0b2ea82cb88bec40b4a50415492ccb20213168837f58f35fbddaaf31ee44f` |
| `kap.md` | 16077 | `fa8d4a64d06da08de7b09c520d4e5a787d4d3548c45762b68859a6bfad2ea015` |
| `meta.json` | 1407 | `47d12a547f12e5ffd240ee345e6cd6d8a505c23694ec0838d5f4bf40984e1040` |

**A0 短轮**：1/1 正常，2.5s，`assistant_message="OK"`，验证 WS 订阅、prompt、turns 读取链路可用。

**C2 通知轮交错**（首轮后台 `sleep 25; echo bg-done > c2_bg_result.txt`，通知轮开始的瞬间提交追问）：

| run | 触发方式 | 通知轮起点→追问提交 | `POST /prompts` 状态 | 通知轮时长 | 追问轮时长 | 追问轮正文 | marker |
|:---|:---|--:|:---|--:|--:|:---|:---|
| `20261122-234252-c2` | `turn.started(ordinal=1)` | 27ms | `queued` | 5035ms | 6985ms | `C2-OK` | `bg-done` |
| `20261122-234346-c2` | 同上 | 26ms | `queued` | 4446ms | 4673ms | `C2-OK` | `bg-done` |
| `20261122-234427-c2` | 同上 | 30ms | `queued` | 2630ms | 4652ms | `C2-OK` | `bg-done` |

3/3 的轮次结构均为：`turn 0`（用户首轮，含 Bash 后台调用）→ `turn 1`（`kind=agent`，首个 item 为 `notification`，`notification_type=task.completed`）→ 通知轮结束后 `turn 2` 才出现 `user_message(prompt_ids=["c2-2"])` 并正常执行。**没有出现** ACP 基线中的「追问到达即空 `end_turn`」「通知轮不可见」「end_turn 后隐藏执行」签名；通知轮本身在 WS 流中可见，追问被服务端排队而不是被提前结算。

**事件对照**：KAP 侧观测到 `turn.started/ended`、`item.started/ended/delta/updated`、`notification`、`context`、`todo`、`interaction`（本轮未触发审批/提问）、`task.*`、`session/agent/goal` 等帧；ACP 侧的 `session/prompt` 响应边界在 KAP 中对应 `turn.ended`，通知在 ACP 无同构可见物，KAP 以真实 `agent` turn + `notification` item 暴露。

**工具链修正（非 KAP 缺陷）**：首版探针只回 WS 控制帧 pong，未回应用层 JSON `{"type":"ping"}`，订阅在约 2 分钟后静默失效（`20261122-233548-c2`，该轮模型还跑偏到无关配置话题，不作为闸门样本）。补上 JSON pong 后 3/3 稳定；T2 必须实现应用层心跳，不能依赖 WS 控制帧。

**证据路径**：探针 `.temp/kap-t1/kap_probe.py`；干净回放 `runs/20261122-234252-c2/`、`runs/20261122-234346-c2/`、`runs/20261122-234427-c2/`（`events.jsonl`、`turns.json`、`turns-summary.json`）；契约存档同目录上一级。`.temp/` 已 gitignore，token 未落文档。

### T2 kap provider 集成

- 实现 `kap/` 子包：服务生命周期、REST/WS 客户端、事件映射、错误码处理（`40901/40931/40932`）、会话恢复。
- `spec.py` 注册入口 + `registry.py` 组合；模型目录沿用本厂商 `catalog.py`，不另建私有源。
- 单元测试：信封解析、事件映射、降级路径、占用/接管分支。

### T3 GUI 接入

- kap presenter 与工厂注册，卡片映射覆盖文本增量、工具调用、usage、notice；不可映射事件降级纯文本卡片。
- 设置项：Kimi 链路选择（ACP/kap），默认 ACP。
- 架构守卫用例同步更新（新增目录的隔离规则与受控反例）。

### T4 端到端验收

- `bash scripts/check.sh` 全绿（隐私扫描 + 守卫 + ruff + pytest）。
- 真实会话验收：正常轮、后台通知轮、追问、手动停止、会话恢复、长任务；对照 ACP 链路确认目标缺陷消失且无新增假完成/残留提示。
- 验收数据（版本、事件序列元数据、结论）回写本文档；原型脚本与验证输出保留在 `.temp/` 并注明路径。

### T5 文档同步

- `维护手册/后端CLI工具维护说明手册.md` 第 1 节补充 kap 链路的启动、验证、诊断入口（实验性声明与降级行为），不改动 DSH/Local 章节。

### T终 实施回写（固定收尾，不可省略）

- 将「与计划的偏差 / 验证结果 / 经验沉淀」三部分回写到本文档末尾，新增「实施结果回写」一节。
- 本文档状态由「草稿」更新为「已实施」。
- 子计划不新建 `文档/修改记录/` 条目；主计划最终归档时新建 1 份《总实施记录》，汇总全部子项改动与验证，并与本文档互相引用。
- 回写要求以 `work plans/README.md`《实施回写规范》为准；即使本计划其他步骤与此冲突，回写不可省略。

## 6. 风险与注意点

- ⚠️ 官方标注该 REST/WS API 为**实验性**：端点、字段、事件类型可能随版本变更；升级 CLI 后须以在线 schema 复核，解析失败必须降级而非报错阻断聊天。
- ⚠️ 目标缺陷"在 kap 链路消失"是假设而非已证实事实，T1 闸门就是为此而设；若复现，止损成本限于原型。
- ⚠️ 常驻服务模型带来新故障面：端口发现失败、token 轮换、多实例并存、会话被其他客户端占用（`40931`）；不得用强制接管掩盖真实占用。
- 🔴 不得为 kap 链路修改 `providers/acp/`、DSH、Local 任何代码；不得跨厂商共享 kap 私有字段。
- 🔴 token 属于凭证：不进日志、不进文档、不进测试夹具。

## 7. 已确认决策

- 用户拍板（2026-11-22）：增量接入，不替换 ACP；DSH 及其他后台继续走 ACP；先写计划文档，审阅通过后才开工。
- 用户拍板（2026-11-22）：KAP 1.0.0 版本门槛（CLI ≥ v2.7.0、Desktop ≥ v1.4.0）为硬性要求，后端升级由用户手动完成，本计划不承担升级操作。
- 官方文档确认 kap-server 为 Desktop/Web UI 同款服务接口，属文档化公开面（实验性），非私有逆向。

---

## 8. 实施结果回写（2026-11-23 00:01 UTC+8）

T1–T5 全部完成。以下为相对原计划的偏差、验证结果与经验。

### 8.1 与计划的偏差

1. **未新建 kap presenter**：KAP 的工具事件先适配成 ACP `session/update` 形状，再交给同厂商 `KimiUpdateMapper`；GUI 侧沿用 `vendor="kimi"` 的 `KimiCardPresenter` 与 Kimi 卡片工厂。计划允许「能映射现有卡片则复用」，实测 Bash/Edit/Read/搜索类工具均能复用；未知工具落 `McpCard` 通用卡。未改 `vendors/kimi/factory.py` 或卡片包。
2. **`kimi-kap` 声明写在现有 `kimi/spec.py`**，没有新建独立 spec 文件；`registry.py` 注册序为 `kimi-acp`、`kimi-kap`、`dsh-acp`、`local-acp`，`DEFAULT_BACKEND` 仍为 `kimi-acp`。KAP 的 `available` 额外校验 CLI ≥ 2.7.0；`--version` 解析失败 fail-open，低于门槛 fail-closed。
3. **新增常驻事件 pump（计划未列）**：WS reader 只做帧解析与心跳，pump 线程是唯一消费者。轮内把事件转给 `chat()`，空闲期继续接收 `context` 用量并应答待答交互。原因：`context` 终值实测晚于 `turn.ended` 约 250ms，且通知轮活跃时用户可能不在聊天轮；直接由 `chat()` 消费会丢终值、也无法处理空闲期审批。配套 `post_turn_usage_grace_s=1.0`。
4. **会话恢复保留 session id 并走 `:reload`**：私有服务重启后 origin 变化不再清空 session id；`GET` 返回 40401 时 `POST /sessions/{id}:reload`，失败才新建。真实冒烟验证了同一 session 在服务重启后继续可用。
5. **审批语义映射**：KAP 会话固定 `permission_mode=manual`，由 GUI 既有权限四态决定放行/弹窗；`allow_always` 映射为 `decision=approved, scope=session`，`allow_once` 映射为 `approved`，`None/拒绝` 映射为 `rejected`。提问交互把每个 question 单独交给 GUI，回传 `answers[qid] = {kind: single, option_id}`；无处理器时 `dismissed=true`，不阻塞会话。
6. **不自动 `:takeover`**：`40931` 只转成可执行中文提示，遵守计划「不得用强制接管掩盖真实占用」。`40901/40932/401/403/40401` 均有独立文案。
7. **图片走 KAP `image` content 的 `path` source**，未走 `/api/v1/files` 上传；KAP 与 IDE 同机同用户，路径直读最简，且不复制附件字节。
8. **T1 首轮探针的 WS 断订阅是工具缺陷，不是 KAP 缺陷**：KAP 心跳为应用层 JSON `{"type":"ping"}`，首版只回 WS 控制帧 pong，约 2 分钟后静默失效；补 JSON pong 后 3/3 稳定。该轮模型还跑偏到无关配置话题，未计入闸门样本。
9. **T5 除维护手册外同步更新了 `src/ide/llm/README.md`**：补 KAP 文件职责、`KimiKapLLM` 契约、用量通道与 `post_turn_usage_grace_s` 说明；未改 DSH/Local 章节。
10. **测试快照更新**：`tests/test_llm_spec_cache.py` 注册序/标签快照加入 `kimi-kap`；`tests/test_provider_boundaries.py` 把 `ide.llm.providers.kimi.kap` 加入厂商私有模块，并新增「通用 GUI 不得直接 import KAP provider」受控反例。

### 8.2 验证结果

- **门禁**：`bash scripts/check.sh`（`UV_CACHE_DIR`/`TMPDIR` 指向 `./.temp/`，规避 `~/.cache/uv` 只读）→ 隐私扫描通过、QLabel 守卫通过、ruff 通过、`373 passed`。
- **KAP 单测**：4 个文件共 `30` 项（事件映射/传输发现与版本门槛/provider 轮次绑定与审批/GUI 卡片路由）全绿。
- **全量测试**：`.venv/bin/python -m pytest tests -q` → `373 passed`（KAP 新增 30 项；另有注册表/守卫快照修订）。
- **真实链路验收**（隔离 `KIMI_CODE_HOME`/`XDG_CACHE_HOME`，Kimi CLI 2.7.2，未写真实会话）：
  - 普通轮：`KimiKapLLM.chat("只回复 OK")` → 正文 `OK`；usage 终值 `13383/1048576`，轮末 consumption 正常。
  - 工具轮：Bash 审批 → 卡片 `tool_call`/`tool_call_update`（`command`、`status=in_progress/completed`、输出）→ marker `tool-ok` 落盘。
  - 通知交错：首轮后台 `sleep 25` → 通知轮 `ordinal=1`（`notification` item）；24s 后追问返回 `status=queued`；通知轮 `ended` 被忽略；`ordinal=2` 用户轮正常执行，正文 `C2-OK`，marker `bg-done`。
  - 长任务：60s 前台 Bash，66.5s 正常收尾，正文 `LONG-DONE`，marker `long-done` 落盘；无提前 `end_turn`。
  - 停止：长任务中 `cancel()` → `TurnCancelled`，0.03s 返回；session `activity=idle`、`last_turn_outcome=interrupted`。
  - 恢复：SIGKILL 私有服务后下一轮自动重连并 `:reload`，同一 `session_id` 继续，正文 `TWO`。
- **证据路径**：T1 契约与回放在 `.temp/kap-t1/`；provider 冒烟 `.temp/kap-t1/provider_smoke.py`、`provider_tool_smoke.py`、`provider_c2_smoke.py`、`provider_lifecycle_smoke.py` 及其 `.out/.err`。`.temp/` 已 gitignore，未写入 token/prompt 正文。

### 8.3 经验沉淀

1. **KAP 的事件顺序不能按 ACP 直觉假设**：通知轮的 `turn.started` 可能在聊天空闲期被消费/丢弃，随后只看到它的 `turn.ended`。绑定用户轮必须严格用 `user_message_id` 或 `prompt_ids` 命中 prompt；**绝不能**把「没有 `user_message` 的 ended」当本轮收尾，否则会把后台通知正文当用户结果并提前返回。
2. **事件可重复，旧轮 started 会迟到**：用 `turn.ordinal` 单调过滤；同一连接上重复 `turn.started` 不能重置当前绑定。
3. **`context` 终值晚于 `turn.ended`**：WS 消费者必须常驻到轮后（pump + 宽限），不能把 `chat()` 返回当作事件消费终点；否则徽章永远停在轮首的十几 token。
4. **应用层心跳优先于控制帧**：KAP 的 JSON ping/pong 才是订阅保活契约；控制帧 pong 只算协议礼貌。任何新 WS 客户端先按 `asyncapi.json` 的 `ping`/`pong` message 实现，不要只看 RFC6455。
5. **服务进程 SIGTERM 可能不退出**：`kimi web` 在存在活动轮/后台任务时 10s 内可能不响应 TERM；`KapServer.close()` 必须 TERM→wait→KILL 兜底，冒烟脚本也要按此写。
6. **`--json` 的 token 只存在内存**：私有服务发现走 `$KIMI_CODE_HOME/server.token`；`--json` 启动的实例不写该文件，所以发现链只能复用 Desktop/普通 `kimi web`，私有实例由本进程持有。不要把 token 写日志或文档。
7. **工具载荷复用比新建一套便宜**：KAP item 的 `name`/`input`/`display`/`output` 与 Kimi ACP `rawInput`/`rawOutput` 形状接近，适配层集中在一个 `_acp_tool_*` 函数即可复用全部 Kimi 卡片与 diff/摘要逻辑；只有未知工具才降级通用卡。
8. **实验性契约要留指纹**：`openapi.json`/`asyncapi.json`/`kap.md` 的版本与 SHA-256 存档在 `.temp/`，CLI 升级后先 diff 再改解析；不要把在线 schema 当永久稳定 API。
