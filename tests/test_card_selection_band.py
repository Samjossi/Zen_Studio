"""卡片轨正文块选区带自绘断言：BodyText/BodyHtml 抑原生带 + 对称自绘带。

思源黑体 leading 垫底使 Qt 原生选区带视觉偏下；正文块改用
selection_band 自绘（行墨盒上下 ±SELECTION_PAD_Y 对称），本文件核对
样式表抑制声明在位、自绘矩形纵向对称，以及覆写路径（set_extra_style /
各卡上色点位）不丢抑制声明。

需 offscreen 平台（tests/conftest.py 已统一注入 QT_QPA_PLATFORM=offscreen）。
"""
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import QApplication

from gui.panels.chat.cards import (
    BodyHtml,
    BodyText,
    CardColors,
    OpenStateMap,
    ThinkingCard,
    make_tool_card,
)
from gui.panels.chat.transcript import TextStreamBlock
from gui.selection_band import SELECTION_PAD_Y, selection_rects


def _app():
    return QApplication.instance() or QApplication([])


def _first_line_metrics(edit):
    """首行墨盒 metrics + 视口锚点（selection_rects 同源口径）。"""
    block = edit.document().findBlock(0)
    line = block.layout().lineAt(0)
    anchor = edit.cursorRect(QTextCursor(block))
    return line, anchor


class TestSuppressionQss:
    def test_body_text_stylesheet(self):
        _app()
        sheet = BodyText("正文").styleSheet()
        assert "#CardBodyText" in sheet
        assert "selection-background-color: rgba(0,0,0,0)" in sheet
        assert "selection-color: palette(text)" in sheet

    def test_body_html_stylesheet(self):
        _app()
        sheet = BodyHtml("<p>正文</p>").styleSheet()
        assert "#CardBodyHtml" in sheet
        assert "selection-background-color: rgba(0,0,0,0)" in sheet
        assert "selection-color: palette(text)" in sheet


class TestBandSymmetry:
    def test_body_text_band_symmetric(self):
        _app()
        edit = BodyText("第一行\n第二行")
        edit.resize(400, 200)
        edit.show()
        edit.selectAll()
        rects = selection_rects(edit)
        assert len(rects) >= 2  # 两行各一段
        line, anchor = _first_line_metrics(edit)
        band = rects[0]
        # 带高 = 行墨盒（ascent+descent）+ 上下对称留白
        assert abs(
            band.height()
            - (line.ascent() + line.descent() + 2 * SELECTION_PAD_Y)) < 0.5
        # 带上缘 = 行墨盒上缘 - PAD（上下对称即居中）
        assert abs(band.top() - (anchor.top() - SELECTION_PAD_Y)) < 0.5

    def test_body_html_band_symmetric(self):
        _app()
        edit = BodyHtml("<b>AI：</b>正文测试")
        edit.resize(400, 200)
        edit.show()
        edit.selectAll()
        rects = selection_rects(edit)
        assert rects
        line, anchor = _first_line_metrics(edit)
        band = rects[0]
        assert abs(
            band.height()
            - (line.ascent() + line.descent() + 2 * SELECTION_PAD_Y)) < 0.5
        assert abs(band.top() - (anchor.top() - SELECTION_PAD_Y)) < 0.5

    def test_no_selection_no_rects(self):
        _app()
        edit = BodyText("无选区")
        edit.show()
        assert selection_rects(edit) == []


class TestTextStreamBlockInherits:
    def test_stream_block_suppressed(self):
        """AI 正文流式块继承 BodyHtml，抑制声明随样式表在位。"""
        _app()
        block = TextStreamBlock("AI", QColor("#3a7bd5"), lambda p: False)
        assert "selection-background-color: rgba(0,0,0,0)" in block.styleSheet()


_SUPPRESSED = "selection-background-color: rgba(0,0,0,0)"


def _colors() -> CardColors:
    return CardColors("#888888", "#777777", "#ff0000", "#eeeeee",
                      "#dddddd", "#0000ff", "#00aa00", "#aa0000")


class TestExtraStyleKeepsSuppression:
    """附加样式入口回归：重建整表不得丢抑制声明（Qt 样式表整表替换语义）。"""

    def test_body_text_extra_style(self):
        _app()
        edit = BodyText("正文")
        edit.set_extra_style("color: red; font-style: italic;")
        sheet = edit.styleSheet()
        assert "#CardBodyText" in sheet
        assert _SUPPRESSED in sheet
        assert "color: red;" in sheet
        assert "font-style: italic;" in sheet

    def test_body_html_extra_style(self):
        _app()
        edit = BodyHtml("<p>正文</p>")
        edit.set_extra_style("color: red;")
        sheet = edit.styleSheet()
        assert "#CardBodyHtml" in sheet
        assert _SUPPRESSED in sheet
        assert "color: red;" in sheet


class TestCardRestylePaths:
    """各卡上色点位回归：历史上四处裸 setStyleSheet 覆写冲掉抑制声明
    （入参区 / failed 红字块 / bash 失败输出 / 思维链斜体），逐路径钉死。"""

    def test_input_detail_block_suppressed(self):
        """通用入参区（用户真机截图实证漏网面）。"""
        _app()
        card = make_tool_card(
            _colors(), OpenStateMap(),
            {"tool_kind": "search", "title": "Grep", "tool_call_id": "t-in",
             "input_detail": "prompt: x"})
        blocks = card.findChildren(BodyText)
        assert blocks
        for block in blocks:
            assert _SUPPRESSED in block.styleSheet()

    def test_error_body_block_suppressed(self):
        _app()
        card = make_tool_card(
            _colors(), OpenStateMap(),
            {"tool_kind": "search", "title": "Grep", "tool_call_id": "t-err"})
        card._on_failed({"error": "boom"})
        blocks = card.findChildren(BodyText)
        assert blocks
        for block in blocks:
            assert _SUPPRESSED in block.styleSheet()

    def test_bash_failed_output_suppressed(self):
        _app()
        card = make_tool_card(
            _colors(), OpenStateMap(),
            {"tool_kind": "execute", "title": "Bash", "tool_call_id": "t-bash"})
        card._on_failed({"error": "boom"})
        assert _SUPPRESSED in card._output.styleSheet()

    def test_thinking_card_body_suppressed(self):
        _app()
        card = ThinkingCard(_colors(), lambda b: (None, b))
        assert _SUPPRESSED in card._text.styleSheet()
        assert "font-style: italic;" in card._text.styleSheet()
