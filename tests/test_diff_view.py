"""Unified Diff 变更对比视图单元测试（work plans/2026-0924-1749 计划 T5）。

- unified_rows：增/删/改/无差异/超行数五类输入的 kind 序列断言（纯函数，
  不依赖 Qt/git）
- fetch_base_content：monkeypatch 注入假 run_git 测上游命中/HEAD 回退/
  双侧失败/二进制嗅探（不落真实 git 仓库，对齐 test_git_dir_status.py
  造服务范式）
- DiffViewDialog：offscreen 构造 + 假服务注入，覆盖渲染着色与降级占位页
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QApplication

from core.diff_view import MAX_DIFF_LINES, unified_rows
from core.git import content as git_content
from gui.diff_view_dialog import DiffViewDialog
from gui.theme import get_theme_palette

REPO = "/repo"  # 虚构仓库根（不落盘）


# ----------------------------------------------------------------------
# unified_rows（core 纯函数）
# ----------------------------------------------------------------------
def kinds(rows):
    return [kind for kind, _ in rows]


def test_modified_rows():
    """改：del 紧跟 add，两端上下文行，hunk 头居首。"""
    rows = unified_rows("a\nb\nc", "a\nX\nc")
    assert kinds(rows) == ["hunk", "ctx", "del", "add", "ctx"]
    assert rows[0][1].startswith("@@")
    assert rows[2] == ("del", "-b")
    assert rows[3] == ("add", "+X")


def test_added_rows():
    rows = unified_rows("a", "a\nb")
    assert kinds(rows) == ["hunk", "ctx", "add"]
    assert rows[2] == ("add", "+b")


def test_deleted_rows():
    rows = unified_rows("a\nb", "a")
    assert kinds(rows) == ["hunk", "ctx", "del"]
    assert rows[2] == ("del", "-b")


def test_no_diff_returns_empty():
    assert unified_rows("a\nb", "a\nb") == []
    assert unified_rows("", "") == []


def test_over_line_limit_short_circuits():
    """任一单侧超 MAX_DIFF_LINES → 空列表（交由 UI 提示文件过大）。"""
    big = "\n".join(str(i) for i in range(MAX_DIFF_LINES + 1))
    assert unified_rows(big, "x") == []
    assert unified_rows("x", big) == []


def test_header_lines_skipped_positionally():
    """文件头 ---/+++ 按位置跳过：内容行本身以 -- 开头不误判。"""
    rows = unified_rows("--x", "--y")
    assert kinds(rows) == ["hunk", "del", "add"]
    assert rows[1] == ("del", "---x")
    assert rows[2] == ("add", "+--y")


# ----------------------------------------------------------------------
# fetch_base_content（假 run_git 注入，不落真实仓库）
# ----------------------------------------------------------------------
def _fake_run_git(mapping: dict[str, str]):
    """按 git show 参数返回预置内容的假 run_git。"""
    def run_git(_repo_dir, *args):
        if args[:1] == ("show",):
            return mapping.get(args[1])
        return None
    return run_git


def test_base_content_upstream_first(monkeypatch):
    monkeypatch.setattr(git_content, "run_git", _fake_run_git({
        "@{upstream}:a.py": "upstream 内容",
        "HEAD:a.py": "HEAD 内容",
    }))
    assert git_content.fetch_base_content(REPO, "a.py") == "upstream 内容"


def test_base_content_falls_back_to_head(monkeypatch):
    """无上游（@{upstream} 命令失败 → None）回退 HEAD。"""
    monkeypatch.setattr(git_content, "run_git", _fake_run_git({
        "HEAD:a.py": "HEAD 内容",
    }))
    assert git_content.fetch_base_content(REPO, "a.py") == "HEAD 内容"


def test_base_content_missing_path_returns_none(monkeypatch):
    monkeypatch.setattr(git_content, "run_git", _fake_run_git({}))
    assert git_content.fetch_base_content(REPO, "gone.py") is None


def test_base_content_binary_returns_none(monkeypatch):
    """二进制嗅探：解码后含 NUL 视为不可对比，双侧均 None。"""
    monkeypatch.setattr(git_content, "run_git", _fake_run_git({
        "@{upstream}:a.bin": "PK\x00\x01garbage",
        "HEAD:a.bin": "PK\x00\x01garbage",
    }))
    assert git_content.fetch_base_content(REPO, "a.bin") is None


# ----------------------------------------------------------------------
# DiffViewDialog（offscreen 构造 + 假服务注入）
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _fake_service(enabled: bool = True):
    """对话框只读 is_enabled/repo_root 两个结论（构造注释契约），
    SimpleNamespace 即满足。"""
    return SimpleNamespace(is_enabled=enabled, repo_root=REPO if enabled else None)


def _patch_content(monkeypatch, base: str | None, worktree: str | None):
    monkeypatch.setattr(
        git_content, "fetch_base_content", lambda _root, _rel: base)
    monkeypatch.setattr(
        git_content, "fetch_worktree_content", lambda _root, _rel: worktree)


def test_dialog_renders_colored_rows(qapp, monkeypatch):
    """注入样例文本：del/add/hunk 入行序列，HTML 携带主题红绿色值。"""
    _patch_content(monkeypatch, base="a\nb\nc", worktree="a\nX\nc")
    dialog = DiffViewDialog(_fake_service())
    dialog.show_diff(f"{REPO}/a.py")
    assert dialog._stack.currentWidget() is dialog._browser
    assert kinds(dialog._rows) == ["hunk", "ctx", "del", "add", "ctx"]
    chat = get_theme_palette(dialog._theme)["chat"]
    html = dialog._browser.toHtml()
    assert chat["diff_del_fg"] in html, "删除行着色（红）"
    assert chat["diff_add_fg"] in html, "新增行着色（绿）"
    assert chat["reasoning_fg"] in html, "hunk 头着色（灰）"
    assert "-b" in html and "+X" in html


def test_dialog_apply_theme_rerenders(qapp, monkeypatch):
    """apply_theme 换主题后按新调色板重渲染（亮/暗色值跟随）。"""
    _patch_content(monkeypatch, base="a\nb", worktree="a\nB")
    dialog = DiffViewDialog(_fake_service())
    dialog.show_diff(f"{REPO}/a.py")
    dialog.apply_theme("graphite")
    dark_chat = get_theme_palette("graphite")["chat"]
    html = dialog._browser.toHtml()
    assert dark_chat["diff_add_fg"] in html, "暗主题新增行色值跟随"
    assert dark_chat["diff_del_fg"] in html, "暗主题删除行色值跟随"


def test_dialog_not_enabled_placeholder(qapp, monkeypatch):
    dialog = DiffViewDialog(_fake_service(enabled=False))
    dialog.show_diff(f"{REPO}/a.py")
    assert dialog._stack.currentWidget() is dialog._placeholder
    assert "git" in dialog._placeholder.text()


def test_dialog_no_base_placeholder(qapp, monkeypatch):
    """基准侧无内容（未跟踪/基准不存在）→ 占位页。"""
    _patch_content(monkeypatch, base=None, worktree="a")
    dialog = DiffViewDialog(_fake_service())
    dialog.show_diff(f"{REPO}/new.py")
    assert dialog._stack.currentWidget() is dialog._placeholder
    assert "基准" in dialog._placeholder.text()


def test_dialog_worktree_read_failure_placeholder(qapp, monkeypatch):
    _patch_content(monkeypatch, base="a", worktree=None)
    dialog = DiffViewDialog(_fake_service())
    dialog.show_diff(f"{REPO}/a.py")
    assert dialog._stack.currentWidget() is dialog._placeholder
    assert "读取失败" in dialog._placeholder.text()


def test_dialog_too_large_placeholder(qapp, monkeypatch):
    big = "\n".join(str(i) for i in range(MAX_DIFF_LINES + 1))
    _patch_content(monkeypatch, base="a", worktree=big)
    dialog = DiffViewDialog(_fake_service())
    dialog.show_diff(f"{REPO}/big.py")
    assert dialog._stack.currentWidget() is dialog._placeholder
    assert "过大" in dialog._placeholder.text()


def test_dialog_no_diff_placeholder(qapp, monkeypatch):
    _patch_content(monkeypatch, base="a\nb", worktree="a\nb")
    dialog = DiffViewDialog(_fake_service())
    dialog.show_diff(f"{REPO}/a.py")
    assert dialog._stack.currentWidget() is dialog._placeholder
    assert "无差异" in dialog._placeholder.text()


def test_dialog_outside_repo_placeholder(qapp, monkeypatch):
    dialog = DiffViewDialog(_fake_service())
    dialog.show_diff("/elsewhere/a.py")
    assert dialog._stack.currentWidget() is dialog._placeholder
    assert "不在当前仓库" in dialog._placeholder.text()
