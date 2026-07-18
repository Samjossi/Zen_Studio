# GUI 包说明

> **状态**：草稿
> **范围**：`gui/` 包 — Zen Studio 图形界面
> **时间**：2026-07-19 02:55（UTC+8）

---

## 1. 概述

`gui/` 存放 Zen Studio 全部图形界面代码，基于 **PySide6**。入口文件 [`main.py`](../main.py) 仅负责创建应用并显示主窗口，不包含任何界面实现。

## 2. 文件结构

| 文件 | 说明 |
|:---|:---|
| [`__init__.py`](__init__.py) | 包初始化，对外导出 `MainWindow` |
| [`main_window.py`](main_window.py) | 主窗口：三栏布局 + 菜单栏/状态栏 + 主题切换 |
| [`theme.py`](theme.py) | 主题加载：读 `config/settings.json` 应用 qss 与全局字体 |
| [`panels/__init__.py`](panels/__init__.py) | 面板包初始化，对外导出 `FileExplorer` |
| [`panels/file_explorer.py`](panels/file_explorer.py) | 文件树面板（移植自 PyGPT explorer 裁剪版） |
| [`panels/chat/`](panels/chat/) | 聊天面板子包（左栏）：`panel.py` 装配 / `output.py` 输出区 / `input.py` 输入框 / `model_bar.py` 模型版本行 / `worker.py` 流式线程 / `permission_dialog.py` ACP 工具审批对话框 |
| [`panels/viewer/`](panels/viewer/) | 文件查看面板子包（中栏上）：`panel.py` 装配 / `code_viewer.py` 只读查看器（行号栏）/ `highlighter.py` Pygments 高亮器 |

> LLM 调用层为后端逻辑，位于项目根 [`llm/`](../llm/)（与 `gui/` 平级）：`base.py` Protocol / `registry.py` 注册表 / `providers/kimi_cli.py`（stream-json 子进程）与 `providers/kimi_acp.py`（ACP 长驻）两个 Kimi Code CLI 后端。前端经 `from llm import get_llm` 消费。

## 3. 布局图

主窗口采用 `QSplitter` 嵌套布局：外层水平三栏，中栏内部垂直拆分为上下两部分，分隔条均可拖动调整：

```
┌────────────────────────── Zen Studio (1200×800) ──────────────────────────┐
│  菜单栏：文件 / 编辑 / 视图（噪音过滤开关、明暗主题切换）                      │
│ ┌──────────────┬──────────────────────────────────┬──────────────┐      │
│ │ ┌──────────┐ │                                  │              │      │
│ │ │ 输出区   │ │         中栏（上，550 px）        │              │      │
│ │ ├──────────┤ │                                  │              │      │
│ │ │ 输入区   │ ├──────────────────────────────────┤     右栏     │      │
│ │ └──────────┘ │  ▲ 可拖动分隔条 (垂直 QSplitter) ▼ │   文件树   │      │
│ │   左栏       │         中栏下（250 px）          │ FileExplorer│      │
│ │  ChatPanel   │                                  │              │      │
│ │  (320 px)    │                                  │              │      │
│ └──────────────┴──────────────────────────────────┴──────────────┘      │
│        ◀═══ 可拖动分隔条 (水平 QSplitter) ═══▶                             │
│  状态栏：就绪 / 主题切换提示                                                 │
└────────────────────────────────────────────────────────────────────────────┘
```

| 栏位 | 初始尺寸 | 当前内容 |
|:---|:---:|:---|
| 左栏 | 320 px | **AI 聊天面板 `ChatPanel`**（上输出下输入，本机 Kimi Code CLI） |
| 中栏（上） | 550 px（高） | **文件查看面板 `ViewerPanel`**（只读 + Pygments 高亮 + 行号栏） |
| 中栏下 | 250 px（高） | 占位面板 |
| 右栏 | 250 px | **文件树 `FileExplorer`**（根目录为项目根，双击文件经 `file_opened` 信号打开到查看器） |

## 4. 聊天面板（左栏）

`ChatPanel` 上输出（QTextBrowser）下输入（QTextEdit）垂直分栏，Enter 发送 / Shift+Enter 换行；流式调用放 `ChatWorker(QThread)` 后台线程，逐块信号上屏，UI 不冻结。输入区顶行内嵌 `ModelBar`（模型 + 版本双下拉）。对话统一经本机 agent CLI（Kimi Code CLI），代码库零 API KEY。

