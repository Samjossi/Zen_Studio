> **状态**：待审阅（2026-08-03 09:59 创建，T1 帧取证已实证，未经任何代码改动）
> **范围**：`llm/providers/acp.py`（write 形态 diff 合成，唯一修复点）、
> `.temp/`（取证探针、复现/截图/冒烟脚本，不入库）
> **时间**：2026-08-03 09:59（创建，UTC+8）
> **优先级**：中（write 卡路径副标题已由 0919 计划带上屏，缺 +N −M
> 徽标与 hunk body；不影响正确性，影响信息一致性）

# Write 卡 diff 计数与 hunk body 补全计划

## 1. 背景

0919 计划闭环后用户实机核验：Write 卡标题行已见
「📝 Write 文档二.md · 测试文件夹/ ✔」——两段式路径副标题生效，
但 **+N −M 红绿徽标与展开后的 hunk body 缺失**（Edit 卡两者俱全）。

Write 与 Edit 在 kimi 系后端同归 `kind="edit"`、同走 DiffCard，
用户预期信息规格一致：新建文件就是「全量新增」，应见 `+N −0`
与全绿 hunk body。

本计划与 0919 同方法论：帧取证先行（已执行）、协议层单点修复、
MOCK 帧序列 + offscreen 截图 + 多模态核验 + 断言回归闭环。

## 2. T1 取证结论（2026-08-03 09:5x 实证，探针 `.temp/probe_write_frames.py`）

kimi 后端强制 Write 新建文件会话，全帧落盘
`.temp/write_frames_kimi.json`：

- **write 与 edit 同 kind**：Write 工具 `kind="edit"`、title 首帧
  "Write"、in_progress 帧 "Writing <路径>"——DiffCard 分派与标题
  恒定逻辑（0919 `_accept_title_update=False`）天然覆盖，无需改；
- **路径副标题已通**：in_progress 帧 `rawInput={path, content}`，
  0919 的 summary 迟到刷新命中 `rawInput.path`——用户截图已证实；
- **断点（唯一）**：write 的 in_progress 帧 content **无
  `type:"diff"` 项**（edit 有），只有 rawInput JSON 的文本快照——
  `_extract_diff` 拿不到 diff 项返回 None，徽标/body 无数据源；
- **可用原料**：`rawInput.content` 是写入**全文**（字符串）——
  新建文件语义的 diff 即 `oldText="" + newText=rawInput.content`，
  数据是后端真实给的，合成仅是真实数据的 diff 化重表达，
  **不违反「不臆造」纪律**（与 edit 的 difflib 管线同源同构）；
- 早先 in_progress 流式快照帧（无 kind/title/rawInput，content 为
  JSON 增量文本）维持路由丢弃语义不变（0919 已验）。

## 3. 修复方案（待拍板）

### 3.1 技术方案（协议层单点，`llm/providers/acp.py`）

`_map_tool_call_update` 现有 diff 盲提之后补一级**write 形态合成**：

```python
if diff := _extract_diff(update.get("content")):
    ...  # 既有（edit 形态）
elif write_diff := _extract_write_diff(update):
    payload["diff_stat"], payload["diff_hunks"], truncated = write_diff
    ...
```

新增 `_extract_write_diff(update)`：
- 条件收敛：`kind == "edit"` 且 `rawInput.content` 为字符串且
  `old_string`/`new_string` 不在 rawInput（edit 形态排除，防与
  edit 合成语义混淆）；
- 合成：构造伪 diff 项 `{"oldText": "", "newText": rawInput.content}`，
  **直接复用 `_extract_diff` 管线**（difflib hunk 化、+N −0 计数、
  软上限 1000 行保头截断、truncated 标记——零新增截断逻辑）；
- 空 content（写空文件）返回 None（无 diff 不臆造，退化为路径
  副标题纯标题行，与 edit 缺省同语义）。

`_map_tool_call` 首帧同样补一道（其他后端可能首帧带全量 rawInput，
通用防御；kimi 首帧空壳无 rawInput，自然 no-op）。

### 3.2 渲染层零改动

DiffCard 已消费 `diff_stat/diff_hunks`（0919 T3）——全 + 行三色
body 自然呈现全绿，`+N −0` 徽标自动上屏。两轨 fallback 文本
（`_tool_call_fallback`/`_tool_update_fallback`）的计数随行同样
自动受益。

### 3.3 任务分解

- **T2 协议层**：`_extract_write_diff` 新增 + `_map_tool_call_update` /
  `_map_tool_call` 各补一级 elif 合成（§3.1）；
- **T3 验证闭环**：
  1. MOCK write 帧序列（首帧空壳 → in_progress 带 rawInput.content
     全文无 diff 项 → completed 裸帧）offscreen 截图，多模态核验
     「Write 文档名 · 目录 +3 −0 ✔」+ 展开全绿 hunk；
  2. 协议层断言（`.temp/test_map_session_update.py`）：write 形态
     合成 +N −0、edit 形态不触发合成、空 content 返回 None、
     软上限截断；
  3. GUI 冒烟（`.temp/smoke_chat_cards.py`）：write 卡徽标 +N −0 +
     全绿 body + 双帧去重；既有 83 项全量复跑防回归；
  4. 真机收尾：用户实机对照。

## 4. 风险与取舍

- **R1 覆盖写语义偏差**：write 已存在文件时 rawInput.content 仍是
  新全文、旧内容不可得（后端不给）——合成 diff 恒为「全量新增」
  口径（+N −0），与真实增删有偏差。协议层不碰文件系统（R1 纪律：
  泛化层不臆造协议、渲染层只读），记为已知取舍写入实现注释；
- **R2 大文件 write**：全文合成 diff 成本 O(全文)，软上限 1000 行
  截断由 `_extract_diff` 既有管线兜底，超长置 truncated 尾注；
- **R3 edit 形态误判**：条件里显式排除 `old_string`/`new_string`
  键存在的情形，edit 语义不受合成路径污染；
- **R4 其他后端**：合成条件只看 rawInput 结构不看后端身份，
  kilocode/opencode 系若同构 rawInput 同样受益，无异构分支。

## 5. 验收标准

- 实机 Write 新建文件后，不展开卡片即可见：`文件名 · 目录 +N −0`
  红绿徽标；展开见全绿 hunk body（超长截断尾注）；
- 修前/修后 offscreen 截图成对存档 `.temp/`，多模态核验通过；
- 协议层 + GUI 冒烟新增断言全过，既有断言零回归；
- 降级形态：空 content / 无 rawInput 时退回纯标题行（不臆造）。
