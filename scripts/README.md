# scripts/ — 工具脚本目录

> **状态**：已实施
> **范围**：`scripts/` — 开发辅助脚本（非运行时代码，不打包）
> **时间**：2026-07-31 01:30（UTC+8）；2026-09-09 修订（测试脚本收编至 `tests/`，新增门禁入口 `check.sh`）

---

## 1. 定位

项目维护用的**离线工具脚本**集中地。本目录代码不被 GUI/LLM 运行时 import，也不进 PyInstaller 产物；一律经 `uv run` 在开发态手动执行，强制使用项目 `.venv`（AGENTS.md 约束）。

> 测试脚本已于 2026-09-09 全部收编至 `tests/`（pytest 化，见
> `work plans/2026-0909-2244_测试门禁落地计划.md`），提交前门禁统一走
> `scripts/check.sh`（ruff + pytest）。

## 2. 脚本清单

| 脚本 | 用途 | 用法 |
|:---|:---|:---|
| `scripts/check.sh` | **提交前门禁唯一入口**：ruff check（E/F/W，排除 vendored）+ pytest `tests/`（offscreen） | `scripts/check.sh` |
| `scripts/render_logo.py` | Logo 栅格化：读 `assets/logo/正式logo/logo.svg` 母版，一次渲染八尺寸 PNG 落回同目录（幂等覆盖写；换标流程 = 改母版 → 重跑本脚本，禁止手改单件 PNG；QSvgRenderer/QImage 来自既有 PySide6，零新增依赖，offscreen 可跑） | `uv run scripts/render_logo.py` |
| `scripts/shot_tool_cards.py` | Kimi 工具卡 mock 截图全量回归（40+ 场景）：帧 → Kimi mapper → Kimi presenter → Kimi 工厂，产物 `.temp/card_shots/` 与 `manifest.md`；嵌套场景另归档 `文档/帧存档/` | `.venv/bin/python scripts/shot_tool_cards.py` |
| `scripts/shot_vendor_cards.py` | 分厂商代表卡片截图（Kimi/DSH/Local 显式各选本厂商 mapper、presenter、工厂）：Agent/AskUserQuestion/ReadMediaFile/TodoList、read_image/todo_write/subagent；每张断言卡片类来自目标厂商包，产物 `.temp/vendor_card_shots/`，不写文档目录 | `.venv/bin/python scripts/shot_vendor_cards.py [--vendor kimi\|dsh\|local\|all]` |
| `scripts/capture_tool_frames.py` | kimi 真机工具帧取证（0812-0735 计划 T3）：AcpConnection 直连 kimi，todolist/write/edit/execute 四场景各开独立 ACP 会话，全序列帧 + 审批载荷落盘 `文档/帧存档/<场景>_<时间戳>.json`（edit 场景自动铺底探针文件、write 场景自动清目标保证「新建」语义） | `.venv/bin/python scripts/capture_tool_frames.py [场景名...]` |

## 3. 纪律

- 新脚本必须在头部 docstring 写明：用途、用法、依赖来源（优先复用项目已有依赖，新增第三方依赖需先过选型）；
- 脚本产生的临时文件/缓存一律落项目根内 `./.temp/` 或 `./.tmp/`，严禁系统临时目录；
- 测试一律写进 `tests/` 由 pytest 收编，`scripts/` 不再新增 `test_` / `smoke_` 前缀脚本；
- 厂商专用脚本直接 import 目标厂商模块（如 `ide.llm.providers.kimi.updates`、`ide.gui.panels.chat.vendors.kimi.factory`）。`ide.llm` 顶层只导出稳定契约、ACP 公共类型与注册表 API，不再导出具体 provider 类或探测函数。
