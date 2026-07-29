> **状态**：已实施
> **范围**：中栏文件查看器（`gui/panels/viewer/`）Markdown 渲染预览 + 「使用 Typora 打开」外部跳转
> **时间**：2026-07-29 11:55（设计）(UTC+8)
> **实施**：2026-07-29 12:35（T1–T7 全部落地，见文末实施记录）(UTC+8)
> **优先级**：中
> **上游文档**：`../选型记录/2026-0725-2027_Markdown渲染预览功能选型.md`（选型已定夺，决策见 §8 决策记录）；架构先例：`2026-0729-1102_图片文件预览功能实施计划.md`、`2026-0729-1120_音视频播放功能实施计划.md`

# Markdown 渲染预览 + 使用 Typora 打开 实施计划

## 1. 决策锁定（2026-07-29 审阅定夺）

选型文档 §8 三项待决策项已全部拍板，另增补 Typora 外部跳转功能：

| 决策项 | 定夺 |
|:---|:---|
| 打开视图 | ✅ **`.md` 打开即渲染视图**：不做"源码 ↔ 渲染"双模式切换。本功能定位轻量预览，源码编辑/深度阅读交给 Typora 等专业软件 |
| 切换入口 | ✅ **取消**：无双模式，标题栏按钮/右键切换项均不做 |
| `.mdx` | ✅ **不启用**：工作区罕见，且含 JSX 语法 `setMarkdown` 无法正确渲染 |
| 渲染控件 | ✅ **方案 A：`QTextBrowser.setMarkdown`**（GFM 方言，零新增依赖） |
| Typora 打开 | ✅ **本期新增**：文件树右键菜单 + Markdown 渲染页右键菜单，双入口调起系统 Typora |

范围收敛后，本期交互目标：

- ✅ `.md`/`.markdown` 经 kind 分流直接进渲染页（GFM：表格/任务列表/删除线/围栏代码块）
- ✅ 相对路径图片/链接以 md 文件所在目录为基准解析
- ✅ 外部修改 watcher 去抖重载（Typora 保存后渲染页自动刷新，体验闭环）
- ✅ 明暗主题随 `apply_theme` 重建样式（不新增主题令牌）
- ✅ 「使用 Typora 打开」：文件树右键（`.md`/`.markdown` 限定）+ 渲染页右键，双入口
- ❌ 源码↔渲染双模式、`.mdx`、代码块语法高亮、Mermaid/数学公式

## 2. 协议合规边界（实施红线）

依据 `2026-0729-1028_theia实质代码审计与协议补全建议.md`：

1. **theia-zen（EPL-2.0）**：本期对其**零代码借鉴**——其"源码/预览双模式"已被决策排除，实施中不得翻阅、移植其 `markdown-preview-handler.ts`/`preview-widget.ts` 任何实现。
2. **VS_Code_Python / Multi_Cli_Studio（MIT）**：仅确认 `setMarkdown` 在 PySide6 可用这一事实与 GFM 支持范围；`setMarkdown` 是 Qt 内建 API，无代码移植需求，无版权声明义务。
3. 本计划产出代码为 Zen Studio 资产，整体随项目走 MIT。

## 3. 总体设计

### 3.1 架构形态（沿用图片/媒体计划的 QStackedLayout 分流先例）

`ViewerPanel._stack` 现有三页（文本 `CodeViewer` / 图片 `ImageViewer` / 媒体 `MediaViewer`），本期新增第四页：

| 页 | 控件 | 承载 |
|:---|:---|:---|
| Markdown 页（新增） | `MarkdownView`（新文件 `gui/panels/viewer/markdown_view.py`） | `.md` / `.markdown` 渲染预览 |

`open_file` 分流（插入在图片判断之后、文本流程之前）：

```python
# gui/panels/viewer/panel.py（open_file 分流示意）
if suffix in VIDEO_EXTS or suffix in AUDIO_EXTS:
    return self._open_media(p)
if suffix in IMAGE_EXTS:
    return self._open_image(p)
if suffix in MARKDOWN_EXTS:            # {"md", "markdown"}，不含点、全小写
    return self._open_markdown(p)
# 以下维持现有文本流程不变
```

- 行为变化声明：`.md` 原走文本页（CodeViewer + Pygments 高亮），本期起改走渲染页——**这是有意的行为变更**，非回归。
- 读取失败/解码失败仍回落文本页 `_show_placeholder`，现有占位/删除/二进制行为零回归。
- 离开媒体页红线不变：`_open_markdown` 内首先 `self.media_viewer.stop()`。

### 3.2 MarkdownView 控件设计（新文件 `gui/panels/viewer/markdown_view.py`）

