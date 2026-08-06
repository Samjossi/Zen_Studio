"""ACP 工具审批对话框：工具名/参数摘要 + 选项按钮（允许一次/始终允许/拒绝）。

QuestionDialog（0807-0148 计划 T2）：AskUserQuestion 类交互请求的专用
对话框——「提问-选项」语义（选项是答案不是审批动作），与 PermissionDialog
的工具审批语义分野：选项文案用 agent 提供的 name 原文，不走 KIND_LABELS
审批语义映射。
"""
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
from gui.theme import CHAT_PACK, WARNING_COLOR
from llm import PermissionParams

#: 选项 kind → 中文按钮文案（agent 提供英文 name 时兜底）
KIND_LABELS = {
    "allow_once": "允许一次",
    "allow_always": "始终允许",
    "reject_once": "拒绝",
    "reject_always": "始终拒绝",
}

#: 自由作答引导文案（0807-0445 计划，方案 B；QuestionDialog 与 QuestionCard
#: 共用单一来源）。背景：T0 spike 实证 kimi ACP request_permission 通道无法
#: 回传自由文本（H1 文本原文/H2 约定前缀/H3 _meta 全部失效——未知 optionId
#: 被静默视为 dismiss，.temp/frame_archive/askuser_other_*.json），客户端
#: 无法附加可回传文本的 Other 输入项，故降级为引导提示：Skip 后正文作答。
OTHER_HINT_TEXT = "💡 选项都不合适？点「Skip」后在聊天输入框直接回复，即可自由作答"


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
            warning.setStyleSheet(f"color: {WARNING_COLOR}; font-weight: bold;")

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


class QuestionDialog(QDialog):
    """AskUserQuestion 类交互请求对话框（0807-0148 计划 T2，止血交互载体）。

    语义：agent 提问 → 用户勾选 → optionId 原样回传。选项按钮文案用 agent
    提供的 name 原文（答案选项，禁走 KIND_LABELS 审批语义映射）；agent 附
    带的 Skip（reject_once）选项照常展示，用户显式跳过。

    multi_select 说明（实证结论，.temp/frame_archive/askuser_20260807_023820.json）：
    kimi 多选题（rawInput.questions[].multi_select=true）经 ACP 降级为单选
    ——request_permission 响应模型只能回一个 optionId，选项逐题单个编码。
    故本对话框统一单选按钮组；若未来后端出现多选组合编码，再升级复选框
    形态（TODO，须先抓帧证实回传协议）。

    自由作答（0807-0445 计划，方案 B）：按钮组上方展示 OTHER_HINT_TEXT
    引导提示——T0 spike 实证 ACP request_permission 通道无法回传自由文本
    （H1/H2/H3 全灭），无法附加可回传文本的 Other 输入项，故引导用户
    Skip 后在聊天输入框正文作答。
    """

    def __init__(self, params: PermissionParams, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 提问")
        self.setModal(True)
        self._option_id: str | None = None

        layout = QVBoxLayout(self)
        questions = self._extract_questions(params)
        if questions:
            for question in questions:
                q_label = QLabel(f"❓ {question}", self)
                q_label.setWordWrap(True)
                q_font = q_label.font()
                q_font.setBold(True)
                q_label.setFont(q_font)
                layout.addWidget(q_label)
        else:
            hint = QLabel("AI 请求你作出选择：", self)
            hint.setWordWrap(True)
            layout.addWidget(hint)

        buttons = QDialogButtonBox(self)
        for opt in params.get("options") or []:
            # 选项是答案不是审批动作：文案用 agent 提供 name 原文（不映射
            # KIND_LABELS——「允许一次」贴在「牛肉面」上是语义错乱）
            label = opt.get("name") or opt.get("optionId", "?")
            button = buttons.addButton(label, QDialogButtonBox.ButtonRole.ActionRole)
            button.clicked.connect(lambda _checked=False, oid=opt.get("optionId"): self._choose(oid))
        # 自由作答引导（0807-0445 方案 B）：ACP 通道回传不了自由文本，
        # 引导用户 Skip 后走正文输入（弱化色，单一来源 CHAT_PACK tool_fg）
        other_hint = QLabel(OTHER_HINT_TEXT, self)
        other_hint.setWordWrap(True)
        other_hint.setStyleSheet(f"color: {CHAT_PACK['tool_fg']};")
        layout.addWidget(other_hint)
        layout.addWidget(buttons)

    def _choose(self, option_id: str | None) -> None:
        self._option_id = option_id
        self.accept()

    def selected_option_id(self) -> str | None:
        """用户选中的 optionId（agent 提供原值）；关闭/ESC 返回 None（上层按拒绝兜底）。"""
        return self._option_id

    @staticmethod
    def _extract_questions(params: PermissionParams) -> list[str]:
        """问题文本提取：rawInput.questions 优先（结构化路径），
        toolCall.content 文本块兜底（kimi 实证：request_permission 的
        toolCall 不带 rawInput，问题文本在 content 块——
        .temp/frame_archive/askuser_*.json）。"""
        tool_call = params.get("toolCall") or {}
        raw = tool_call.get("rawInput")
        if isinstance(raw, dict) and isinstance(raw.get("questions"), list):
            questions = [q["question"] for q in raw["questions"]
                         if isinstance(q, dict) and isinstance(q.get("question"), str)]
            if questions:
                return questions
        texts: list[str] = []
        for block in tool_call.get("content") or []:
            text = ((block or {}).get("content") or {}).get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
        return texts
