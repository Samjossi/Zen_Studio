"""终端选区控制器：纯逻辑（零 Qt 依赖，可单测）。

选区语义：可视快照行坐标 (y, x)，端点含端格；锚点=按下处，活动端点=拖拽处。
退化为点的点击不建选区；视图迁移（滚动/推屏/resize/换屏）由 widget 负责清除。
"""
from typing import TypeAlias

#: 网格单元坐标 (y, x)：可视快照行坐标，端点含端格
Cell: TypeAlias = tuple[int, int]


class SelectionController:
    """鼠标拖选状态机：press → drag×N → release；归一化与文本提取。"""

    def __init__(self) -> None:
        self._anchor: Cell | None = None  # 锚点（按下处）
        self._end: Cell | None = None     # 活动端点（拖拽处）

    # ------------------------------------------------------------------
    # 鼠标事件状态机（widget 事件转发到此）
    # ------------------------------------------------------------------
    def press(self, cell: Cell) -> None:
        """左键按下：锚点与端点同置（待拖拽或退化为点击）。"""
        self._anchor = cell
        self._end = cell

    def drag(self, cell: Cell) -> bool:
        """拖拽更新活动端点；返回端点是否变化（调用方据此决定重绘）。"""
        if self._anchor is None or cell == self._end:
            return False
        self._end = cell
        return True

    def release(self) -> bool:
        """左键释放：退化为点击则清除；返回是否消费了本次按下（曾有锚点）。"""
        if self._anchor is None:
            return False
        if self._anchor == self._end:
            self.clear()  # 退化为点击：不建选区
        return True

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def has_anchor(self) -> bool:
        """是否存在进行中的按下（press 后 release 前）。"""
        return self._anchor is not None

    def has_selection(self) -> bool:
        """是否存在有效选区（退化为点的点击不算）。"""
        return (self._anchor is not None and self._end is not None
                and self._anchor != self._end)

    def clear(self) -> bool:
        """清除选区（幂等）；返回是否确有选区被清（调用方据此决定重绘）。"""
        if self._anchor is None:
            return False
        self._anchor = None
        self._end = None
        return True

    def normalized(self) -> tuple[Cell, Cell]:
        """选区按阅读序（先 y 后 x）归一化为 (起, 止)；调用前须保证两端点非 None。"""
        assert self._anchor is not None and self._end is not None
        return ((self._anchor, self._end) if self._anchor <= self._end
                else (self._end, self._anchor))

    def extract_text(self, snapshot: list) -> str:
        """选区纯文本：归一化阅读序 + 跨行拼接 + 行尾 rstrip（网格补空白不带上屏）。

        :param snapshot: 可视快照（screen.snapshot(scroll_offset) 的产物，
                         元素为 [(char, style), ...] 行列表）
        """
        if not self.has_selection():
            return ""
        (y0, x0), (y1, x1) = self.normalized()
        y1 = min(y1, len(snapshot) - 1)  # resize 竞态保护
        lines: list[str] = []
        for y in range(y0, y1 + 1):
            row = snapshot[y]
            start_col = x0 if y == y0 else 0
            end_col = x1 + 1 if y == y1 else len(row)  # 端点含端格 → 半开 +1
            lines.append("".join(ch for ch, _ in row[start_col:end_col]).rstrip())
        return "\n".join(lines)

    @staticmethod
    def pos_to_cell(px: int, py: int, cell_w: float, cell_h: float,
                    row_count: int, column_count: int) -> Cell:
        """像素坐标 → 网格 (y, x)，clamp 进网格（拖入滚动条区不越界）。

        格宽为浮点度量（work plans/2026-0722-2013），// 结果须 int 收敛
        ——返回坐标下游作切片索引，float 会 TypeError。
        """
        x = min(max(int(px // cell_w), 0), column_count - 1)
        y = min(max(int(py // cell_h), 0), row_count - 1)
        return y, x
