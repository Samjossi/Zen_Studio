AGENTS

- 仅操作项目目录内文件，禁止访问外部路径
- 强制使用项目 `.venv` 的 Python，禁止系统全局 Python
- 如需生成临时文件、缓存或测试数据，必须存放在项目根目录内（如 `./.temp/`、`./tmp/` 或 `./cache/`），严禁使用系统临时目录（如 `/tmp`、`/var/tmp`、`C:\Windows\Temp`、`$TMPDIR` 等）
- 有时候使用绝对路径的时候就会被系统认为是目录外的文件而被要求权限，这个时候就尝试一下相对路径
- Always think and respond in Chinese (中文). 所有思考过程和输出必须使用中文。
- commit message 使用中文

## 工程结构与常用命令

- `local_cli/`：服务端主包。`acp/`（transport/dispatcher/session/turn/tool_loop 五模块，协议内核，不 import GUI；tool_loop 是有界工具循环）、`model/`（LanguageModel 抽象 + Mock/GGUF 实现，GGUF 走 llama-server 子进程编排）、`toolkit/`（工具内核：definitions/parser/executor/fence_filter，不 import acp/ 与 model/；fence_filter 是围栏块上屏抑制的流式过滤器）、`config.py`（TOML 配置加载）、`gui/`（PySide6）
- 入口：`.venv/bin/local-cli`（`local-cli acp` 协议模式 / `local-cli gui` 桌面模式 / `local-cli models` 枚举 GGUF 别名）
- 配置：`~/.local-cli/config.toml`（`$LOCAL_CLI_CONFIG` 可覆盖路径）；键 `backend`（mock 默认 / gguf）、`model_dir`（默认 `~/models`）、`llama_server_bin`、`ctx_size`（默认 4096）、`threads`、`tools_enabled`（默认 true）、`tool_max_iterations`（默认 5）
- 协议回归：`.venv/bin/python tools/spike_handshake.py`（应 43 过 / 0 挂；默认 mock 后端）
- 补充验证：`.venv/bin/python tools/verify_extras.py`（应 31 过 / 0 挂）
- GGUF 冒烟：`.venv/bin/python tools/smoke_gguf.py`（真实 llama-server 全链路，自带测试配置；模型目录非 `~/models` 时用 `LOCAL_CLI_MODEL_DIR` 指定。应 17 过 / 0 挂；工具轮落盘为软断言，模型不守围栏格式只警告不计挂）
- 协议真值来源：Zen Studio 项目根的 `dream-acp/protocol/dream-acp-v1.md`（本目录即 `Local_Cli/`，从其视角为 `../dream-acp/protocol/dream-acp-v1.md`；线协议不变）；stdout 只写协议帧，日志一律 stderr
