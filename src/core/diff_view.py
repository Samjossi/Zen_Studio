"""Unified Diff 差异行生成：difflib 纯函数封装（零 Qt / 零 git 依赖，可脱离 UI 单测）。

选型：文档/选型记录/2026-0924-1746_变更对比Diff视图功能方案选型.md
（difflib 标准库 + Qt 原生渲染，零新增依赖）。
"""
from __future__ import annotations

import difflib

#: 差异行元组（kind, text）；kind ∈ del（删除行）/ add（新增行）/
#: ctx（上下文行）/ hunk（@@ 块头）；text 保留 difflib 原始行首前缀
#: （-/+/空格），hunk 为 @@ 行原文
DiffRow = tuple[str, str]

#: 单侧文本行数上限：difflib 纯 Python 实现，万行以上偏慢；
#: 超限由调用方走降级文案，不做分块优化
MAX_DIFF_LINES = 20000


def unified_rows(old_text: str, new_text: str, context: int = 3) -> list[DiffRow]:
    """基准文本 vs 工作区文本 → Unified Diff 行序列。

    跳过 `---`/`+++` 文件头（UI 自有信息行承载路径）；无差异或任一
    文本超 MAX_DIFF_LINES 返回空列表（调用方据此分别提示无差异/文件过大）。
    """
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    if len(old_lines) > MAX_DIFF_LINES or len(new_lines) > MAX_DIFF_LINES:
        return []
    rows: list[DiffRow] = []
    # enumerate 按位置跳过前两行文件头：无差异时 unified_diff 整体不产出
    # （含头），故位置判定安全
    for index, line in enumerate(difflib.unified_diff(
            old_lines, new_lines, n=context, lineterm="")):
        if index < 2:
            continue
        if line.startswith("@@"):
            rows.append(("hunk", line))
        elif line.startswith("-"):
            rows.append(("del", line))
        elif line.startswith("+"):
            rows.append(("add", line))
        else:
            rows.append(("ctx", line))
    return rows
