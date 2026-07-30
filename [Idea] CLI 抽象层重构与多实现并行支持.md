---
id: 20260726-001
type: idea
status: done
project: 
created: 2026-07-26
---

# [Idea] CLI 抽象层重构与多实现并行支持

## 动机
当前系统 CLI 为硬编码单一实现，随着后续需要对接不同协议/厂商的 CLI（如本地 CLI、远程 ACP CLI、容器内 CLI 等），必须将原有 CLI 抽象为统一接口，并支持多实现并行注册与切换，避免后续每新增一种 CLI 都侵入核心代码。

## 方案
1. **抽象接口层**：定义 `BaseCLI` 抽象基类，统一生命周期方法（`connect()`、`execute()`、`disconnect()`、`is_available()`）。
2. **迁移现有实现**：将当前硬编码 CLI 迁移为 `LegacyCLI(BaseCLI)`，保持行为不变。
3. **新增并行实现**：新增第二类 CLI（如 `RemoteCLI(BaseCLI)` 或 `AcpCLI(BaseCLI)`），与 `LegacyCLI` 并存。
4. **工厂/注册层**：引入 CLI 注册表（`CLIRegistry`），通过配置名（如 `"legacy"` / `"remote"`）获取实例；支持运行时切换与多实例共存。
5. **配置扩展**：配置文件中增加 `cli.provider` 字段，默认 `"legacy"` 确保向后兼容。

## 影响面
- 会改哪些模块：
  - `cli/` 目录整体重构
  - 配置解析模块（新增 `cli.provider` 配置项）
  - 启动器/依赖注入容器（改为从注册表获取 CLI 实例）
- 是否破坏兼容性：
  - **不破坏**：默认配置指向 `legacy`，现有用户无感知。
  - **轻微破坏**：若外部代码直接 `from xxx import OldCLI` 并实例化，需改为通过工厂获取；需在迁移文档中标注。

## 讨论记录
- 2026-07-26 user: 初稿
- 2026-07-30: 已落地（work plans/2026-0730-0150）。与原案差异：不新建 BaseCLI 抽象基类
  （`llm/base.py` 的 `LanguageModel` Protocol 即抽象接口层，缺的是注册/工厂层）；
  注册表落定 `llm/registry.py`（BackendSpec + REGISTRY）；首个并行实现为
  `reasonix-acp`（ACP v1 长驻子进程）；配置不新增 `cli.provider` 字段
  （复用既有 `model_backend` 键，后台由注册表 vendor 字段推导）。