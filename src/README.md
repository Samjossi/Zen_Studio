# src 源码目录

> **状态**：已实施
> **范围**：源码目录边界、IDE 自有包、内置第三方依赖与增量模块位置
> **时间**：2026-11-10 06:48（UTC+8）

## 1. 目录结构

```text
# src/
src/
├── README.md
├── ide/
│   ├── __init__.py
│   ├── core/
│   ├── gui/
│   └── llm/
├── markdown_it/
└── mdurl/
```

`src/` 是 Python 模块搜索根，不是业务包。开发入口、测试和辅助脚本将本目录加入模块搜索路径后，IDE 代码统一经 `ide.core`、`ide.gui`、`ide.llm` 导入。

## 2. IDE 自有代码

`ide/` 是 Zen Studio IDE 产品本体的唯一命名空间。只有确属 IDE 的代码才放入其中：

| 包 | 职责 |
|:---|:---|
| `ide/core/` | 零 Qt 依赖的底层设施：路径、版本、Git 数据层、子进程环境与外部应用调起 |
| `ide/gui/` | 基于 PySide6 的图形界面、面板、菜单、主题与持久化接线 |
| `ide/llm/` | 本机 agent CLI 的统一调用层、ACP 连接与后端注册表 |

依赖方向保持单向：`ide.gui` 可以调用 `ide.llm`，两者都可以调用 `ide.core`；`ide.core` 不得反向依赖 GUI 或 LLM，`ide.llm` 不得导入 GUI。

详细机制见 `src/ide/core/README.md`、`src/ide/gui/README.md`、`src/ide/llm/README.md`。

## 3. 内置第三方依赖

`markdown_it/` 和 `mdurl/` 是原样内置的第三方包，不属于 IDE 自有代码。它们与 `ide/` 平级，继续分别经 `markdown_it`、`mdurl` 导入。

不要把这些包迁入 `ide/`，也不要在普通功能修改中改写其源码；更新依赖时应保持其上游包结构、许可和内部导入完整。两者的 ruff 排除配置位于 `pyproject.toml`。

## 4. 新增增量模块

未来实施独立业务模块时，优先在 `src/<模块名>/` 新建与 `ide/` 平级的真实包，而不是把新业务散入 `ide/core/`、`ide/gui/` 或 `ide/llm/`。只有对 IDE 本体能力的直接修改才进入 `ide/`。

当前项目仍是纯 IDE，不存在量化模块，也不创建空 `quant/` 或其他占位目录。

本结构只规定代码归属，不代表项目已经提供稳定的插件 API 或第三方扩展 SDK。外部模块需要调用 IDE 能力时，应在具体实施中明确公开接口和依赖方向，避免直接依赖 IDE 内部私有实现。

## 5. 修改布局后的检查

移动或新增包时，除 import 外还须检查：

- 文件路径字符串、动态模块名、monkeypatch 和日志器名称；
- `parents[N]` 等项目根或资源路径推导；
- 测试、截图探针、静态守卫与子进程的模块搜索路径；
- PyInstaller 的 import 收编和冻结态资源加载；
- 现行 README、维护手册和协议真值引用。

提交前运行 `bash scripts/check.sh`；涉及包结构时还应实际启动主窗口，并执行标准 AppImage 构建与产物冒烟验证。
