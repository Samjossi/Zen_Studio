"""终端语义层：pyte 屏幕模型封装（字节流 → 屏幕状态快照；不碰 Qt 绘制）。

设计约束：颜色用名字串（不依赖 QtGui），主题切换免重算；
快照即纯数据，未来"终端内容喂 AI"直接消费。
"""
import sys
from dataclasses import dataclass

import pyte


class _TolerantScreen(pyte.HistoryScreen):
    """容错 HistoryScreen：容忍 pyte 0.8.2 对私有 SGR 序列的分发缺陷。

    bash 5.1+ / readline 会下发 modifyOtherKeys 等私有 SGR（如 `CSI > 4;2 m`），
    pyte 经 HistoryScreen._make_wrapper 以 `private=True` 调用
    `select_graphic_rendition(*attrs)`（不接受 kwargs）→ TypeError。
    此处吞掉 private 关键字后按正常 SGR 处理。
    """

    def select_graphic_rendition(self, *attrs: int, private: bool = False, **kwargs) -> None:
        super().select_graphic_rendition(*attrs)


@dataclass(frozen=True)
class CellStyle:
    """单元格样式值对象（颜色用名字，非 QColor）。"""
    fg: str = "default"
    bg: str = "default"
    bold: bool = False
    reverse: bool = False
    underline: bool = False


#: 空白单元（未写入格）
BLANK_CELL: tuple[str, CellStyle] = (" ", CellStyle())


class TerminalScreen:
    """pyte `HistoryScreen` 封装：喂流、快照、尺寸、回滚。"""

    def __init__(self, column_count: int = 80, line_count: int = 24, history: int = 1000) -> None:
        self._screen = _TolerantScreen(column_count, line_count, history=history)
        self._stream = pyte.Stream(self._screen)

    # ------------------------------------------------------------------
    # 写入与尺寸
    # ------------------------------------------------------------------
    def feed(self, data: bytes) -> None:
        """喂入 PTY 字节流（仅 GUI 线程调用，pyte 非线程安全）。

        解析异常降级为丢弃该块并记录 stderr——终端模拟器必须容错，
        任何生僻序列都不应拖垮 UI。
        """
        try:
            self._stream.feed(data.decode("utf-8", errors="replace"))
        except Exception as e:  # noqa: BLE001 — pyte 解析容错兜底
            print(f"[terminal] pyte 解析异常（已丢弃该块）: {e}", file=sys.stderr)

    def resize(self, line_count: int, column_count: int) -> None:
        self._screen.resize(max(1, line_count), max(1, column_count))

    @property
    def column_count(self) -> int:
        return self._screen.columns

    @property
    def line_count(self) -> int:
        return self._screen.lines

    @property
    def cursor(self):
        """光标（pyte Cursor：x/y/attrs）。"""
        return self._screen.cursor

    @property
    def title(self) -> str:
        """终端标题（shell 经 OSC 0/2 序列设置；空串表示未设置）。"""
        return self._screen.title

    # ------------------------------------------------------------------
    # 读取
    # ------------------------------------------------------------------
    def count_scrollback_lines(self) -> int:
        """回滚区行数（顶部滚出历史的行数）。"""
        return len(self._screen.history.top)

    def snapshot(self, offset: int = 0) -> list[list[tuple[str, CellStyle]]]:
        """屏幕快照：offset=0 当前屏；offset=N 向上回滚 N 行。

        返回 line_count 行 × column_count 列的 (字符, CellStyle) 网格，不足补空白。
        """
        line_count, column_count = self._screen.lines, self._screen.columns
        buffer = self._screen.buffer
        if offset > 0:
            # 回滚视图：历史行 + 当前屏头部拼接取窗口
            all_rows = list(self._screen.history.top) + [buffer[y] for y in range(line_count)]
            start = max(0, len(all_rows) - line_count - offset)
            view = all_rows[start:start + line_count]
        else:
            view = [buffer[y] for y in range(line_count)]

        rows: list[list[tuple[str, CellStyle]]] = []
        for line in view:
            row: list[tuple[str, CellStyle]] = []
            for x in range(column_count):
                ch = line.get(x)
                if ch is None:
                    row.append(BLANK_CELL)
                else:
                    row.append((ch.data, CellStyle(
                        fg=ch.fg, bg=ch.bg, bold=ch.bold,
                        reverse=ch.reverse, underline=ch.underscore)))
            rows.append(row)
        while len(rows) < line_count:  # 历史不足一屏时前补空行
            rows.insert(0, [BLANK_CELL] * column_count)
        return rows

    def to_plain_text(self) -> str:
        """当前屏纯文本（"终端内容喂 AI"的协议出口，本期备用）。"""
        return "\n".join(self._screen.display)
