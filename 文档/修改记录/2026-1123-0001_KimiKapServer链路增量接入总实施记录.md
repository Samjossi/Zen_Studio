# Kimi KAP（kap-server）链路增量接入总实施记录

> **状态**：已实施（2026-11-23 00:01，工作树未提交）
> **范围**：`src/ide/llm/providers/kimi/kap/` 新增、`src/ide/llm/providers/kimi/{spec,catalog,__init__}.py`、`src/ide/llm/registry.py`、`tests/` 与两份文档；不改公共 `src/ide/llm/providers/acp/`、DSH、Local，不修改本机 Kimi CLI 与用户配置
> **时间**：2026-11-23 00:01（实施，UTC+8）；设计见 `work plans/2026-1122-2308_KimiKapServer链路增量接入落地计划.md`（下称「计划」），偏差与经验见该文档 §8
> **验证环境**：Kimi Code CLI 2.7.2、KAP 1.0.0、server 2.7.2；隔离 `KIMI_CODE_HOME`/`XDG_CACHE_HOME` 于 `.temp/kap-t1/`

---

## 1. 改动清单

| 文件 | 改动 |
|:---|:---|
| `src/ide/llm/providers/kimi/kap/transport.py` | 新增：KAP REST 信封与错误码、WS 客户端（RFC6455 + 应用层 JSON ping/pong）、服务实例发现（`server/instances/*.json` + `server.token`，desktop 优先）、私有 `kimi web --port 0 --json` 启动与 TERM→KILL 回收 |
| `src/ide/llm/providers/kimi/kap/events.py` | 新增：KAP `item.*` → ACP `session/update` 形状适配，复用 `KimiUpdateMapper` 产出 `ToolCallPayload`/`ToolUpdatePayload`；文本 delta/补尾、tool 快照、todo、context 用量、turn consumption |
| `src/ide/llm/providers/kimi/kap/provider.py` | 新增：`KimiKapLLM`。常驻服务多会话、prompt→`user_message` 精确绑定、通知轮隔离、排队/取消、审批/提问交互、`context`/`turn.usage` 用量、`turn.ended` 终态、`:reload` 恢复；独立 pump 线程消费 WS，空闲期更新用量并应答交互 |
| `src/ide/llm/providers/kimi/kap/__init__.py` | 新增包出口：`KimiKapLLM`、`KapError` |
| `src/ide/llm/providers/kimi/catalog.py` | 新增 `kimi_kap_available()`：CLI ≥ 2.7.0 门槛；解析失败 fail-open，低于门槛 fail-closed |
| `src/ide/llm/providers/kimi/spec.py` | 新增 `KAP_SPEC`（`kimi-kap` / `Kimi KAP`，vendor `kimi`，实验性 `dev_note`）；`SPEC`（`kimi-acp`）保持默认 |
| `src/ide/llm/providers/kimi/__init__.py` | 公开 `KimiKapLLM`、`kimi_kap_available`；文档串改为 ACP/KAP 双入口 |
| `src/ide/llm/registry.py` | 注册序追加 `kimi-kap`：`kimi-acp`、`kimi-kap`、`dsh-acp`、`local-acp`；`DEFAULT_BACKEND` 不变 |
| `tests/test_kimi_kap_events.py` | 新增 10 项：文本补尾、思维链、tool start/update/end、diff、todo、context、turn usage、reset |
| `tests/test_kimi_kap_provider.py` | 新增 9 项：轮次绑定、通知轮隔离、未绑定通知尾、迟到旧轮、取消、审批/提问/多选映射 |
| `tests/test_kimi_kap_transport.py` | 新增 8 项：实例排序、坏 JSON、回环限制、发现探活、复用服务、CLI 版本门槛 |
| `tests/test_kimi_kap_gui.py` | 新增 3 项：KAP Bash 载荷 → Kimi `BashCard`、未知工具 → `McpCard`、Kimi presenter |
| `tests/test_llm_spec_cache.py` | 注册序/标签快照加入 `kimi-kap`，补 KAP spec 元数据断言 |
| `tests/test_provider_boundaries.py` | `ide.llm.providers.kimi.kap` 加入厂商私有模块；新增通用 GUI 直接 import KAP provider 的受控反例 |
| `维护手册/后端CLI工具维护说明手册.md` | 第 1 节新增 KAP 启动/发现/验证/诊断/降级/隐私说明；不改 DSH/Local 章节 |
| `src/ide/llm/README.md` | 文件结构、`KimiKapLLM` 契约、用量通道与轮后宽限说明 |