| 组件 | 说明 |
|:---|:---|
| `LanguageModel` Protocol | 统一接口 `chat(messages) -> Iterator[Chunk]`，与 UI 解耦 |
| `Chunk` | 流式块：`kind="text"` 正文 / `kind="reasoning"` 过程信息（思维链或工具调用摘要灰字展示） |
| `LLMRegistry` | 名称 → provider 注册表，`get_llm("kimi-cli" \| "kimi-acp")` 取实例 |
| `KimiCliLLM` | Kimi Code CLI 后端（spawn `-p` + stream-json）：消息粒度上屏、session_id 续接多轮、工具调用灰字摘要；⚠️ auto 权限下 agent 可在项目目录自主读写/执行 |
| `KimiAcpLLM` | Kimi ACP 后端（长驻 `kimi acp` + JSON-RPC）：**token 级流式**、思维链可见（`agent_thought_chunk`）、`session/new` 原生会话、`session/set_config_option` 会话内切模型 |
| `PermissionDialog` | ACP 工具审批模态框：工具名/参数摘要 + 选项按钮（允许一次/始终允许/拒绝）；reader 线程请求转 GUI 线程弹出，180s 无响应按拒绝兜底 |
| `ModelBar` | 输入区顶行双下拉：模型（Kimi CLI / Kimi ACP，不可用时禁用）+ 版本（模型别名联动刷新），切换下次请求生效，发送中锁定 |
| 多轮 | 历史由各后端会话管理；请求失败的用户消息不入历史，错误上屏不崩溃 |

## 5. 文件查看面板（中栏上）

`ViewerPanel`：标题行（路径 + 状态提示）+ `CodeViewer(QPlainTextEdit)`。**AI-first 定位：永久只读**（代码修改一律经 AI agent 落盘；`setReadOnly` 技术上可逆，不锁死）。选型依据：[`work options/2026-0719-0205_中栏代码显示与语法高亮选型报告.md`](../work%20options/2026-0719-0205_中栏代码显示与语法高亮选型报告.md)。

| 组件 | 说明 |
|:---|:---|
| `CodeViewer` | 只读、等宽字体、行号栏（lineNumberArea 经典模式）+ 当前行高亮（行号为人与 AI 的对话坐标系）、软换行（超出宽度的长行按单词边界折行、无空格长串硬断，行号保持逻辑行号） |
| `PygmentsHighlighter` | 整文档一次 lexing → 区间缓存，`highlightBlock` 按块二分取格式；`get_lexer_for_filename` 探测语言、未知回退纯文本；多行 token（块注释/多行字符串）天然正确；明暗双配色表随主题切换重建 |
| 外部修改自动重载 | `QFileSystemWatcher` 监视当前文件（**AI 写盘为主修改路径**），150ms 防抖重载、保留滚动位置、标题行提示"已重新加载"；文件被删显示占位 |
| 守卫 | >1 MB 截断并提示；二进制文件占位提示（不尝试解码上屏） |
| 接线 | `main_window`：`file_explorer.file_opened` → `viewer_panel.open_file`；主题切换 → `viewer_panel.apply_theme` |

## 6. 文件树面板（右栏）

`FileExplorer` 移植自 PyGPT explorer 的裁剪版，基于 `QTreeView + QFileSystemModel`（经 `NoiseFilterProxyModel` 排除式过滤噪音目录），无 `window` 式上帝对象依赖，可独立实例化。

| 功能 | 说明 |
|:---|:---|
| 浏览 | 目录展开/折叠（懒加载）、多选（Ctrl/Shift） |
| 信号 | `file_opened(str)` — 双击文件时发射绝对路径，已接 `ViewerPanel.open_file`（中栏上查看器） |
| 右键菜单 | 打开、在文件管理器中显示、新建文件、新建目录、重命名、删除（带确认） |
| 噪音过滤 | 默认隐藏 `__pycache__`/`.git`/`.venv`/`node_modules`，视图菜单可切换 |
| 已剔除 | 向量库索引、zip 打包、拖放剪贴板、qrc 图标（用系统图标） |

## 7. 运行方式

```bash
# 项目根目录执行
uv run main.py
```
