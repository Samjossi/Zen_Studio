# Kimi 上游 ACP 缺陷跟踪任务

> **任务一句话**：跟踪 MoonshotAI/kimi-code 官方对「ACP 适配层忙态提前回 `end_turn`」缺陷的修复进度，按判定标准给出结论与应对建议。
> **创建**：2026-11-21（首次调查后立档）
> **上游仓库**：https://github.com/MoonshotAI/kimi-code （旧仓库 MoonshotAI/kimi-cli 已归档，勿查）
> **跟踪性质**：只读观察 + 汇报。不修改上游仓库、不代用户提交 issue 或评论，除非用户当次明确授权。

---

## 1. 背景与缺陷定义

Kimi Code CLI 的 ACP 适配层在 agent 内部轮次仍在执行时提前响应 `session/prompt` 的 `stopReason: "end_turn"`，并伴随三类症状：

1. **通知轮不可见**：后台任务完成等事件自启动的内部轮次（`trigger=notify`）不向 ACP 客户端发任何 `session/update`，主机侧永远停在"已完成"；
2. **忙态 prompt 被吞**：此类轮次活跃期间到达的 `session/prompt` 被立即回空 `end_turn`，文本不进入执行、无回答（上游 issue #5095 报告者称之为 answered with empty end_turn and lost）；
3. **`session/cancel` 覆盖不到通知轮**（2026-11-21 我方探针实测：须用 `session/close` 才能终止并回收后台子进程）。

**我方证据链**（跟踪时引用用）：
- `文档/审计报告/2026-1121-1412_ACP提前end_turn引发IDE假完成与停止失效诊断报告.md`（11-21 13:53 事故全时间线）
- `文档/审计报告/2026-1121-1225_KimiCLI_ACP提前end_turn与隐藏通知轮次审计报告.md`（2.7.1 探针：`session/close` 能力声明与实测）
- `work plans/2026-1121-1420_Kimi假完成与停止失效修复落地计划.md`（客户端防线实施记录，含开发态真实链路验证日志）

**客户端防线现状**：已实施（2026-11-21，396 项测试全绿）。`src/ide/llm/providers/kimi/turn_state.py` 四态终态确认 + drain + `session/close` 停止分流。上游修复不阻塞我方交付；本跟踪的目的在于：**官方修复到位后及时验证并决策防线的去留/简化**。

## 2. 跟踪对象清单（2026-11-21 快照）

### 2.1 核心 issue（与缺陷直接同签名）

