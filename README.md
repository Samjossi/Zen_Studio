# Zen Studio

> **状态**：已发布（v1.0）
> **范围**：项目总览 — 定位、特性、架构、运行与文档体系
> **时间**：2026-07-29 22:20（UTC+8）

**AI 优先的桌面 IDE**：以本机 agent CLI 为统一后端（零密钥），集 AI 聊天、多类型文件预览、文件树与真 PTY 终端于一体。基于 Python 3.12 + PySide6，Linux 桌面优先，可打包为 AppImage 分发。

---

## 1. 项目定位

Zen Studio 的设计哲学是 **AI-first**：代码修改一律经 AI agent 落盘，IDE 本体承担「看、问、跑」三件事——

- **看**：中栏文件查看器永久只读（文本 / 图片 / PDF / Markdown 渲染 / 音视频就地预览），行号栏为人与 AI 的对话坐标系；
- **问**：左栏 AI 聊天面板直连本机 agent CLI，文件树拖拽即插 `@路径` 引用；
- **跑**：中栏下真 PTY 多会话终端（ANSI 颜色 / 交互程序全可用）。

与 VS Code 类通用编辑器不同，本项目刻意**不做**文本编辑、不做插件市场、不做 `QWebEngineView` 级重量级渲染（四份预览选型一致否决），保持轻量。

## 2. 核心特性

| 特性 | 说明 |
|:---|:---|
| AI 聊天面板 | 多标签会话（上限 4，标签间 provider 完全隔离可并行）；token 级流式 + 思维链可见（ACP 后端）；发送/停止双态按钮；拖入文件插 `@相对路径` 引用；ACP 工具审批模态框（允许一次/始终允许/拒绝） |
| 统一 CLI 后端 | 对话统一经本机 agent CLI（OAuth 自管凭证），代码库**零 API KEY**；传输层统一为 ACP 长驻协议（Zed/JetBrains 同款集成方式），kimi / reasonix / OpenCode / Kilo Code 四后台可选 |
| 多类型文件预览 | 只读查看器五页分流：文本（Pygments 高亮 + 行号栏）/ 图片（位图 + 矢量 SVG + GIF 动图 + 棋盘格透明底）/ PDF（连续滚动 + 翻页缩放）/ Markdown（GFM 渲染，可「使用 Typora 打开」）/ 音视频（就地播放，双击即播） |
| 真 PTY 终端 | ptyprocess + pyte + 自绘字符网格；多会话 tab；OSC 动态标题；查找浮层；Ctrl+C/V 与文本选择反转适配 |
| Git 集成 | 文件树状态着色（M/U/D）、查看器差异徽标（`+a -b`）、右栏下变更面板（VS Code SCM 简化版）、状态栏差异统计；事件驱动刷新（窗口激活 / 外部重载 / 手动） |
| 多开工作区 | 一进程绑定一工作区根；「在新窗口打开文件夹」起独立进程，窗口状态按工作区哈希分别持久化（VS Code `workspaceStorage` 语义） |
| 主题体系 | 多主题注册表（云白/暖米/晴空/薄荷/暗色），自带思源黑体 / 更纱黑体双字体，主题令牌化治理 |
| 外部修改自动重载 | `QFileSystemWatcher` 监视当前文件（AI 写盘为主修改路径），150ms 防抖重载并保留滚动位置 |

## 3. 界面布局

```
┌────────────────────────── Zen Studio ──────────────────────────┐
│  菜单栏：文件 / 编辑 / 视图 / 终端 / 设置 / 帮助                 │
│ ┌──────────────┬───────────────────────────┬──────────────┐   │
│ │   AI 聊天    │   文件查看器（五页预览）   │   文件树     │   │
│ │  ChatTabs    │   ViewerPanel             │ FileExplorer │   │
│ │  （左栏）    ├───────────────────────────├──────────────┤   │
│ │              │   终端 TerminalPanel      │  Git 变更    │   │
│ │              │   （中栏下，多会话 PTY）  │ ChangesPanel │   │
│ └──────────────┴───────────────────────────┴──────────────┘   │
│  状态栏：就绪 / 主题提示 + 当前文件 Git 差异统计                 │
└─────────────────────────────────────────────────────────────────┘
```

详细布局与面板机制见 `gui/README.md`。

## 4. 技术栈与架构

| 层 | 技术 |
|:---|:---|
| GUI | PySide6 ≥ 6.11.1（Qt 6），预览能力全部来自 Qt 自带模块（`QGraphicsView` / `QtPdf` / `QTextBrowser.setMarkdown` / `QtMultimedia`），零新增第三方依赖 |
| 语法高亮 | Pygments（整文档 lexing + 区间缓存） |
| 终端 | ptyprocess（PTY）+ pyte（VT 语义层）+ 自绘 QWidget |
| LLM 调用 | `LanguageModel` Protocol 薄层，子进程对接本机 CLI（stream-json / ndjson JSON-RPC） |
| Git | subprocess 调系统 git CLI（零 Qt 依赖纯 Python 包） |
| 打包 | PyInstaller onedir → AppImage（唯一构建入口 `building/build_appimage.sh`） |
| 依赖与运行 | uv（`pyproject.toml` + `uv.lock`），Python ≥ 3.12 |

关键架构约束：

