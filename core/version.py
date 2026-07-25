"""应用版本单一来源（single source of truth）。

版本规范：自 1.0 起，每次发布 +0.1（1.0 → 1.1 → 1.2 …），发版时人工
递增本文件常量。以 Python 模块承载而非裸文本文件——PyInstaller 将源码
收进 PYZ 归档，打包态无需 spec datas 额外收编即可读得。

消费方一律 import 本模块常量，禁止散落写死：
- `gui/main_window.py` 关于对话框
- `llm/providers/kimi_acp.py` ACP initialize 的 clientInfo
- `pyproject.toml` 的 version 为包元数据副本，发版时同步手改
"""

#: 应用版本号（发版人工 +0.1）
APP_VERSION = "1.0"
