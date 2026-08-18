# 后端CLI工具维护说明手册

> 本文档汇总了团队常用 后端CLI工具的官方说明地址，供工程师在遇到疑惑时快速查阅。
> 最后更新：2026-08-13（新增 §5 Zen Studio 私有通道登记：kimi wire 子代理旁路）

---

## 1. Kimi Code（原 Kimi CLI）

Kimi 官方推出的 AI 编程代理，支持终端、VS Code 扩展及 API 接入。

| 资源             | 地址                                                         |
| ---------------- | ------------------------------------------------------------ |
| 主文档站         | https://www.kimi.com/code/docs/                              |
| CLI 快速开始     | https://www.kimi.com/code/docs/kimi-code-cli/guides/getting-started.html |
| 中文帮助中心入门 | https://www.kimi.com/zh-cn/help/kimi-code/cli-getting-started |
| GitHub 仓库      | https://github.com/MoonshotAI/kimi-code                      |
| 旧版文档（参考） | https://moonshotai.github.io/kimi-cli/zh/                    |

**安装命令：**
```bash
# 新版（Node.js，推荐）
curl -fsSL https://www.kimi.com/code/install.sh | bash

# 旧版（Python/uv，逐步停止维护）
uv tool install kimi-cli
```

> 提示：新版 Kimi Code CLI 已从 Python/uv 迁移至 Node.js，旧用户可通过 `kimi migrate` 一键迁移配置和会话历史。

---

## 2. OpenCode

100% 开源（MIT）的终端 AI 编程代理，支持 Claude、GPT、Gemini 及 75+ 家模型提供商。

| 资源                 | 地址                            |
| -------------------- | ------------------------------- |
| 官方网站             | https://opencode.ai/            |
| 官方文档             | https://opencode.ai/docs        |
| CLI 参考（中文社区） | https://opencodecn.com/docs/cli |
| GitHub 仓库          | https://github.com/sst/opencode |

**安装命令：**
```bash
curl -fsSL https://opencode.ai/install | bash
# 或
npm i -g opencode-ai
```

---

## 3. Kilo Code

开源（MIT）的 AI 编程 Agent，支持 VS Code、JetBrains 和 CLI，可接入 500+ 模型。

| 资源              | 地址                                                         |
| ----------------- | ------------------------------------------------------------ |
| 官方网站          | https://kilo.ai/                                             |
| 完整文档          | https://kilo.ai/docs/                                        |
| CLI 专用说明      | https://kilo.ai/cli                                          |
| GitHub 仓库       | https://github.com/Kilo-Org/kilocode                         |
| VS Code 扩展      | https://marketplace.visualstudio.com/items?itemName=kilocode.Kilo-Code |
| JetBrains 插件    | https://plugins.jetbrains.com/plugin/kilo-code               |
| Skills / MCP 市场 | https://github.com/Kilo-Org/kilo-marketplace                 |

**安装命令：**
```bash
# npm
npm install -g @kilocode/cli

# curl
curl -fsSL https://kilo.ai/cli/install | bash

# Homebrew
brew install Kilo-Org/tap/kilo
```

---

## 4. Reasonix

DeepSeek 原生的开源终端 AI 编程代理，核心设计围绕 prefix-cache 稳定性优化。

| 资源                  | 地址                                                         |
| --------------------- | ------------------------------------------------------------ |
| GitHub 仓库           | https://github.com/esengine/DeepSeek-Reasonix                |
| 官方文档              | https://reasonix.io/docs                                     |
| 项目主页              | https://deepseekreasonix.com/                                |
| NPM 包页面            | https://www.npmjs.com/package/reasonix                       |
| VS Code 扩展          | https://marketplace.visualstudio.com/items?itemName=SivanLiu.reasonix-agent |
| DeepSeek API 接入指南 | https://api-docs.deepseek.com/zh-cn/quickstart               |

**安装命令：**
```bash
# 试用
npx reasonix code

# 全局安装
npm i -g reasonix@next

# macOS Homebrew
brew install esengine/reasonix/reasonix
```

> 本机配置备注：2026-08-12 起 `~/.reasonix/config.toml` 已设 `[sandbox] bash = "off"`
> （Ubuntu 24.04 AppArmor 默认策略拦截 bwrap 致沙箱不可用、bash 工具瘫痪，
> 详见《文档/修改记录/2026-0812-0301_Reasonix沙箱不可用导致AI反复尝试bash问题调查报告.md》）。

---

## 5. Zen Studio 私有通道登记（无协议契约，格式漂移自查）

### 5.1 kimi wire.jsonl 子代理旁路（2026-08-13 登记）

**机制**：Zen Studio 的 kimi-acp 后端在轮次内旁路读取会话落盘目录
`~/.kimi-code/sessions/<工作区键>/<sessionId>/agents/agent-N/wire.jsonl`
（子代理 wire），增量解析合成嵌套工具卡（实现：`llm/providers/kimi_acp.py`
`_WireSidecar`；开关：config/settings.json `kimi_wire_sidecar`，默认开）。
该通道是**客户端私有行为**，kimi-code 无任何格式契约，CLI 升级可能漂移。

**登记的格式假设**（实测样本：2026-08-13 `wd_dream_cli` 会话 agent-0
wire.jsonl，546 行）：

- 记录单位：逐行 JSON（jsonl）；相关记录 `type == "context.append_loop_event"`，
  事件体在 `event` 键内；
- 工具调用帧：`event.type == "tool.call"`，含 `toolCallId`（`tool_XXX` 形态）、
  `name`（工具名原文，如 `Bash`/`Edit`/`Write`/`Read`/`Grep`/`TodoList`）、
  `args`（入参 dict——Bash 为 `command`、Edit 为 `path/old_string/new_string`
  蛇形、Write 为 `path/content`、TodoList 为 `todos`）；
- 工具回执帧：`event.type == "tool.result"`，含同名 `toolCallId` 与
  `result.output`（纯文本；**错误以 output 文本形态落盘，无独立 error 字段**）；
- 无 in_progress 中间帧（旁路只合成起止两帧）；思维链为
  `content.part` 事件（v1 跳过不渲染）；
- 子代理目录命名 `agent-N`（N 递增），与在途 Agent 调用的关联按目录
  创建时序先到先得（单在途假设）。

**熔断与降级**：单行 JSON 解析失败累计 ≥8 次旁路静默收束；目录发现失败/
wire 缺失/关联不上均静默回退纯 ACP 行为（子代理仅起止 + 成果摘要）。

**失效排查路径**：

1. 症状「子代理卡片不再嵌套」→ 先看 `agents/agent-N/wire.jsonl` 是否仍
   存在且为上述形态（`head -5` 对照登记假设）；
2. 格式漂移确认后：临时回退——`config/settings.json` 置
   `"kimi_wire_sidecar": false`；
3. 根治——按新样本修订 `_synthesize_wire_call/_synthesize_wire_result`
   与登记假设，并跑 `.venv/bin/python scripts/test_subagent_nested.py`
   （驱动样本 `.temp/subagent_wire_sample.jsonl` 需同步重制）。

---

## 使用建议

1. **优先查阅官方文档**：各工具的官方文档站通常包含最新版本的使用说明、配置项和故障排查。
2. **关注 GitHub Release**：重大版本更新和 Breaking Changes 会在 GitHub Release 页面说明。
3. **Issue 反馈**：遇到 Bug 或功能请求时，优先在对应 GitHub 仓库提交 Issue。
4. **定期更新手册**：建议每季度检查一次各工具的官方地址是否有变更，并同步更新本文档。

---

*本文档由 Kimi AI 整理生成，仅供内部维护参考。*