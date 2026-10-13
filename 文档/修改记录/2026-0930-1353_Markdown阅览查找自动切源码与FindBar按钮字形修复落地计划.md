# Markdown 阅览模式查找自动切源码 + FindBar 按钮字形修复落地计划

> **状态**：已实施
> **范围**：`src/gui/panels/viewer/panel.py`、`assets/themes/base.qss`、`tests/test_viewer_find_shortcut.py`
> **时间**：2026-09-30 13:53（设计）/ 2026-09-30 13:57（修订：用户拍板两项待定决策）/ 2026-09-30 14:10（实施）/ 2026-09-30 14:17（附录：备选字形备查）(UTC+8)
> **优先级**：中
> **前置文档**：
> - `work plans/2026-0930-1314_文件树与查看面板Ctrl+F查找快捷键落地计划.md`（查看面板 Ctrl+F 入口）
> - `文档/修改记录/2026-0806-0327_Markdown阅览源码双模式滑块开关计划.md`（双模式机制，已归档）

## 1. 需求/背景

两个查找体验问题（用户实机截图反馈）：

1. **Markdown 阅览模式下查找被拒之门外**：.md 文件默认进渲染页，`show_find` 对渲染页只弹弱提示「Markdown 渲染页不支持查找」，用户须手动拨标题行的阅览/源码开关再查找。需求：阅览模式下触发查找（Ctrl+F 或菜单「查找」）时**自动切到源码模式并直接打开查找浮层**，省去手动操作。
2. **查找浮层三个按钮（↑/↓/×）看不见**：用户判断为「浅色背景下按钮文字是白色」。经离屏探针实证（见 §2），真实病根不是颜色，而是**字形被整体裁剪**，且六个主题（含深色）全部空白、终端面板同病。

## 2. 现状梳理（含实证结论）

### 2.1 Markdown 双模式与查找降级

- 双模式机制（0327 计划）：标题行 `_md_switch`（ToggleSwitch，仅 md 页可见），关=阅览（`markdown_view` 渲染页）/ 开=源码（复用文本页只读 CodeViewer）；切换槽 `_on_md_mode_toggled`，无公开 set 接口，经 `_md_switch.setChecked()` 驱动。
- `show_find`（`src/gui/panels/viewer/panel.py`）按当前页逐页降级：图片/媒体/**Markdown 渲染页**/PDF 各弹弱提示后 return。
- 关键既有设计收益：源码模式下当前页变为 CodeViewer，降级判定天然不命中，查找自动恢复可用——因此「切源码 → 开浮层」链路零新机制。
- 0327 计划已定：源码模式下查找浮层自动恢复可用。本计划只是把「用户手动切」变成「触发查找时自动切」。

### 2.2 FindBar 按钮空白的真实病根（离屏探针实证）

- FindBar（`src/gui/panels/find_bar.py`）被查看面板与终端面板共用；三按钮为裸 `QPushButton`，代码侧 `setFixedSize(24, 22)`。
- 全仓 qss 无 `#FindBar` 选择器；按钮吃 `assets/themes/base.qss` 全局规则 `QPushButton { padding: 3px 11px; … }`。
- **病根**：内容盒宽 = 24 − 2×11 = 2px，↑↓× 字形被水平内边距整体裁掉。探针对照实验：同按钮 `padding: 0` 或放大尺寸，字形立即出现；浅色（cloud）与深色（graphite）主题下均为空白——与用户「白色文字」的假设不符，不是颜色问题。
- 次要风险（探针发现）：本机字体回退链可能把 ↑ 落进 Noto Color Emoji 以彩色位图渲染，修复 padding 后字形颜色未必服从主题文字色，实施时须一并目验，必要时换字形（如 ▲▼ 或 ‹ ›）。

### 2.3 主题体系

单模板 `assets/themes/base.qss`（`$token` 占位）经 `theme.py render_theme()` 按六主题令牌渲染；浅色四主题（cloud 云白/wheat 暖米/sky 晴空/mint 薄荷）、深色两主题（graphite 石墨/midnight 深夜），无独立 qss 文件。

## 3. 方案设计

### 3.1 改进一：阅览模式查找自动切源码

- 改动点唯一：`show_find` 中 Markdown 渲染页分支，由「弱提示后 return」改为：
  1. `_md_switch.setChecked(True)`——复用既有切换槽完成源码上屏（`_on_md_mode_toggled` 同步执行，含 1MB 截断/UTF-8 守卫全套）；
  2. 不设 return，流程自然下落到浮层打开段（此时当前页已是 CodeViewer，后续页判定不命中）。
- 守卫：仅当 `_md_switch_box` 可见（确为 md 文件的双模式场景）才自动切；异常情况（渲染页残留但开关不可见）维持原弱提示，不新增行为。
- 菜单「查找」分发路径（`find_focused` → `show_find`）与 Ctrl+F 路径共用同一入口，自动切对两条路径同时生效，零额外改动。
- 弱提示（用户已拍板）：切换后 `_show_hint("已切换到源码模式")` 一条，告知用户界面为何变化（3s 自动消失，无打扰）。

### 3.2 改进二：FindBar 按钮字形修复