| # | 标题（截断） | 快照状态 | 说明 |
|:--|:---|:---|:---|
| [#5095](https://github.com/MoonshotAI/kimi-code/issues/5095) | ACP: turns self-started by background-task notifications emit no session/update; prompts arriving during them are answered with empty end_turn and lost | open，无 label/assignee，3 评论全为报告者自补证据 | **与我们事故完全同签名**（Orca 客户端，2.6.2→2.6.3 复现，触发类含子代理）。跟踪的主锚点 |
| [#4944](https://github.com/MoonshotAI/kimi-code/issues/4944) | ACP autonomous turns drop output, settle queued prompts early, and ignore cancellation | open，1 评论 | 补充 `/compact` 与 bash 超时转后台两个触发路径 |
| [#4109](https://github.com/MoonshotAI/kimi-code/issues/4109) | ACP: turns Kimi starts itself (background task results, CronCreate) send no session/update events | open，3 评论 | 含社区根因分析与参考修法（Dante-dan：turn 生命周期跟踪与 RPC 结算应分离） |
| [#4964](https://github.com/MoonshotAI/kimi-code/issues/4964) | A stream that ends without finish_reason is accepted as a completed turn (recorded as end_turn) | open，0 评论 | 无 finish_reason 记为 end_turn，同族 |
| [#1485](https://github.com/MoonshotAI/kimi-code/issues/1485) | kimi acp completes prompt with end_turn but emits zero agent_message_chunk updates | open，**有 bug 标签** | 7 月已报，最老的同族 issue |

### 2.2 相关 issue（同根因邻近症状，次优先级）

- [#4572](https://github.com/MoonshotAI/kimi-code/issues/4572) ACP 模式 ~10.5s 中止 LLM 请求（APITimeoutError）
- [#1813](https://github.com/MoonshotAI/kimi-code/issues/1813) / [#1865](https://github.com/MoonshotAI/kimi-code/issues/1865) / [#3107](https://github.com/MoonshotAI/kimi-code/issues/3107)：provider 失败被吞成 `end_turn` 的一类（#3161 同名已关闭，疑为重复 PR）
- [#1855](https://github.com/MoonshotAI/kimi-code/issues/1855) / [#1858](https://github.com/MoonshotAI/kimi-code/issues/1858)：ACP usage 不上报（我方已有客户端兜底，非阻塞）

### 2.3 相关 PR（修复动作观察点）

| # | 标题 | 快照状态 | 说明 |
|:--|:---|:---|:---|
| [#1586](https://github.com/MoonshotAI/kimi-code/pull/1586) | feat(acp): add agent-core-v2 ACP server | open（2026-07-13 提出） | sailist 提交。**疑似官方根治方向**（重写 ACP server），五个月未合并。合并 ≈ 官方动手了的强信号 |
| [#4510](https://github.com/MoonshotAI/kimi-code/pull/4510) | feat(acp): report background tasks that outlive a turn to the client | open（2026-10-29），未合并、无人工审查 | 社区补丁，仅覆盖"任务报告"子症状 |
| [#3224](https://github.com/MoonshotAI/kimi-code/pull/3224) | fix(agent-core-v2): normalize interrupted turns on restore | open（2026-08-25） | agent-core-v2 系列配套，间接信号 |

### 2.4 版本观察点

- 当前最新正式版 `@moonshot-ai/kimi-code@2.7.2`（2026-11-20 发布），changelog **无 ACP 修复条目**；2.7.0/2.7.1 同。我方 11-21 在 2.7.2 复现缺陷。
- 关注 canary 通道（`kimi-code-desktop@1.5.0-canary.*` 等）是否先出现 ACP 相关修复。

## 3. 跟踪操作手册（每次跟踪照做）

### 3.1 检查步骤

```bash
# ① 核心 issue 状态与官方回应（重点看 state/labels/assignee/milestone/comments 变化）
for n in 5095 4944 4109 4964 1485; do
  curl -sS --retry 3 "https://api.github.com/repos/MoonshotAI/kimi-code/issues/$n" \
    -o .temp/kimi-fix-probe/track_issue_$n.json
  sleep 2   # 无认证限速 60 次/小时，勿并行猛刷
done

# ② 新评论（是否有官方成员 / MEMBER association 发言）
for n in 5095 4944 4109 4964 1485; do
  curl -sS --retry 3 "https://api.github.com/repos/MoonshotAI/kimi-code/issues/$n/comments" \
    -o .temp/kimi-fix-probe/track_comments_$n.json
  sleep 2
done

# ③ PR 进展
for n in 1586 4510 3224; do
  curl -sS --retry 3 "https://api.github.com/repos/MoonshotAI/kimi-code/pulls/$n" \
    -o .temp/kimi-fix-probe/track_pr$n.json
  sleep 2
done

# ④ 新 release 的 changelog 是否含 ACP 修复（有新 tag 时；@ 须转义为 %40）
curl -sS --retry 3 \
  "https://api.github.com/repos/MoonshotAI/kimi-code/releases/tags/%40moonshot-ai%2Fkimi-code%40<新版本号>" \
  -o .temp/kimi-fix-probe/track_release.json

# ⑤ 是否有新出现的同类 issue/PR
curl -sS --retry 3 \
  "https://api.github.com/search/issues?q=repo:MoonshotAI/kimi-code+ACP+end_turn&sort=created&order=desc&per_page=30" \
  -o .temp/kimi-fix-probe/track_search.json
# 另用关键词 notify / autonomous turn / session%2Fupdate 各搜一次
```

### 3.2 判定标准（每次跟踪必给其一）

| 判定 | 条件 | 我方动作 |
|:---|:---|:---|
| **官方已修** | 满足任一：#5095（或同签名 issue）被官方关闭并关联修复 PR/版本；或某正式版 release notes 明确列出本缺陷修复（notify turn 可见 / busy prompt 拒绝或排队执行 / end_turn 时机）；或 #1586 合入且发布 | ① 本机升级该版本；② 重跑 `.temp/kimi-fix-probe/live_turn_probe.py`（真实链路探针，预期：A 轮不再 desync、无 status 块、无提前 end_turn）；③ 按 `work plans/2026-1121-1420` 回写节的 T7 清单跑 GUI 验收；④ 防线决策（见 §4） |
| **部分修复** | 只修子症状（如仅 #4510 合入：任务完成可见，但忙态 prompt 仍被吞） | 更新防线判定口径注释；保持防线；继续跟踪 |
| **仍无进展** | 核心 issue 无官方回应、无 label/assignee、无相关 release 条目 | 防线维持；可选建议用户向 #5095 补充我方脱敏复现材料（须用户授权后执行） |

### 3.3 汇报格式（跟踪结束时输出）

```
跟踪日期 / 执行人：
① 对象状态表：#编号 → state / label / assignee / 新评论数（有官方发言引用原文）
② 新 release：版本号 + ACP 相关条目（引用原文）或无
③ 新出现的同类 issue/PR：编号与一句话
④ 判定：官方已修 / 部分修复 / 仍无进展
⑤ 建议动作（按 §3.2 判定矩阵）
```

## 4. 官方修复后的防线决策预案

| 防线组件 | 官方根治后的建议 | 理由 |
|:---|:---|:---|
| `turn_state.py` 四态终态确认 | **保留**，可放宽 `tool_use` 判失配口径（若官方保证 notify turn 有协议级信号） | 防御纵深：私有旁路核对成本极低（风险轮次才触发），且能兜住官方后续回归 |
| drain + status 块 | 保留（触发频率将趋零） | 同上 |
| `session/close` 停止分流 | 保留 | close 是正确的会话级清理，官方修复不改变其语义 |
| wire 元数据跟踪 | 保留 | 同时服务用量/子代理旁路，非本次新增成本 |

> 决策权在用户；以上为预设建议，验证数据齐后交用户拍板。

## 5. 边界与注意

- **只读跟踪**：不得在上游仓库做任何写操作；代提交 issue/评论必须用户当次明确授权，且只能用脱敏材料（`文档/审计报告/2026-1121-1412_…诊断报告.md` 已脱敏，可直接引用其 wire 元数据时间线；正文/路径/工具载荷不得外发）。
- GitHub API 无认证限速 60 次/小时：§3.1 全跑约 15 次，单轮跟踪足够；不要写循环高频轮询。
- 网络抖动常见：curl 失败重试 ≤4 次、间隔 5 秒；仍失败在汇报中注明"本次数据不全"。
- 旧仓库 `MoonshotAI/kimi-cli` 已归档（指向 kimi-code），其 issue（如 #2394）不再更新，勿混入状态表。
- 本文件的状态快照会过期：**每次跟踪以第 2 节为对照基线**，汇报变更量而非复述全文。

## 6. 历史快照（首次调查，2026-11-21）

- 核心 issue 全部 open、无 label/assignee/milestone，无任何官方成员公开回应（评论均为报告者/社区）。
- 最新正式版 2.7.0/2.7.1/2.7.2（11-19/20 发布）changelog 无 ACP 修复条目；我方 11-21 在 2.7.2 实测复现。
- 唯一疑似官方根治动作：PR #1586（agent-core-v2 ACP server 重写，2026-07-13 提出）open 五个月未合并。
- 结论：**官方零承诺、无排期**；客户端防线（我方 2026-11-21 已实施）为当前唯一确定有效的保障。

## 7. 跟踪快照（2026-11-21，第二次跟踪）

- **判定：仍无进展**。
- 核心 issue #5095/#4944/#4109/#4964/#1485 状态与基线完全一致：全部 open、无 assignee/milestone、无官方成员评论（comments 数未变：3/1/3/0/0）。
- PR #1586/#4510/#3224 全部仍 open 未合并。
- 无新正式版（最新仍为 2.7.2）；今日新发的 desktop canary `1.5.0-canary.20261121032349` body 无 ACP 条目。
- 新发现同签名 issue **#4027**（2026-09-24，harness 自发轮次——cron/后台任务结果——永远到不了客户端；open、无 label、唯一评论为社区）：与 #5095 同签名，补入跟踪清单。另注意到 agent-core-v2 系列已合入若干"保留排队 prompt / 中断轮次恢复"PR（#4298 等），说明 v2 重写仍在推进，但未触及当前 ACP 适配层。
- 动作：防线维持现状，继续只读跟踪。
