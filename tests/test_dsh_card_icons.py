"""DSH 卡片图标分型与 read_image 图片卡（0200 计划 T2–T5）。

图标矩阵走真实 `DshUpdateMapper` + `make_tool_card`，确保锁定的是运行时
分派结果而不是手工 payload；图片卡状态直接驱动卡片 update 钩子，覆盖
「仅入参 / 结果图 / 同源 / 缺失 / 超大 / 无路径」六态。
"""
import json
import shutil
import uuid
from pathlib import Path

import pytest
from PySide6.QtGui import QColor, QPixmap
from PySide6.QtWidgets import QApplication, QLabel

from ide.gui.panels.chat.card_base import (
    CardColors,
    OpenStateMap,
    ThinkingCard,
)
from ide.gui.panels.chat.vendors.dsh import cards as dsh_cards
from ide.gui.panels.chat.vendors.dsh import factory as dsh_factory
from ide.llm.providers.dsh.updates import DshUpdateMapper

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SESSION_ID = "dsh-icons"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def colors():
    return CardColors(
        reasoning_fg="#8899aa",
        tool_fg="#aabbcc",
        tool_error_fg="#ff5555",
        user_bubble_bg="#223344",
        tool_output_bg="#112233",
        link_fg="#66ccff",
        diff_add_fg="#55cc55",
        diff_del_fg="#cc5555",
    )


@pytest.fixture
def mapper():
    return DshUpdateMapper()


@pytest.fixture
def media_dir(qapp):
    root = PROJECT_ROOT / ".temp" / f"pytest-dsh-icons-{uuid.uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    yield root
    shutil.rmtree(root, ignore_errors=True)


def _frame(update: dict) -> dict:
    return {"method": "session/update",
            "params": {"sessionId": SESSION_ID, "update": update}}


def _call(mapper, title: str, kind: str, raw_input: dict | None = None,
          tid: str = "t1") -> dict:
    update = {"sessionUpdate": "tool_call", "toolCallId": tid,
              "title": title, "kind": kind, "status": "pending"}
    if raw_input is not None:
        update["rawInput"] = raw_input
    chunk = mapper.map_session_update(_frame(update))
    assert chunk is not None
    return chunk.payload


def _completed(mapper, tid: str, *, output: str | None = None,
               images: list[str] | None = None,
               error: str | None = None) -> dict:
    update = {"sessionUpdate": "tool_call_update", "toolCallId": tid,
              "status": "failed" if error else "completed"}
    if output is not None:
        update["rawOutput"] = {"output": output}
    if images is not None:
        update["rawOutput"] = {"output": json.dumps(
            [{"type": "image_url", "imageUrl": {"url": src}} for src in images],
            ensure_ascii=False)}
    if error is not None:
        update["rawOutput"] = {"output": error}
    chunk = mapper.map_session_update(_frame(update))
    assert chunk is not None
    return chunk.payload


def _png(path: Path, color: str = "#c76b1e") -> Path:
    pix = QPixmap(32, 24)
    pix.fill(QColor(color))
    assert pix.save(str(path), "PNG")
    return path


def _read_image_card(mapper, colors, path: str | None,
                     tid: str = "img") -> dsh_cards.MediaReadCard:
    raw_input = {"file_path": path} if path else None
    payload = _call(mapper, "read_image", "other", raw_input, tid=tid)
    card = dsh_factory.make_tool_card(colors, OpenStateMap(), payload)
    assert isinstance(card, dsh_cards.MediaReadCard)
    return card


# ----------------------------------------------------------------------
# T2：DSH 私有图标覆盖机制
# ----------------------------------------------------------------------
class _PlainCard(dsh_cards.ToolCard):
    """未声明覆盖：沿用 ACP kind 图标。"""


class _DeclaredCard(dsh_cards.ToolCard):
    _header_icon = "★"


def _shell_payload(kind: str = "read") -> dict:
    return {"tool_call_id": "shell", "title": "shell", "tool_kind": kind,
            "status": "pending"}


