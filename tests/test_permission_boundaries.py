"""T4 权限边界：中性请求、厂商分类、四态矩阵与公共宿主隔离。

覆盖三类回归：
- 权限四态与危险命令决策不因分类搬家而改变，question 在 auto_all 下仍询问；
- Local 保留提问结构，DSH 未被重构顺带激活未实证提问能力；
- 公共策略/GUI 不再解析厂商载荷，provider 只把分类后的中性请求交给 GUI。
"""
import ast
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QLabel

from ide.gui.panels.chat.permission_dialog import PermissionDialog, QuestionDialog
from ide.llm.permission_policy import (
    DECISION_ALLOW,
    DECISION_ASK,
    MODE_AUTO_ALL,
    MODE_AUTO_GUARDED,
    MODE_CONFIRM_ALL,
    MODE_CONFIRM_EXECUTE,
    decide_permission,
    select_option_id,
)
from ide.llm.providers.acp import AcpConnection, PermissionRequest
from ide.llm.providers.dsh.permissions import (
    build_permission_request as build_dsh_request,
)
from ide.llm.providers.local.permissions import (
    build_permission_request as build_local_request,
)

PROVIDERS = Path(__file__).resolve().parents[1] / "src" / "ide" / "llm" / "providers"

_ALLOW_OPTIONS = [
    {"optionId": "allow_once", "name": "允许一次", "kind": "allow_once"},
    {"optionId": "allow_always", "name": "总是允许", "kind": "allow_always"},
    {"optionId": "reject_once", "name": "拒绝", "kind": "reject_once"},
]


def _params(kind: str = "execute", command: str | None = None,
            title: str = "Bash", raw_input: dict | None = None,
            options: list | None = None) -> dict:
    tool_call: dict = {"toolCallId": "tid-1", "title": title, "kind": kind}
    if command is not None:
        tool_call["rawInput"] = {"command": command}
    if raw_input is not None:
        tool_call["rawInput"] = raw_input
    return {"sessionId": "sid", "toolCall": tool_call,
            "options": _ALLOW_OPTIONS if options is None else options}


def _permission(params: dict) -> PermissionRequest:
    return PermissionRequest(params=params, kind="permission")


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


# ----------------------------------------------------------------------
# 权限四态矩阵与 optionId 回传
# ----------------------------------------------------------------------
def test_four_state_matrix_and_dangerous_command():
    safe_bash = _permission(_params(command="ls -la"))
    dangerous_bash = _permission(_params(command="rm -rf /"))
    read_tool = _permission(_params(kind="read", title="Read"))

    assert decide_permission(safe_bash, MODE_CONFIRM_ALL) == (DECISION_ASK, None)
    assert decide_permission(read_tool, MODE_CONFIRM_ALL) == (DECISION_ASK, None)
    assert decide_permission(safe_bash, MODE_CONFIRM_EXECUTE) == (DECISION_ASK, None)
    assert decide_permission(read_tool, MODE_CONFIRM_EXECUTE) == (DECISION_ALLOW, None)
    assert decide_permission(safe_bash, MODE_AUTO_GUARDED) == (DECISION_ALLOW, None)
    assert decide_permission(read_tool, MODE_AUTO_GUARDED) == (DECISION_ALLOW, None)
    assert decide_permission(dangerous_bash, MODE_AUTO_GUARDED)[0] == DECISION_ASK
    assert decide_permission(dangerous_bash, MODE_AUTO_GUARDED)[1] is not None
    assert decide_permission(dangerous_bash, MODE_AUTO_ALL) == (DECISION_ALLOW, None)


def test_unknown_mode_falls_back_to_auto_guarded():
    assert decide_permission(
        _permission(_params(command="ls -la")), "not-a-mode") == (DECISION_ALLOW, None)
    assert decide_permission(
        _permission(_params(command="rm -rf /")), "not-a-mode")[0] == DECISION_ASK


def test_question_always_asks_even_in_auto_all():
    for mode in (MODE_CONFIRM_ALL, MODE_CONFIRM_EXECUTE,
                 MODE_AUTO_GUARDED, MODE_AUTO_ALL):
        decision, reason = decide_permission(
            PermissionRequest(params=_params(title="AskUserQuestion"),
                              kind="question", questions=("选哪个？",)),
            mode)
        assert decision == DECISION_ASK, mode
        assert reason is None


def test_select_option_prefers_allow_always_and_keeps_agent_value():
    assert select_option_id(_ALLOW_OPTIONS) == "allow_always"
    assert select_option_id(list(reversed(_ALLOW_OPTIONS))) == "allow_always"
    assert select_option_id([{"optionId": "x", "kind": "reject_once"}]) is None
    assert select_option_id([]) is None


def test_connection_rejection_fallback_is_unchanged():
    """handler 返回 None → reject_once；无 handler → allow_once（C2 语义）。"""
    params = {"options": _ALLOW_OPTIONS}

    rejecting = object.__new__(AcpConnection)
    rejecting._agent_name = "test"
    rejecting._permission_handler = lambda _params: None
    sent: list[dict] = []
    rejecting._write_reverse = lambda payload, *_args: sent.append(payload)
    rejecting._handle_reverse(
        {"method": "session/request_permission", "id": 1, "params": params})
    assert sent[-1]["result"]["outcome"] == {
        "outcome": "selected", "optionId": "reject_once"}

    automatic = object.__new__(AcpConnection)
    automatic._agent_name = "test"
    automatic._permission_handler = None
    sent.clear()
    automatic._write_reverse = lambda payload, *_args: sent.append(payload)
    automatic._handle_reverse(
        {"method": "session/request_permission", "id": 2, "params": params})
    assert sent[-1]["result"]["outcome"] == {
        "outcome": "selected", "optionId": "allow_once"}


