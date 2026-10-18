# 接入 DeepSeek Harness 后台落地计划

> **状态**：已实施（阶段一；GUI 内人工验证待做）
> **范围**：`src/llm/`（注册表与 providers）、ModelBar 四级下拉、配置与测试
> **时间**：2026-10-18 07:32（设计，UTC+8）/ 2026-10-18 07:54（实施）
> **优先级**：高

---

## 1. 背景与目标

AI 聊天面板的模型选择按钮（ModelBar，`src/gui/panels/chat/model_bar.py`）是「后台 → 接口 → 模型 → 推理强度」四级下拉。后台的唯一数据源是 `src/llm/registry.py` 的 `REGISTRY` 硬编码注册表，现有 6 个注册项（kimi / reasonix / opencode / kilocode（已封存）/ dream / local），**全部后台同构**：长驻 `<cli> acp` 子进程 + stdio ndjson JSON-RPC（ACP 协议），共享连接层 `src/llm/providers/acp.py`。

**本计划目标（只做增量，不做删减）**：

- 新增 DeepSeek Harness（dsh）专用后台，注册进 `REGISTRY`，ModelBar 后台菜单可切换。
- 端到端验证：切换后台 → 对话 → 推理强度切换 → 审批交互，全部可用。
- 移除非 Kimi 后台：**推迟到 dsh 后台验证稳定之后**，见 §5 远期备注，本计划不执行。

## 2. 关键事实（调研结论）

DeepSeek Harness 仓库已克隆至 `参考代码/deepseek-harness`，官方文档 `https://deepseek-harness.github.io/deepseek-harness/`。

- `dsh acp`（等价 `dsh --profile acp`）是一等公民 CLI 模式：标准 ACP v1、stdio 换行分帧 JSON-RPC，**与现有 6 个后台协议形态完全一致**，`src/llm/providers/acp.py` 连接层可直接复用。协议约定见 `参考代码/deepseek-harness/packages/acp/acp/README.md`。
- `session/new` 响应自带完整 configuration-option 状态（`model` 选项清单来自运行时 LLM catalog，模型声明推理档位时附 `reasoning_effort` 选项）；运行时切换走 `session/set_config_option`。
- 审批为标准的 server→client `session/request_permission`（一次性 allow/reject），可直接挂到现有审批 UI。
- 支持 `session/cancel`、`session/list` / `session/resume` 持久会话、图片 prompt；不支持 terminals、modes、commands、`session/load`。
- 默认模型路由 `deepseek-official` / `deepseek-v4-flash`，可被 patch 替换；凭据由 dsh 自管（`$DSH_HOME/.credentials.yaml`、环境变量 `DEEPSEEK_API_KEY` 等），宿主代码库保持零密钥原则。
- ⚠️ dsh 处于 developer preview，官方明示会有兼容性破坏变更，接入面需按版本锁定验证。
- ⚠️ dsh **没有** `dsh models` 这类 CLI 枚举命令；模型清单只有 ACP `session/new` 的 config options 或 Web 设置页探测两个来源。

## 3. 阶段一：新增 dsh-acp 后台

### 3.1 任务清单

