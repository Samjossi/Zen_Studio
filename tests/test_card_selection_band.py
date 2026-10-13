"""卡片轨正文块选区带自绘断言：BodyText/BodyHtml 抑原生带 + 对称自绘带。

思源黑体 leading 垫底使 Qt 原生选区带视觉偏下；正文块改用
selection_band 自绘（行墨盒上下 ±SELECTION_PAD_Y 对称），本文件核对
样式表抑制声明在位、自绘矩形纵向对称。

需 offscreen 平台（tests/conftest.py 已统一注入 QT_QPA_PLATFORM=offscreen）。
"""
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import QApplication

from gui.panels.chat.cards import BodyHtml, BodyText
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