# ----------------------------------------------------------------------
# 厂商分类：Local 保留历史结构，DSH 不激活未实证提问
# ----------------------------------------------------------------------
def test_local_question_forms_are_classified_independently():
    structured = _params(
        title="AskUserQuestion",
        raw_input={"questions": [{"question": "颜色？"}]})
    request = build_local_request(structured)
    assert request.kind == "question"
    assert request.questions == ("颜色？",)

    historical_ask = _params(
        title="ask",
        raw_input={"question": "今晚吃啥", "options": []})
    request = build_local_request(historical_ask)
    assert request.kind == "question"
    assert request.questions == ("今晚吃啥",)

    ordinary = _params(command="pwd")
    request = build_local_request(ordinary)
    assert request.kind == "permission"
    assert request.params is ordinary


def test_dsh_question_shaped_payload_stays_permission():
    """DSH 提问回环未实证：带 Kimi 式字段也不得升级为 question。"""
    shaped = _params(
        title="AskUserQuestion",
        raw_input={"questions": [{"question": "颜色？"}]})
    request = build_dsh_request(shaped)
    assert request.kind == "permission"
    assert request.questions == ()
    assert request.params is shaped


# ----------------------------------------------------------------------
# provider 适配：GUI 收到中性请求，原始参数不丢
# ----------------------------------------------------------------------
class _FakeConnection:
    def __init__(self) -> None:
        self.handler = None
        self.terminated = False

    def set_permission_handler(self, handler):
        self.handler = handler

    def terminate(self):
        self.terminated = True


@pytest.mark.parametrize(("provider_cls", "builder", "expected_kind"), [
    ("dsh", build_dsh_request, "permission"),
    ("local", build_local_request, "question"),
])
def test_provider_adapts_raw_params_to_neutral_request(
        provider_cls, builder, expected_kind):
    from ide.llm.providers.dsh.provider import DshAcpLLM
    from ide.llm.providers.local.provider import LocalAcpLLM

    classes = {"dsh": DshAcpLLM, "local": LocalAcpLLM}
    provider = classes[provider_cls]()
    connection = _FakeConnection()
    provider._conn = connection
    seen: list[PermissionRequest] = []

    def handler(request: PermissionRequest) -> str:
        seen.append(request)
        return "allow_once"

    try:
        provider.set_permission_handler(handler)
        assert connection.handler is not None
        params = _params(
            title="AskUserQuestion",
            raw_input={"questions": [{"question": "颜色？"}]})
        assert connection.handler(params) == "allow_once"
        assert seen and seen[0].params is params
        assert seen[0].kind == expected_kind
        assert seen[0].kind == builder(params).kind

        provider.set_permission_handler(None)
        assert connection.handler is None
    finally:
        provider.close()


# ----------------------------------------------------------------------
# 公共宿主与依赖方向
# ----------------------------------------------------------------------
def test_question_dialog_uses_neutral_request_questions(qapp):
    params = _params(
        title="AskUserQuestion",
        raw_input={"questions": [{"question": "颜色？"}, {"question": "水果？"}]},
        options=[{"optionId": "q0", "name": "红色", "kind": "allow_once"}])
    request = build_local_request(params)
    dialog = QuestionDialog(request)
    try:
        texts = [label.text() for label in dialog.findChildren(QLabel)]
        assert "❓ 颜色？" in texts
        assert "❓ 水果？" in texts
        assert not hasattr(QuestionDialog, "_extract_questions")
    finally:
        dialog.deleteLater()

    permission = PermissionDialog(build_local_request(_params(command="ls")))
    try:
        assert permission.windowTitle() == "工具审批"
    finally:
        permission.deleteLater()


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_vendor_permissions_do_not_import_each_other():
    assert not (PROVIDERS / "kimi" / "permissions.py").exists()
    for vendor in ("dsh", "local"):
        others = [f"ide.llm.providers.{other}"
                  for other in ("kimi", "dsh", "local") if other != vendor]
        modules = _imported_modules(PROVIDERS / vendor / "permissions.py")
        for module in modules:
            assert not any(module == other or module.startswith(other + ".")
                           for other in others), \
                f"{vendor}/permissions.py 横向导入了其他厂商：{module}"


def test_public_permission_policy_imports_no_vendor_and_has_no_tool_names():
    path = PROVIDERS.parent / "permission_policy.py"
    modules = _imported_modules(path)
    for vendor in ("kimi", "dsh", "local"):
        assert not any(module == f"ide.llm.providers.{vendor}"
                       or module.startswith(f"ide.llm.providers.{vendor}.")
                       for module in modules)
    source = path.read_text(encoding="utf-8")
    assert "askuserquestion" not in source.lower()
    assert "_QUESTION_TOOL_NAMES" not in source