| # | 任务 | 位置 | 说明 |
|:--|:---|:---|:---|
| 1 | 新建 `dsh_acp.py` | `src/llm/providers/dsh_acp.py` | 参照 `kimi_acp.py` 同构实现 provider：spawn `dsh acp`，鸭子类型接口 `set_permission_handler` / `set_model` / `set_effort`；模型与强度切换映射到 `session/set_config_option` |
| 2 | 二进制探测 | `src/llm/providers/dsh_acp.py` | `available()` 探测 dsh 可执行性（本机已有 `~/.dsh` 运行痕迹，落地前经 spike 脚本确认 `npx @deepseek-ai/dsh` 可执行；注意首次 boot 会初始化 acp profile）；DSH_HOME 跟随 dsh 原生默认 `~/.dsh`，IDE 不设不碰——密钥配置面归 dsh 官方渠道，与 kimi 同口径，AppImage 分发信任链干净 |
| 3 | 注册 BackendSpec | `src/llm/registry.py` | 新增注册项：`vendor="dsh"`、vendor_label `DSH`、`name="dsh-acp"`、label `DeepSeek Harness`；插入序决定菜单序，置于 kimi 之后 |
| 4 | 模型列表 `list_models` | `src/llm/registry.py` | 因无 CLI 枚举命令，初版用静态表（默认路由模型 + 常用 deepseek 型号），后续视需要升级为一次性 `session/new` 探测动态清单（spike 验证开销后再定） |
| 5 | 推理强度 `list_efforts` | `src/llm/registry.py` | 静态档位表（如 `off` / `high` / `max`），与 dsh 模型级 `reasoningEfforts` 映射对齐；查无档位 = 无强度轴（按钮禁用），沿用现有语义 |
| 6 | re-export | `src/llm/__init__.py` | 按现有模式导出 dsh 后台符号 |
| 7 | 冒烟脚本 | `scripts/`（可选） | 参照 `scripts/probe_subagent_frames.py` 形态，写 dsh acp 握手/单轮 prompt 的 spike 脚本，落地前先跑通 |
| 8 | 单元测试 | `tests/test_dsh_backend.py` | 参照现有后台测试形态：注册项存在性、available 探测 mock、模型/强度表、序列化记忆表兼容 |
| 9 | 文档同步 | `src/llm/README.md`、`术语表.md` | 登记新后台；`src/gui/README.md` 的后台清单描述同步 |

### 3.2 不需要改动的部分（确认项）

- ModelBar 四级联动与 `src/gui/settings_dialog.py` 模型页均由 `REGISTRY` 驱动，注册后自动出现新后台，零 UI 改动。
- `src/gui/settings.py` 持久化键（`model_backend` / `model_versions` / `model_efforts`）按接口名记忆，天然兼容新后台。
- `src/llm/providers/acp.py` 连接层为全部后台共享，原则上不动；若 dsh 的 ACP 方言有差异（如不支持的 capability 需裁剪请求），差异收敛在 `dsh_acp.py` 内处理。

### 3.3 验收标准

- ModelBar 后台菜单出现 `DSH`，切换后接口为 `DeepSeek Harness`，模型/推理强度下拉正常。
- 切到 dsh 后台可完成一轮真实对话（流式输出、取消可用）。
- 切换模型与推理强度对下一 turn 生效，重启应用后记忆值恢复。
- 触发需审批的工具调用时，审批弹窗正常应答。
- `bash scripts/check.sh`（隐私扫描 + lint + 测试）全绿。

## 4. 风险与对策

| 风险 | 等级 | 对策 |
|:---|:---:|:---|
| dsh developer preview 兼容性破坏变更 | ⚠️ | 锁定已验证的 dsh 版本；接入面集中在 `dsh_acp.py` 单文件，升级时逐行 diff 协议约定 README |
| 模型清单无 CLI 枚举，静态表可能与实际配置漂移 | 🟡 | 初版静态表兜底；spike 验证 `session/new` 动态清单开销后决定是否升级 |
| 首次 boot acp profile 初始化耗时/失败 | 🟡 | `available()` 探测覆盖 boot 路径；失败时在 ModelBar 按现有「不可用」语义灰显 |
| 凭据缺失导致会话失败 | 🟡 | 凭据归 dsh 自管；发送失败时透传 dsh 报错文案，不在宿主侧做密钥管理 |

## 5. 远期备注：移除非 Kimi 后台（本计划不执行）

dsh 后台验证稳定后，可考虑将 `REGISTRY` 收敛为 kimi-acp + dsh-acp 两家。
`registry.py` 注册表架构与 ModelBar 四级架构保留（kimi + dsh 两家时四级
仍有意义）。以下为调研定位的候选改动面存档：

- `src/llm/registry.py` 删 5 个注册项、`src/llm/providers/` 删对应 5 个客户端文件、`src/llm/context_limits.py`（仅 reasonix 使用）。
- 失效测试：`tests/test_kilocode_archived.py`、`tests/test_local_backend.py` 等；UI 层 `src/gui/panels/chat/panel.py` 的 `_show_dev_note` / `_blocked_by_archive`。
- `dream-acp/` 目录与 reasonix 专属 spike 脚本归档；`src/llm/README.md`、`src/gui/README.md`、术语表、维护手册同步。

---

*创建于 2026-10-18 07:32 (UTC+8)*