def test_header_icon_falls_back_to_kind_icon(qapp, colors):
    card = _PlainCard(colors, OpenStateMap(), _shell_payload("read"))
    assert card._icon_label.text() == "📄"


def test_header_icon_uses_class_attribute(qapp, colors):
    card = _DeclaredCard(colors, OpenStateMap(), _shell_payload("read"))
    assert card._icon_label.text() == "★"


def test_header_icon_keyword_overrides_class_attribute(qapp, colors):
    card = _DeclaredCard(colors, OpenStateMap(), _shell_payload("read"),
                         header_icon="☆")
    assert card._icon_label.text() == "☆"


# ----------------------------------------------------------------------
# T3/T4：可见卡型图标矩阵
# ----------------------------------------------------------------------
@pytest.mark.parametrize(("title", "kind", "raw_input", "expect_icon"), [
    ("bash", "execute", {"command": "pytest -q"}, "💻"),
    ("write", "edit", {"file_path": "a.py", "content": "x = 1\n"}, "📝"),
    ("read", "read", {"file_path": "a.py"}, "📄"),
    ("grep", "search", {"pattern": "media_path"}, "🔍"),
    ("web_fetch", "fetch", {"url": "https://example.invalid"}, "🌐"),
    ("subagent", "other", {"prompt": "探索"}, "🤖"),
    ("read_image", "other", {"file_path": "a.png"}, "\U0001F441\uFE0F"),
    ("todo_write", "other", {"todos": []}, "🎯"),
    ("mcp__demo__tool", "other", {"arg": "x"}, "🔌"),
    ("未收录工具", "other", {"arg": "x"}, "🧩"),
    ("AskUserQuestion", "other", {"questions": []}, "🧩"),
])
def test_dsh_visible_card_icons(qapp, colors, mapper, title, kind,
                                raw_input, expect_icon):
    payload = _call(mapper, title, kind, raw_input)
    card = dsh_factory.make_tool_card(colors, OpenStateMap(), payload)
    assert card._icon_label.text() == expect_icon


def test_read_image_icon_keeps_vs16(qapp, colors, mapper):
    card = _read_image_card(mapper, colors, "a.png")
    assert card._icon_label.text() == "\U0001F441\uFE0F"
    assert len(card._icon_label.text()) == 2, "VS16 被吃掉会退成灰黑文本眼"


def test_mcp_prefix_does_not_hijack_read_image(qapp, colors, mapper):
    """工具名分派优先于 MCP 前缀：mcp__demo__read_image 仍是图片卡。"""
    payload = _call(mapper, "mcp__demo__read_image", "other",
                    {"file_path": "a.png"})
    card = dsh_factory.make_tool_card(colors, OpenStateMap(), payload)
    assert isinstance(card, dsh_cards.MediaReadCard)
    assert card._icon_label.text() == "\U0001F441\uFE0F"


def test_question_card_reserves_question_mark(qapp, colors):
    card = dsh_cards.QuestionCard(
        colors, OpenStateMap(),
        {"tool_call_id": "q", "title": "AskUserQuestion",
         "tool_kind": "other", "status": "pending"})
    assert card._icon_label.text() == "❓"


def test_todo_card_keeps_clipboard_icon(qapp, colors):
    card = dsh_cards.TodoCard(colors)
    assert "📋" in [label.text() for label in card.findChildren(QLabel)]


def test_thinking_card_keeps_brain_icon(qapp, colors):
    card = ThinkingCard(colors, lambda text: ("", text))
    assert card._icon_label.text() == "🧠"


# ----------------------------------------------------------------------
# T5：read_image 图片优先卡
# ----------------------------------------------------------------------
def test_read_image_input_only_shows_main_preview(qapp, colors, mapper,
                                                  media_dir):
    source = _png(media_dir / "input.png")
    card = _read_image_card(mapper, colors, str(source))
    assert not card._image.isHidden()
    assert card._caption.text() == "输入"
    assert card._input_thumb.isHidden()
    assert "input.png" in card._image.toHtml()
    assert 'width="480"' in card._image.toHtml()


