"""应用版本加载器（single source of truth 的读取口）。

版本规范：`pyproject.toml` 的 version 字段为唯一来源（三段基础版本，
发版时人工拍板或 `building/version.py bump` 代改）；构建号取 git 提交
计数（`git rev-list --count HEAD`）自动追加，完整版本四段
`major.minor.patch.build`。

解析链按运行形态逐级兜底，开发与打包两态显示一致：

- 冻结态（PyInstaller）：读 bundle 内 `version.txt`（spec 构建时注入，
  已是含构建号的完整版本，落点 _internal/ 根即 frozen 态 PROJECT_ROOT）；
- 脚本态：`importlib.metadata`（本项目为 uv virtual source 永不安装成
  dist，此级注定 miss，保留以兼容未来 pip 可装形态）→ 直读
  `pyproject.toml`；git 仓库内再追加提交计数作构建号；
- 全链失效：回退兜底常量并告警，程序不因版本问题启动失败。

消费方一律 import 本模块 APP_VERSION，禁止散落写死、禁止自行读文件：
- `gui/main_window.py` 关于对话框
- `llm/providers/*_acp.py` ACP initialize 的 clientInfo
"""
import importlib.metadata
import subprocess
import tomllib

from core.paths import IS_FROZEN, PROJECT_ROOT

#: 冻结态 bundle 内版本文件（spec datas 收编，构建时已是完整版本）
_VERSION_TXT = PROJECT_ROOT / "version.txt"

#: 兜底版本：全链失效时回退，保证程序不因版本问题启动失败
_FALLBACK_VERSION = "0.0.0"


def _base_version() -> str:
    try:
        return importlib.metadata.version("zen-studio")
    except importlib.metadata.PackageNotFoundError:
        pass
    try:
        with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
            return tomllib.load(f)["project"]["version"]
    except (OSError, KeyError):
        return ""


def _build_number() -> str | None:
    """git 提交计数；非 git 环境（拷贝源码树、浅克隆失真）返回 None。"""
    try:
        out = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def _load_app_version() -> str:
    if IS_FROZEN:
        try:
            return _VERSION_TXT.read_text(encoding="utf-8").strip()
        except OSError:
            pass
    base = _base_version()
    if not base:
        print(f"[version] 版本解析链全失效，回退 {_FALLBACK_VERSION}")
        return _FALLBACK_VERSION
    build = _build_number()
    return f"{base}.{build}" if build else base


#: 应用版本号（模块导入时解析一次，进程内稳定）
APP_VERSION = _load_app_version()
