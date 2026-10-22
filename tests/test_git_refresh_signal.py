"""Git 刷新实时化：files_maybe_changed 信号链路（2026-1022-1627 计划）。

- 纯函数 `_is_fs_mutation_terminal`：kind/status 各分支判定
- `ChatPanel` 喂 tool_call_update 帧断言信号发射（含子代理嵌套帧）
- `ChatTabs` 转发链（面板信号 → 顶层转发信号）
"""
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

import gui.settings as settings_mod
from gui.panels.chat.panel import ChatPanel, _is_fs_mutation_terminal
from gui.panels.chat.tabs import ChatTabs
import gui.panels.chat.model_bar as model_bar_mod
from llm import Chunk
from llm.registry import BackendSpec

#: 持久化隔离目录（项目纪律：临时文件一律落 ./.temp/，禁用系统临时目录）
_TEMP_DIR = Path(__file__).resolve().parents[1] / ".temp" / "test_git_refresh_signal"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def isolated_settings(monkeypatch):
    """settings 读写重定向到 ./.temp/ 隔离文件（防污染真实 settings.json）。"""
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)
    settings_file = _TEMP_DIR / "settings.json"
    settings_file.unlink(missing_ok=True)  # 清上次运行残留，保证默认值语义
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(settings_mod, "SETTINGS_LOCK_FILE", _TEMP_DIR / "settings.lock")
    return settings_file


@pytest.fixture()
def fake_registry(monkeypatch):
    """伪造单接口注册表（ModelBar 构建不探测真实 CLI）。"""
    spec = BackendSpec(
        name="fake-acp",
        label="Fake ACP",
        vendor="fake",
        vendor_label="Fake",
        available=lambda: True,
        list_models=lambda: ["m1"],
        factory=lambda **_kw: None,
        efforts=("auto",),
        default_effort="auto",
    )
    monkeypatch.setattr(model_bar_mod, "vendor_groups", lambda: {"fake": [spec]})
    monkeypatch.setattr(model_bar_mod, "spec_of", lambda name: spec)
    monkeypatch.setattr(model_bar_mod, "vendor_of", lambda name: "fake")
    monkeypatch.setattr(model_bar_mod, "resolve_efforts",
                        lambda spec, model: (spec.efforts, spec.default_effort))
    return spec


def _update_chunk(payload: dict) -> Chunk:
    return Chunk(kind="tool_call_update", text="", payload=payload)


# ----------------------------------------------------------------------
# 纯函数：_is_fs_mutation_terminal
# ----------------------------------------------------------------------
def test_edit_completed_hits():
    assert _is_fs_mutation_terminal(
        {"status": "completed", "tool_kind": "edit"}) is True


def test_edit_failed_hits():
    # multi-edit 中断等场景可能部分落盘，failed 同样提示刷新
    assert _is_fs_mutation_terminal(
        {"status": "failed", "tool_kind": "edit"}) is True


def test_execute_completed_hits():
    # execute 覆盖 sed/重定向/代码生成/git checkout 等命令行改盘
    assert _is_fs_mutation_terminal(
        {"status": "completed", "tool_kind": "execute"}) is True


@pytest.mark.parametrize("kind", ["read", "search", "fetch", "think", "todo", "other"])
def test_non_mutation_kinds_miss(kind):
    assert _is_fs_mutation_terminal(
        {"status": "completed", "tool_kind": kind}) is False


def test_in_progress_misses():
    # 改动未落定，中间帧不触发
    assert _is_fs_mutation_terminal(
        {"status": "in_progress", "tool_kind": "edit"}) is False


def test_missing_fields_miss():
    # 缺 kind / 缺 status 容错不误报
    assert _is_fs_mutation_terminal({"status": "completed"}) is False
    assert _is_fs_mutation_terminal({"tool_kind": "edit"}) is False
    assert _is_fs_mutation_terminal({}) is False


# ----------------------------------------------------------------------
# 面板级：tool_call_update 帧 → files_maybe_changed
# ----------------------------------------------------------------------
def _feed_and_count(panel: ChatPanel, chunks: list[Chunk]) -> int:
    fired: list[bool] = []
    panel.files_maybe_changed.connect(lambda: fired.append(True))
    for chunk in chunks:
        panel._on_chunk(chunk)
    panel.files_maybe_changed.disconnect()
    return len(fired)


def test_panel_emits_on_edit_terminal(qapp, isolated_settings, fake_registry):
    panel = ChatPanel("fake-acp", "m1", str(_TEMP_DIR))
    count = _feed_and_count(panel, [
        _update_chunk({"tool_call_id": "t1", "status": "completed",
                       "tool_kind": "edit"}),
    ])
    assert count == 1


def test_panel_silent_on_read_and_progress(qapp, isolated_settings, fake_registry):
    panel = ChatPanel("fake-acp", "m1", str(_TEMP_DIR))
    count = _feed_and_count(panel, [
        _update_chunk({"tool_call_id": "t1", "status": "completed",
                       "tool_kind": "read"}),
        _update_chunk({"tool_call_id": "t2", "status": "in_progress",
                       "tool_kind": "edit"}),
    ])
    assert count == 0


def test_panel_emits_on_nested_subagent_frame(qapp, isolated_settings, fake_registry):
    # 子代理嵌套帧（带 parent_tool_call_id）与主流同路径，同样触发
    panel = ChatPanel("fake-acp", "m1", str(_TEMP_DIR))
    count = _feed_and_count(panel, [
        _update_chunk({"tool_call_id": "task1/edit1",
                       "parent_tool_call_id": "task1",
                       "status": "completed", "tool_kind": "edit"}),
    ])
    assert count == 1


# ----------------------------------------------------------------------
# 转发链：ChatPanel.files_maybe_changed → ChatTabs.files_maybe_changed
# ----------------------------------------------------------------------
def test_tabs_forwards_signal(qapp, isolated_settings, fake_registry):
    tabs = ChatTabs(str(_TEMP_DIR))
    tabs.add_tab()
    panel = tabs._tabs.widget(0)
    fired: list[bool] = []
    tabs.files_maybe_changed.connect(lambda: fired.append(True))
    panel._on_chunk(_update_chunk(
        {"tool_call_id": "t1", "status": "completed", "tool_kind": "execute"}))
    assert len(fired) == 1
