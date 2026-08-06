# ReadMediaFile 入参图片略缩图计划

> **状态**：草稿
> **范围**：`llm/providers/acp.py`（协议层载荷提取）、`llm/base.py`（载荷契约）、`gui/panels/chat/cards.py`（渲染层）、`scripts/shot_tool_cards.py`（可视化验证）
> **时间**：2026-08-07 01:58（设计，UTC+8）
> **优先级**：中（可读性增强，不阻塞功能）
> **前序**：`work plans/2026-0806-1712_工具调用卡片渲染修复计划.md`（T1 入参迟到回填、T4 BodyHtml 出参图片、T5 工具名分派表，本计划直接复用三项机制）

---

## 1. 背景与症状

ReadMediaFile 卡片当前只显示入参路径文本（`path: .tmp/diag_xxx_基线.png`），**人类看不到 AI 正在读的图片内容**。影响：

| # | 痛点 | 场景证据 |
|:---:|:---|:---|
| S1 | **无法即时判断 AI 读取的对象是否正确**：人需凭路径自行找到图片文件、点开查看，才能核对 AI 读的是不是该读的那张 | 截图：ReadMediaFile 卡入参仅一行路径文本 |
| S2 | **多图分次读取场景认知负担翻倍**：AI 逐张读取并要求逐张确认时，人每张都要"路径→找文件→打开→核对"走一遍 | 截图：用户要求"分次读取，同意了才能读下一张" |

诉求：**入参指向的图片以略缩图形式内嵌在卡片里，仅供人类查看**（不回传 AI、不改协议语义）。

---

## 2. 可行性分析（复用既有资产）

本需求的三块积木在 0806 计划中已全部建成，本计划只做组装：

| 积木 | 既有机制 | 位置 |
|:---|:---|:---|
| 路径载荷 | T1 入参迟到回填（`_input_detail_seen` 簿记 + update 帧回填通道），kimi 首帧空壳场景已覆盖 | `llm/providers/acp.py` |
| 图片渲染 | T4 出参区 BodyHtml（QTextBrowser）+ `<img src="file://..." width="...">` 内嵌、width 硬限防撑破 | `gui/panels/chat/cards.py` McpCard |
| 工具名分派 | T5 `_TOOL_NAME_CARDS` 字典注册式二级分派（此前裁决"ReadMediaFile 无需专用卡"，本需求改变该裁决） | `gui/panels/chat/cards.py` |

另：用户气泡图片回显（`gui/panels/chat/transcript.py`）已实证 `file://` 本地图片在 Qt 原生渲染链路的可行性。

---

## 3. 修复方案

按"协议层 → 渲染层"推进，共三个任务。

### T1：协议层 `media_path` 结构化载荷（`llm/providers/acp.py` + `llm/base.py`）

**改动点 1 — 提取**：`_map_tool_call` 与 `_map_tool_call_update` 中，当工具名归一化后为 `readmediafile`（或 rawInput 携带 `path`/`filePath` 且值指向图片扩展名）时，将路径原值装入载荷：

```
payload["media_path"] = <rawInput.path 或 rawInput.filePath 原值>
```

- 判定口径：工具名命中优先；扩展名白名单兜底（`.png .jpg .jpeg .gif .bmp .webp`），非图片路径不装填（ReadMediaFile 也可读非图片，无图可缩）。
- **迟到回填复用 T1 簿记**：首帧空壳（kimi 系）时 `media_path` 随 update 帧与 `input_detail` 同频回填，同一 `_input_detail_seen` 账本，不新增簿记。
- 纪律不变：路径原样下传（相对路径不解析），GUI 不碰 rawInput。

**改动点 2 — 契约**（`llm/base.py`）：`ToolCallPayload` 与 `ToolUpdatePayload` 增补 `media_path: str` 可选字段，docstring 注明"入参图片本地路径（略缩图数据源，仅供人类查看，不回传）"。

### T2：渲染层 `MediaReadCard` 专用卡（`gui/panels/chat/cards.py`）

0806 计划"ReadMediaFile 复用 McpCard"的裁决随本需求撤销，新建 `MediaReadCard(McpCard)`：

| 设计点 | 决策 |
|:---|:---|
| 分派 | `_TOOL_NAME_CARDS` 增补 `"readmediafile": MediaReadCard`（归一化机制既有） |
| 继承 | 继承 McpCard（入参/出参两节、T4 出参图片渲染原样保留），只增略缩图区 |
| 略缩图位置 | 入参区 path 文本**下方**，入参与出参之间（阅读顺序：路径 → 缩略图 → 出参） |
| 载体 | 复用 BodyHtml，`<img src="file://<绝对路径>" width="320">`；width 硬限 320px（出参图 480px 的 2/3——略缩图定位是"辨认"不是"细读"） |
| 路径解析 | 相对路径按项目工作目录解析为绝对路径再拼 `file://`（截图实证 kimi 下发的是 `.tmp/...` 相对路径）；解析后文件不存在 → 不渲染略缩图（path 文本仍在，静默降级） |
| 回填钩子 | 重写 `_set_input_detail`：父类回填 path 文本后，若载荷带 `media_path` 且略缩图未渲染则补渲（幂等，首帧优先不覆盖） |
| 大小护栏 | 文件 >10MB 不内嵌（QTextBrowser 全量加载，大图拖慢滚动；width 只限显示不限加载），留一行 `（图片过大，未生成略缩图）` 占位 |

