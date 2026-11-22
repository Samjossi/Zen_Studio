# Kimi kap-server 链路增量接入落地计划

> **状态**：草稿（待审阅）
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