`QTextBrowser` 派生，对外接口 `open_markdown(path) -> str | None`（返回错误原因字符串或 None，与 `ImageViewer.open_image` 同模式，供面板回落占位）：

| 能力 | 实现 |
|:---|:---|
| 渲染 | 读文件（沿用 `MAX_BYTES` 1MB 截断守卫，截断提示由面板标题行承接）→ `setMarkdown(text, MarkdownFeature.MarkdownDialectGitHub)` |
| 相对图片 | `setSearchPaths([md 所在目录])` + `document().setBaseUrl(QUrl.fromLocalFile(目录))`，相对路径图片就地显示 |
| 链接分发 | `setOpenLinks(False)` + `anchorClicked`：`http(s)`/`mailto` → `QDesktopServices.openUrl` 系统浏览器；相对 `.md` 等文件链接 → 发射 `file_link_clicked(str)` 信号，面板转 `open_file`（工作区内文档互跳）；`#锚点` → `scrollToAnchor` |
| 右键菜单 | 重写 `contextMenuEvent`：`createStandardContextMenu()` 经 `make_translucent_popup()` 后追加「使用 Typora 打开」（合规 `gui/popups.py` 规约：新建 QMenu 一律过透明化修复点） |
| 主题 | `apply_theme(palette)`：`document().setDefaultStyleSheet()` 重建 h1–h6/code/blockquote/table/hr 配色，色值取主题调色板现有令牌派生，**不新增令牌** |
| 字号 | `refresh_font()`：`document().setDefaultFont()` 随全局字号重建 |
| 大文件 | 超 1MB 截断渲染，标题行追加「（已截断：超过 1 MB）」（与文本页一致） |

### 3.3 ViewerPanel 改造点清单

| # | 改造 | 说明 |
|:--|:---|:---|
| 1 | `QStackedLayout` 增加第四页 `MarkdownView` | 卡片、标题行、外边距不动 |
| 2 | `open_file` 分流 + `_open_markdown` | 渲染页上屏、标题行更新、Git 徽标刷新、watcher 照常挂载；`MarkdownView.file_link_clicked` 接回 `open_file` |
| 3 | 查找浮层兼容 | 渲染页下 `show_find` 弱提示「Markdown 渲染页不支持查找」（FindBar 绑定 CodeViewer 文档，与图片/媒体页同模式降级） |
| 4 | `_show_placeholder` / `_reload` | 占位回落文本页不变；watcher 去抖重载经 `open_file` 自然重新分流、重新 `setMarkdown` |
| 5 | `apply_theme` / `refresh_font` 分发 | 渲染页走 `MarkdownView.apply_theme` / `refresh_font` |
| 6 | `_image_buttons` 可见性 | 进/出渲染页时隐藏图片按钮组（沿用现有各页切换惯例） |

### 3.4 「使用 Typora 打开」设计

**共享逻辑**（新模块 `core/external_apps.py`，纯函数、无 Qt 依赖，便于探针测试）：

```python
def find_typora() -> str | None: ...
    # Linux/Windows：shutil.which("typora")
    # macOS 回退：/Applications/Typora.app 存在则返回 "open" 调用形态
def open_in_typora(path: str) -> str | None: ...
    # 返回 None 成功 / 错误原因字符串
    # subprocess.Popen(..., stdout=DEVNULL, stderr=DEVNULL) 非阻塞，
    # 不用 os.system / run，不管子进程生命周期
```

**入口与降级**：

| 入口 | 位置 | 行为 |
|:---|:---|:---|
| 文件树右键 | `gui/panels/file_explorer/actions.py` `open_context_menu`，插于「打开」之后 | 仅当选中项为 `.md`/`.markdown` **文件**且 `find_typora()` 命中时显示；点击调 `open_in_typora`，失败 `QMessageBox.critical`（沿用该文件既有错误提示风格） |
| 渲染页右键 | `MarkdownView.contextMenuEvent` | 同样以 `find_typora()` 命中为显示前提；失败经面板 `_show_hint` 弱提示 |

- **未安装 Typora 时菜单项直接隐藏**（保持菜单干净，不置灰占位）。
- **体验闭环**：Typora 保存 → 现有 `QFileSystemWatcher` 去抖重载 → 渲染页自动刷新（零新增代码）。

## 4. 任务清单

