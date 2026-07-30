# core/ — 底层设施包

> **状态**：已实施
> **范围**：`core/` 包 — 版本 / 路径 / Git 数据层 / 外部应用调起
> **时间**：2026-07-31 01:30（UTC+8）

---

## 1. 定位

`core/` 是 Zen Studio 的**零 Qt 依赖底层设施包**，被 `gui/`（前端）与 `llm/`（后端）两方消费。架构红线：**依赖方向单向**（`gui/` → `llm/`，两者 → `core/`），`core/` 不 import 任何 GUI/LLM 代码，也不引入 PySide6——保持纯 Python 可单测。

## 2. 文件结构

| 文件 | 说明 |
|:---|:---|
| `core/__init__.py` | 包初始化 |
| `core/version.py` | **版本加载器**：单一来源本体为 `config/version.json`（发版人工 +0.1，数据文件承载不硬编码），本模块导入时读取并导出 `APP_VERSION`（缺失/损坏回退兜底并告警）；`pyproject.toml` 的 version 为其副本，发版同步手改；消费方一律 import，禁止散落写死、禁止自行读文件 |
| `core/paths.py` | **项目级路径常量唯一推导点**：全库其余模块一律 import，禁止手写 `parents[N]` 推导；PyInstaller frozen 兼容（`sys._MEIPASS` 检测），打包态用户数据写 XDG 配置目录（`${XDG_CONFIG_HOME:-~/.config}/zen-studio/`），开发态维持项目内 `config/` |
| `core/external_apps.py` | 外部应用程序调起：「使用 Typora 打开」的探测与启动共享逻辑（OOP 封装 + 依赖注入，探针/进程创建构造注入可假依赖单测；`subprocess.Popen` 非阻塞，禁止 `os.system` 阻塞 UI） |
| `core/git/` | Git 数据层子包（见 §3） |

## 3. Git 数据层（`core/git/`）

subprocess 调系统 git CLI 的纯 Python 包（选型：`2026-0720-0135_Git文件装饰与简易差异指示方案选型.md` 方案 A）。GUI 侧由 `MainWindow` 持有唯一 `GitStatusService` 实例注入各面板消费（事件驱动刷新，详见 `gui/README.md` §9）。

| 文件 | 说明 |
|:---|:---|
| `core/git/runner.py` | git CLI 调用封装：超时保护、异常静默、环境检测；失败一律返回 None/False 静默降级（无 git 环境属正常场景而非故障） |
| `core/git/status.py` | `git status --porcelain=v1 -z` 解析 → {相对路径: 状态}（`-z` 格式规避中文/空格路径转义问题） |
| `core/git/numstat.py` | `git diff --numstat -z HEAD` 解析 → {相对路径: (新增行数, 删除行数)} |
| `core/git/service.py` | `GitStatusService` 数据层门面：状态/统计查询聚合 + 结果缓存 + 环境降级 |

## 4. 纪律

- ❌ 禁止在 `core/` 引入 PySide6 或任何 GUI 依赖；
- ❌ 禁止在 `core/` 外手写项目根 / assets / config 路径推导（一律走 `core/paths.py`）；
- ❌ 禁止在 `core/` 外写死版本号字面量或自读版本文件（一律经 `core/version.py` 的 `APP_VERSION`）。
