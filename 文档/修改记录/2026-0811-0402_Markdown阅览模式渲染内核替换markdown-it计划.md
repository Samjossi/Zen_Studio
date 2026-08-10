# Markdown 阅览模式渲染内核替换 markdown-it 计划

> **⚠️ 归档状态**：本文档撰写于 2026-0811-0402，记录当时的实施状态。
> 文档中提及的"已知问题"和"待办项"可能已在后续修复，请勿以本文档推断当前代码。

> **状态**：草稿
> **范围**：`gui/panels/viewer/markdown_view.py`（ViewerPanel Markdown 阅览页）、项目根 vendor 包 `markdown_it/` / `mdurl/`
> **时间**：2026-08-11 04:02（UTC+8，设计）；2026-08-11 04:08（按用户拍板「轻型简洁」精简）；2026-08-11 04:10（依赖路线改源码 vendor 融合）
> **优先级**：中

## 一、需求

用户对当前 Markdown **阅览模式**（源码/阅览双模式开关的默认态，见
`2026-0806-0327_Markdown阅览源码双模式滑块开关计划.md`）的渲染质量不满意，
要求引入参考代码 `参考代码/markdown-it-master/`（JS markdown-it）的相应能力
改造阅览模式。

约束：Python/PySide6 技术栈，不移植 JS 代码；采用官方 Python 移植版
**markdown-it-py**（MIT，与 JS 版 API/插件架构一一对应）替换 Qt 内建
`QTextDocument.setMarkdown` 渲染内核。**用户拍板：保持轻型简洁，不做复杂化。**

## 二、现状分析

- `gui/panels/viewer/markdown_view.py` MarkdownView（QTextBrowser 子类）：
  `open_markdown()` 读盘 → 1MB 截断守卫 → UTF-8 解码 →
  `document().setMarkdown(text, MarkdownDialectGitHub)` 渲染。
- Qt 内建 setMarkdown 痛点：GFM 不完整（复杂表格/任务列表粗糙）、
  样式可控性弱（标题阶梯/代码块排版受内建转换器制约）。
- 可保留外围机制（本计划**不动**）：相对资源解析（searchPaths + baseUrl +
  loadResource）、链接三分发（anchorClicked）、右键「全选」、选区带自绘、
  1MB 截断守卫、阅览/源码开关（panel.py 侧零改动——对外接口不变）。
- 聊天区 `gui/panels/chat/output.py` 不涉及。

## 三、方案设计（精简版）

### D1 渲染内核：markdown-it-py 源码 vendor 融合（不经 uv/pip）

- **依赖路线（用户拍板）**：源码先备份至 `参考代码/` 再融合进项目，
  **不走 uv add / pip 安装**——`pyproject.toml` / `uv.lock` 零改动。
- 备份快照（已于 2026-08-11 完成，浅克隆）：
  - `参考代码/markdown-it-py-master/`（executablebooks/markdown-it-py，
    commit `bff75ed`，2026-07-08）；
  - `参考代码/mdurl-master/`（executablebooks/mdurl，commit `524d2ed`，
    2025-09-24）——markdown-it-py 的**唯一运行依赖**（pyproject 声明
    `mdurl~=0.1`，用于链接 URL 规范化）。
- 融合方式：从两份快照各拷贝纯包目录至**项目根**——`markdown_it/` 与
  `mdurl/`（项目根在 sys.path，`main.py` 启动即可 `from markdown_it
  import MarkdownIt`，import 形态与 pip 安装完全一致，未来若切回 uv
  依赖消费代码零改动）。随包附 `LICENSE` / `LICENSE.markdown-it`（MIT）。
  仅拷包目录，不带上游 tests/docs/benchmarking。
- preset `gfm-like` 自带 CommonMark 全量 + 表格 + 删除线，
  **不启用 linkify**（免 linkify-it-py/uc-micro-py 第二依赖链）。
- 协议合规：两包均 MIT 许可，纯 Python，PyInstaller 按 import 链自动收编。
- 渲染调用形态：

```python
# gui/panels/viewer/markdown_view.py
from markdown_it import MarkdownIt

md = MarkdownIt("gfm-like")  # gfm-like 默认含 table/strikethrough
html = md.render(text)
self.setHtml(html)
```

### D2 任务列表：单规则渲染 ☑/☐（不引插件包）

- GFM 任务列表（`- [ ]` / `- [x]`）不引 mdit-py-plugins：用 markdown-it-py
  的 core ruler 加一条极简行内规则（约 20 行），匹配列表项开头的
  `[ ]`/`[x]` 标记，直接渲染为 Unicode `☐`/`☑` 文本（包 `<span>` 着色）。
- QTextBrowser 不支持 `<input>` 复选框，Unicode 字符是唯一轻量可行形态。

### D3 setHtml 外围兼容性（不变项确认）

- `setHtml` 同样走 loadResource + searchPaths + baseUrl：相对图片解析不变。
- `anchorClicked` 链接三分发不变；`#锚点` 页内跳转维持现状水平
  （不引 anchors 插件，标题无 id 则锚点不增强，可接受）。
