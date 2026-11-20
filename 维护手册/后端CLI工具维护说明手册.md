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

本次真实验收环境输出 `2.7.1`。

### ACP 原会话恢复与事故取证

- Zen Studio 要求 CLI 在 `initialize` 的 `agentCapabilities.sessionCapabilities.resume` 中声明恢复能力；2.6.3 实测为 `{}`（支持）。不支持时不会退回 `session/load` 或新建空会话，应升级 CLI 后重试。
- 恢复仅由下一次手动发送触发，顺序为 `initialize → session/resume → session/set_config_option`；`resume` 参数为原 `sessionId`、创建时 `cwd` 与 `mcpServers`。响应可不返回 `sessionId`，客户端沿用原 ID。
- 普通手动停止不重建进程；仅 stdout EOF、进程意外退出和真实写帧失败进入下轮恢复。Kimi 不使用停滞看门狗。原中断 prompt 永不自动重放，恢复失败时新 prompt 也不发送。
- 开发态日志位于项目 `config/logs/zen-studio.log`，桌面冻结态位于 `~/.config/zen-studio/logs/zen-studio.log`。检索 `generation`、`sessionId`、`requestId`、`resume`、`connection_dead` 和 `recovery_failed`，先确认连接代次与退出码，再区分 IDE 写失败、CLI EOF 和控制请求超时。
- ACP stdout 永远是协议帧，完整帧与 prompt 正文不得写入诊断日志。stderr 由 IDE 持续排空，仅保留有界脱敏尾部并在异常时落日志；CLI 自身完整日志仍按官方数据位置另行查看，取证时不得复制凭证或上传完整会话内容。

### 后台通知轮次边界

- Kimi 轮次只以对应 `session/prompt` response 的 `stopReason` 收尾。IDE 不读取私有 wire 判断 busy、成功、失配、恢复或待发，也没有轮前/轮后 drain。
- 后台通知期间提前 `end_turn`、追问未形成真实执行轮或工具副作用重复，应按上游 ACP 契约问题独立取证；不得重新引入本地私有状态机掩盖。
- 取证保留 CLI 版本、`requestId`、`sessionId`、`stopReason`、ACP 帧顺序和脱敏 wire 元数据；不得包含 prompt 正文、完整工具参数/输出、diff 或凭证。

### 上下文用量采集与迟到通知诊断

- 官方 ACP `usage_update` 仍是 prompt response 后的轮末单帧，携带 `used/size`。Zen 的轮内刷新额外读取私有 `agents/main/wire.jsonl`；该文件没有 ACP 公共契约保证，解析失败必须降级，不得影响聊天主流程。
- 轮内优先读取 `token_counting.measured.tokens` 作为实测上下文水位；旧版本或事件缺失时，以 `usage.record` 的 `inputOther + inputCacheRead + inputCacheCreation + output` 兜底。模型窗口来自模型目录 `maxContextSize`，不得使用 `llm.request.maxTokens`。
- 轮内读取采用每会话增量游标与残行缓冲，首次仅取 64KB 尾部；大工具输出后沿用最后有效采样。会话目录只缓存成功定位，失败按 2/5/15 秒退避；空模型目录在下一轮 worker 准备时重试，GUI tick 不启动目录子进程。
- response 后保留 3 秒有界用量收尾窗口；Kimi 面板另有最长 10 秒非 busy 宽限，由 `poll_usage()` 回收迟到 usage。空闲回收必须非阻塞取得轮次锁，正文、工具、响应和 Token 消耗账本不得走该通道；用量缺失或迟到不改变轮次终态。
- 排查时只记录事件类型、脱敏数值、时间差、失败类别与重试次数。若轮内长期为 `—`，依次检查目录定位/模型窗口重试日志、wire 是否出现有效 measured 或 usage.record、轮后宽限是否仍在运行；不得复制完整 wire、prompt、工具参数或凭证。

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

## 3. Local CLI

Local CLI 是独立仓库维护的本地模型 agent，Zen Studio 通过 `local-cli acp` 接入。模型目录由服务端扫描 `~/.local-cli/config.toml` 中 `model_dir` 下的 `*.gguf` 生成；协议参考保留在本仓库 `dream-acp/protocol/dream-acp-v1.md`，该目录不再是 Dream 后台注册入口。

### 安装