- 修复：`assets/themes/base.qss` 追加作用域规则 `#FindBar QPushButton { padding: 0; }`（不动全局 QPushButton 规则，零外溢）。
- 字形预案（用户已拍板）：T4 目验若确认 ↑↓ 落 emoji 字体、颜色异常，字形替换为 ▲/▼/×（几何字符走文本字体、服从 pen 色，用户认可为恰当方案），改动落 `find_bar.py` 按钮文本单点，不扩散改动面。
- 终端查找浮层共用同一组件，一修两愈；终端为深底，修复后同样受益。

### 改动清单

1. `src/gui/panels/viewer/panel.py`：`show_find` Markdown 渲染页分支改为自动切源码 + 下落开浮层（加开关可见守卫）。
2. `assets/themes/base.qss`：追加 `#FindBar QPushButton { padding: 0; }`。
3. 测试：查看器测试新增自动切源码用例（见 T2）；FindBar 按钮渲染属视觉项，走视觉验证（见 T4）。

### 明确不做

- 不改图片/媒体/PDF 页的降级弱提示（无源码模式可切，维持现状）。
- 不新增公开的「设置 Markdown 模式」接口（自动切换经 `_md_switch.setChecked` 复用既有槽即可）。
- 不动全局 `QPushButton` 规则与其他按钮。
- 不做 FindBar 按钮颜色专项调整——病根是裁剪不是颜色；若 T4 目验发现 emoji 字形颜色异常，按 3.2 的字形替换预案处理并记录微调。

## 4. 实施任务

| 编号 | 任务 | 验收 |
| --- | --- | --- |
| T1 | `show_find` Markdown 渲染页分支：开关可见时 `setChecked(True)` 自动切源码并下落开浮层，随后 `_show_hint("已切换到源码模式")`；不可见维持弱提示 | 阅览模式触发查找 → 自动进源码模式、浮层打开且可搜到文档内容、弱提示弹出；开关不可见异常态仍弱提示 |
| T2 | 查看器测试新增用例：打开 .md 进阅览模式 → 模拟 Ctrl+F → 断言开关已切源码、浮层可见、输入检索词有命中；菜单路径（直接调 `show_find`）同效回归 | 全部通过，offscreen 运行，临时数据落 `.temp/` 内 |
| T3 | `base.qss` 追加 `#FindBar QPushButton { padding: 0; }` | lint/qss 选择器守卫脚本（`scripts/check_qss_selector_guard.py`）通过 |
| T4 | 视觉验证（按 `视觉验证闭环开发指南`）：离屏探针截图六主题下查看面板+终端两处 FindBar，确认 ↑↓× 字形可见、颜色服从主题；若 emoji 字形异常按预案换 ▲/▼/× 后复验 | 六主题 × 两面板的截图存档 `.temp/` 并逐张目验通过 |
| T5 | 回归：`bash scripts/check.sh` 全绿 | 全绿 |

## 5. 影响面评估

- 改进一仅动 `show_find` 一个分支，切换逻辑 100% 复用既有槽；非 md 文件路径零变化。
- 改进二仅追加一条 scoped qss 规则，选择器带 `#FindBar` 锚定，不影响其他 QPushButton。
- 两改进互相独立，可分别回退。
- 用户认知影响：阅览模式按查找会改变页面形态（渲染 → 源码），符合需求意图；弱提示可降低困惑。

## 6. 回退

```
git checkout -- src/gui/panels/viewer/panel.py assets/themes/base.qss tests/
```


## 7. 实施微调说明与视觉验证留痕

- 实施与方案无偏差。T1 额外补一道防线：`setChecked(True)` 后若源码读取失败（槽内已提示），当前页仍是渲染页，此时直接 return 不开浮层，避免对陈旧文本页误搜。
- T2 测试落在 `tests/test_viewer_find_shortcut.py`（Ctrl+F 路径 + 菜单路径两用例，共用断言辅助）。
- T4 视觉验证：探针 `.temp/probe_findbar_1353/probe.py` 输出六主题 × 查看/终端两面板共 12 张截图（同目录 `viewer_*.png` / `terminal_*.png`），逐张目验通过——↑↓× 字形全部可见，颜色服从主题（浅底深字/深底浅字），无 emoji 彩色字形问题，**▲/▼/× 预案未启用**。
- T5 门禁：隐私扫描 ✔、lint ✔、113 项测试全过（111 既有 + 2 新增）。


## 8. 附：上下按钮备选字形（未来换款备查）

现用字形为箭头式 ↑/↓（配 × 关闭）。若未来想换三角款式，候选（上/下成对）：

| 款式 | 字形（上/下） | 说明 |
| --- | --- | --- |
| 空心大三角 | △ ▽ | 轮廓款，视觉最轻 |
| 实心大三角 | ▲ ▼ | 原预案款，最醒目 |
| 实心小三角 | ▴ ▾ | 小尺寸实心，紧凑 |
| 空心小三角 | ▵ ▿ | 小尺寸空心，最秀气 |

改动点唯一：`src/gui/panels/find_bar.py` 中 `prev_button`/`next_button` 的文本（单点替换，x 关闭钮不动）。选定款式后宜按 T4 同款探针六主题复验一遍字形渲染。

> **已换款（2026-09-30）**：先后试「空心大三角 △▽」（用户目验嫌丑）后定款「实心小三角 ▴▾」（× 不动），同步更新 `find_bar.py` 内三处字形注释；T4 同款探针复跑六主题 × 两面板 12 张截图，逐张目验通过（字形可见、颜色服从主题），`check.sh` 全绿（113 项测试）。