| # | 任务 | 产出 | 依赖 | 优先级 |
|:--|:---|:---|:---|:---|
| T1 | 新建 `core/external_apps.py`：`find_typora` / `open_in_typora`（跨平台探测 + Popen 非阻塞调起） | 共享逻辑，独立可测 | — | 高 |
| T2 | 新建 `gui/panels/viewer/markdown_view.py`：`QTextBrowser` 派生 + GFM `setMarkdown` + 相对资源解析（searchPaths/baseUrl）+ 链接分发（外部/工作区内/锚点）+ `file_link_clicked` 信号 | 新控件，独立可用 | — | 高 |
| T3 | MarkdownView 右键菜单：`createStandardContextMenu` 经 `make_translucent_popup` + 追加「使用 Typora 打开」（`find_typora` 命中才显示） | 入口 2 | T1、T2 | 高 |
| T4 | MarkdownView 主题与字号：`setDefaultStyleSheet` 重建元素配色（现有令牌派生，零新增令牌）+ `refresh_font` | 主题适配 | T2 | 中 |
| T5 | `ViewerPanel` 改造（§3.3 全部 6 项）：分流、`_open_markdown`、查找降级、重载/占位、主题/字号分发、按钮组可见性 | 面板集成 | T2 | 高 |
| T6 | 文件树右键入口：`ExplorerActions.open_context_menu` 插「使用 Typora 打开」（md 文件 + 探测命中限定） | 入口 1 | T1 | 高 |
| T7 | 验证（见 §5） | 验收 | 全部 | 高 |

**预计工期**：单实施会话可完成（控件自写量小，面板/菜单改造点明确，Typora 调用为纯标准库逻辑）。

## 5. 验证清单

### 5.1 自动化

- 现有 smoke 套件与 `check_compliance` / `check_theme_tokens` 全过（零回归）。
- 新增 headless 探针 `.tmp/probe_markdown_view.py`（按项目惯例不入库）：
  - 分流断言：`.md`/`.markdown` 进渲染页、`.mdx` 仍进文本页、文本/图片/媒体分流零回归；
  - GFM 渲染断言：含表格/任务列表/删除线/围栏代码块的 md 经 `setMarkdown` 后 `toHtml()` 含对应元素；
  - 相对资源：`setSearchPaths`/`baseUrl` 指向 md 所在目录；
  - 链接分发：mock `QDesktopServices.openUrl` 与 `file_link_clicked`，三类链接（http/相对 md/锚点）各走各道；
  - Typora：monkeypatch `shutil.which` 有/无两态，断言菜单项显隐与 `Popen` 调用参数（不真正启动 Typora）。

### 5.2 目检走查（实机）

| 场景 | 验收点 |
|:---|:---|
| `AGENTS.md`、各 `README.md`、`work plans/` 下计划文档 | 标题/列表/表格/任务列表/代码块/引用/水平线渲染正常 |
| 含相对路径图片的 md | 图片就地显示；md 在子目录时相对基准正确 |
| 含链接的 md | http 链接跳系统浏览器；相对 `.md` 链接在工作区内跳转；页内锚点定位 |
| 文件树右键 `.md` | 显示「使用 Typora 打开」，点击后 Typora 启动且 Zen Studio 不卡死 |
| 渲染页右键 | 标准菜单（复制/全选）之上含「使用 Typora 打开」，菜单背景透明化无白底 |
| 未安装 Typora 环境（或 PATH 摘除模拟） | 两入口菜单项均不显示 |
| Typora 中编辑保存当前 md | watcher 去抖重载，渲染页自动刷新 + 「已重新加载」提示 |
| 渲染页下打开查找 | 弱提示「Markdown 渲染页不支持查找」，不弹浮层 |
| 明暗主题切换 | 标题/代码块/引用块/表格配色随主题，无残色 |
| 超 1MB md | 截断渲染 + 标题行截断提示 |
| 切文本/图片/媒体/Markdown | 页面切换正确，Git 徽标、标题行、占位行为零回归；媒体页切走停播红线不破 |

## 6. 明确不做（防范围蔓延）

- 源码 ↔ 渲染双模式切换（决策已否，见选型 §8）。
- `.mdx` 支持（决策已否）。
- 代码块语法高亮、Mermaid、LaTeX 数学（Qt 富文本上限，见选型 §6；未来有需求再评估，当前过度设计）。
- 渲染页内查找（FindBar 绑定 CodeViewer 文档，渲染页降级弱提示；若未来需要再立项）。
- PDF/音视频之外的其他文件类型预览——各自独立选型立项；本期 `QStackedLayout` 四页架构已验证可水平扩展。
- 「使用其他编辑器打开」可配置化——本期硬编码 Typora；多编辑器需求出现时再演进为设置项。

---

## 7. 实施记录（2026-07-29 12:35）

**T1–T7 全部落地。**

**产出**：
- 新增 `core/external_apps.py`（约 90 行）：`TyporaLauncher` 类封装跨平台
  探测与调起——Linux/Windows 走 `shutil.which("typora")`，macOS 回退
  `/Applications/Typora.app` + `open -a`；`which`/`popen`/`platform` 全部
  构造注入（探针传假实现，不启动真实 Typora）；Popen 非阻塞 + DEVNULL；
  模块级 `default_launcher` 共享实例。
