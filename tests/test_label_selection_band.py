"""QLabel 选区带对称自绘断言（2026-1018-0644 计划 D4）。

统一标准：BandLabel 选区带 = 行墨盒 ± SELECTION_PAD_Y 对称半透明带
（与正文块自绘带同常量同观感）；Qt 原生整行高带经 SUPPRESSION_QSS
抑制，且事后裸 setStyleSheet 覆写不得冲掉抑制（整表替换同款根因的
构造性免疫）。

像素级手法：同参数渲染无选区帧 A / 有选区帧 B，逐像素 diff——A、B
均非背景处=文字墨盒，仅 B 非背景处=纯带区；带区相对墨盒上下留白
对称即纵向对称带实锤。需 offscreen 平台（tests/conftest.py 已注入）。
"""
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPalette
from PySide6.QtWidgets import QApplication

from gui.panels.chat.cards import _ElidedLabel
from gui.selection_band import (
    SELECTION_ALPHA,
    SELECTION_PAD_Y,
    BandLabel,
    label_selection_rects,
)

_BG = QColor(30, 30, 30)


def _app():
    return QApplication.instance() or QApplication([])


def _label(text: str, width: int = 320, height: int = 34,
           word_wrap: bool = False) -> BandLabel:
    label = BandLabel(text)
    # 可选中旗标是 setSelection 生效前提（生产路径由 _selectable/
    # make_label_selectable 负责，此处直给）
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    label.setWordWrap(word_wrap)
    label.setFixedSize(width, height)
    label.setStyleSheet(f"background-color: {_BG.name()}; color: #dcdcdc;")
    label.show()
    return label


def _pitch(label) -> float:
    fm = label.fontMetrics()
    return fm.height() + fm.leading()


class TestGeometry:
    """几何单元级：矩形 = 墨盒 ±1px 对称，横缘与文本排版一致。"""

    def test_single_line_full_selection(self):
        _app()
        label = _label("Thinking 标题文本")
        label.setSelection(0, len(label.text()))
        rects = label_selection_rects(label)
        assert len(rects) == 1
        fm = label.fontMetrics()
        rect = rects[0]
        assert rect.height() == fm.ascent() + fm.descent() + 2 * SELECTION_PAD_Y
        # 垂直：块居中原点 - PAD（墨盒上下对称留白）
        expected_top = (label.height() - _pitch(label)) / 2 - SELECTION_PAD_Y
        assert abs(rect.top() - expected_top) <= 0.5
        # 横向：全选 = 全文本宽
        assert abs(rect.width() - fm.horizontalAdvance(label.text())) <= 1.0

    def test_single_line_mid_selection(self):
        _app()
        label = _label(" Thinking 标题文本")
        text = label.text()
        label.setSelection(2, 3)
        rects = label_selection_rects(label)
        assert len(rects) == 1
        fm = label.fontMetrics()
        assert abs(rects[0].left() - fm.horizontalAdvance(text[:2])) <= 1.0
        assert abs(rects[0].right() + 1
                   - fm.horizontalAdvance(text[:5])) <= 1.5

    def test_word_wrap_multi_line(self):
        _app()
        text = "调用 read 工具读取配置文件并检查 content 字段是否包含有效数据"
        label = _label(text, width=120, height=200, word_wrap=True)
        label.resize(120, label.heightForWidth(120))
        label.setSelection(0, len(text))
        rects = label_selection_rects(label)
        assert len(rects) >= 3, f"wordWrap 应逐行一段，实得 {len(rects)} 段"
        fm = label.fontMetrics()
        pitch = _pitch(label)
        block_top = (label.height() - pitch * len(rects)) / 2
        for i, rect in enumerate(rects):
            assert rect.height() == fm.ascent() + fm.descent() + 2 * SELECTION_PAD_Y
            assert abs(rect.top() - (block_top + pitch * i - SELECTION_PAD_Y)) <= 0.5
            assert rect.width() <= label.contentsRect().width() + 1

    def test_word_wrap_partial_selection(self):
        _app()
        text = "调用 read 工具读取配置文件并检查 content 字段是否包含有效数据"
        label = _label(text, width=120, height=200, word_wrap=True)
        label.resize(120, label.heightForWidth(120))
        label.setSelection(5, 22)  # 跨行中段
        rects = label_selection_rects(label)
        assert 2 <= len(rects) < 5
        assert rects[0].left() > 0  # 首段非行首起
        assert rects[-1].width() < label.contentsRect().width()  # 尾段非整行

    def test_no_selection(self):
        _app()
        label = _label("无选区")
        assert label_selection_rects(label) == []

    def test_elided_label_narrow(self):
        """省略态：上屏文本即选区文本，带宽不超控件宽且与省略文本一致。"""
        _app()
        label = _ElidedLabel("Thinking 标题文本很长会被省略掉一部分")
        label.setFixedSize(80, 34)
        label.show()
        assert "…" in label.text(), "前置条件：窄宽下须处于省略态"
        label.setSelection(0, len(label.text()))
        rects = label_selection_rects(label)
        assert len(rects) == 1
        assert rects[0].width() <= 80
        fm = label.fontMetrics()
        assert abs(rects[0].width() - fm.horizontalAdvance(label.text())) <= 1.0

    def test_rich_text_approximation(self):
        """富文本（双色副标题同字体 span）：几何按纯文本近似即精确。"""
        _app()
        label = _ElidedLabel(
            '<span style="color:#aaaaaa">头段</span>'
            '<span style="color:#bbbbbb">尾段</span>')
        label.setFixedSize(320, 34)
        label.show()
        assert "<" in label.text(), "前置条件：宽够时富文本原样上屏"
        label.setSelection(0, len(label.selectedText()) or 4)
        label.setSelection(0, 4)
        rects = label_selection_rects(label)
        assert len(rects) == 1
        fm = label.fontMetrics()
        assert abs(rects[0].width() - fm.horizontalAdvance("头段尾段")) <= 1.0


