# Local CLI

本地模型 ACP 服务端：为 ACP 客户端（如 Zen Studio IDE）提供本机 GGUF 模型后端——
模型在本地跑，代码让 agent 写，零密钥零云端。

## 模式

- `local-cli acp`：协议模式（默认），stdin/stdout 跑 dream-acp v1 线协议，供 IDE 长驻连接
- `local-cli gui`：PySide6 桌面模式，独立聊天窗口
- `local-cli models`：枚举 `model_dir` 下的 GGUF 模型别名

## 安装与配置

- 入口：`pip install -e .` 后 `.venv/bin/local-cli`
- 配置：`~/.local-cli/config.toml`（`$LOCAL_CLI_CONFIG` 可覆盖路径），键见 `AGENTS.md`
- 模型：放 `model_dir`（默认 `~/models`，`LOCAL_CLI_MODEL_DIR` 可覆盖），
  运行时按别名拉起 `llama-server` 子进程走本地 HTTP——代码与模型完全分离，
  模型放哪都行，配置寻址即可

## 验证链

- `bash scripts/check.sh`：提交前门禁（隐私扫描 + 协议回归 + 补充验证）
- `.venv/bin/python tools/smoke_gguf.py`：真实 llama-server 全链路冒烟（不入门禁，按需跑）

## 协议

线协议真值：`协议/dream-acp-v1.md`（vendor 副本，上游为 Zen Studio 仓库
`dream-acp/protocol/dream-acp-v1.md`，两侧 diff 应恒为空）。

## 出处

本项目原为 Zen Studio 仓库的 `Local_Cli/` 子目录，2026-10-05 外迁为独立仓库；
更早由 Dream_Cli 复制改名而来（参考实现见 `参考代码/dream-acp/`，仅本地参考不入库）。
外迁前的提交历史留在 Zen Studio 仓库可查。
