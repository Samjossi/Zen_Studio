# AskUserQuestion 一卡多问后续问题静默跳过定责报告

> **状态**：已确认（取证闭环，定责 agent 侧）
> **范围**：`llm/providers/kimi_acp.py`（kimi ACP 桥）、`llm/permission_policy.py`（question 识别）、`gui/panels/chat/question_bridge.py`（提问串行化）、`gui/panels/chat/cards.py`（QuestionCard）、`scripts/capture_askuser_2q_frames.py`（取证脚本，本次新增）
> **时间**：2026-08-28 14:32（UTC+8，取证与定稿）
> **优先级**：中（功能缺陷在 agent 侧，IDE 无责、无需改动；留档防后续误判为 IDE 回归）

---

## 1. 问题现象

用户在 Zen Studio 聊天面板中实测：agent（kimi 后端）通过 AskUserQuestion 在**一张卡片内连问两个问题**，用户点击回答了第一个问题后，**第二个问题没有任何提问入口**——既无卡内按钮组激活，也无降级弹窗，整个工具调用直接结束，后续问题被静默跳过。用户原话：「你都不给我选择，我怎么选择呢？」

待定责问题：这是 IDE（Zen Studio）代码层面的缺陷，还是 AI（kimi CLI agent）侧的行为？

## 2. 结论（先说答案）

🔴 **agent 侧（Kimi Code CLI 的 ACP 适配层）缺陷，IDE 侧无责。**

kimi CLI 0.34.0 将多问 AskUserQuestion 桥接到 ACP 时，**只把第一问桥接为 `session/request_permission`，收到第一问答案后立即将整个工具调用置为 completed，剩余问题从未上线**。IDE 侧提问链路（识别 → 串行桥 → 卡内交互/弹窗兜底）经代码走查确认完好，即便第二问请求真的到达也有完备的激活与兜底路径——但它从未到达。

## 3. 取证过程与实证数据

### 3.1 取证方法

新增一次性诊断脚本 `scripts/capture_askuser_2q_frames.py`（蓝本 `scripts/capture_reasonix_ask_frames.py`）：以 `AcpConnection` 直连真实 kimi 后端，诱导一次两问 AskUserQuestion 调用，**逐条完整记录 `request_permission` 载荷**并回第一个 allow 类选项放行，全序列帧落盘。

诱导 prompt 要点：明确要求「一次调用里连问两个问题（questions 数组给两项）」，第一问颜色（红色/蓝色），第二问水果（苹果/香蕉）。

### 3.2 关键实证（E1-E4）

| # | 实证项 | 观察结果 | 含义 |
|:---:|:---|:---|:---|
| E1 | `request_permission` 到达次数 | **全程仅 1 次**（两问场景） | 第二问从未成为一次真实提问请求 |
| E2 | 唯一一次请求的 options | 仅第一问选项：`q0_opt_0`(红色)/`q0_opt_1`(蓝色)/`q0_skip`(Skip，kind=reject_once) | 一次请求只编码一问的选项 |
| E3 | 回答第一问后的帧序列 | `tool_call_update` 直接 `completed`，无第二条 `request_permission` | agent 答完第一问即收掉整个工具调用 |
| E4 | completed 出参 | `{"answers":{"你喜欢什么颜色？":"红色"}}` —— **只有第一问的答案** | 第二问答案缺失，被静默丢弃 |

存档：`文档/帧存档/askuser_2q_20260828_132205.json`（73 条 update 帧 + 1 次审批载荷 + 轮次响应 `stopReason=end_turn`；agent 标识 Kimi Code CLI 0.34.0）。

### 3.3 协议层根因

ACP v1 的 `session/request_permission` 语义为**一问一答**：单个 `options` 数组、回传单个 `optionId`，无多问表单原生语义。多问场景要完整表达，只能由 agent **逐题串行发多次请求**。kimi 的 optionId 命名空间（`q0_opt_N`、`q0_skip`；reasonix 同系为 `q1:N`、`q1:cancel`，见 `2026-0812-0952_reasonix ask提问工具自动选答修复计划.md` E3）显示其实现**预留了按题编号的能力**，但当前版本在两问场景下只发出了 `q0` 一条，`q1` 请求始终未发。

