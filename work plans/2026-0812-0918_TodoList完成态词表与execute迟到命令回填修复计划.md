# TodoList 完成态词表失配与 execute 迟到命令回填修复计划

> **状态**：已闭环（2026-08-12，验收 B1-B7 全过，见修订记录 R1）
> **范围**：`llm/providers/acp.py`（todo status 归一 + 迟到 command 提取）、`gui/panels/chat/panel.py`（迟到 command 簿记）、`scripts/shot_tool_cards.py`（mock 场景校正）、真机复验
> **时间**：2026-08-12 09:18（设计，UTC+8）
> **优先级**：高（两项均为真机实证的真 bug，影响每次 kimi 会话的 TodoList 与 Bash 卡片显示正确性）
> **依据**：`.temp/frame_archive/todolist_a2_frames_20260812_080109.json`（A2 卡 2 甲项 `status:"done"` 实证）、`.temp/frame_archive/execute_20260812_075201.json`（execute 首帧空壳、command 迟到实证）
> **前序**：`2026-0812-0735_卡片防回归mock补盲与帧证据制度化计划.md` R1（两发现另案记录）；`2026-0812-0336_TodoList清单kimi字段名失配修复计划.md`（同范式：kimi 系字段词表与内部约定失配，协议层归一）

---

## 1. 背景

0735 计划 T3 真机取证顺带抓到两个真 bug，均为 kimi 系帧形态与内部约定的失配，与 0336（title/content 键失配）同族：

### Bug 1：TodoList 完成态 status 为 `"done"`，渲染词表失配

- **实证**：A2 帧存档中 kimi 回传的完成条目 `status` 取值为 `"done"`（与回执文本 `[done]` 同源），不是 ACP 文档词表的 `"completed"`；
- **断链点**：`_extract_todo_entries`（`acp.py:703-704`）对 status 原样透传；下游三处消费点只认 `"completed"/"cancelled"`——`_make_todo_row`（`cards.py:1134`，图标与删除线）、`_fill_todo_list`（`cards.py:1163`，x/y 计数）、`output.upsert_todo_block`（`output.py:373`，旧轨灰化删除线）；
- **后果**：完成项显示 ☐ 无删除线、x/y 进度副标题不计入（A2 卡 2 副标题「0/4」失真）。方向为"少显示"，非崩溃，故潜伏至今；
- **附带**：`_todo_fallback_text`（`acp.py:673-677`）的 marks 缺省 `"[x]"` 恰好把 `"done"` 当完成处理——兜底文本与卡片渲染口径已不一致，归一后自然对齐。

### Bug 2：kimi execute 的 command 迟到到达，`$ ` 命令头与输出尾滚失效

- **实证**：execute 帧存档——首帧 `title="Bash"`、`kind="execute"`、**无 rawInput**；command 随 in_progress 帧迟到（迟到帧 `title="Running: echo …"`、`rawInput={command}`）；
- **断链点**（三段）：
  1. `_map_tool_call`（`acp.py:788-792`）仅首帧提取 command——首帧无 rawInput，`payload["command"]` 不产生；
  2. 迟到帧经 `_map_tool_update` 只走 `_record_input_detail`（`acp.py:855-856`，入参区回填 command 行）——**payload 无 `"command"` 键**，路由层 `panel.py:906` 的 `_tool_commands` 簿记永不建立；
  3. 簿记缺失的连锁：`panel.py:921`（输出帧补 `$ ` 头）、`panel.py:963`（`_allow_progress_frame` 尾滚放行）均以 `tid in self._tool_commands` 为闸门——全部不通过；
- **后果**：kimi bash 卡实际无 `$ ` 粗体命令头、运行中输出尾滚帧全丢弃（仅 completed 帧定格输出）。mock 场景 22 曾按首帧齐备假设构造（假绿土壤），场景 30 已按实证形态记录现状。

---

## 2. 修复原则

| # | 原则 | 说明 |
|:---:|:---|:---|
| P1 | **协议层归一，渲染层不动** | 0336 同范式：各家词表/时序差异在 `acp.py` 单点消化，内部统一约定（`completed`、首帧齐备语义），`cards.py`/`output.py` 词表与逻辑零改动 |
| P2 | **最小改动** | Bug 1 只加一张状态映射表；Bug 2 只加"迟到 command 提取 + 迟到簿记"两小段，不改既有首帧路径 |
| P3 | **实证先行** | 改前以 `.temp/frame_archive/` 两份存档帧为真值；改后 mock + 真机双验，防新一轮假绿 |
| P4 | **降级方向安全** | 提取/簿记失败时行为回退现状（无 `$ ` 头、无尾滚），不得引入崩溃或误显示 |

---

## 3. 修复方案

### T1：Bug 1——todo status 词表归一（acp.py 单点）

**改动点**：`_extract_todo_entries`（`acp.py:703-704`）。

