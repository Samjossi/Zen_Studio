# GUI 包说明

> **状态**：草稿
> **范围**：`gui/` 包 — Zen Studio 图形界面
> **时间**：2026-07-18 07:42（设计，UTC+8）

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
| [`panels/chat/`](panels/chat/) | 聊天面板子包（左栏）：`panel.py` 装配 / `output.py` 输出区 / `input.py` 输入框 / `model_bar.py` 模型版本行 / `worker.py` 流式线程 |

> LLM 调用层为后端逻辑，位于项目根 [`llm/`](../llm/)（与 `gui/` 平级）：`base.py` Protocol / `registry.py` 注册表 / `providers/deepseek.py` DeepSeek 直连。前端经 `from llm import get_llm` 消费。

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
| 左栏 | 320 px | **AI 聊天面板 `ChatPanel`**（上输出下输入，DeepSeek 流式） |
| 中栏（上） | 550 px（高） | 占位面板 |
| 中栏下 | 250 px（高） | 占位面板 |
| 右栏 | 250 px | **文件树 `FileExplorer`**（根目录为项目根，双击文件发射 `file_opened` 信号） |

## 4. 聊天面板（左栏）

`ChatPanel` 上输出（QTextBrowser）下输入（QTextEdit）垂直分栏，Enter 发送 / Shift+Enter 换行；流式调用放 `ChatWorker(QThread)` 后台线程，逐块信号上屏，UI 不冻结。输入区顶行内嵌 `ModelBar`（模型标签 + 版本下拉框），显示当前模型名+版本并可切换。

| 组件 | 说明 |
|:---|:---|
| `LanguageModel` Protocol | 统一接口 `chat(messages) -> Iterator[Chunk]`，与 UI 解耦 |
| `Chunk` | 流式块：`kind="text"` 正文 / `kind="reasoning"` 思维链（仅当次显示，不回传 API） |
| `LLMRegistry` | 名称 → provider 注册表，`get_llm("deepseek")` 取实例 |
| `DeepSeekLLM` | openai SDK 直连 DeepSeek，双版本（`deepseek-chat` V3.2 通用 / `deepseek-reasoner` V3.2 思考）；`set_model()` 切换，密钥仅从 `api_key/deepseek` 文件读取（取首个 `sk-` 行） |
| `ModelBar` | 输入区顶行：显示 `DeepSeek · V3.2 通用（deepseek-chat）` 格式（`label_for()` 单点维护），下拉切换版本（下次请求生效），发送中锁定 |
| 思考块渲染 | reasoner 思维链以灰字斜体（QTextCharFormat）实时上屏，与正文空行分隔 |
| 多轮 | 对话历史随请求发送（仅正文）；请求失败的用户消息不入历史，错误上屏不崩溃 |

## 5. 文件树面板（右栏）

`FileExplorer` 移植自 PyGPT explorer 的裁剪版，基于 `QTreeView + QFileSystemModel`（经 `NoiseFilterProxyModel` 排除式过滤噪音目录），无 `window` 式上帝对象依赖，可独立实例化。

| 功能 | 说明 |
|:---|:---|
| 浏览 | 目录展开/折叠（懒加载）、多选（Ctrl/Shift） |
| 信号 | `file_opened(str)` — 双击文件时发射绝对路径，供编辑器接入 |
| 右键菜单 | 打开、在文件管理器中显示、新建文件、新建目录、重命名、删除（带确认） |
| 噪音过滤 | 默认隐藏 `__pycache__`/`.git`/`.venv`/`node_modules`，视图菜单可切换 |
| 已剔除 | 向量库索引、zip 打包、拖放剪贴板、qrc 图标（用系统图标） |

## 6. 运行方式

```bash
# 项目根目录执行
uv run main.py
```
