"""会话活动时间线色块条：AI 活动横排色块自绘组件（1824 计划 T1）。

移植自 Kilo Code VS Code 插件 TaskTimeline（参考代码/kilocode-main
packages/kilo-vscode/webview-ui/src/components/chat/TaskTimeline.tsx +
utils/timeline/{colors,sizes,geometry}.ts，只读参考）：
- 每个 AI 活动（正文段/思维链段/工具调用/todo）一个彩色竖条，横排平铺；
- 颜色按活动类型分类，高度按内容量归一化分档；
- 超出横向滚动（滚轮），贴右缘时新增色块自动跟随。

与参考实现的差异（移植改进点，1824 计划 §2.3/§3）：
1. 读/写分类直用 ACP tool_kind（agent 自报语义：read/search→读、edit→写），
   替代参考侧的「工具名集合猜读/写」——更准且不维护猜测表；
2. 数据粒度为「当前段」聚合：本仓 Chunk 流中 text/reasoning 是流式增量，
   连续同 kind 增量归入同一色块（等价参考侧 part 语义），kind 切换开新块；
3. 条高依据为内容量代理（工具取 len(title)+len(summary)，GUI 不解析
   rawInput——1602 纪律，不为此扩协议载荷）；
4. 错误态不另设 category：failed 标志覆盖着色（保留原类型 tooltip），
   等价参考侧 error 最高优先级覆盖语义；
5. 命中检测由二分查找简化为 O(1) 除法——条宽恒定（BAR_W）前提下
   两者等价；键盘导航未移植（一级无点击跳转，1824 计划 §7）。

一级范围（1824 计划 D4）：仅 tooltip + 横滚/自动跟随；点击跳转与
悬停联动高亮不做（需 ChatOutput 工具锚点基础设施，二级再议）。
"""
from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QToolTip, QWidget

from llm.base import Chunk

# ----------------------------------------------------------------------
# 几何常量（直译 sizes.ts / geometry.ts，原值照搬实证观感良好）
# ----------------------------------------------------------------------
BAR_W = 12        #: 条宽恒定（无逐段时长数据，等宽）
GAP = 1           #: 条间距
MIN_H = 8         #: 最小条高
MAX_H = 26        #: 最大条高
PAD = 4           #: 归一化余量（最高条也不顶满，直译 sizes.ts）
RADIUS = 2.0      #: 圆角半径（一级从简全圆角，视觉差异可忽略）

#: tool_kind → 色块分类（ACP 协议层 agent 自报语义，比参考侧工具名猜测更准）
_KIND_CATEGORY = {
    "read": "tool_read",
    "search": "tool_read",
    "edit": "tool_write",
    # execute/fetch/think/other 及未知值 → tool_other（映射表缺省落点）
}

#: 色块分类 → 默认 tooltip 词（工具块 tooltip 为 title — summary，另算）
_CATEGORY_TIP = {
    "text": "正文",
    "reasoning": "思维链",
    "todo": "待办清单",
}


@dataclass
class TimelineBar:
    """单个色块（等价参考侧一个 part 的渲染投影）。

    content 为高度归一化依据，随流式累加；failed 为失败覆盖标志
    （tool_call_update failed 帧置位，着色优先级最高，等价参考侧
    state.status=error → palette.error）。
    """

    category: str             # text/reasoning/tool_read/tool_write/tool_other/todo
    tip: str                  # tooltip 文本
    content: int              # 内容量（高度归一化依据）
    tool_call_id: str | None = None  # 工具块簿记键（failed 改色用）
    failed: bool = False