现状：`status` 为字符串即原样透传。改为经映射表归一后存储：

```python
#: todo status 外部词表 → 内部约定归一（渲染层只认 pending/in_progress/
#: completed/cancelled）：kimi 系完成态为 "done"（回执 [done] 同源，
#: todolist_a2_frames_20260812_080109.json 实证）；未收录取值原样透传
#: （渲染层按缺省 pending 容错，降级方向安全）
_TODO_STATUS_NORMALIZE = {"done": "completed"}
```

- 仅 `"done" → "completed"` 一条，不大写化、不模糊匹配——有实证才收，防过度归一误伤其他 provider 词表；
- docstring（`acp.py:682-687`）同步补一句词表归一说明；
- `_apply_todo_diff` 比对口径（content+status+priority）自动受益——归一在提取层，快照存储与比对同为内部词表，跨调用 diff 语义不变；
- `_todo_fallback_text` 的 marks 缺省 `"[x]"` 行为不变（`"done"` 归一为 `"completed"` 后仍落缺省），兜底与卡片口径自然对齐，无需改动。

### T2：Bug 2——迟到 command 提取与簿记（协议层 + 路由层各一小段）

**T2-1 协议层提取**：`_map_tool_update`（`acp.py:855` 附近，`_record_input_detail` 同位）。

- in_progress 帧 `rawInput.command` 为非空字符串时提取进 `payload["command"]`（净化复用 `_clean_terminal_text(...).strip()`，与 788-792 首帧路径同口径）；
- **防重复**：与 `_input_detail_seen` 同账本——首帧已带 command 的形态（kilocode/opencode 首帧齐备）不重复提取；kimi 迟到形态只提首次，后续帧不再附；
- **kind 判定**：以取证帧 `execute_20260812_075201.json` 核实迟到帧 `kind` 字段在场性——在场则限 `kind == "execute"`；缺省则按 `rawInput.command` 存在即提取（渲染层仅 BashCard 消费 `command` 键出 `$ ` 头，其他卡无消费点，误提无害，P4）。实现时以存档帧为准并在代码注释注明实证出处；
- 首帧 `_map_tool_call` 的 788-792 路径**不动**（kilocode 系首帧齐备形态零影响）。

**T2-2 路由层簿记**：`panel.py` `_on_activity_chunk` 的 `tool_call_update` 分支（910 行区域）。

- 在 in_progress 放行判定（918 行）**之前**补：`payload.get("command")` 且 `tid not in self._tool_commands` 时建立簿记（`setdefault` 语义，首帧已簿记者不覆盖）；
- 效果链：簿记建立 → 同帧/后续带 output 的帧过 `panel.py:963` 闸门（200ms 节流逻辑不变）→ `panel.py:921` 补 `$ ` 头 → BashCard 命令头与尾滚恢复；旧轨（930-936 冻结路径）同闸门自动受益，不改；
- 轮次收尾清理（`panel.py:790`）已覆盖，无需新增。

### T3：mock 校正与双验

1. **mock 场景校正**：
   - 场景 16/20（TodoList）：完成项中挑一条改为 `status="done"`（混入实证形态，同 0336 脏条目防御语义）——看点更新为：done 项 ☑ + 删除线 + 计入 x/y；
   - 场景 30（execute 迟到）：in_progress 帧补 `rawInput.command` 后的新看点——`$ ` 命令头在迟到帧后建立、尾滚输出帧放行（需给 `_MiniRouter` 注入 `_tool_commands` 簿记语义，与真实路由对齐；注入方式参照 21 场景注释记录的缺口）；
2. **回归**：`.venv/bin/python scripts/shot_tool_cards.py` 全场景 + 弹窗读图闭环；`.venv/bin/python scripts/test_question_permission.py` 通过；
3. **真机复验**：复跑 `.venv/bin/python scripts/capture_tool_frames.py`（重取 execute 帧核对提取结果）与 `.temp/e2e_todolist_cards.py`（A1/A2 复验——A2 诱导 prompt 调整为"建立清单后将其中一项标记完成再继续"，实证 done 项 ☑ + 删除线 + x/y 计数 + bash 卡 `$ ` 头）；新帧存档与截图落 `.temp/frame_archive/`（入库仍待 0735 计划 T1）；
4. **状态回写**：0735 计划 R1 遗留问题 ①② 标注已由本计划闭环。

---

## 4. 验收标准