对照：kimi CLI 原始终端 TUI 的 AskUserQuestion 多问表单工作正常——多问支持存在于 CLI 本体，丢失仅发生在其 **ACP 适配层**。

## 4. IDE 侧链路走查（排除 IDE 责任）

按 `2026-0807-0148_AskUserQuestion自动选答修复计划.md` 建立的链路逐环核对：

1. **识别**：`llm/permission_policy.py` `is_question_request` 双通道（结构签名 + 工具名白名单），question 请求恒 `DECISION_ASK`，自动选答路径对 question 不可达——🟢 正常。
2. **路由**：`gui/panels/chat/panel.py` `_ask_permission` question 分支走 `QUESTION_BRIDGE.ask`——🟢 正常。
3. **串行化**：`gui/panels/chat/question_bridge.py` 仿 PERMISSION_QUEUE 的「活动条目 + 点击回调推进」骨架，**天然支持同一工具调用的多次提问请求逐条激活**——🟢 设计即覆盖多问场景。
4. **交互面**：`gui/panels/chat/cards.py` QuestionCard `activate_options` 卡内按钮组；卡片已答/缺失时降级 `gui/panels/chat/permission_dialog.py` QuestionDialog 模态兜底——🟢 即便第二问到达时卡片已终态，用户也至少会看到弹窗。

**反推验证**：若第二问的 `request_permission` 真到达而 IDE 丢失，则必然留下「请求到达但未呈现」的痕迹（超时 abort、stale 标记、弹窗记录）；取证显示线上根本没有第二条请求，IDE 侧无任何可丢失的对象。

## 5. 影响面与边界

| 场景 | 表现 | 责任方 |
|:---|:---|:---|
| kimi 后端 + 多问 AskUserQuestion | 仅第一问可答，其余静默跳过 | kimi CLI ACP 适配层 |
| kimi 后端 + 单问 AskUserQuestion | 🟢 正常（0807-0148 计划已闭环） | — |
| reasonix 后端 + ask 工具 | 逐题独立请求（toolCallId `ask-1-qN` 形态，0812-0952 E6），多问逐题呈现 | 🟢 正常 |
| 其他 ACP 后端 + 多问提问工具 | 未实证，存在同形态风险 | 各后端 |

⚠️ 用户感知补充：多问场景下卡片会渲染全部问题文本（渲染帧 `rawInput.questions` 同构全量携带），但实际仅第一问可交互——**「看得见、答不着」**的体感缺陷，易被误判为 IDE 交互丢失，本报告即为此定责留档。

## 6. 后续行动建议

| # | 行动 | 责任方 | 状态 |
|:---:|:---|:---|:---|
| A1 | 向 Kimi Code CLI 提 issue：ACP 适配层多问 AskUserQuestion 仅桥接首问，附本报告 §3 取证帧 | 用户/上游 | 待办 |
| A2 | 上游修复后（预期形态：逐题串行多次 `request_permission`，`q1_opt_*` 上线），用 `scripts/capture_askuser_2q_frames.py` 复跑验证，审批请求应为 2 次 | IDE 侧 | 待上游 |
| A3 | 上游修复后真机回归 IDE 多问交互（卡内第二问激活或弹窗兜底路径） | IDE 侧 | 待上游 |
| A4 | IDE 侧代码：**无需任何改动**（链路走查 §4 确认完好） | — | ✅ 已确认 |

## 7. 参考资料

- 取证存档：`文档/帧存档/askuser_2q_20260828_132205.json`
- 取证脚本：`scripts/capture_askuser_2q_frames.py`
- 单问交互闭环：`2026-0807-0148_AskUserQuestion自动选答修复计划.md`
- reasonix ask 逐题形态实证：`2026-0812-0952_reasonix ask提问工具自动选答修复计划.md`（E2/E3/E6）
- Other 自定义输入方案：`2026-0807-0445_AskUserQuestion自定义输入Other选项计划.md`

---

*撰写：2026-08-28 14:32 (UTC+8)*
