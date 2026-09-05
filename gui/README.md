# GUI 包说明

> **状态**：已实施
> **范围**：`gui/` 包 — Zen Studio 图形界面
> **时间**：2026-07-20 05:49（UTC+8，创建）/ 2026-07-31 01:30（修订）

---

## 1. 概述

`gui/` 存放 Zen Studio 全部图形界面代码，基于 **PySide6**。入口文件 `main.py` 仅负责创建应用并显示主窗口，不包含任何界面实现。

## 2. 文件结构

| 文件 | 说明 |
|:---|:---|
| `gui/__init__.py` | 包初始化，对外导出 `MainWindow` |
| `gui/main_window.py` | 主窗口：三栏布局（右栏再上下拆分）+ 状态栏 + 窗口几何/分隔栏状态持久化 + Git 状态事件驱动刷新 + 菜单槽函数（打开文件夹/字号/恢复布局等） |
| `gui/controllers.py` | MainWindow 职责外移的控制器：`GitStatusController`（Git 服务编排/去抖/四面板扇出刷新）等组合成员，依赖构造注入 |
| `gui/settings.py` | 通用配置持久化：读写 `config/settings.json`，"读全量 → 合并 → 写回"统一入口（主题/窗口几何/分隔栏/模型选择/工作区根共用；模型版本为按接口记忆的 `model_versions` 表） |
| `gui/settings_dialog.py` | 设置中心对话框：左导航 + 右分页（页面注册表驱动），唯一偏好配置面；控件 change 即时持久化即时应用 |
| `gui/window_state.py` | 窗口状态持久化：读写 `config/window_state/<hash8>.json`（按工作区哈希分文件，`default.json` 供新工作区首开继承） |
| `gui/root_ownership.py` | 一窗一根占用登记与唤活协议：`QLocalServer` 按工作区根 listen（`config/sockets/<hash8>.sock`），命中已占用根发唤活消息并以退出码 3 退出；陈旧套接字探测自愈（2026-08-31） |
| `gui/recent_projects.py` | 最近打开的工作区根存取（`config/recent_projects.json`），文件菜单「最近打开的项目」子菜单消费；另管 `last_closed_root` 键（closeEvent 记录最后关闭的根，供启动恢复） |
| `gui/theme.py` | 主题体系：`THEME_META` 多主题注册表（云白/暖米/晴空/薄荷/暗色，按 light/dark 两族）+ qss 应用 + 自带双字体注册（思源黑体 / 更纱黑体）+ `GIT_STATUS_COLORS` Git 状态色表 |
| `gui/title_bar.py` | 自定义标题栏（无边框窗口）：Logo + 标题文字 + 最小化/最大化/关闭三按钮 |
| `gui/window_resize.py` | 无边框窗口八向边缘缩放热区（窗口级事件过滤器） |
| `gui/popups.py` | 弹出浮层透明化：消除矩形窗口套圆角内容的双框观感 |
| `gui/menus/` | 菜单栏子包：`registry.py` Action 注册表（`菜单.动作` 键名全局可寻址）/ `assembler.py` 装配器 / 每菜单一文件（file/edit/view/terminal/settings/help） |
| `gui/panels/__init__.py` | 面板包初始化，对外导出 `FileExplorer`、`ViewerPanel` |
| `gui/panels/find_bar.py` | 查找浮层组件：viewer 与 terminal 两面板共用的右上角悬浮查找条（搜索语义归宿主面板） |
| `gui/panels/welcome.py` | 空白窗口占位部件：`WelcomePanel`（文件树槽位：「未打开文件夹」+「打开文件夹…」按钮 + 最近打开项目快捷列表，条目只显项目名、悬停看全路径，点击就地填充）与 `PlaceholderPanel`（聊天槽位置灰占位，2026-08-31 一窗一根计划 D4；最近列表 2026-09-01 迭代） |
| `gui/panels/file_explorer/` | 文件树子包（右栏上）：`explorer.py` 主控件 / `model.py` 模型层（噪音过滤 + Git 状态着色）/ `actions.py` 右键菜单动作 |
| `gui/panels/changes/` | Git 变更面板子包（右栏下）：`panel.py` 已变更文件列表（状态着色 + 增减行数，VS Code SCM 简化版） |
| `gui/panels/chat/` | 聊天面板子包（左栏）：`tabs.py` 标签容器（选择状态层 + 上限 4 标签）/ `panel.py` 装配（含底行：模型选择按钮 + 发送/停止双态按钮）/ `output.py` 输出区（旧轨）/ `transcript.py` 卡片轨对话区视图（0645 计划新轨；0813-1919 计划 T3 父指针路由——带 `parent_tool_call_id` 的子代理内部帧委派父 SubagentCard 内嵌区）/ `cards.py` KiloCode 式卡片折叠组件族（SubagentCard 内嵌「子代理活动」区复用 make_tool_card 嵌套显示子代理内部工具卡，0813-1919 计划 T2）/ `input.py` 输入框（文件拖入 → `@路径` 引用）/ `model_bar.py` 模型选择三按钮（纯视图）/ `worker.py` 流式线程 / `permission_dialog.py` ACP 工具审批对话框 / `permission_queue.py` 多标签审批串行弹窗队列 |
| `gui/panels/viewer/` | 文件查看面板子包（中栏上，五页预览分流）：`panel.py` 装配（QStackedLayout 五页 + Git 差异徽标）/ `code_viewer.py` 只读文本查看器（行号栏）/ `highlighter.py` Pygments 高亮器 / `image_viewer.py` 图片页（位图 + SVG + GIF）/ `pdf_viewer.py` PDF 页（QPdfView 连续滚动）/ `markdown_view.py` Markdown 渲染页（GFM）/ `media_viewer.py` 音视频播放页 |
| `gui/panels/terminal/` | 终端面板子包（中栏下）：`panel.py` 装配 / `widget.py` 自绘终端控件 / `screen.py` pyte 语义层 / `session.py` PTY 会话 / `palette.py` ANSI 双主题色板 / `selection.py` 选区控制器（纯逻辑零 Qt 依赖）/ `agent_bridge.py` ACP terminal/* 反向能力 GUI 桥（AI tab，2026-08-17） |

> LLM 调用层为后端逻辑，位于项目根 `llm/`（与 `gui/` 平级）：`base.py` Protocol / `providers/` 下 kimi / reasonix / OpenCode / Kilo Code 四家 ACP 长驻后端（传输层统一为 ACP，kimi CLI `-p` 模式已于 2026-07-31 移除）。多标签改造后 provider 由每个 `ChatPanel` 自持（`ChatPanel._build_providers` 装配单点）。
>
> Git 数据层同理位于项目根 `core/git/`：`GitStatusService`（subprocess 调系统 git CLI，零 Qt 依赖纯 Python 包），GUI 侧经 `main_window` 注入各面板消费。

## 3. 布局图

主窗口采用 `QSplitter` 嵌套布局：外层水平三栏；中栏内部垂直拆分为上下两部分；右栏内部同样垂直拆分为文件树（上）与变更面板（下）。分隔条均可拖动调整，且窗口几何与四处分隔栏状态在关闭时持久化、启动时恢复：

```
┌────────────────────────── Zen Studio (1200×800) ──────────────────────────┐
│  菜单栏：文件 / 编辑 / 视图 / 终端 / 设置 / 帮助（gui/menus 装配，无快捷键） │
│ ┌──────────────┬──────────────────────────────────┬──────────────┐      │
│ │ ┌──────────┐ │                                  │  文件树      │      │
│ │ │ 输出区   │ │         中栏（上，550 px）        │ FileExplorer │      │
│ │ ├──────────┤ │                                  ├──────────────┤      │
│ │ │ 输入区   │ ├──────────────────────────────────┤ ▲ 垂直分隔条 ▼│      │
│ │ └──────────┘ │  ▲ 可拖动分隔条 (垂直 QSplitter) ▼ │  变更面板    │      │
│ │   左栏       │         中栏下（250 px）          │ ChangesPanel │      │
│ │  ChatPanel   │                                  │  (170 px)    │      │
│ │  (320 px)    │                                  │              │      │
│ └──────────────┴──────────────────────────────────┴──────────────┘      │
│        ◀═══ 可拖动分隔条 (水平 QSplitter) ═══▶                             │
│  状态栏：就绪 / 主题切换提示 + 右侧常驻当前文件 Git 差异统计                │
└────────────────────────────────────────────────────────────────────────────┘
```

| 栏位 | 初始尺寸 | 当前内容 |
|:---|:---:|:---|
| 左栏 | 320 px | **AI 聊天面板 `ChatTabs`**（多标签，上输出下输入，本机 agent CLI 四后台 ACP 可选） |
| 中栏（上） | 550 px（高） | **文件查看面板 `ViewerPanel`**（五页预览分流：文本/图片/PDF/Markdown/音视频，永久只读 + Git 差异徽标） |
| 中栏下 | 250 px（高） | **终端面板 `TerminalPanel`**（真 PTY：ptyprocess + pyte + 自绘 QWidget） |
| 右栏（上） | 340 px（高） | **文件树 `FileExplorer`**（根目录为项目根，双击文件经 `file_opened` 信号打开到查看器） |
| 右栏（下） | 170 px（高） | **Git 变更面板 `ChangesPanel`**（已变更文件列表：状态着色 + `+N` `-N` 行数） |

## 4. 菜单栏（`gui/menus/` 子包）

模块化菜单 + 全局 Action 注册表（方案选型见 `2026-0720-0433_菜单栏与设置体系方案选型.md`，实施计划见 `2026-0720-0510_菜单栏与设置体系实施计划.md`）。`MainWindow` 以 `MenuBar(self).setup()` 一行完成构建；**全部菜单项不绑定快捷键**（保持简单；文本控件 Qt 内建 Ctrl+C/V/A 属控件级行为，不在此列）。

| 机制 | 说明 |
|:---|:---|
| Action 注册表 | `ActionRegistry`（`dict[str, QAction]` 薄封装），键名 = `菜单.动作`（如 `view.terminal`、`appearance.theme.dark`）；任何模块经 `MainWindow.menus.get(key)` 按名取 action 改勾选态/启停 |
| 每菜单一文件 | `file_menu.py` / `edit_menu.py` / `view_menu.py` / `terminal_menu.py` / `settings_menu.py` / `help_menu.py`，签名统一 `build(menubar, ctx, actions)`（ctx = MainWindow）；新增顶层菜单 = 新文件 + 装配器登记，不触碰现有菜单 |
| 单选组 | `QActionGroup(exclusive)` + `setData()` 载荷单回调（主题组、AI 模型后端/版本组） |
| 面板显隐 | 单一入口法：`MainWindow.set_xxx_visible` 同步注册表勾选态与可见性，菜单勾选与面板头部「−」按钮汇入 |
| 启用态刷新 | 编辑（复制/全选）与终端（清屏/终止）菜单在 `aboutToShow` 按焦点控件能力/会话存活态即时刷新 |

菜单内容速览：**文件**（打开文件 / 在新窗口打开文件夹=多开进程 / 打开配置目录 / 退出）；**编辑**（复制 / 全选——转发焦点控件；查找——按焦点分发终端或查看器浮层）；**视图**（四面板显隐 / 噪音过滤 / 恢复默认布局 / Git 刷新 / 外观▸主题互斥组）；**终端**（新建 / 清屏 / 重开 / 终止，与头部按钮、右键菜单同一实现路径）；**设置**（设置中心…——唯一偏好配置面 / 打开配置文件 / 恢复默认设置，三项入口菜单）；**帮助**（关于）。

**一窗一根与空白新窗口**（2026-08-31，work plans/2026-0831-2350 计划）：一进程绑定一工作区根（启动参数 `uv run main.py [folder]` 注入，缺省回退项目根；`--blank` 起空白窗口不绑定目录），换根入口仍为 `subprocess.Popen` 起新进程，进程边界天然隔离文件树/终端/Git/agent cwd。**同一工作区根同时只允许一个窗口**：`main()` 经 `gui/root_ownership.py` 的 `QLocalServer` 按根占用登记（`config/sockets/<hash8>.sock`），任何入口（菜单 spawn / 命令行 / 双击图标）命中已占用根时唤活已有窗口（showNormal + raise + activateWindow，对齐 VS Code）后以退出码 3 退出，不再同根多开；崩溃残留的陈旧套接字由 connect 探测失败 → removeServer → 重试 listen 自愈。「新建窗口」改为空白窗口：文件树槽位欢迎占位、聊天槽位置灰占位、终端禁用不自动 spawn shell、布局读写走全局 `default.json`；空窗内经「打开文件夹」就地填充（复用换根关旧窗路径）。非默认工作区窗口标题标注根路径（空窗标题「未打开文件夹」）。共享配置并发治理：`settings.json` 经 flock 文件锁串行化"读-合并-写" + 原子写；窗口状态按工作区哈希分文件（`config/window_state/<hash8>.json`，2026-07-24 收编子目录对齐 VS Code `workspaceStorage/`），各窗口恢复各自几何；新工作区首开继承全局 `config/window_state/default.json`（最近关闭窗口布局，关闭时双写、后写胜，VS Code 语义）。

## 5. 聊天面板（左栏）

`ChatTabs` 标签容器（上限 4）：持有模型选择状态层（当前 backend/version 单一来源、统一写盘、阻断广播同步），下方每标签一个独立 `ChatPanel`。`ChatPanel` 上输出（QTextBrowser）下输入（QTextEdit）垂直分栏，输出/输入两区 Qt 内建 Ctrl+滚轮缩放字体均已禁用（2026-0806-0223，wheelEvent 守卫吞掉 Ctrl 滚轮事件，字号唯设置中心全局字号链管控）。输入区底行左端为模型选择三按钮（`ModelBar` 纯视图实例：后台 → 接口 → 模型三级下拉，按钮直显当前值短文本——接口剥后台前缀、模型别名取 `/` 末段，点击弹勾选菜单，全部标签共享同一选择，任一标签切换经 ChatTabs 广播到其余标签 UI 与全部 provider），右端为**发送/停止双态按钮**（空闲=发送且空文本禁用，busy=■ 停止直停本标签，Esc 同效）；Enter 发送 / Shift+Enter 换行；流式调用放 `ChatWorker(QThread)` 后台线程，逐块信号上屏，UI 不冻结。每标签**自持 provider 实例**（独立 `kimi acp` 连接，标签间完全隔离可并行）。对话统一经本机 agent CLI（kimi / reasonix / OpenCode / Kilo Code 四后台 ACP 可选），代码库零 API KEY。从文件树或系统文件管理器**拖入文件 → 落点插入 `@工作区相对路径 ` 引用**（纯文本透传，由后端 agent CLI 解析）；模型选择持久化到 `config/settings.json`。多标签审批经全局 `PermissionQueue` 串行弹窗；任一标签响应中即禁用全部标签选择按钮与设置中心模型页。

**标签全关与关闭异步化**（2026-07-22，文档/修改记录/2026-0722-1117）：标签可全部关闭——零标签时 `QStackedWidget` 切到占位页（提示 + 「新建会话」按钮），选择状态在 ChatTabs 状态层不随标签消失（新建标签注入恢复）；序号非全关不复用（防指代漂移），全关即重置回「会话 1」。关闭路径两段式：GUI 段毫秒级（`request_stop` + 断信号 + 起 daemon 清理线程），线程段先 `terminate`（杀 acp 进程并幂等注入死讯/错误帧，主动解封 worker 的 `next_update()`/`request()` 阻塞点）后 `worker.wait(3000)`，结束经 `QTimer.singleShot` 回 GUI 线程 `deleteLater`——关闭标签 GUI 零冻结，且不销毁运行中的 QThread。

| 组件 | 说明 |
|:---|:---|
| `LanguageModel` Protocol | 统一接口 `chat(messages) -> Iterator[Chunk]`，与 UI 解耦 |
| `Chunk` | 流式块：`kind="text"` 正文 / `kind="reasoning"` 过程信息（思维链或工具调用摘要灰字展示） |
| `KimiAcpLLM` | Kimi ACP 后端（长驻 `kimi acp` + JSON-RPC，kimi 后台唯一接口）：**token 级流式**、思维链可见（`agent_thought_chunk`）、`session/new` 原生会话、`session/set_config_option` 会话内切模型 |
| `PermissionDialog` | ACP 工具审批模态框：工具名/参数摘要 + 选项按钮（允许一次/始终允许/拒绝）；reader 线程请求转 GUI 线程弹出，180s 无响应按拒绝兜底 |
| `ModelBar` | 输入区底行左端三按钮（纯视图，每标签实例）：后台（CLI 产品分组）→ 接口（接入实现，不可用时禁用标「未检测到」）→ 模型（别名联动刷新），按钮**直显当前值短文本**（接口剥后台前缀 `Kimi ACP`→`ACP`、模型别名取 `/` 末段 `kimi-code/k3-256k`→`k3-256k`，宽度贴合文本），点击弹 InstantPopup 菜单——按内容加宽显示全文、当前项 ✓ 勾选；tooltip 三行全名链兜底；切换下次请求生效，任一标签发送中全标签锁定；选择与写盘归 ChatTabs 状态层（`model_backend` + 按接口记忆的 `model_versions` 表，2026-07-31 起，见 文档/修改记录/2026-0731-0052），启动时恢复 |
| 多轮 | 历史由各后端会话管理；请求失败的用户消息不入历史，错误上屏不崩溃 |

## 6. 文件查看面板（中栏上）

`ViewerPanel`：标题行（路径 + **Git 差异徽标** + 状态提示）+ `QStackedLayout` 五页预览分流（2026-07-29 全部落地，四项选型见 `文档/选型记录/`）。**AI-first 定位：永久只读**（代码修改一律经 AI agent 落盘；`setReadOnly` 技术上可逆，不锁死）。选型依据：`2026-0719-0205_中栏代码显示与语法高亮选型报告.md`。

| 页 | 文件类型 | 实现 |
|:---|:---|:---|
| 文本页 | 代码与纯文本 | `CodeViewer(QPlainTextEdit)` + Pygments 高亮 + 行号栏 |
| 图片页 | 位图 / SVG / GIF | `ImageViewer(QGraphicsView)`：fit/实际像素、滚轮缩放、拖拽平移、同目录循环翻页、GIF 动画、棋盘格透明底 |
| PDF 页 | `.pdf` | `PdfViewer`（QPdfView，QtPdf 内核）：MultiPage 连续滚动、缩放三件套、翻页 |
| Markdown 页 | `.md` / `.markdown` | `MarkdownView(QTextBrowser.setMarkdown)` GFM 渲染；右键菜单精简为单项「全选」（代码页同）；中栏不设 Typora 入口（右栏文件树右键有）；Qt 内建 Ctrl+滚轮缩放已禁用 |
| 音视频页 | 常见音视频格式 | `MediaViewer`（QtMultimedia）：就地播放，双击即播（播放状态机移植自 PyGPT） |

| 组件 | 说明 |
|:---|:---|
| `CodeViewer` | 只读、等宽字体、行号栏（lineNumberArea 经典模式）+ 当前行高亮（行号为人与 AI 的对话坐标系）、软换行（超出宽度的长行按单词边界折行、无空格长串硬断，行号保持逻辑行号）；Qt 内建 Ctrl+滚轮缩放字体已禁用（2026-0806-0223，与聊天区/Markdown 页同款 wheelEvent 守卫，字号唯全局字号链管控） |
| `PygmentsHighlighter` | 整文档一次 lexing → 区间缓存，`highlightBlock` 按块二分取格式；`get_lexer_for_filename` 探测语言、未知回退纯文本；多行 token（块注释/多行字符串）天然正确；明暗双配色表随主题切换重建 |
| 外部修改自动重载 | `QFileSystemWatcher` 监视当前文件（**AI 写盘为主修改路径**），150ms 防抖重载、保留滚动位置、标题行提示"已重新加载"；文件被删显示占位 + 标题行瞬时提示（3 秒自动消失，0813 计划：持续语义由占位状态承担；`open_file` 打开新文件时清除残留提示） |
| Git 差异徽标 | `set_git_service` 注入 `GitStatusService` 后，`open_file` 查询 numstat，标题行路径后追加 `+a -b` 徽标（无改动/非仓库不显示）；外部重载时发射 `externally_reloaded` 供主窗口联动刷新 Git 状态 |
| 守卫 | >1 MB 截断并提示；二进制文件占位提示（不尝试解码上屏） |
| 查找浮层 | 右上角悬浮（不占布局，同终端浮层形态）：当前文档搜索 + 命中高亮（经 `CodeViewer.set_search_highlights` 与当前行高亮合并上屏）+ 上一个/下一个；编辑菜单「查找」按焦点分发进入，Esc 关闭 |
| 接线 | `main_window`：`file_explorer.file_opened` → `viewer_panel.open_file`；主题切换 → `viewer_panel.apply_theme`；字号调整 → `viewer_panel.refresh_font` |

## 7. 终端面板（中栏下）

`TerminalPanel`：单行头部栏（**tab 区**＋固定操作组）+ `TerminalWidget` 自绘终端。**真 PTY 多会话终端**（ANSI 颜色/交互程序/`kimi login` 全可用）。AI-first 语境下为用户终端 **+ AI tab**（2026-08-17，ACP terminal/* 反向能力落地：支持该能力的 agent（kimi/reasonix）Bash 命令落到用户可见的 `🤖 <命令>` tab 执行，实时输出、可 Ctrl+C 干预、手关等价 kill+release；桥接层 `agent_bridge.py` 仅经面板公开方法操作，连接层派发见 `llm/providers/acp.py`）。OOP 五层单向依赖（详见 `2026-0719-0412_中栏下终端面板实施计划.md` §2）：

头部栏（阶段一/二重构，见 `文档/修改记录/2026-0719-0955_*.md`）：左起 tab 区（每会话一 tab：shell 名/OSC 动态标题＋行内 ×；`＋`新建；激活态下划线随主题）＋状态＋「清屏」（写 Ctrl+L，shell 自清）＋「−」隐藏（视图菜单可恢复）；固定高度随字号重算。终端区右键菜单承接重开/终止/关闭（Theia 功能分层）；`Ctrl+F` 查找为右上角浮层（不占布局）。

| 层 | 类 | 说明 |
|:---|:---|:---|
| 装配 | `TerminalPanel` | 多会话栈（每会话一套 PtySession+TerminalScreen，widget 重绑定切换）；tab/动态标题/右键菜单/查找浮层；字节流 → screen 喂入 → widget 刷新（唯一交汇点） |
| Qt | `TerminalWidget` | 自绘字符网格（同色合并绘制）+ 光标反显、按键 → VT100 静态映射、滚动条映回滚区、30ms 刷新节流；空会话占位绘制、查找命中高亮；Ctrl+F/右键只发信号（决策在 panel） |
| 语义 | `TerminalScreen` | pyte `HistoryScreen` 容错子类封装（私有 SGR 序列兜底 + 解析异常降级）；快照即纯数据（颜色用名字，主题切换免重算）；`title`（OSC 0/2）供 tab 动态标题；`plain_text()` 预留"终端内容喂 AI"出口 |
| I/O | `PtySession(QObject)` | `ptyprocess.spawn($SHELL)`（cwd 可注入：默认项目根，工作区切换后新会话用新根、已存在会话与重开保持原目录，`TERM=xterm-256color`）；reader 线程 → `data_received` 信号（GUI 线程零锁消费）；**进程代次守卫**（重开后旧代退出/数据信号作废）；`aboutToQuit` 置位防销毁期信号竞态；`terminate` 幂等 + atexit |
| 配色 | `AnsiPalette` | ANSI 16 色 × 明暗双主题；256 色 hex 串直接解析 |

## 8. 文件树面板（右栏上）

`FileExplorer` 移植自 PyGPT explorer 的裁剪版，基于 `QTreeView + QFileSystemModel`（经 `NoiseFilterProxyModel` 排除式过滤噪音目录），无 `window` 式上帝对象依赖，可独立实例化。

| 功能 | 说明 |
|:---|:---|
| 浏览 | 目录展开/折叠（懒加载）、多选（Ctrl/Shift） |
| 信号 | `file_opened(str)` — 双击文件时发射绝对路径，已接 `ViewerPanel.open_file`（中栏上查看器） |
| 右键菜单 | 打开、在文件管理器中显示、新建文件、新建目录、重命名、删除（带确认） |
| 噪音过滤 | 默认隐藏 `__pycache__`/`.git`/`.venv`/`node_modules`；持久化偏好（`noise_filter`），视图菜单与设置中心外观页双入口 |
| Git 状态着色 | 代理模型注入 `GitStatusService`，`ForegroundRole` 按文件状态查 `theme.GIT_STATUS_COLORS` 着色（天蓝 M / 绿 U / 红 D）；默认仅文件着色，目录聚合着色为预留开关 |
| 拖出 | 选中文件可拖出（`QDrag` + 本地 URL），落入聊天输入框即插入 `@相对路径` 引用 |
| 已剔除 | 向量库索引、zip 打包、拖放剪贴板（仅保留上述拖出）、qrc 图标（用系统图标） |

## 9. Git 集成（core/git + 变更面板）

Git 数据层 `core/git/` 为零 Qt 依赖的纯 Python 包（subprocess 调系统 git CLI；选型见 `2026-0720-0135_Git文件装饰与简易差异指示方案选型.md`）。`MainWindow` 持有唯一 `GitStatusService` 实例并注入各面板，**事件驱动刷新**：窗口激活 / 查看器外部重载 / 视图菜单手动刷新三个事件源，经 300ms 去抖汇流后一次刷新 → 文件树着色、查看器徽标、变更面板、状态栏统计四处同步；非 git 环境下所有入口静默跳过。

`ChangesPanel`（右栏下）：已变更文件列表，VS Code SCM 面板简化版（实施计划见 `2026-0720-0215_Git变更面板实施计划.md`）：

| 要点 | 说明 |
|:---|:---|
| 列表 | 文件名（按状态着色，已删除加删除线）｜绿 `+N`｜红 `-N`；增减两列按内容收紧贴右；未跟踪文件逐条列出（`status --untracked-files=all`） |
| 信号 | `file_opened(str)` 双击打开到查看器（绝对路径）/ `deleted_activated(str)` 删除行双击 → 状态栏提示 / `collapse_requested()` 头部「−」收起 |
| 头部栏 | 标题（含数量）+ 「−」收起按钮；显隐单一入口归主窗口（视图菜单勾选动作同步） |
| 空态 | 非 Git 仓库 / 无变更 显示占位行 |

## 10. 运行方式

```bash
# 项目根目录执行
uv run main.py
```
