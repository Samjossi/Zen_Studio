"""外部应用程序调起：「使用 Typora 打开」的探测与启动共享逻辑。

（2026-07-29，见 文档/修改记录/2026-0729-1155_Markdown渲染预览与Typora打开功能实施计划 T1）

设计：OOP 封装 + 依赖注入——探测（which）与进程创建（Popen）均为构造注入，
GUI 层（文件树右键、Markdown 渲染页右键）共享同一启动器实例；
探针测试注入假依赖即可断言，不真正启动外部程序。

跨平台策略：Linux/Windows 走 PATH 探测 `typora`；macOS 回退
`/Applications/Typora.app` + `open -a Typora`。启动一律
subprocess.Popen 非阻塞（丢弃标准输出/错误，不管子进程生命周期），
禁止 os.system / run 阻塞 UI。
"""
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

#: macOS 回退探测：Typora 标准安装位置
_MACOS_TYPORA_APP = Path("/Applications/Typora.app")


class TyporaLauncher:
    """系统 Typora 探测与调起（跨平台，非阻塞）。"""

    def __init__(
        self,
        which: Callable[[str], str | None] = shutil.which,
        popen: Callable[..., subprocess.Popen] = subprocess.Popen,
        platform: str = sys.platform,
        macos_app: Path = _MACOS_TYPORA_APP,
    ) -> None:
        """
        :param which: 可执行文件探测（注入假实现即可模拟未安装环境）
        :param popen: 进程创建（注入假实现即可断言调起参数）
        :param platform: 平台标识（sys.platform，注入以模拟 macOS 分支）
        :param macos_app: macOS 回退探测的 .app 路径
        """
        self._which = which
        self._popen = popen
        self._platform = platform
        self._macos_app = macos_app

    def _command(self, path: Path) -> list[str] | None:
        """构造调起命令；未检测到 Typora 返回 None。"""
        if exe := self._which("typora"):  # Linux / Windows（Typora 安装注册 PATH）
            return [exe, str(path)]
        if self._platform == "darwin" and self._macos_app.exists():
            return ["open", "-a", "Typora", str(path)]
        return None

    def is_available(self) -> bool:
        """是否检测到系统 Typora（决定右键菜单项显隐）。"""
        return self._which("typora") is not None or (
            self._platform == "darwin" and self._macos_app.exists())

    def open(self, path: Path | str) -> str | None:
        """调起 Typora 打开指定文件；成功返回 None，失败返回原因字符串。"""
        target = Path(path)
        command = self._command(target)
        if command is None:
            return "未检测到 Typora（未安装或不在 PATH 中）"
        try:
            self._popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as e:
            return str(e)
        return None


#: 模块级共享实例（GUI 各入口缺省复用；探测结果随调用实时查询，不缓存）
default_launcher = TyporaLauncher()
