"""应用版本加载器（single source of truth 的读取口）。

版本规范：自 1.0 起，每次发布 +0.1（1.0 → 1.1 → 1.2 …），发版时人工
递增 `config/version.json` 的 version 字段。

单一来源迁移留痕（2026-07-31）：原为本模块硬编码常量，迁至
`config/version.json` 数据文件承载——版本号不再硬编码进源码，
非 Python 工具（脚本 / CI / 安装器）亦可直接读取；PyInstaller 打包
经 spec datas 将 `config/version.json` 收编至解包根 `config/` 下，
路径推导复用 `core/paths.py` 的 PROJECT_ROOT（frozen 态自动指向
sys._MEIPASS），开发与打包两态行为一致。

消费方一律 import 本模块 APP_VERSION，禁止散落写死、禁止自行读文件：
- `gui/main_window.py` 关于对话框
- `llm/providers/*_acp.py` ACP initialize 的 clientInfo
- `pyproject.toml` 的 version 为包元数据副本，发版时同步手改
"""
import json

from core.paths import PROJECT_ROOT

#: 版本文件（单一来源本体）
VERSION_FILE = PROJECT_ROOT / "config" / "version.json"

#: 兜底版本：文件缺失/损坏时回退，保证程序不因版本文件问题启动失败
_FALLBACK_VERSION = "0.0"


def _load_app_version() -> str:
    """从 config/version.json 读取版本号。

    防御三层：文件不存在/不可读（OSError）、JSON 损坏（JSONDecodeError）、
    version 字段缺失或非非空字符串，均回退兜底值并打印告警，不抛异常。
    """
    try:
        data = json.loads(VERSION_FILE.read_text(encoding="utf-8"))
        version = data.get("version")
        if isinstance(version, str) and version:
            return version
    except (OSError, json.JSONDecodeError):
        pass
    print(f"[version] 版本文件缺失或损坏（{VERSION_FILE}），回退 {_FALLBACK_VERSION}")
    return _FALLBACK_VERSION


#: 应用版本号（模块导入时自 config/version.json 加载一次，进程内稳定）
APP_VERSION = _load_app_version()
