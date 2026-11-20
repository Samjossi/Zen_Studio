"""Markdown 阅览行内代码带：哨兵定位、自绘几何、字体与复制回归。"""
import re

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QFont, QImage, QKeyEvent, QTextCursor
from PySide6.QtWidgets import QApplication

from ide.gui.panels.viewer.markdown_view import (
    _INLINE_CODE_SENTINEL,
    MarkdownView,
    _build_renderer,
)
from ide.gui.selection_band import SELECTION_PAD_Y
from ide.gui.theme import apply_theme, get_mono_family, get_theme_palette


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    font = QFont(app.font())
    font.setPointSize(13)
    app.setFont(font)
    yield app


def _make_view(source: str, width: int = 760) -> MarkdownView:
    view = MarkdownView(get_theme_palette("cloud"))
    view.resize(width, 200)
    view.setHtml(view._renderer.render(source))
    view.show()
    QApplication.processEvents()
    QApplication.processEvents()
    return view


def _family_at(view: MarkdownView, position: int) -> str:
    cursor = QTextCursor(view.document())
    cursor.setPosition(position)
    cursor.movePosition(
        QTextCursor.MoveOperation.NextCharacter,
        QTextCursor.MoveMode.KeepAnchor,
    )
    return cursor.charFormat().font().family()


def test_renderer_adds_sentinels_and_preserves_escaping(qapp):
    html = _build_renderer().render("行内 `<tag>&` 测试")
    assert (f"<code>{_INLINE_CODE_SENTINEL}&lt;tag&gt;&amp;"
            f"{_INLINE_CODE_SENTINEL}</code>") in html


def test_stylesheet_uses_mono_font_and_keeps_pre_background(qapp):
    palette = get_theme_palette("cloud")
    sheet = MarkdownView._build_stylesheet(palette)
    assert f'code {{ font-family: "{get_mono_family()}"; }}' in sheet
    assert f"pre {{ background-color: {palette['chrome']['code_bg']};" in sheet
    assert not re.search(r"(?:^|\s)(?:pre\s+)?code[^{]*\{[^}]*background-color", sheet)


def test_code_block_does_not_gain_inline_sentinels(qapp):
    html = _build_renderer().render("```py\nblock\n```\n")
    assert "<pre><code" in html
    assert _INLINE_CODE_SENTINEL not in html


def test_inline_code_ranges_match_document_text(qapp):
    view = _make_view("前文 `alpha` 中段 `beta` 尾")
    plain = view.document().toPlainText()
    expected = []
    for content in ("alpha", "beta"):
        start = plain.index(content)
        expected.append((start, start + len(content)))
    assert view._inline_code_ranges == expected


def test_unmatched_sentinel_is_ignored(qapp):
    view = _make_view("普通文本")
    view.setHtml(f"<p>落单 {_INLINE_CODE_SENTINEL} 定位符</p>")
    QApplication.processEvents()
    assert view._inline_code_ranges == []
    assert not view.grab().isNull()


def test_wrapped_inline_code_has_one_rect_per_line(qapp):
    view = _make_view("行内 `abcdefghijklmnopqrstuvwxyz0123456789` 尾", width=180)
    assert len(view._inline_code_ranges) == 1
    assert len(view._inline_code_rects()) >= 2


def test_inline_code_uses_bundled_sarasa(qapp):
    view = _make_view("行内 `code文字` 尾")
    assert get_mono_family() == "Sarasa Term SC"
    assert _family_at(view, view.document().toPlainText().index("code")) == "Sarasa Term SC"


def test_copy_menu_and_ctrl_c_strip_sentinels(qapp):
    view = _make_view("前 `alpha` 后\n\n次段 `beta`")
    view.selectAll()
    menu = view._build_context_menu()
    menu.actions()[0].trigger()
    assert QApplication.clipboard().text() == "前 alpha 后\n次段 beta "

    plain = view.document().toPlainText()
    start = plain.index("alpha") - 1
    cursor = view.textCursor()
    cursor.setPosition(start)
    cursor.setPosition(start + len("alpha") + 2, QTextCursor.MoveMode.KeepAnchor)
    view.setTextCursor(cursor)
    event = QKeyEvent(
        QEvent.Type.KeyPress,
        Qt.Key.Key_C,
        Qt.KeyboardModifier.ControlModifier,
    )
    QApplication.sendEvent(view, event)
    assert QApplication.clipboard().text() == "alpha"


def _is_color(pixel: QColor, expected: QColor, tolerance: int = 2) -> bool:
    return all(abs(actual - target) <= tolerance for actual, target in zip(
        (pixel.red(), pixel.green(), pixel.blue()),
        (expected.red(), expected.green(), expected.blue()),
    ))


def _band_rows(image: QImage, color: QColor) -> list[tuple[int, int]]:
    bands = []
    y = 0
    while y < image.height():
        has_band = any(
            _is_color(image.pixelColor(x, y), color)
            for x in range(image.width())
        )
        if not has_band:
            y += 1
            continue
        top = y
        while y < image.height() and any(
                _is_color(image.pixelColor(x, y), color)
                for x in range(image.width())):
            y += 1
        bands.append((top, y - 1))
    return bands


def test_pixel_band_is_centered_on_ink(qapp):
    """Sarasa Term SC 13pt 下，带心与墨心偏差不超过 1px。"""
    view = _make_view("普通文本参照行\n\n行内 `code_span` 灰底测试 结束")
    image = view.viewport().grab().toImage().convertToFormat(
        QImage.Format.Format_ARGB32)
    bands = [band for band in _band_rows(
        image, QColor(get_theme_palette("cloud")["chrome"]["code_bg"]))
        if band[1] > band[0]]
    assert len(bands) == 1
    top, bottom = bands[0]
    middle = (top + bottom) // 2
    band_color = QColor(get_theme_palette("cloud")["chrome"]["code_bg"])
    xs = [x for x in range(image.width())
          if _is_color(image.pixelColor(x, middle), band_color)]
    assert xs
    left, right = min(xs), max(xs)
    ink_rows = [y for y in range(top, bottom + 1)
                for x in range(left, right + 1)
                if (lambda c: c.red() < 150 and c.green() < 150 and c.blue() < 150)(
                    image.pixelColor(x, y))]
    assert ink_rows
    band_center = (top + bottom) / 2
    ink_center = (min(ink_rows) + max(ink_rows)) / 2
    assert abs(ink_center - band_center) <= 1.0

    start, _ = view._inline_code_ranges[0]
    block = view.document().findBlock(start)
    line = block.layout().lineAt(0)
    expected_height = line.ascent() + line.descent() + 2 * SELECTION_PAD_Y
    assert abs((bottom - top + 1) - expected_height) <= 1.0