- 新增 `gui/panels/viewer/markdown_view.py`（约 180 行）：`MarkdownView`
  （`QTextBrowser` 派生）——`document().setMarkdown(text, GFM 方言)` 渲染；
  `MARKDOWN_EXTS = {"md", "markdown"}`；相对资源 `setSearchPaths` +
  `baseUrl`（尾随斜杠目录语义）；链接三分发（http/mailto→系统浏览器、
  相对文件→`file_link_clicked` 信号、`#锚点`→`setSource` 页内跳转）；
  右键菜单构建/弹出分离（`_build_context_menu`）；`apply_theme` 重建文档
  样式表（h 系/code/blockquote/table/hr，色值取 `text`/`muted_text`/
  `accent`/`border`/`chrome.line_number_bg` 现有令牌，零新增令牌）；
  `refresh_font` 跟随 app 字号；1MB 截断守卫（截断场景宽容解码防切断
  UTF-8 多字节字符）；`truncated` 属性供面板标题行提示。
- `gui/panels/viewer/panel.py` 改造（§3.3 六项全落地）：`QStackedLayout`
  第四页、`.md`/`.markdown` 分流 `_open_markdown`（读取/解码失败回落占位、
  媒体停播红线保持）、查找降级弱提示「Markdown 渲染页不支持查找」、
  `apply_theme`/`refresh_font` 分发、`file_link_clicked` 接回 `open_file`、
  `typora_failed` 接弱提示。
- `gui/panels/file_explorer/actions.py`：`ExplorerActions` 新增 `typora`
  构造注入；菜单装配拆出 `_assemble_menu`（构建/弹出分离），「使用
  Typora 打开」插于「打开」之后（md 文件 + 探测命中限定），调起失败
  `QMessageBox.critical`。
- 探针：`.tmp/probe_markdown_view.py`（37 项断言）、
  `.tmp/walkthrough_markdown_view.py`（双主题实渲截图走查）。

**偏差与落定**：
- ①PySide6 6.11 的 `QTextBrowser.setMarkdown` 绑定仅单参、且
  `Qt.MarkdownFeature` 不在 `Qt` 命名空间——改走
  `document().setMarkdown(text, QTextDocument.MarkdownFeature.MarkdownDialectGitHub)`。
- ②相对链接解析初版 `base.resolved(url)` 把目录末段当文件段替换——
  baseUrl/resolve 基准统一加尾随斜杠（目录语义）。
- ③1MB 截断可切断 UTF-8 多字节字符致整篇解码失败回落占位——截断场景
  改 `errors="ignore"` 宽容解码（未截断仍严格解码以识别二进制伪装）。
- ④Qt 任务列表渲染产物是 `<li class="checked"/"unchecked">`（CSS marker
  ☐/☑），探针断言按此校准。
- ⑤PySide6 的 C++ 方法（`QMenu.exec`）类级 monkeypatch 不生效（真实弹
  菜单离屏卡死）——菜单一律重构为**构建/弹出分离**（`_build_context_menu`/
  `_assemble_menu`），探针直接断言装配产物，不打桩 Qt 内部方法。
- ⑥`FileExplorer` 未加 `typora` 透传参数（注入点收敛在 `ExplorerActions`，
  探针直接装配动作集），生产面零膨胀。

**验证**：探针 37/37 全过（TyporaLauncher 两态+macOS 回退+OSError、
分流/.mdx 不启用/二进制回落/截断、GFM 表格/任务列表/删除线/围栏代码、
searchPaths/baseUrl、链接三分发、查找降级、四主题+字号、两入口菜单项
三态显隐）；`check_theme_tokens` 全过；主窗口四主题冒烟 4/4；图片探针、
媒体探针回归全过；双主题实渲截图读图核实（表格边框/删除线/标题层级/
引用弱化渲染正确）。离屏冒烟 stderr 的终端线程 RuntimeError 为预存在
拆除竞态噪声（同图片计划实施记录结论），与本改动无关。

**合规**：theia-zen 全程零接触；`setMarkdown` 为 Qt 内建 API，无移植；
两新文件 docstring 已注明借鉴边界。

**遗留**（实机走查为准）：真实 Typora 进程的调起手感（探针全部 mock）、
相对图片实机显示效果、Typora 保存后 watcher 重载闭环的实机确认。

---

*撰写于 2026-07-29 11:55 (UTC+8) | 依据：`../选型记录/2026-0725-2027_Markdown渲染预览功能选型.md`（决策已定夺）+ 2026-07-29 审阅增补 Typora 打开功能 | 合规基线：`../审计报告/2026-0729-1028_theia实质代码审计与协议补全建议.md`*