def test_read_image_completed_promotes_result_and_demotes_input(
        qapp, colors, mapper, media_dir):
    source = _png(media_dir / "input.png")
    result = _png(media_dir / "result.png", "#2a9d8f")
    card = _read_image_card(mapper, colors, str(source))
    card.apply_update(_completed(mapper, "img", images=[result.as_uri()]))
    assert card._caption.text() == "结果"
    assert card._result_src == result.as_uri()
    assert not card._input_thumb.isHidden()
    assert not card._input_caption.isHidden()


def test_read_image_same_source_dedupes(qapp, colors, mapper, media_dir):
    source = _png(media_dir / "same.png")
    card = _read_image_card(mapper, colors, str(source))
    card.apply_update(_completed(mapper, "img", images=[source.as_uri()]))
    assert not card._image.isHidden()
    assert card._input_thumb.isHidden()
    assert not not card._input_caption.isHidden()


def test_read_image_missing_file_degrades_without_broken_image(
        qapp, colors, mapper):
    card = _read_image_card(mapper, colors, "does/not/exist.png")
    assert not not card._image.isHidden()
    assert not card._placeholder.isHidden()
    assert "文件不存在" in card._placeholder.text()
    assert not card._path_host.isHidden()


def test_read_image_large_file_skips_preview(qapp, colors, mapper, media_dir,
                                             monkeypatch):
    monkeypatch.setattr(dsh_cards, "MEDIA_THUMB_MAX_BYTES", 10)
    source = _png(media_dir / "large.png")
    card = _read_image_card(mapper, colors, str(source))
    assert not not card._image.isHidden()
    assert "图片过大" in card._placeholder.text()


def test_read_image_without_media_path_falls_back_to_text(qapp, colors, mapper):
    card = _read_image_card(mapper, colors, None)
    assert card._path_host.isHidden()
    card.apply_update(_completed(mapper, "img", output="无可显示图片"))
    assert not card._output.isHidden()
    assert "无可显示图片" in card._output.toPlainText()


def test_read_image_failure_shows_error_text(qapp, colors, mapper):
    card = _read_image_card(mapper, colors, "a.png")
    card.apply_update(_completed(mapper, "img", error="读取失败：权限不足"))
    assert not card._output.isHidden()
    assert "权限不足" in card._output.toPlainText()


def test_read_image_late_path_after_result_adds_secondary(
        qapp, colors, mapper, media_dir):
    source = _png(media_dir / "late.png")
    result = _png(media_dir / "late_result.png", "#2a9d8f")
    card = _read_image_card(mapper, colors, None)
    card.apply_update(_completed(mapper, "img", images=[result.as_uri()]))
    assert card._input_thumb.isHidden()
    late = {"tool_call_id": "img", "status": "completed",
            "media_path": str(source)}
    card.apply_update(late)
    assert not card._input_thumb.isHidden()
    assert "late.png" in card._input_thumb.toHtml()
    assert 'width="320"' in card._input_thumb.toHtml()


def test_read_image_output_after_result_stays_hidden(
        qapp, colors, mapper, media_dir):
    """结果图已定格后再收到纯文本完成帧，文本不得压到图下。"""
    source = _png(media_dir / "input.png")
    result = _png(media_dir / "result.png", "#2a9d8f")
    card = _read_image_card(mapper, colors, str(source))
    card.apply_update(_completed(mapper, "img", images=[result.as_uri()]))
    card.apply_update(_completed(mapper, "img", output="迟到的文本"))
    assert card._output.isHidden()


def test_read_image_subtitle_is_filename_and_directory(qapp, colors, mapper,
                                                       media_dir):
    source = _png(media_dir / "pic.png")
    card = _read_image_card(mapper, colors, str(source))
    assert card._subtitle_label._full_text == "pic.png · " + media_dir.name
    assert card._subtitle_label.toolTip() == str(source)
    assert card._path_label._full_text == str(source)
    assert card._path_label.toolTip() == str(source)