- **依赖方向单向**：`gui/`（前端）→ `llm/`（后端），`core/` 为零 Qt 依赖底层；包内不反向 import；
- **版本单一来源**：`core/version.py` 的 `APP_VERSION`（发版人工 +0.1），`pyproject.toml` 版本为其副本；
- **AI 友好代码协议**：全库遵循 `AI 友好代码协议-v1.2.0.md`（方法级拆分、docstring 决策留痕、禁止上帝对象）。

## 5. 目录结构

| 路径 | 说明 |
|:---|:---|
| `main.py` | 入口：参数解析（工作区根 / 自动截图）→ 主题 → 主窗口 |
| `gui/` | 全部图形界面代码（主窗口 / 菜单 / 五面板 / 主题 / 设置中心），详见 `gui/README.md` |
| `llm/` | LLM 调用薄层（`LanguageModel` Protocol + 两个 Kimi CLI 后端），详见 `llm/README.md` |
| `core/` | 底层设施：版本单一来源 / 路径解析 / Git 数据层 / 外部应用调起 |
| `assets/` | 主题 qss、自带字体（OFL）、Logo 成套件与候选池 |
| `building/` | PyInstaller spec、AppImage 构建脚本与产物 |
| `config/` | 运行时配置（settings / 最近项目 / 窗口状态，gitignored 数据） |
| `scripts/` | 工具脚本（Logo 渲染等） |
| `work charter/` `work plans/` `work options/` | 章程 / 计划 / 选型三级工作文档（当前批次已归档至 `文档/`） |
| `文档/` | 归档文档库：修改记录 / 选型记录 / 审计报告 / 理论依据 / 提示语句 |
| `参考代码/` | 三个参考项目（gitignored 隔离分发，仅供阅读借鉴） |

## 6. 快速开始

### 6.1 环境准备

```bash
# 需要 Python ≥ 3.12 与 uv；虚拟环境创建约定见 ai创建虚拟环境.md
uv sync
```

前置条件：本机已安装 Kimi Code CLI（≥ 0.2.0）并完成 `kimi login`（OAuth 凭证由 CLI 自管于 `~/.kimi-code/`，IDE 不接触）。

### 6.2 运行

```bash
# 开发态运行（缺省以项目根为工作区）
uv run main.py

# 指定工作区根
uv run main.py /path/to/workspace

# 界面走查自动截图（输出至 .tmp/）
uv run main.py --auto-screenshot --screenshot-interval 1
```

### 6.3 打包与运行产物

```bash
# 重新打包（AppImage 唯一构建入口）
bash building/build_appimage.sh

# 运行打包程序（AppImage）
building/dist/Zen_Studio-x86_64.AppImage

# 运行打包程序（onedir 中间产物）
building/dist/zen-studio/zen-studio
```

更多命令见 `常用命令.md`。

## 7. 配置

| 配置 | 位置 | 说明 |
|:---|:---|:---|
| 应用偏好 | `config/settings.json` | 主题 / 字号 / 噪音过滤 / 模型选择；多进程并发经 flock 文件锁 + 原子写治理 |
| 窗口状态 | `config/window_state/` | 按工作区哈希分文件持久化几何与分隔栏；`default.json` 供新工作区首开继承 |
| 最近项目 | `config/recent_projects.json` | 最近打开的工作区列表 |
| 模型目录 | 动态解析 | 可用模型别名经 `kimi provider list --json` 实时拉取，IDE 侧零硬编码 |

设置入口统一为菜单「设置 ▸ 设置中心…」（唯一偏好配置面）。

## 8. 安全模型（零密钥）

- 代码库**零密钥字面量、零密钥读取路径**：凭证由各 agent CLI 自行管理（kimi 为 OAuth）；
- agent 权限：ACP 后端支持工具审批四态（允许一次/始终允许/拒绝 + 设置中心默认档），agent 可在**项目目录**内读写文件与执行命令（CLI 静态 deny 规则生效）；
- `.gitignore` 保留 `api_key/` 条目防未来误存密钥；`参考代码/` 已隔离不分发。

## 9. 文档体系

项目采用「章程 → 选型 → 计划 → 修改记录」四级文档流，命名与引用规范见 `文档编写规范.md`：

| 文档 | 作用 |
|:---|:---|
| `AGENTS.md` | AI / 工程师操作约束（仅操作项目目录、强制 `.venv`、中文响应等） |
| `文档编写规范.md` | 文档命名、头部元信息、文件引用、符号约定（v3.0 强制约束型） |
| `AI 友好代码协议-v1.2.0.md` | 代码编写强制协议 |
| `ZZZ_接手必读_下一步要做的事.md` | 接手入口（⚠️ 撰写于 2026-07-25，其中图片/PDF/Markdown/音视频四项预览已于 2026-07-29 全部落地，实际状态以 `文档/修改记录/` 为准） |
| `gui/README.md` `llm/README.md` | 两大核心包的详细机制说明 |
| `文档/修改记录/` | 全部实施计划与诊断报告归档（时间戳命名） |
| `文档/选型记录/` | 技术选型论证归档 |

## 10. 许可证

🔴 根目录 `LICENSE` 文件待补（结论已确立：项目可整体采用 MIT，需附 NOTICE 致谢 PyGPT 移植片段与字体 OFL 核对，见 `文档/审计报告/` 下 theia 实质代码审计报告 §4.2）。在补入前，请勿对外分发本仓库。

---

*Zen Studio v1.0 | 文档撰写：2026-07-29 22:20 (UTC+8)*