- 性能：纯 Python 渲染 1MB 截断上限内可接受，探针附带计时（< 1s 级）。

### D4 样式表：适度扩展排版（Qt 富文本 CSS 子集内）

- `_build_stylesheet()` 从「仅配色」适度扩展：h1–h6 字号阶梯（相对关键字
  large/x-large，**不做动态 pt 计算**）、表格边框/表头底色、pre/code 底色、
  blockquote 缩进着色。色值全部沿用现有调色板令牌派生，不新增令牌。
- **代码块不做语法着色**：维持单色底块（现状风格），不引 Pygments——
  由此 `apply_theme()` 维持仅换样式表，**无需缓存原文、无需重渲染**，
  主题切换路径零改动。
- `refresh_font()` 机制不变（正文跟随 app 字号）。

### D5 对外接口不变

- MarkdownView 对外接口（`open_markdown()` / `truncated` /
  `file_link_clicked` / `apply_theme()` / `refresh_font()`）签名不变，
  `gui/panels/viewer/panel.py` 零改动；模块 docstring 注明本计划。

## 四、任务分解

- **T1** vendor 融合：从 `参考代码/markdown-it-py-master/markdown_it/`、
  `参考代码/mdurl-master/mdurl/` 拷贝包目录至项目根（附 LICENSE 文件）；
  离屏 import 冒烟 + gfm-like 渲染样例断言。
- **T2** 渲染内核替换：`open_markdown()` 解码后改走 `md.render()` +
  `setHtml()`；新增 `_build_renderer()`（D1 装配 + D2 任务列表规则）。
- **T3** 样式表扩展（D4）：`_build_stylesheet()` 排版样式表。
- **T4** 冒烟探针 `.temp/probe_md_it.py`（QT_QPA_PLATFORM=offscreen）：
  1. 样例 md（标题阶梯/表格/任务列表/围栏代码块/删除线/相对图片/
     工作区链接）渲染断言——HTML 含预期标签、任务列表出现 ☑/☐；
  2. 相对图片 loadResource 命中回归；
  3. 链接三分发回归（http/文件/锚点）；
  4. 1MB 截断守卫回归 + 渲染计时。
- **T5** 视觉验证（按 `视觉验证闭环开发指南.md`）：真实样例文档截图，
  核对标题/表格/代码块/任务列表观感，与替换前对比。
- **T6** 文档收尾：模块 docstring 注明本计划；本文档补实施记录、
  状态转「已实施」并移回 `文档/修改记录/`。

## 五、影响面与风险

| 项 | 说明 |
|---|---|
| 行为变更 | 阅览模式渲染观感变化（预期提升）；源码模式/开关/链接分发/右键菜单不变 |
| 依赖新增 | vendor 两包（markdown_it + mdurl，MIT、纯 Python）入项目根，打包体积增量 < 0.5MB 级；pyproject/uv.lock 零改动 |
| 风险 R1 | Qt 富文本 CSS 子集能力有限，部分排版意图（圆角/em 字号）不可达——D4 相对关键字方案，T5 视觉验证兜底 |
| 风险 R2 | 自写任务列表规则覆盖不全（如标记后多空格/嵌套列表项）——规则从简匹配，T4-1 断言主流形态，边角形态降级为原文显示 `[ ]` 可接受 |
| 风险 R3 | 大文件渲染耗时——1MB 截断上限内 T4-4 计时兜底 |
| 风险 R4 | vendor 快照与上游脱钩，后续升级需人工重拷——接受（备份快照与 LICENSE 随仓可查来源 commit） |

## 六、不做的事

- 不移植 JS 代码，不引入 QWebEngine。
- 不引入 mdit-py-plugins：无脚注、无标题锚点、无 front matter 隐藏
  （低频需求，YAML 头按普通文本显示可接受）。
- 不做代码块语法着色（不引 Pygments highlight 回调；`pygments` 依赖
  仅供 CodeViewer 现状使用）。
- 不做主题切换重渲染（无内联色需求，apply_theme 维持仅换样式表）。
- 不启用 linkify 裸 URL 自动链接（免依赖链；URL 文本显示可点击性维持现状）。
- 不改造聊天区 Markdown 渲染、源码模式、Typora 入口、查找浮层、选区带自绘。
- 不新增主题令牌。

## 七、审阅结论

- 2026-08-11 04:08 用户拍板：技术路线认可（markdown-it-py 轻型替换）；
  总原则**保持轻型简洁**——插件集、Pygments 语法着色、主题重渲染、
  linkify 全部砍掉，仅保留 gfm-like 内核 + 任务列表 ☑/☐ +
  排版样式表适度扩展。
- 2026-08-11 04:10 用户拍板：依赖路线改为**源码 vendor 融合**——
  markdown-it-py / mdurl 源码已备份至 `参考代码/markdown-it-py-master/`、
  `参考代码/mdurl-master/`，实施时从快照拷贝包目录入项目根，
  不经 uv/pip。
