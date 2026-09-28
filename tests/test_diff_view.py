"""Unified Diff 变更对比视图单元测试（1749 计划 T5 + 1914 计划 T4）。

- unified_rows：增/删/改/多 hunk/单行 hunk（无逗号形态）/无差异/超行数
  输入的 kind 与 old_no/new_no 行号序列断言（纯函数，不依赖 Qt/git）
- fetch_base_content：monkeypatch 注入假 run_git 测上游命中/HEAD 回退/
  双侧失败/二进制嗅探（不落真实 git 仓库，对齐 test_git_dir_status.py
  造服务范式）
- DiffViewDialog：offscreen 构造 + 假服务注入，覆盖表格化渲染（行号/
  整行底色/hunk 蓝带）、单侧缺失空文本对比（新文件全绿/删除全红）
  与降级占位页
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.diff_view import MAX_DIFF_LINES, unified_rows
from core.git import content as git_content
from gui.diff_view_dialog import DiffViewDialog
from gui.theme import get_theme_palette, list_available_themes

REPO = "/repo"  # 虚构仓库根（不落盘）


# ----------------------------------------------------------------------
# unified_rows（core 纯函数，含双侧行号）
# ----------------------------------------------------------------------
def kinds(rows):
    return [kind for kind, _, _, _ in rows]


def test_modified_rows():
    """改：del 紧跟 add，两端上下文行，hunk 头居首；行号双侧计数。"""
    rows = unified_rows("a\nb\nc", "a\nX\nc")
    assert kinds(rows) == ["hunk", "ctx", "del", "add", "ctx"]
    assert rows[0] == ("hunk", "@@ -1,3 +1,3 @@", None, None)
    assert rows[1] == ("ctx", " a", 1, 1)
    assert rows[2] == ("del", "-b", 2, None)
    assert rows[3] == ("add", "+X", None, 2)
    assert rows[4] == ("ctx", " c", 3, 3)


def test_added_rows():
    rows = unified_rows("a", "a\nb")
    assert rows == [("hunk", "@@ -1 +1,2 @@", None, None),
                    ("ctx", " a", 1, 1),
                    ("add", "+b", None, 2)]


def test_deleted_rows():
    rows = unified_rows("a\nb", "a")
    assert rows == [("hunk", "@@ -1,2 +1 @@", None, None),
                    ("ctx", " a", 1, 1),
                    ("del", "-b", 2, None)]


def test_multi_hunk_counters_reinit():
    """多 hunk：每个 hunk 头重置双侧计数器（context=1 强制分块）。"""
    old = "\n".join(f"line{i}" for i in range(1, 21))
    new_lines = old.splitlines()
    new_lines[0] = "CHANGED1"
    new_lines[19] = "CHANGED20"
    rows = unified_rows(old, "\n".join(new_lines), context=1)
    assert kinds(rows) == ["hunk", "del", "add", "ctx", "hunk", "ctx", "del", "add"]
    assert rows[4][1] == "@@ -19,2 +19,2 @@"
    assert rows[5] == ("ctx", " line19", 19, 19)
    assert rows[6] == ("del", "-line20", 20, None)
    assert rows[7] == ("add", "+CHANGED20", None, 20)


def test_single_line_hunk_no_comma():
    """单行 hunk 头 `@@ -1 +1 @@`（无逗号计数形态）行号同样正确。"""
    rows = unified_rows("only", "changed")
    assert rows == [("hunk", "@@ -1 +1 @@", None, None),
                    ("del", "-only", 1, None),
                    ("add", "+changed", None, 1)]


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
    assert rows[1] == ("del", "---x", 1, None)
    assert rows[2] == ("add", "+--y", None, 1)


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
# 主题包新键（1914 计划 T2）
# ----------------------------------------------------------------------
def test_chat_pack_diff_visual_keys_all_themes():
    """六主题 chat 包均含整行底色/hunk 蓝带四新键，且亮暗双套值不同。"""
    keys = ("diff_add_bg", "diff_del_bg", "diff_hunk_fg", "diff_hunk_bg")
    combos = set()
    for name in list_available_themes():
        chat = get_theme_palette(name)["chat"]
        assert all(key in chat for key in keys), name
        combos.add(tuple(chat[key] for key in keys))
    assert len(combos) == 2, "亮暗双套各一份（四亮主题共享亮套、两深主题共享暗套）"


# ----------------------------------------------------------------------
# DiffViewDialog（offscreen 构造 + 假服务注入）
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def _fake_service(enabled: bool = True, repo_root: str = REPO):
    """对话框只读 is_enabled/repo_root 两个结论（构造注释契约），
    SimpleNamespace 即满足。"""
    return SimpleNamespace(is_enabled=enabled, repo_root=repo_root if enabled else None)


def _patch_content(monkeypatch, base: str | None, worktree: str | None):
    monkeypatch.setattr(
        git_content, "fetch_base_content", lambda _root, _rel: base)
    monkeypatch.setattr(
        git_content, "fetch_worktree_content", lambda _root, _rel: worktree)


def test_dialog_renders_colored_rows(qapp, monkeypatch):
    """注入样例文本：行序列入表，HTML 携带行号、整行底色与 hunk 蓝带色值。"""
    _patch_content(monkeypatch, base="a\nb\nc", worktree="a\nX\nc")
    dialog = DiffViewDialog(_fake_service())
    dialog.show_diff(f"{REPO}/a.py")
    assert dialog._stack.currentWidget() is dialog._browser
    assert kinds(dialog._rows) == ["hunk", "ctx", "del", "add", "ctx"]
    chat = get_theme_palette(dialog._theme)["chat"]
    html = dialog._browser.toHtml()
    assert "<table" in html, "表格化布局"
    assert chat["diff_del_fg"] in html, "删除行前景（红）"
    assert chat["diff_add_fg"] in html, "新增行前景（绿）"
    assert chat["diff_del_bg"] in html, "删除行整行底色（浅红）"
    assert chat["diff_add_bg"] in html, "新增行整行底色（浅绿）"
    assert chat["diff_hunk_fg"] in html and chat["diff_hunk_bg"] in html, "hunk 蓝带"
    assert 'colspan="3"' in html, "hunk 行跨三列"
    assert "-b" in html and "+X" in html
    # 行号入格：del 行旧号 2、add 行新号 2（前缀行号均来自样例行序）
    assert ">2</" in html, "行号单元格"


def test_dialog_apply_theme_rerenders(qapp, monkeypatch):
    """apply_theme 换暗主题后按新调色板重渲染（前景/底色均跟随）。

    暗套底色为 #AARRGGBB 低透明叠底，Qt toHtml 序列化为 rgba(...) 形态，
    故断言其展开式子串。
    """
    _patch_content(monkeypatch, base="a\nb", worktree="a\nB")
    dialog = DiffViewDialog(_fake_service())
    dialog.show_diff(f"{REPO}/a.py")
    dialog.apply_theme("graphite")
    dark_chat = get_theme_palette("graphite")["chat"]
    html = dialog._browser.toHtml()
    assert dark_chat["diff_add_fg"] in html, "暗主题新增行前景跟随"
    assert dark_chat["diff_del_fg"] in html, "暗主题删除行前景跟随"
    assert dark_chat["diff_hunk_fg"] in html, "暗主题 hunk 蓝字跟随"
    assert "rgba(46,160,67" in html, "暗主题新增行底色（叠底 alpha 展开式）"
    assert "rgba(248,81,73" in html, "暗主题删除行底色（叠底 alpha 展开式）"


def test_dialog_not_enabled_placeholder(qapp, monkeypatch):
    dialog = DiffViewDialog(_fake_service(enabled=False))
    dialog.show_diff(f"{REPO}/a.py")
    assert dialog._stack.currentWidget() is dialog._placeholder
    assert "git" in dialog._placeholder.text()


def test_dialog_new_file_empty_base_all_added(qapp, monkeypatch):
    """基准侧无该文件（新文件）→ 空基准对比：整篇全绿 + 信息行标注。"""
    _patch_content(monkeypatch, base=None, worktree="a\nb")
    dialog = DiffViewDialog(_fake_service())
    dialog.show_diff(f"{REPO}/new.py")
    assert dialog._stack.currentWidget() is dialog._browser
    assert kinds(dialog._rows) == ["hunk", "add", "add"]
    assert "新文件" in dialog._info_label.toolTip()
    chat = get_theme_palette(dialog._theme)["chat"]
    html = dialog._browser.toHtml()
    assert chat["diff_add_bg"] in html, "整篇新增行绿底"
    assert chat["diff_del_bg"] not in html, "无删除行红底"


def test_dialog_deleted_file_empty_worktree(qapp, monkeypatch, tmp_path):
    """工作区无此文件（盘上不存在，如暂存后被删）→ 空工作区对比：整篇全红。"""
    _patch_content(monkeypatch, base="a\nb", worktree=None)
    dialog = DiffViewDialog(_fake_service(repo_root=str(tmp_path)))
    dialog.show_diff(str(tmp_path / "gone.py"))  # tmp_path 下确无此文件
    assert dialog._stack.currentWidget() is dialog._browser
    assert kinds(dialog._rows) == ["hunk", "del", "del"]
    assert "已删除" in dialog._info_label.toolTip()


def test_dialog_placeholder_centered(qapp, monkeypatch):
    """占位页文案垂直水平居中（对齐常规占位页观感）。"""
    dialog = DiffViewDialog(_fake_service(enabled=False))
    dialog.show_diff(f"{REPO}/a.py")
    assert dialog._placeholder.alignment() == Qt.AlignmentFlag.AlignCenter


def test_dialog_worktree_read_failure_placeholder(qapp, monkeypatch, tmp_path):
    """文件在盘但读不出（二进制/编码失败）→ 维持读取失败占位。"""
    target = tmp_path / "a.bin"
    target.write_bytes(b"PK\x00garbage")
    _patch_content(monkeypatch, base="a", worktree=None)
    dialog = DiffViewDialog(_fake_service(repo_root=str(tmp_path)))
    dialog.show_diff(str(target))
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
