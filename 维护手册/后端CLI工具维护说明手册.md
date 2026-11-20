# 后端CLI工具维护说明手册

> 本文档只保留团队常用后端 CLI 的安装、运行、更新与验证命令。
> 升级后需重启 Zen Studio，再验证版本与实际会话。
> 最后更新：2026-11-20

---

## 1. Kimi Code（原 Kimi CLI）

Kimi 官方 AI 编程代理，Zen Studio 通过 `kimi acp` 接入。

| 资源 | 地址 |
| --- | --- |
| 主文档站 | https://www.kimi.com/code/docs/ |
| CLI 快速开始 | https://www.kimi.com/code/docs/kimi-code-cli/guides/getting-started.html |
| GitHub 仓库 | https://github.com/MoonshotAI/kimi-code |

### 安装

```bash
curl -fsSL https://code.kimi.com/kimi-code/install.sh | bash
```

### 运行

交互式：

```bash
kimi
```

ACP（Zen Studio 使用）：

```bash
kimi acp
```

### 更新

```bash
kimi upgrade -y
```

### 验证

```bash
kimi --version
```

当前应输出 `2.6.3`。

### ACP 原会话恢复与事故取证

- Zen Studio 要求 CLI 在 `initialize` 的 `agentCapabilities.sessionCapabilities.resume` 中声明恢复能力；2.6.3 实测为 `{}`（支持）。不支持时不会退回 `session/load` 或新建空会话，应升级 CLI 后重试。
- 恢复仅由下一次手动发送触发，顺序为 `initialize → session/resume → session/set_config_option`；`resume` 参数为原 `sessionId`、创建时 `cwd` 与 `mcpServers`。响应可不返回 `sessionId`，客户端沿用原 ID。
- 普通手动停止不重建进程；EOF、写断管、进程退出和看门狗停滞才进入下轮恢复。原中断 prompt 永不自动重放，恢复失败时新 prompt 也不发送。
- 开发态日志位于项目 `config/logs/zen-studio.log`，桌面冻结态位于 `~/.config/zen-studio/logs/zen-studio.log`。检索 `generation`、`sessionId`、`requestId`、`resume`、`connection_dead` 和 `recovery_failed`，先确认连接代次与退出码，再区分 IDE 写失败、CLI EOF 和控制请求超时。
- ACP stdout 永远是协议帧，完整帧与 prompt 正文不得写入诊断日志。stderr 由 IDE 持续排空，仅保留有界脱敏尾部并在异常时落日志；CLI 自身完整日志仍按官方数据位置另行查看，取证时不得复制凭证或上传完整会话内容。

---

## 2. DeepSeek Harness（DSH）

DeepSeek 官方开源的 Agent Harness，CLI 命令为 `dsh`；Zen Studio 通过 `dsh acp` 接入。

| 资源 | 地址 |
| --- | --- |
| GitHub 仓库 | https://github.com/deepseek-ai/deepseek-harness |
| 官方文档站 | https://deepseek-harness.github.io/deepseek-harness/ |
| CLI 说明 | https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/cli/README.zh.md |
| 模型配置指南 | https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/user/guide/providers.zh.md |

### 安装

```bash
npm i -g @deepseek-ai/dsh@0.2.2-rc.5
```

### 运行

Web UI：

```bash
dsh web
```

ACP（Zen Studio 使用）：

```bash
dsh acp
```

### 更新

```bash
npm i -g @deepseek-ai/dsh@latest
```

### 验证

```bash
dsh --version
```

当前应输出 `0.2.2-rc.5`；Node.js 要求 `>=24.9.0`。

---

## 3. Dream ACP（开发中）

Zen Studio 自维护的 ACP 协议对接层，协议真值在本仓库 `dream-acp/`；本身不含模型。

| 资源 | 地址 |
| --- | --- |
| 协议正式文档 | `dream-acp/protocol/dream-acp-v1.md` |
| 开发底本说明 | `dream-acp/README.md` |
| 示例 agent | `dream-acp/example/dream` |
| 协议断言脚本 | `dream-acp/tools/spike_handshake.py` |

### 安装

无需安装，仓内自带示例 agent。

### 运行

```bash
./dream-acp/example/dream acp
```

### 更新

随本仓库更新。

### 验证

```bash
.venv/bin/python dream-acp/tools/spike_handshake.py
```

---

## 4. 已封存工具

OpenCode、Kilo Code、Reasonix、本地模型（Local CLI）已封存：界面保留入口但发送被拦停。历史资料见 git 历史。