本仓库未记录可核实的 Local CLI 现行安装命令，待独立仓库资料补充；不要套用 Dream 示例 agent 作为安装方式。安装后可放在 `PATH`、`$LOCAL_HOME/bin/local-cli` 或 `~/.local-cli/bin/local-cli`。

### 运行

模型枚举：

```bash
local-cli models
```

ACP（Zen Studio 使用）：

```bash
local-cli acp
```

### 更新

本仓库未记录可核实的 Local CLI 现行更新命令，待独立仓库资料补充。

### 验证

```bash
local-cli models
```

正常输出为每行一个 GGUF 模型别名；空输出、非零退出或二进制缺失时，IDE 模型列表回退为空，不伪造可用模型。未安装时界面标注「未检测到」，这与后台封存无关。完整对话验证还需在 `~/.local-cli/config.toml` 配置有效 `model_dir` 并准备 GGUF 模型。

---

## 4. 源码目录、架构守卫与扩展流程

本节记录现行依赖边界；CLI 安装、更新和真机验证仍以第 1–3 节为准。

### 4.1 目录职责

| 路径 | 职责 |
|:---|:---|
| `src/ide/llm/__init__.py` | 公共出口：稳定契约、ACP 公共类型、注册表 API；不再导出具体 provider 类或探测函数 |
| `src/ide/llm/registry.py` | 后台组合根：`BackendSpec`/`REGISTRY`/`DEFAULT_BACKEND`；唯一允许 import 具体厂商后台的模块 |
| `src/ide/llm/providers/acp/` | 稳定 ACP 传输、协议定型、prompt 与标准 content 纯函数；不含厂商工具名、私有配置键或落盘路径 |
| `src/ide/llm/providers/<vendor>/` | 各厂商 provider、models、updates 与 permissions；厂商之间禁止横向 import |
| `src/ide/gui/panels/chat/card_base.py` | 无厂商语义的卡片生命周期、折叠/复制/图片等 UI 基础件 |
| `src/ide/gui/panels/chat/vendors/registry.py` | 卡片组合根：唯一允许 import 具体厂商卡片包的模块 |
| `src/ide/gui/panels/chat/vendors/<vendor>/` | 各厂商 `factory.py`/`cards.py`/`presenter.py`；互不借用，最多 import 本厂商 `models.py` 纯类型 |
| `src/ide/gui/panels/chat/panel.py` | 只选择当前厂商工厂、管理卡片生命周期和转发不透明事件 |

### 4.2 新增后台 provider

1. 在 `src/ide/llm/providers/<vendor>/` 建厂商包，`__init__.py` 只公开本厂商入口；ACP 系后端复用 `providers/acp/` 的 `AcpConnection`，provider 自行组装完整启动 argv。
2. 在 `src/ide/llm/registry.py` 注册 `BackendSpec`；`available`/`list_models`/`factory` 必须惰性，import 无副作用。
3. 不要修改 `src/ide/llm/__init__.py` 追加具体类；GUI 通过注册表工厂消费，无需新增厂商分支。
4. 厂商私有工具名、载荷字段、配置键、错误关键词和落盘路径只写入本厂商目录。

### 4.3 新增厂商卡片包

1. 在 `src/ide/gui/panels/chat/vendors/<vendor>/` 建 `factory.py`、`cards.py`、`presenter.py`、`__init__.py`；工厂只理解本厂商工具名与载荷，presenter 只维护本厂商账本。
2. 在 `src/ide/gui/panels/chat/vendors/registry.py` 注册工厂、presenter 与卡片模块；通用面板不得直接 import 厂商卡片包。
3. 具体卡片可先从现有厂商等价复制，复制后独立维护；禁止跨厂商共享具体卡片、公共 `card_kind` 或万能卡片基类。

### 4.4 架构守卫

- `tests/test_provider_boundaries.py` 用标准库 AST 扫描 `src/ide/**/*.py` 与 `main.py`，覆盖：厂商后台/卡片横向隔离、两个组合根例外、provider 不反向 import registry/GUI、公共层不含厂商工具分派表/私有载荷/configId/落盘路径、旧平铺模块路径。
- 守卫内置受控反例，反例只在测试内存构造；新增规则时必须同时提供「会失败」的反例，避免全绿只是没扫到目标路径。
- 提交前统一运行 `bash scripts/check.sh`（隐私扫描 + QLabel 守卫 + ruff + pytest）；架构守卫失败时不得通过删除测试或放宽断言绕过。
