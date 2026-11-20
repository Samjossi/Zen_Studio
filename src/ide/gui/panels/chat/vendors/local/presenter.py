"""Local 卡片路由 presenter：工具标题/命令账本与迟到帧闸门。

本类只处理 Local 工具载荷在通用面板与输出区之间的路由纪律，不创建
卡片、不解析具体卡片格式。卡片分派归 factory，具体格式归 cards；通用面板
只把 Chunk 载荷原样交给 prepare_call/prepare_update/append_todo。
"""
import time

#: 新轨 in_progress 尾滚帧同 tid 节流间隔（秒）
_TAIL_THROTTLE_S = 0.2


class LocalCardPresenter:
    """Local 工具活动路由状态（随面板/轮次重置，不跨实例共享）。"""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._tool_titles: dict[str, str] = {}
        self._tool_commands: dict[str, str] = {}
        self._tail_last: dict[str, float] = {}

    def prepare_call(self, payload: dict) -> dict:
        """首帧：登记标题与命令账本，原样返回载荷。"""
        if tid := payload.get("tool_call_id"):
            if payload.get("title"):
                self._tool_titles[tid] = payload["title"]
            if payload.get("command"):
                self._tool_commands[tid] = payload["command"]
        return payload

    def prepare_update(self, payload: dict) -> dict | None:
        """更新帧：补标题、记账迟到 command、尾滚闸门与命令头回填。

        返回 None 表示本帧被 200ms 尾滚节流丢弃；否则返回可交给输出区
        的载荷（输出帧补齐 `$ ` 命令头）。
        """
        tid = payload.get("tool_call_id") or ""
        if not payload.get("title"):
            payload = {**payload, "title": self._tool_titles.get(
                tid, tid[:8] or "?")}
        if payload.get("command") and tid not in self._tool_commands:
            self._tool_commands[tid] = payload["command"]
        if payload.get("status") == "in_progress" \
                and not self._allow_progress_frame(payload, tid):
            return None
        if payload.get("output") and tid in self._tool_commands:
            payload = {**payload, "command": self._tool_commands[tid]}
        return payload

    def _allow_progress_frame(self, payload: dict, tid: str) -> bool:
        """in_progress 帧放行：迟到 diff/摘要/入参/图片/清单不限工具放行；
        尾滚帧仅 execute 且同 tid 200ms 节流。"""
        if payload.get("diff_hunks") or payload.get("summary"):
            return True
        if payload.get("input_detail") or payload.get("images"):
            return True
        if payload.get("todos"):
            return True
        if tid not in self._tool_commands or not payload.get("output"):
            return False
        now = time.monotonic()
        if now - self._tail_last.get(tid, 0.0) < _TAIL_THROTTLE_S:
            return False
        self._tail_last[tid] = now
        return True

    @staticmethod
    def is_fs_mutation_terminal(payload: dict) -> bool:
        """终态帧且 kind 为改盘类（edit/execute）→ 应提示刷新 Git。"""
        return payload.get("status") in ("completed", "failed") \
            and payload.get("tool_kind") in ("edit", "execute")

    @staticmethod
    def append_todo(output, payload: dict) -> None:
        """todo 通道：取出本厂商 entries 全量快照交给输出区整刷。"""
        output.upsert_todo_block(payload.get("entries") or [])