### T3：mock 验证场景扩充（`scripts/shot_tool_cards.py`）

既有基建复用：`_make_asset_png`（`.temp/` 落盘真实 PNG，场景 01 已用）、
首帧空壳 → update 帧迟到回填的帧序形态（场景 01 蓝本）。新场景沿用
现有 `NN_` 数字前缀命名风格：

| 场景 | 构造 | 截图看点 |
|:---|:---|:---|
| `NN_readmedia_入参略缩图` | `_make_asset_png` 落盘，首帧带 `media_path` | 入参 path 文本下方显示略缩图 |
| `NN_readmedia_迟到回填略缩图` | 首帧空壳 → in_progress 帧带 media_path 回填（场景 01 帧序复用） | 略缩图随后到载荷补渲，不重复不缺失 |
| `NN_readmedia_非图片入参` | media_path 指向 `.txt` | 无略缩图，path 文本正常 |
| `NN_readmedia_路径不存在` | media_path 指向已删除文件 | 静默降级，无破图占位 |

闭环纪律同 0806 计划 §5：`.venv/bin/python scripts/shot_tool_cards.py` → 查看 `.temp/card_shots/*.png` → 对照打勾。

---

## 4. 验收标准

| # | 验收项 | 预期 | 验证方式 |
|:---:|:---|:---|:---|
| A1 | kimi 后端 ReadMediaFile 读取 `.tmp/` 下图片 | 卡片入参区 path 文本下方显示该图略缩图，人类无需打开文件即可辨认 | 真机截图 |
| A2 | 首帧空壳时序（kimi 典型） | 略缩图随 update 帧补渲出现 | 真机 + mock |
| A3 | 读取非图片文件（文本/代码） | 无略缩图，卡片表现与现状一致 | mock |
| A4 | 分次读取 4 张图（截图场景复现） | 每张卡各有对应略缩图，逐张确认流程认知负担消除 | 真机截图 |
| A5 | 略缩图不进入任何回传通道 | 协议层仅新增展示侧载荷，无 agent 方向数据流 | 代码审查 |

---

## 5. 风险与注意事项

| 符号 | 项 | 说明 |
|:---:|:---|:---|
| ⚠️ | **相对路径基准目录** | kimi 下发相对路径（`.tmp/...`）的基准是 agent 工作目录还是 IDE 项目根需实证；两目录通常一致，不一致时略缩图静默缺失（降级方向安全），真机 A1 验证时确认 |
| ⚠️ | ~~**截图中出参 base64 裸漏**~~（已澄清，2026-08-07 复核） | 疑点不成立：`acp.py:241-242` 出参识别为 `block.get("image_url") or block.get("imageUrl")` 双键名兼容，camelCase `imageUrl` 已覆盖；mock 场景 `01_readmedia_kimi_首帧空壳` 即以 camelCase 构造并实证"无 base64 原文裸露"。截图中裸漏若为真机现象，需另案核查（非键名失配方向） |
| ⚠️ | **大图加载性能** | QTextBrowser 内嵌为全量解码，width 属性只限显示；10MB 护栏 + 多卡堆叠场景需真机观察滚动流畅度 |
| 🟡 | **略缩图与出参图并存** | ReadMediaFile 出参（T4 修好后）也会渲染同一张图——入参略缩图（320px 辨认用）与出参图（480px 结果用）并存是刻意的：入参图回答"AI 要读什么"，出参图回答"AI 读到了什么"，两者语义不同不去重 |
| 🟡 | **其他读图类工具** | 后续若出现别的按路径读图的工具（MCP 形态），扩展名白名单 + 工具名表集中维护即可纳入，不新增机制 |
| 🟡 | **协议文档同步** | `media_path` 载荷字段补充进 `dream-acp/protocol/dream-acp-v1.md`（同前序计划纪律） |

---

## 6. 实施顺序

1. **T1**（载荷）——协议层提取 + 契约，无 UI 依赖。
2. **T2**（MediaReadCard）——承接 T1 载荷落地渲染。
3. **T3**（mock 场景）——四场景截图闭环（camelCase 疑点已在 §5 澄清，无需顺带实证）。

T1+T2 完成后即可真机跑 A1/A4（截图场景复现），T3 作回归基建固化。
