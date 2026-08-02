# dream-acp — Dream CLI 的 ACP 接入协议包

> **版本**：v1.0（协议文档版本，见 `protocol/dream-acp-v1.md` 文头）
> **定位**：Dream CLI 的 ACP（Agent Client Protocol）接入规约与可执行示例，
> 自包含、零依赖、零 import 任何宿主项目代码——**搬走即完整**。

## 这是什么

Dream 是一个本地模型 CLI 后台（本地模型推理 + agent 循环）。它通过 ACP 与
IDE 类客户端（Zen Studio 等）对接：客户端以 `dream acp` 启动长驻子进程，
走 stdio ndjson JSON-RPC 完成握手、会话、流式对话、工具审批与取消。

本文件夹是 Dream 实现 ACP 接入的**开发底本与协议真值来源**，含三样：

```
dream-acp/
├── README.md                  # 本文件
├── protocol/
│   └── dream-acp-v1.md        # 《Dream ACP 接入协议 v1.0》：逐条规约
├── example/
│   └── dream                  # 最小示例 agent（Python 单文件可执行，零依赖）
└── tools/
    └── spike_handshake.py     # 管道级握手/轮次/cancel/审批断言脚本
```

## 快速开始

```bash
# 1. 示例 agent 可直接执行（Python 3.10+ 标准库，无第三方依赖）
./example/dream acp          # 以 ACP 模式启动（stdio ndjson，日志走 stderr）

# 2. 跑 spike 断言（自动拉起示例 agent，逐条验证协议面）
python3 tools/spike_handshake.py
python3 tools/spike_handshake.py --bin ./example/dream   # 显式指定被测 agent
```

## 各部分说明

| 路径 | 作用 |
|:---|:---|
| `protocol/dream-acp-v1.md` | 协议正式文档：帧格式、方法表、错误码表、时序、实测教训附录。Dream 实现者逐条对照即可。文头带版本号与变更纪律 |
| `example/dream` | 最小示例 agent：不接任何模型，但**覆盖协议每一条**——握手、cwd 绝对路径硬校验、流式 chunk、思维链通道、usage_update、演示工具 + 审批三态、`stopReason="error"` 错误路径、两个演示模型别名。是「协议的可执行形态」 |
| `tools/spike_handshake.py` | 不依赖任何测试框架的断言脚本：以子进程管道直连 agent，逐条断言协议行为。实现 Dream 时可把 `--bin` 指向真实二进制做冒烟 |

## 迁出说明（作为 Dream 项目底本使用）

本文件夹设计为整体迁入 Dream 项目仓库：

1. **整目录复制**，无需改动任何文件内容——无相对路径依赖、无宿主项目 import、
   无第三方依赖（仅需 Python 3.10+ 运行示例与 spike）。
2. 迁入后建议路径 `dream/acp/` 或仓库根 `acp/`；`spike_handshake.py` 的
   `--bin` 参数指向编译/安装后的真实 `dream` 二进制即可复用全部断言。
3. 协议文档版本号独立演进（当前 v1.0）。任何协议面变更（新增方法/字段语义
   变化）必须升版本并在文档文尾追加变更记录——见 `protocol/dream-acp-v1.md`
   「变更纪律」节。
4. 示例 agent 在 Dream 真实实现完成后可保留为协议回归测试的最小参照物：
   真实 agent 通过同一套 spike 断言即视为协议兼容。

## 与客户端的关系

本协议以 ACP `protocolVersion: 1` 为线协议协商值，以 Zen Studio 的
`llm/providers/acp.py` 客户端实现与四个已接入后台（Kimi / Reasonix /
OpenCode / Kilo Code）的实测教训为真值来源。文档附录 A 逐条记录这些实测
教训——它们不是建议，是已踩过的坑。
