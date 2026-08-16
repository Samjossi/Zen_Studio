# scripts/ — 工具脚本目录

> **状态**：已实施
> **范围**：`scripts/` — 开发辅助脚本（非运行时代码，不打包）
> **时间**：2026-07-31 01:30（UTC+8）

---

## 1. 定位

项目维护用的**离线工具脚本**集中地。本目录代码不被 GUI/LLM 运行时 import，也不进 PyInstaller 产物；一律经 `uv run` 在开发态手动执行，强制使用项目 `.venv`（AGENTS.md 约束）。

## 2. 脚本清单

| 脚本 | 用途 | 用法 |
|:---|:---|:---|
| `scripts/render_logo.py` | Logo 栅格化：读 `assets/logo/logo.svg` 母版，一次渲染八尺寸 PNG 落回同目录（幂等覆盖写；换标流程 = 改母版 → 重跑本脚本，禁止手改单件 PNG；QSvgRenderer/QImage 来自既有 PySide6，零新增依赖，offscreen 可跑） | `uv run scripts/render_logo.py` |
| `scripts/test_git_dir_status.py` | `GitStatusService` 目录聚合（`_dir_status` / `status_of_dir`）单元测试：不依赖真实 git/仓库，注入 `_status` 后断言 `_build_dir_status()` | `uv run python scripts/test_git_dir_status.py` |
| `scripts/capture_tool_frames.py` | kimi 真机工具帧取证（0812-0735 计划 T3）：AcpConnection 直连 kimi，todolist/write/edit/execute 四场景各开独立 ACP 会话，全序列帧 + 审批载荷落盘 `文档/帧存档/<场景>_<时间戳>.json`（edit 场景自动铺底探针文件、write 场景自动清目标保证「新建」语义） | `.venv/bin/python scripts/capture_tool_frames.py [场景名...]` |
| `scripts/test_follow_lock.py` | 对话区跟随锁（2317 计划 T6）双轨行为断言：offscreen 实例化旧轨 ChatOutput/新轨 ChatTranscriptView，以 setValue 模拟用户手势，断言「贴底跟随 / 上翻解锁 / 解锁不下拉 / 回底恢复 / 回底钮 / 发消息强制回底」六项 | `QT_QPA_PLATFORM=offscreen PYTHONPATH=. .venv/bin/python scripts/test_follow_lock.py` |

## 3. 纪律

- 新脚本必须在头部 docstring 写明：用途、用法、依赖来源（优先复用项目已有依赖，新增第三方依赖需先过选型）；
- 脚本产生的临时文件/缓存一律落项目根内 `./.temp/` 或 `./.tmp/`，严禁系统临时目录；
- 冒烟测试类脚本命名以 `test_` / `smoke_` 前缀区别于一劳永逸的工具脚本。