class ActivityTimeline(QWidget):
    """AI 活动色块横排条（自绘，固定高度，横向溢出滚轮滚动 + 自动跟随）。

    :param colors: 色块分类 → 十六进制色值，必含键：
        text / reasoning / tool_read / tool_write / tool_other / todo / error
        （由 ChatPanel 按 ChatPack 组装注入，构造与 set_colors 同构——
        沿用 1602 计划 T7 的 set_activity_colors 注入范式）
    """

    def __init__(self, colors: dict[str, str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._colors = {key: QColor(value) for key, value in colors.items()}
        self._bars: list[TimelineBar] = []
        #: 各条高度（px，随新增全量重算——D6 全量归一，条数有限 O(n) 无压力）
        self._heights: list[int] = []
        #: 「当前段」指针：可聚合段（text/reasoning）的末块下标；
        #: None = 下一块强制新开（工具/todo 帧后、轮次收尾后）
        self._current: int | None = None
        #: toolCallId → 色块下标簿记（failed 帧改色，复用 _tool_titles 同范式）
        self._tool_index: dict[str, int] = {}
        self._offset = 0        # 横向滚动偏移（px，等价 scrollLeft）
        self._pinned = True     # 贴右缘跟随态（用户滚离后解除，滚回恢复）
        self.setFixedHeight(MAX_H + 4)  # 上下各 2px 边距
        self.setMouseTracking(True)

    # ------------------------------------------------------------------
    # 主题注入
    # ------------------------------------------------------------------
    def set_colors(self, colors: dict[str, str]) -> None:
        """主题切换：更新全部分类色并重绘（色块条为自绘，既有块同步换色）。"""
        self._colors = {key: QColor(value) for key, value in colors.items()}
        self.update()

    # ------------------------------------------------------------------
    # 记录层：Chunk → 色块（段聚合 + toolCallId 簿记，等价 collect/colors/sizes）
    # ------------------------------------------------------------------
    def feed(self, chunk: Chunk) -> None:
        """消费一个流式块（旁路分接，不改动既有路由语义）。

        text/reasoning 走「当前段」聚合；tool_call 恒开新块；
        tool_call_update failed 帧按簿记改色；todo 更新既有块；usage 忽略。
        """
        if chunk.kind in ("text", "reasoning"):
            self._feed_text(chunk.kind, len(chunk.text))
        elif chunk.kind == "tool_call":
            self._feed_tool_call(chunk.payload or {})
        elif chunk.kind == "tool_call_update":
            self._feed_tool_update(chunk.payload or {})
        elif chunk.kind == "todo":
            self._feed_todo((chunk.payload or {}).get("entries") or [])
        # usage 等其余 kind 不上条

    def end_turn(self) -> None:
        """轮次收尾：作废「当前段」指针，下一轮强制开新块（防跨轮串位，
        与 output.reset_activity_anchors 同位置调用）。"""
        self._current = None

    def clear(self) -> None:
        """清空全部色块（新会话/切换后端；与 output 文本同生命周期）。"""
        self._bars.clear()
        self._heights.clear()
        self._tool_index.clear()
        self._current = None
        self._offset = 0
        self._pinned = True
        self.update()

    def _feed_text(self, kind: str, length: int) -> None:
        """正文/思维链增量：末块同类则累加，否则开新块（段聚合核心规则）。"""
        if (self._current is not None and self._current == len(self._bars) - 1
                and self._bars[self._current].category == kind):
            self._bars[self._current].content += length
        else:
            self._bars.append(TimelineBar(kind, _CATEGORY_TIP[kind], max(1, length)))
            self._current = len(self._bars) - 1
        self._on_bars_changed()

    def _feed_tool_call(self, payload: dict) -> None:
        """工具调用帧：恒开新块（等价参考侧逐 part 一条），并簿记锚点键。"""
        category = _KIND_CATEGORY.get(payload.get("tool_kind") or "", "tool_other")
        title = payload.get("title") or "?"
        summary = payload.get("summary") or ""
        tip = f"{title} — {summary}" if summary else title
        bar = TimelineBar(category, tip, max(1, len(title) + len(summary)),
                          tool_call_id=payload.get("tool_call_id"))
        self._bars.append(bar)
        if bar.tool_call_id:
            self._tool_index[bar.tool_call_id] = len(self._bars) - 1
        self._current = None  # 工具帧切断正文/思维链段聚合
        self._on_bars_changed()

    def _feed_tool_update(self, payload: dict) -> None:
        """状态更新帧：failed 终态将对应块改错误色（覆盖类型色，最高优先级）。"""
        if payload.get("status") != "failed":
            return
        index = self._tool_index.get(payload.get("tool_call_id") or "")
        if index is None:
            return  # 未知锚点（簿记外到达）：忽略，不上条不改色
        self._bars[index].failed = True
        if error := payload.get("error"):
            self._bars[index].tip += f"（{error}）"
        self.update()

    def _feed_todo(self, entries: list) -> None:
        """todo 快照帧：更新既有 todo 块内容量（或首帧开块）。"""
        for bar in self._bars:
            if bar.category == "todo":
                bar.content = max(1, len(entries))
                self._on_bars_changed()
                return
        self._bars.append(
            TimelineBar("todo", _CATEGORY_TIP["todo"], max(1, len(entries))))
        self._current = None
        self._on_bars_changed()

    # ------------------------------------------------------------------
    # 尺寸与几何（直译 sizes.ts：全量归一化；pinned 贴底判定）
    # ------------------------------------------------------------------
    def _on_bars_changed(self) -> None:
        """色块增改后：全量重算高度（D6），跟随态下滚到最右，触发重绘。"""
        raw = [bar.content for bar in self._bars]
        top = max(raw, default=1)
        self._heights = [
            round(MIN_H + min(1.0, c / max(1, top)) * (MAX_H - MIN_H - PAD))
            for c in raw
        ]
        if self._pinned:
            self._offset = self._max_offset()
        self.update()

    def _content_width(self) -> int:
        """全部色块平铺总宽（gap 平铺，直译 geometry.ts 累加规则）。"""
        return len(self._bars) * (BAR_W + GAP)

    def _max_offset(self) -> int:
        return max(0, self._content_width() - self.width())

    # ------------------------------------------------------------------
    # 渲染（等价 geometry/SVG path：按条自绘圆角矩形，底对齐）
    # ------------------------------------------------------------------
    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bottom = self.height() - 2
        for index, bar in enumerate(self._bars):
            x = index * (BAR_W + GAP) - self._offset
            if x + BAR_W < 0 or x > self.width():
                continue  # 视口外裁剪
            color = self._colors["error"] if bar.failed \
                else self._colors[bar.category]
            height = self._heights[index]
            path = QPainterPath()
            path.addRoundedRect(x, bottom - height, BAR_W, height, RADIUS, RADIUS)
            painter.fillPath(path, color)
        painter.end()

    # ------------------------------------------------------------------
    # 交互（一级范围 D4：tooltip + 滚轮横滚 + 自动跟随）
    # ------------------------------------------------------------------
    def _hit(self, x: int) -> int:
        """视口 x 坐标 → 色块下标；未命中返回 -1。

        条宽恒定（BAR_W+GAP 平铺）下二分命中等价于 O(1) 除法定位
        （参考实现 geometry.ts hit() 为二分——适配逐条异宽；本仓等宽简化）。
        """
        index = (x + self._offset) // (BAR_W + GAP)
        if 0 <= index < len(self._bars):
            within = (x + self._offset) - index * (BAR_W + GAP)
            if within < BAR_W:  # 落在条体上（gap 区不算命中）
                return index
        return -1

    def mouseMoveEvent(self, event) -> None:
        index = self._hit(int(event.position().x()))
        if index < 0:
            QToolTip.hideText()
            return
        bar = self._bars[index]
        QToolTip.showText(event.globalPosition().toPoint(), bar.tip, self)

    def leaveEvent(self, event) -> None:
        QToolTip.hideText()

    def wheelEvent(self, event) -> None:
        """滚轮横滚（纵滚轮折算横向）；滚离右缘解除跟随，滚回恢复。"""
        delta = event.angleDelta().y() or event.angleDelta().x()
        if delta == 0:
            return
        step = (BAR_W + GAP) * 3  # 每次三格，手感与参考侧拖拽横滚相当
        self._offset = min(max(0, self._offset - (step if delta > 0 else -step)),
                           self._max_offset())
        # pinned 判定（直译 sizes.ts：视口距右缘 ≤ 一个条宽视为贴底）
        self._pinned = self._max_offset() - self._offset <= BAR_W
        self.update()
