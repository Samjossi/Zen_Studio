AGENTS

- 仅操作项目目录内文件，禁止访问外部路径
- 强制使用项目 `.venv` 的 Python，禁止系统全局 Python
- 如需生成临时文件、缓存或测试数据，必须存放在项目根目录的 `./.temp/` 内，严禁使用系统临时目录（如 `/tmp`、`/var/tmp`、`C:\Windows\Temp`、`$TMPDIR` 等）
- 有时候使用绝对路径的时候就会被系统认为是目录外的文件而被要求权限，这个时候就尝试一下相对路径
- 输出使用中文
- 提交前必跑本地门禁 `bash scripts/check.sh`（隐私扫描 + lint + 测试），绿了才提交
- 注释只回答 why（设计意图、坑、约束），不记录 when/what changed（日期、计划编号、拍板记录）；历史归属 git log 与 `文档/修改记录/`。
- Git 提交/推送/远程托管的全部约定以根目录 `AGENTS.local.md` 为唯一信息来源（已 gitignore，禁止提交）；本文件不记录任何 Git 细节
- 若 `AGENTS.local.md` 不存在，说明当前环境无此配置，不得凭猜测操作 git 远程