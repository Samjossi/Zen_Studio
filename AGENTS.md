AGENTS

- 仅操作项目目录内文件，禁止访问外部路径
- 强制使用项目 `.venv` 的 Python，禁止系统全局 Python
- 如需生成临时文件、缓存或测试数据，必须存放在项目根目录内（如 `./.temp/`、`./tmp/` 或 `./cache/`），严禁使用系统临时目录（如 `/tmp`、`/var/tmp`、`C:\Windows\Temp`、`$TMPDIR` 等）
- 有时候使用绝对路径的时候就会被系统认为是目录外的文件而被要求权限，这个时候就尝试一下相对路径
- Always think and respond in Chinese (中文). 所有思考过程和输出必须使用中文。
- commit message 使用中文
- Git 远程托管在本地 NAS 的 Forgejo 服务（<NAS内网地址>，Web 端口 3000），按 `组织/仓库名` 模式管理；推送/克隆统一使用本机 ssh 别名 `forgejo`（格式：`forgejo:组织名/仓库名.git`），禁止按文件系统路径推送（会被 Forgejo 的 pre-receive 钩子拒绝）
- 操作 git 远程前先运行 `git remote -v` 确认当前配置，不要凭 URL 猜测仓库布局