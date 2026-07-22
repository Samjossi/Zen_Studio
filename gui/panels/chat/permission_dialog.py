"""ACP 工具审批对话框：工具名/参数摘要 + 选项按钮（允许一次/始终允许/拒绝）。"""
import json

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from gui.popups import TranslucentMenuPlainTextEdit
from llm import PermissionParams

#: 选项 kind → 中文按钮文案（agent 提供英文 name 时兜底）
KIND_LABELS = {
    "allow_once": "允许一次",
    "allow_always": "始终允许",
    "reject_once": "拒绝",
    "reject_always": "始终拒绝",
}


class PermissionDialog(QDialog):
    """`session/request_permission` 模态审批框。

    `selected_option_id()` 返回用户选中的 optionId（agent 提供原值）；
    关闭/ESC 返回 None，由上层按"拒绝"兜底。
    """

    def __init__(
        self,
        params: PermissionParams,
        parent: QWidget | None = None,
        danger_reason: str | None = None,
    ) -> None:
        """
        :param danger_reason: 危险命令黑名单命中原因（方案 F）；非 None 时
            标题改警示并展示原因行（危险场景三态按钮保留，用户可拒）
        """
        super().__init__(parent)
        self.setWindowTitle("危险命令审批" if danger_reason else "工具审批")
        self.setModal(True)
        self._option_id: str | None = None

        tool_call = params.get("toolCall") or {}
        title = tool_call.get("title") or "未知工具"
        kind = tool_call.get("kind") or "other"

        header = QLabel(f"Kimi ACP 请求执行工具：<b>{title}</b>（{kind}）", self)
        header.setWordWrap(True)

        warning: QLabel | None = None
        if danger_reason:
            warning = QLabel(f"⚠ 命中危险命令黑名单：{danger_reason}", self)
            warning.setWordWrap(True)
            warning.setStyleSheet("color: #c0392b; font-weight: bold;")

        detail = TranslucentMenuPlainTextEdit(self)
        detail.setReadOnly(True)
        detail.setPlainText(self._summarize(tool_call))
        detail.setFixedHeight(140)

        buttons = QDialogButtonBox(self)
        for opt in params.get("options") or []:
            label = KIND_LABELS.get(opt.get("kind"), opt.get("name") or opt.get("optionId", "?"))
            button = buttons.addButton(label, QDialogButtonBox.ButtonRole.ActionRole)
            if opt.get("kind") == "allow_once":
                button.setDefault(True)  # 回车默认最保守的允许项
            button.clicked.connect(lambda _checked=False, oid=opt.get("optionId"): self._choose(oid))

        layout = QVBoxLayout(self)
        layout.addWidget(header)
        if warning is not None:
            layout.addWidget(warning)
        layout.addWidget(detail)
        layout.addWidget(buttons)

    def _choose(self, option_id: str | None) -> None:
        self._option_id = option_id
        self.accept()

    def selected_option_id(self) -> str | None:
        return self._option_id

    @staticmethod
    def _summarize(tool_call: dict) -> str:
        """提取工具参数摘要：优先命令/路径等关键字段，兜底 rawInput JSON。

        kimi 的权限请求 toolCall.content 携带可读描述
        （如 "Requesting approval to Running: ls *.md"），rawInput 此时通常未携带。
        """
        parts: list[str] = []
        raw = tool_call.get("rawInput")
        if isinstance(raw, dict):
            cmd = raw.get("command") or raw.get("cmd")
            if cmd:
                parts.append(f"命令：{cmd}")
            path = raw.get("file_path") or raw.get("path") or raw.get("filePath")
            if path:
                parts.append(f"路径：{path}")
            if not parts and raw:
                parts.append(json.dumps(raw, ensure_ascii=False, indent=2)[:800])
        for block in tool_call.get("content") or []:
            text = ((block or {}).get("content") or {}).get("text")
            if text and text.strip():
                parts.append(text.strip())
        for loc in (tool_call.get("locations") or [])[:5]:
            if isinstance(loc, dict) and loc.get("path"):
                line = f":{loc['line']}" if loc.get("line") else ""
                parts.append(f"位置：{loc['path']}{line}")
        return "\n".join(parts) or "（无参数摘要）"
