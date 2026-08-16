"""2317 计划 T6 冒烟验证：双轨跟随锁行为断言（offscreen）。

覆盖计划 §7 的 1/2/3/4/6 项（拖拽与窗口变形属手势/实机项，此处以
setValue 模拟用户手势——valueChanged 方向判定正是唯一入口）。

运行（项目根）：QT_QPA_PLATFORM=offscreen PYTHONPATH=. .venv/bin/python scripts/test_follow_lock.py
"""
import sys

from PySide6.QtWidgets import QApplication

from gui.panels.chat.output import ChatOutput
from gui.panels.chat.transcript import ChatTranscriptView

CHAT_PACK = {
    "reasoning_fg": "#888888",
    "tool_fg": "#888888",
    "tool_error_fg": "#cc0000",
    "user_bubble_bg": "#eeeeee",
    "tool_output_bg": "#f5f5f5",
    "timeline_read_fg": "#3366cc",
    "diff_add_fg": "#22863a",
    "diff_del_fg": "#cb2431",
}


def make_views():
    old = ChatOutput(
        reasoning_color=CHAT_PACK["reasoning_fg"],
        tool_color=CHAT_PACK["tool_fg"],
        error_color=CHAT_PACK["tool_error_fg"],
        user_bubble_bg=CHAT_PACK["user_bubble_bg"],
        tool_output_bg=CHAT_PACK["tool_output_bg"],
        link_fg=CHAT_PACK["timeline_read_fg"])
    new = ChatTranscriptView(CHAT_PACK)
    for v in (old, new):
        v.resize(400, 300)
        v.show()
    return [("旧轨 ChatOutput", old), ("新轨 ChatTranscriptView", new)]


def fill(view, n=30):
    for i in range(n):
        view.append_message("系统", f"历史消息 {i} " + "长文本" * 20)


def pump(app):
    app.processEvents()
    app.processEvents()  # 新轨滚底经 singleShot(0) 延迟一跳


def main() -> int:
    app = QApplication(sys.argv)
    failures = []
    for name, view in make_views():
        bar = view.verticalScrollBar()
        fill(view)
        pump(app)
        # 1. 锁定态：追加后贴底
        assert view._pinned, f"{name}: 初始应锁定"
        assert bar.value() == bar.maximum(), f"{name}: 锁定态追加后应贴底"

        # 2. 用户手势上翻（模拟：值减小）→ 解锁 + 悬浮钮浮现
        bar.setValue(bar.maximum() - 200)
        pump(app)
        assert not view._pinned, f"{name}: 上翻后应解锁"
        assert view._back_to_bottom.isVisible(), f"{name}: 解锁后悬浮钮应浮现"

        # 3. 解锁态继续追加 → 视角不被下拉
        pos = bar.value()
        fill(view, 10)
        pump(app)
        assert bar.value() == pos, (
            f"{name}: 解锁态追加后视角被下拉 {pos} -> {bar.value()}")
        assert not view._pinned, f"{name}: 解锁态追加后不应自动恢复"

        # 4. 滚回底部阈值内 → 恢复锁定 + 悬浮钮隐藏
        bar.setValue(bar.maximum())
        pump(app)
        assert view._pinned, f"{name}: 回底后应恢复锁定"
        assert not view._back_to_bottom.isVisible(), f"{name}: 锁定后悬浮钮应隐藏"

        # 5. 再次解锁后点「回到底部」→ 回底 + 锁定
        bar.setValue(bar.maximum() - 200)
        pump(app)
        assert not view._pinned, f"{name}: 再次上翻应解锁"
        view._back_to_bottom.click()
        pump(app)
        assert view._pinned, f"{name}: 点回底钮后应锁定"
        assert bar.value() == bar.maximum(), f"{name}: 点回底钮后应贴底"

        # 6. P3：解锁态发送新消息 → 强制回底
        bar.setValue(bar.maximum() - 200)
        pump(app)
        assert not view._pinned
        view.append_user_message("你好")
        pump(app)
        assert view._pinned, f"{name}: 发送新消息应强制恢复锁定"
        assert bar.value() == bar.maximum(), f"{name}: 发送新消息应强制回底"

        print(f"[OK] {name} 六项断言全过")
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