## 2. 结构映射与边界

- **KAP 与 ACP 并列，不替换**：Kimi 厂商下 `kimi-acp`（默认）/`kimi-kap`（实验）二选一；DSH、Local 与公共 `providers/acp/` 零改动。
- **厂商边界**：`kap/` 只被 `providers/kimi/spec.py`、`providers/kimi/__init__.py` 与注册表装配；通用 GUI 不直接 import，架构守卫已锁。
- **卡片复用**：KAP 载荷适配为 Kimi 现有 `ToolCallPayload`/`ToolUpdatePayload`，GUI 沿用 `vendor="kimi"` 的 presenter/cards；未知工具降级 `McpCard`。未新增 kap presenter（计划允许复用）。
- **服务生命周期**：优先复用已有 Desktop/`kimi web`（不杀用户服务）；没有才拉起私有服务，标签关闭时 TERM→KILL 回收。
- **隐私**：token 只在内存；日志只记事件类型/`code`/`request_id`/会话与轮次 id；不落 prompt 正文、完整工具载荷、diff 或凭证。

## 3. 验证

- **门禁**：`bash scripts/check.sh`（`UV_CACHE_DIR`/`TMPDIR` 指向 `./.temp/`）→ 隐私扫描通过、QLabel 守卫通过、ruff 通过、`373 passed`。
- **KAP 单测**：4 个新文件共 30 项全绿；全量 `pytest tests -q` 373 passed；KAP 新增 30 项，另有注册表/守卫快照修订。
- **真实链路验收**（隔离 home，不写真实会话）：

| 场景 | 结果 |
|:---|:---|
| 普通轮 | `OK`，2.5s 级；usage 终值 `13383/1048576` |
| 工具审批 + Bash | 审批放行 → `tool_call`/`tool_call_update`（命令、running/completed、输出）→ marker `tool-ok` |
| 后台通知 + 追问交错 | 通知轮 `ordinal=1`（`notification` item）可见；追问 `status=queued`；通知轮 ended 被忽略；`ordinal=2` 用户轮执行，`C2-OK` + marker `bg-done` |
| 60s 前台长任务 | 66.5s 正常收尾，`LONG-DONE`，marker `long-done`；无提前 `end_turn` |
| 手动停止 | `TurnCancelled`，0.03s 返回；session `idle` / `interrupted` |
| 服务重启恢复 | SIGKILL 私有服务后自动重连并 `:reload`，同一 `session_id` 继续，正文 `TWO` |

- **证据**：T1 契约/回放与 provider 冒烟脚本在 `.temp/kap-t1/`（已 gitignore）；运行时 `openapi.json`/`asyncapi.json`/`kap.md` 指纹见计划 §T1 验证结果。

## 4. 偏差与经验

完整清单见计划 §8。核心三条：

1. **只认 `user_message` 绑定**：通知轮的 `turn.started` 可能在空闲期被丢弃，只剩 `turn.ended`；把它当用户轮会提前返回通知正文。必须用 `user_message_id`/`prompt_ids` 命中，`turn.ordinal` 过滤迟到旧轮。
2. **WS 消费者要常驻**：`context` 终值晚于 `turn.ended` 约 250ms；空闲 pump + `post_turn_usage_grace_s=1.0` 才能让徽章拿到终值，并处理空闲期审批。
3. **心跳是应用层 JSON**：只回 RFC6455 控制帧 pong 会被 KAP 断开订阅；必须按 `asyncapi.json` 回 `{"type":"pong"}`。

## 5. 风险与未做

- **实验性契约**：KAP REST/WS 随 CLI 升级可能变化；已存档 schema 指纹，升级后须复核，解析失败降级。
- **未做完整窗口点击式验收**：GUI 侧只做 offscreen 卡片路由测试；真实轮次验收在 provider 层完成（沿用此前 ACP 审计的驱动口径）。未在真实窗口中键入验证。
- **图片附件未做真机验收**：KAP 走 `image` content 的本地 `path` source，链路已实现；未跑真实图片会话。
- **不自动 `:takeover`**：`40931` 只提示，需用户在原客户端处理；这是计划红线，不是缺陷。
- **默认仍 ACP**：KAP 为实验入口，不改变出厂默认。
- **未提交**：全部改动留在工作树，等用户确认后提交。

## 6. 互引

- 计划：`work plans/2026-1122-2308_KimiKapServer链路增量接入落地计划.md`
- 本记录：`文档/修改记录/2026-1123-0001_KimiKapServer链路增量接入总实施记录.md`