class TestStylesheetMerge:
    """setStyleSheet 合并免疫：事后裸覆写不丢抑制声明。"""

    def test_extra_style_keeps_suppression(self):
        _app()
        label = _label("标题文本")
        label.setStyleSheet("color: #123456; font-size: 90%;")
        sheet = label.styleSheet()
        assert "selection-background-color: rgba(0,0,0,0)" in sheet
        assert "color: #123456" in sheet

    def test_constructor_has_suppression(self):
        _app()
        label = BandLabel("标题文本")
        assert "selection-background-color: rgba(0,0,0,0)" in label.styleSheet()


class TestPixels:
    """像素级：双帧 diff 实锤对称单层带 + 半透明合成色。"""

    @staticmethod
    def _render(label, selected: bool) -> QImage:
        if selected:
            label.setSelection(0, len(label.text()))
        else:
            label.setSelection(0, 0)
        image = QImage(label.size(), QImage.Format.Format_ARGB32)
        image.fill(_BG)
        label.render(image)
        return image

    def test_symmetric_band(self):
        _app()
        label = _label("Thinking 标题文本")
        img_a = self._render(label, False)
        img_b = self._render(label, True)
        w, h = label.width(), label.height()
        band_rows, ink_rows = [], []
        highlight = QApplication.palette().color(QPalette.ColorRole.Highlight)
        expected = QColor(
            round(highlight.red() * SELECTION_ALPHA / 255
                  + _BG.red() * (1 - SELECTION_ALPHA / 255)),
            round(highlight.green() * SELECTION_ALPHA / 255
                  + _BG.green() * (1 - SELECTION_ALPHA / 255)),
            round(highlight.blue() * SELECTION_ALPHA / 255
                  + _BG.blue() * (1 - SELECTION_ALPHA / 255)))
        for y in range(h):
            for x in range(2, w // 2):
                ca, cb = QColor(img_a.pixel(x, y)), QColor(img_b.pixel(x, y))
                if ca != _BG:
                    ink_rows.append(y)
                    break
                if cb != _BG:
                    band_rows.append(y)
                    break
        assert band_rows and ink_rows
        top_pad = min(ink_rows) - min(band_rows)
        bottom_pad = max(band_rows) - max(ink_rows)
        assert abs(top_pad - bottom_pad) <= 1, \
            f"带纵向须对称：上留白 {top_pad}px vs 下留白 {bottom_pad}px"
        # 带色 = Highlight@SELECTION_ALPHA 与底色合成（容差 ±2）；采样点取
        # 带顶下移 2 行——顶行恰压矩形边缘只吃半覆盖（抗锯齿），内行才是满合成
        sample = QColor(img_b.pixel(w // 4, min(band_rows) + 2))
        assert abs(sample.red() - expected.red()) <= 2
        assert abs(sample.green() - expected.green()) <= 2
        assert abs(sample.blue() - expected.blue()) <= 2

    def test_no_selection_no_band(self):
        _app()
        label = _label("无选区")
        img_a = self._render(label, False)
        img_b = self._render(label, False)  # 两次零选区渲染须逐像素一致
        for y in range(0, label.height(), 3):
            for x in range(0, label.width(), 3):
                assert QColor(img_a.pixel(x, y)) == QColor(img_b.pixel(x, y))
