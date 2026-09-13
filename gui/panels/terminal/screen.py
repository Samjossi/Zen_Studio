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

    另为维护"缓冲区绝对行号"补两个 pyte 没有的追踪量：

    - `pushed_lines`：累计滚入 history.top 的行数。history.top 是有界 deque，
      溢出时静默丢顶行，pyte 不留痕；用该计数反推拼接缓冲区的绝对行号基线
      （base = pushed_lines - len(history.top)），使已有内容的绝对行号在
      推屏与溢出时都保持稳定（选区跨滚动复制的根基）。只覆盖 index()：
      next_page 仅在 history.position < size 时被 before_event 触发，而本程序
      从不分页（prev_page 无入口），position 恒等于 size，该路径不可达。
    - `history_generation`：reset / ED 3 清空历史时递增。清空后旧绝对行号
      整体失效，widget 据此作废选区。
    """

    def __init__(self, *args, **kwargs) -> None:
        # 须先于 super().__init__ 赋值：pyte 构造链会经 reset → _reset_history 触达这两个属性
        self.pushed_lines = 0
        self.history_generation = 0
        super().__init__(*args, **kwargs)

    def select_graphic_rendition(self, *attrs: int, private: bool = False, **kwargs) -> None:
        super().select_graphic_rendition(*attrs)

    def index(self) -> None:
        top_bottom = self.margins or pyte.screens.Margins(0, self.lines - 1)
        will_push = self.cursor.y == top_bottom[1]
        super().index()
        if will_push:
            self.pushed_lines += 1

    def _reset_history(self) -> None:
        super()._reset_history()
        self.history_generation += 1


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

    @property
    def history_generation(self) -> int:
        """历史清空代数（reset / ED 3 时递增）；选区据此判定绝对行号失效。"""
        return self._screen.history_generation

    def abs_window(self) -> tuple[int, int]:
        """绝对行号有效区间 [起, 止)：拼接缓冲区（回滚 + 当前屏）全段。

        绝对行号以"自创建起累计滚入回滚的行数"为锚，推屏与回滚溢出均不改变
        已有内容的行号（选区跨滚动稳定的前提）。
        """
        top = len(self._screen.history.top)
        base = self._screen.pushed_lines - top
        return base, base + top + self._screen.lines

    def visible_to_abs(self, y: int, scroll_offset: int) -> int:
        """可视行 → 绝对行号（scroll_offset 为回滚行数，0=跟随当前屏）。"""
        base, _ = self.abs_window()
        return base + len(self._screen.history.top) - scroll_offset + y

    def abs_to_visible(self, row: int, scroll_offset: int) -> int:
        """绝对行号 → 可视行（结果可越出 [0, line_count)，调用方自行裁剪）。"""
        base, _ = self.abs_window()
        return row - base - len(self._screen.history.top) + scroll_offset

    def abs_rows(self, start: int, end: int) -> list[list[tuple[str, CellStyle]]]:
        """绝对行号区间 [start, end) 的行内容（clamp 进 abs_window，空区间返回 []）。"""
        base, stop = self.abs_window()
        start, end = max(start, base), min(end, stop)
        if start >= end:
            return []
        all_rows = (list(self._screen.history.top)
                    + [self._screen.buffer[y] for y in range(self._screen.lines)])
        return [self._convert_row(all_rows[i])
                for i in range(start - base, end - base)]

    def _convert_row(self, line) -> list[tuple[str, CellStyle]]:
        """pyte 行（dict[x] → Char）→ 定长 (字符, CellStyle) 行，未写入格补空白。"""
        row: list[tuple[str, CellStyle]] = []
        for x in range(self._screen.columns):
            ch = line.get(x)
            if ch is None:
                row.append(BLANK_CELL)
            else:
                row.append((ch.data, CellStyle(
                    fg=ch.fg, bg=ch.bg, bold=ch.bold,
                    reverse=ch.reverse, underline=ch.underscore)))
        return row

    def snapshot(self, offset: int = 0) -> list[list[tuple[str, CellStyle]]]:
        """屏幕快照：offset=0 当前屏；offset=N 向上回滚 N 行。

        返回 line_count 行 × column_count 列的 (字符, CellStyle) 网格，不足补空白。
        """
        line_count = self._screen.lines
        buffer = self._screen.buffer
        if offset > 0:
            # 回滚视图：历史行 + 当前屏头部拼接取窗口
            all_rows = list(self._screen.history.top) + [buffer[y] for y in range(line_count)]
            start = max(0, len(all_rows) - line_count - offset)
            view = all_rows[start:start + line_count]
        else:
            view = [buffer[y] for y in range(line_count)]

        rows: list[list[tuple[str, CellStyle]]] = [self._convert_row(line) for line in view]
        while len(rows) < line_count:  # 历史不足一屏时前补空行
            rows.insert(0, [BLANK_CELL] * self._screen.columns)
        return rows

    def to_plain_text(self) -> str:
        """当前屏纯文本（"终端内容喂 AI"的协议出口，本期备用）。"""
        return "\n".join(self._screen.display)
