# config/ — 运行时配置目录

> **状态**：已实施
> **范围**：`config/` — 应用偏好 / 窗口状态 / 最近项目的持久化数据
> **时间**：2026-07-31 01:30（UTC+8）

---

## 1. 定位

Zen Studio 的**开发态**运行时配置存放地。本目录内数据文件全部 **gitignored**（用户界面状态/偏好不入库），仅本 README 入库。

⚠️ 打包态（frozen/AppImage）不写本目录——用户数据改指 XDG 配置目录 `${XDG_CONFIG_HOME:-~/.config}/zen-studio/`（AppImage 为只读 squashfs，写解包目录必然失败），路径分流收口于 `core/paths.py`。

## 2. 文件结构

| 文件 | 说明 | 读写方 |
|:---|:---|:---|
| `config/settings.json` | 应用偏好：主题 / 字号 / 模型选择（`model_backend` + 按接口记忆的 `model_versions` 表）/ 审批默认档 / 启动模式（`startup_mode`：恢复上次项目 / 空白窗口） | `gui/settings.py` 统一入口 |
| `config/settings.lock` | `settings.json` 的 flock 文件锁（多开实例并发治理） | `gui/settings.py` |
| `config/recent_projects.json` | 最近打开的工作区根历史（文件菜单「最近打开的项目」数据源）+ `last_closed_root` 键（最后关闭的根，启动恢复数据源） | `gui/recent_projects.py` |
| `config/version.json` | 配置版本标记（内容为 `{"version": "1.0"}`；⚠️ 当前全库无代码读写，疑似早期遗留文件，待确认后清理） | —（暂无消费方） |
| `config/window_state/<hash8>.json` | 窗口几何与分隔栏状态，按工作区根哈希分文件（VS Code `workspaceStorage` 语义） | `gui/window_state.py` |
| `config/window_state/default.json` | 全局默认布局：最近关闭窗口双写、后写胜，供新工作区首开与空白窗口继承 | `gui/window_state.py` |
| `config/sessions/<hash8>.json` | 会话记录存档（各聊天标签文字对话），按工作区根哈希分文件 | `gui/panels/chat/session_store.py` |
| `config/sockets/<hash8>.sock` | 一窗一根占用登记套接字（QLocalServer 按工作区根哈希 listen；进程退出残留的陈旧套接字由下次启动探测自愈清理） | `gui/root_ownership.py` |

## 3. 并发与写盘纪律

- 多开实例（一进程一工作区根）共享 `settings.json`：一律经 `gui/settings.py` 的**「flock 文件锁串行化 + 读全量 → 合并 → 原子写」**路径，禁止任何模块直接 open 写本目录文件；
- 窗口状态与会话存档按工作区哈希分文件，各窗口只读写自己的 `<hash8>.json`（窗口状态外加关闭时双写 `default.json`），天然无跨进程竞争；同根多开已由「一窗一根」占用登记（`config/sockets/`）结构性消除（2026-08-31）；
- 写临时文件必须落项目内 `./.temp/` 或 `./.tmp/`（`config/.*.tmp` 已 gitignore），严禁系统临时目录（AGENTS.md 约束）。
