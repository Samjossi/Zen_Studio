"""ACP `session/prompt` 的 ContentBlock 构造（标准 text/image 块）。"""
import base64
import logging
from pathlib import Path

from ide.core.paths import workspace_display_path
from ide.llm.base import Message

logger = logging.getLogger(__name__)


def build_prompt_blocks(message: Message, workspace_root: str | None = None) -> list[dict]:
    """末条 user 消息 → ACP `session/prompt` 的 ContentBlock 数组。

    恒以 text 块打头；随后每张附件一个 image 块（读盘 base64，调用点
    位于 worker 线程，GUI 零阻塞）。读盘失败（文件被删/无权限）跳过
    该图续发其余块——尽力而为，不阻断整轮发送。

    图片路径透传（2026-0806-1908 计划 T1）：图片内容与路径同等重要——
    AI 不知路径就无法用工具再次读取或准确引用。每张成功入块的图片在
    text 块尾部追加一行「[附图 N] 图片路径：<路径>」，路径形式经
    workspace_display_path 换算（工作区内相对、区外绝对；root 未知时
    退化绝对路径）。读盘失败的图不拼路径（块未发，避免引用未送达内容）。

    空 text 回退占位文案（0340 计划 D5，T0 spike 2026-08-01 实证）：
    部分 CLI 对空 text 块返回协议错误（如 -32603/-32602）——纯图发送时
    统一占位「请查看附图。」；接受空块的 CLI 对占位文案无害且语义更
    明确，故不按后端分支。
    """
    images = message.get("images", [])
    text = message["content"] or ("请查看附图。" if images else "")
    blocks: list[dict] = []
    path_lines: list[str] = []
    for img in images:
        try:
            data = base64.b64encode(Path(img["path"]).read_bytes()).decode()
        except OSError:
            logger.exception("附图读盘失败，跳过该图续发其余块")
            continue
        blocks.append({"type": "image", "data": data, "mimeType": img["mime_type"]})
        path_lines.append(
            f"[附图 {len(path_lines) + 1}] 图片路径："
            f"{workspace_display_path(img['path'], workspace_root)}"
        )
    if path_lines:
        text = "\n".join([text, *path_lines])
    return [{"type": "text", "text": text}, *blocks]


# ----------------------------------------------------------------------
# ACP 协议层定型（传输边界 dict 合理，入口处定型；键名依协议原文 camelCase）