| # | 验收项 | 预期 | 验证方式 |
|:---:|:---|:---|:---|
| B1 | status 归一（T1） | `done` 条目内部存为 `completed`；其他取值透传不受影响 | mock 场景 16/20 截图：done 项 ☑ + 删除线 + 计入 x/y |
| B2 | 兜底口径对齐（T1） | `_todo_fallback_text` 与卡片渲染对 done 项一致按完成处理 | 目检 + 旧轨对照 |
| B3 | 迟到 command 提取（T2-1） | kimi execute 迟到帧 payload 携带 `command`；首帧齐备形态不重复提取 | 复跑 capture_tool_frames.py 后目检帧处理结果（或探针断言） |
| B4 | 迟到簿记与 `$ ` 头（T2-2） | 场景 30：`$ ` 命令头建立、尾滚帧放行；场景 21/22（首帧齐备）零回归 | mock 截图逐张读图 |
| B5 | 既有回归 | 31 场景 + 弹窗零回归；test_question_permission 通过 | 读图闭环 + 测试运行 |
| B6 | 真机复验（T3-3） | A2 复验：done 项 ☑ + 删除线 + x/y 计数正确；bash 卡 `$ ` 头与尾滚在场 | 真机截图 + 帧存档落盘 |
| B7 | 状态回写（T3-4） | 0735 计划 R1 遗留问题 ①② 标注闭环 | 目检 |

---

## 5. 风险与注意事项

| 符号 | 项 | 说明 |
|:---:|:---|:---|
| 🟢 | **回归面** | T1 单点映射（未收录取值透传）；T2 只增路径不改首帧路径；kilocode/opencode 首帧齐备形态全程不动 |
| 🟡 | **过度归一风险（T1）** | 映射表只收 `"done"` 一条实证值；未来其他 provider 新词表按同范式逐个补，不预先穷举 |
| 🟡 | **kind 在场性（T2-1）** | kimi 迟到帧 kind 字段是否携带需以存档帧核实；缺省方案的误提风险已论证无害（仅 BashCard 消费），实现时二选一并在注释注明实证出处 |
| 🟡 | **场景 30 的 mock 保真** | `_MiniRouter` 需补 `_tool_commands` 簿记注入才能实证尾滚放行——mock 路由器对齐真实路由语义，属测试基建补全，不改运行时代码 |
| ⚪ | **旧轨** | 冻结路径（`panel.py:924-939`）同闸门自动受益，不做专项验证，目检即可 |

---

## 6. 实施顺序

1. **T1**（status 归一）——单点改动，配场景 16/20 校正，B1/B2 当日可验；
2. **T2**（迟到 command 提取 + 簿记）——协议层先行、路由层跟随，配场景 30 校正，B3/B4；
3. **T3**（回归 + 真机复验 + 回写）——B5/B6/B7 收口。

T1/T2 无相互依赖，可并行；T3 必须在两者之后。

---

## 修订记录

| 版本 | 时间 | 内容 |
|:---:|:---|:---|
| R0 | 2026-08-12 09:18 | 初稿：依 0735 计划 R1 两项遗留发现成文——T1 todo status 词表归一（`_TODO_STATUS_NORMALIZE={"done":"completed"}`，0336 同范式协议层单点）；T2 execute 迟到 command 提取（`_map_tool_update` 同账本防重复）+ 路由层迟到簿记（`panel.py` in_progress 判定前 `setdefault`）；T3 mock 场景 16/20/30 校正 + 全量回归 + 真机复验 + 0735 遗留回写；验收 B1-B7 |
| R1 | 2026-08-12 | 执行记录（B1-B7 全过）：**T1**——`_TODO_STATUS_NORMALIZE` 单点归一 + docstring 同步；**T2**——协议层迟到提取（`kind=="execute"` 限定，第 14 帧 kind 在场经存档核实）+ `panel.py` 迟到簿记（两轨同闸门）；**计划外一处必要补充**：BashCard `$ ` 命令头原仅在 `_build_body` 构建期渲染，迟到 command 无法上屏——按 `_set_input_detail`/`_set_media_path` 同范式补 `_set_command` 钩子（基类 no-op、BashCard 覆写 body 顶端补挂、幂等首帧优先），P1「渲染层不动」仅就此一处破例，为首帧路径零影响的纯增路径；**T3**——场景 16/20 混入 `status="done"`、场景 30 补尾滚输出帧、`_MiniRouter` 注入 `_tool_commands` 簿记语义（`note_command`/`command_for`，与 panel.py 同语义）；**验证**——31 场景 + 弹窗重截读图零回归（16/20 done 项 ☑+删除线+计入 x/y；30 `$ ` 头建立+尾滚放行；21/22 首帧齐备无变化；17/19/23/24 无回归）、`test_question_permission.py` 通过、存档帧探针断言三项全过（迟到 command 恰好提取一次/done→completed/未收录取值透传）；**真机复验**——`execute_20260812_094018.json` 重取证（迟到形态复现，新帧提取断言通过）、e2e A1/A2 复跑（A2 prompt 改为不指定英文 status 词，原始帧词表实证含 `"done"`，卡 2 甲项 ☑+删除线、副标题 1/4，截图 `todolist_a1/a2_20260812_094028.png` 读图吻合）；**回写**——0735 计划修订记录补 R2 标注遗留 ①② 闭环 |
