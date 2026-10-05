"""工具执行器：三工具在会话 cwd 沙盒内落地执行。

沙盒纪律（§3.2/计划 §6 风险表）：path 一律 Path.resolve 后判定位于 cwd 内，
绝对路径、.. 逃逸、软链逃逸全部返回结构化失败而不是抛异常——轮次层只认
ToolExecutionResult，工具副作用永远不许崩轮次（A.3）。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from local_cli.toolkit.definitions import (
    TOOL_NAME_READ_FILE,
    TOOL_NAME_WRITE_FILE,
)
from local_cli.toolkit.parser import ToolCallRequest

#: 结果截断阈值（防工具输出撑爆上下文窗口，计划 §6；默认 ctx 4096 下留足对话余量）
_READ_RESULT_MAX_CHARS = 8000
_LIST_RESULT_MAX_ENTRIES = 200


@dataclass(frozen=True)
class ToolExecutionResult:
    """一次工具执行的终态（AFCP 3.1：显式数据结构；失败原因同样走此通道）。"""
    is_success: bool
    output_text: str


class _SandboxEscape(Exception):
    """沙盒逃逸的内部信号：execute() 兜底捕获转结构化失败，不对外抛。"""


class ToolExecutor:
    """三工具执行体：构造时锁死会话 cwd（AFCP 2.3 显式依赖）。"""

    def __init__(self, cwd: Path) -> None:
        # 构造即 resolve：后续每次判定都基于同一基准，cwd 自身含软链也不漂移
        self._cwd = cwd.resolve()

    def execute(self, request: ToolCallRequest) -> ToolExecutionResult:
        try:
            if request.tool_name == TOOL_NAME_WRITE_FILE:
                return self._write_file(request.arguments)
            if request.tool_name == TOOL_NAME_READ_FILE:
                return self._read_file(request.arguments)
            return self._list_dir(request.arguments)
        except _SandboxEscape as escape:
            return ToolExecutionResult(is_success=False, output_text=str(escape))
        except OSError as os_error:
            return ToolExecutionResult(
                is_success=False, output_text=f"文件系统错误：{os_error}")

    # ------------------------------------------------------------------
    # 沙盒
    # ------------------------------------------------------------------
    def _resolve_sandboxed(self, raw_path: str) -> Path:
        """相对路径解析到 cwd 内；绝对路径与逃逸（.. / 软链）一律拒。"""
        if Path(raw_path).is_absolute():
            raise _SandboxEscape(
                f"拒绝绝对路径 {raw_path!r}：工具路径必须相对会话工作目录")
        resolved = (self._cwd / raw_path).resolve()
        if not resolved.is_relative_to(self._cwd):
            raise _SandboxEscape(
                f"拒绝越界路径 {raw_path!r}：解析结果逃出会话工作目录")
        return resolved

    # ------------------------------------------------------------------
    # 三工具
    # ------------------------------------------------------------------
    def _write_file(self, arguments: dict[str, str]) -> ToolExecutionResult:
        raw_path = arguments["path"]
        target = self._resolve_sandboxed(raw_path)
        content = arguments["content"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return ToolExecutionResult(
            is_success=True,
            output_text=f"已写入 {raw_path}（{len(content.encode('utf-8'))} 字节）")

    def _read_file(self, arguments: dict[str, str]) -> ToolExecutionResult:
        raw_path = arguments["path"]
        target = self._resolve_sandboxed(raw_path)
        if not target.is_file():
            return ToolExecutionResult(
                is_success=False, output_text=f"文件不存在：{raw_path}")
        text = target.read_text(encoding="utf-8", errors="replace")
        if len(text) > _READ_RESULT_MAX_CHARS:
            text = (text[:_READ_RESULT_MAX_CHARS]
                    + f"\n……（已截断，全文超 {_READ_RESULT_MAX_CHARS} 字符）")
        return ToolExecutionResult(is_success=True, output_text=text)

    def _list_dir(self, arguments: dict[str, str]) -> ToolExecutionResult:
        raw_path = arguments["path"]
        target = self._resolve_sandboxed(raw_path)
        if not target.is_dir():
            return ToolExecutionResult(
                is_success=False, output_text=f"目录不存在：{raw_path}")
        # 目录条目带 / 后缀：模型只看文本，无法区分文件与目录会乱猜下一步
        entries = sorted(child.name + ("/" if child.is_dir() else "")
                         for child in target.iterdir())
        if not entries:
            return ToolExecutionResult(is_success=True, output_text="（空目录）")
        if len(entries) > _LIST_RESULT_MAX_ENTRIES:
            omitted = len(entries) - _LIST_RESULT_MAX_ENTRIES
            entries = entries[:_LIST_RESULT_MAX_ENTRIES]
            entries.append(f"……（另有 {omitted} 条已省略）")
        return ToolExecutionResult(is_success=True, output_text="\n".join(entries))
