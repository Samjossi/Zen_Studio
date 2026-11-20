"""轮次本地中断（TurnAborted）与挂起看门狗用例（2026-1118-1115 计划 D1/D2）。

覆盖：
- 连接层：next_update 拆拍后 timeout 语义不变、abort_turn 秒级解封
  无限等待、begin_turn 清除上轮残留中断；
- worker × kimi provider 全链路：假后端沉默（等价 agent 丢失后台任务
  完成事件）时 request_stop 秒级收尾——修复前此处永久挂起，本用例即
  复现探针 .temp/repro_stuck_session 的固化形态（红进绿出）；
- 正常轮次不受拆拍影响（收尾信号与正文照常）；
- 看门狗：超阈值自动中断 + 系统提示 + 不重复触发；阈值 0 关闭。

全部经内存假后端驱动（monkeypatch subprocess.Popen，os.pipe 收发
ndjson 帧），不起真实 CLI/子进程（tests/README.md 纪律）。
"""
import json
import os
import queue
import subprocess
import threading
import time
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

import ide.gui.panels.chat.model_bar as model_bar_mod
import ide.gui.settings as settings_mod
import ide.llm.providers.acp as acp_mod
import ide.llm.providers.kimi_acp as ka
from ide.gui.panels.chat.panel import ChatPanel
from ide.gui.panels.chat.worker import ChatWorker
from ide.gui.settings import (
    DEFAULT_SETTINGS,
    KEY_TURN_STALL_TIMEOUT_MIN,
    update_settings,
)
from ide.llm.registry import BackendSpec

_TEMP_DIR = Path(__file__).resolve().parents[1] / ".temp" / "test_turn_abort"

#: abort 解封上界（秒）：拆拍节拍 1s，留一倍余量
_ABORT_ESCAPE_S = 3.0


def _wait_for(predicate, timeout: float = 10.0) -> bool:
    """轮询至谓词为真或超时；返回谓词终值（超时 False）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


class _FakeAgentProc:
    """内存 ACP 假后端：连接层 stdin/stdout 经 os.pipe 与 responder 线程
    交换 ndjson 帧。scenario="stuck" 时收 prompt 后永久沉默（等价 agent
    丢失后台任务完成事件）；scenario="normal" 时正常回 prompt 响应。"""

    def __init__(self, scenario: str) -> None:
        self.scenario = scenario
        self.cancel_seen = threading.Event()
        self.stdin_read, self.stdin_write = os.pipe()
        self.stdout_read, self.stdout_write = os.pipe()
        self.stdin = os.fdopen(self.stdin_write, "w", encoding="utf-8", buffering=1)
        self.stdout = os.fdopen(self.stdout_read, encoding="utf-8")
        self._stdout_sink = os.fdopen(self.stdout_write, "w",
                                      encoding="utf-8", buffering=1)
        self._reader_f = os.fdopen(self.stdin_read, encoding="utf-8")
        self._dead = threading.Event()
        self._responder = threading.Thread(target=self._run_responder, daemon=True)
        self._responder.start()

    # -- 协议行为 ------------------------------------------------------
    def _run_responder(self) -> None:
        try:
            for line in self._reader_f:
                if self._dead.is_set():
                    return
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                self._handle(obj)
        except (OSError, ValueError):
            return  # 连接已死（terminate 关写端得 EOF 或管道断开）

    def _handle(self, obj: dict) -> None:
        method, req_id = obj.get("method"), obj.get("id")
        if method == "initialize":
            self._reply(req_id, {
                "protocolVersion": 1, "capabilities": {},
                "serverInfo": {"name": "fake", "version": "0"}})
        elif method == "session/new":
            self._reply(req_id, {"sessionId": "fake-session"})
        elif method == "session/set_config_option":
            self._reply(req_id, {})
        elif method == "session/prompt":
            self._notify({"sessionId": "fake-session", "update": {
                "sessionUpdate": "tool_call", "toolCallId": "c1",
                "title": "Bash", "kind": "execute", "status": "pending",
                "rawInput": {"command": "sleep 999"}, "content": []}})
            if self.scenario == "normal":
                self._reply(req_id, {
                    "stopReason": "end_turn",
                    "content": [{"type": "text", "text": "完成"}]})
            # stuck：不回 prompt 响应——agent 永久沉默的等价模拟
        elif method == "session/cancel":
            self.cancel_seen.set()

    def _reply(self, req_id, result: dict) -> None:
        if req_id is not None:
            self._send({"jsonrpc": "2.0", "id": req_id, "result": result})

    def _notify(self, params: dict) -> None:
        self._send({"jsonrpc": "2.0", "method": "session/update",
                    "params": params})

    def _send(self, msg: dict) -> None:
        try:
            self._stdout_sink.write(json.dumps(msg, ensure_ascii=False) + "\n")
            self._stdout_sink.flush()
        except (OSError, ValueError):
            pass  # 连接已死

    # -- 进程桩接口 ----------------------------------------------------
    def poll(self):
        return 0 if self._dead.is_set() else None

    def terminate(self) -> None:
        self._close()

    def kill(self) -> None:
        self._close()

    def wait(self, timeout=None):
        return 0

    def _close(self) -> None:
        """关管道顺序纪律：先关两个写端（responder 与连接层 _reader 各得
        EOF 自行退出），再 join responder 后关其读端——读端文件对象正被
        对方线程迭代时主线程直接 close 会等读锁，与 EOF 永远互相等不到
        （本文件首版踩实此死锁）。"""
        self._dead.set()
        for f in (self.stdin, self._stdout_sink):
            try:
                f.close()
            except OSError:
                pass
        self._responder.join(timeout=2)
        try:
            self._reader_f.close()
        except OSError:
            pass
        # self.stdout（连接层 _reader 线程的迭代对象）：不在这里关，
        # _reader 收 EOF 退出后随连接对象回收


@pytest.fixture(scope="module")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture()
def isolated_settings(monkeypatch):
    """settings 读写重定向到 ./.temp/ 隔离文件（防污染真实 settings.json）。"""
    _TEMP_DIR.mkdir(parents=True, exist_ok=True)
    settings_file = _TEMP_DIR / "settings.json"
    settings_file.unlink(missing_ok=True)  # 清上次运行残留，保证默认值语义
    monkeypatch.setattr(settings_mod, "SETTINGS_FILE", settings_file)
    monkeypatch.setattr(settings_mod, "SETTINGS_LOCK_FILE", _TEMP_DIR / "settings.lock")
    return settings_file


@pytest.fixture()
def fake_agent(monkeypatch):
    """假后端控制器：替换 subprocess.Popen；scenario 切 stuck/normal。"""
    class _Ctl:
        scenario = "stuck"
        last: _FakeAgentProc | None = None

    def _popen(*_args, **_kwargs):
        _Ctl.last = _FakeAgentProc(_Ctl.scenario)
        return _Ctl.last

    monkeypatch.setattr(subprocess, "Popen", _popen)
    return _Ctl


@pytest.fixture()
def kimi_provider(fake_agent, monkeypatch):
    """真实 KimiAcpLLM + 假进程：_find_bin 指假路径、sidecar 关闭（防读
    真实 session 索引）、模型目录置空（防真实子进程探测）。"""
    monkeypatch.setattr(ka, "_find_bin", lambda: "/fake/kimi")
    monkeypatch.setattr(ka, "_session_dir_of", lambda _sid: None)
    monkeypatch.setattr(ka, "load_kimi_provider_catalog", lambda: {})
    provider = ka.KimiAcpLLM(workspace_root=str(_TEMP_DIR))
    yield provider
    provider.close()


@pytest.fixture()
def fake_registry(monkeypatch):
    """伪造单接口注册表（ModelBar 构建不探测真实 CLI）。"""
    spec = BackendSpec(
        name="fake-acp",
        label="Fake ACP",
        vendor="fake",
        vendor_label="Fake",
        available=lambda: True,
        list_models=lambda: ["m1"],
        factory=lambda **_kw: None,
        efforts=("auto",),
        default_effort="auto",
    )
    monkeypatch.setattr(model_bar_mod, "vendor_groups", lambda: {"fake": [spec]})
    monkeypatch.setattr(model_bar_mod, "spec_of", lambda name: spec)
    monkeypatch.setattr(model_bar_mod, "vendor_of", lambda name: "fake")
    monkeypatch.setattr(model_bar_mod, "resolve_efforts",
                        lambda spec, model: (spec.efforts, spec.default_effort))
    return spec


def _make_worker(provider) -> tuple[ChatWorker, list]:
    events: list = []
    worker = ChatWorker(provider, [{"role": "user", "content": "跑长任务"}], None)
    direct = Qt.ConnectionType.DirectConnection
    worker.chunk_received.connect(
        lambda c: events.append(("chunk", c.kind)), direct)
    worker.finished_with_error.connect(
        lambda e: events.append(("finished", e)), direct)
    worker.stopped_by_user.connect(
        lambda: events.append(("stopped", None)), direct)
    return worker, events


# ----------------------------------------------------------------------
# 连接层：拆拍语义与本地中断
# ----------------------------------------------------------------------
def test_next_update_timeout_semantics_unchanged(fake_agent):
    conn = acp_mod.AcpConnection("/fake/kimi", str(_TEMP_DIR), "fake acp")
    try:
        t0 = time.monotonic()
        with pytest.raises(queue.Empty):
            conn.next_update(timeout=0.3)
        assert time.monotonic() - t0 < 2.0  # 拆拍后不显著拖长显式超时
        conn._updates.put(("update", {"frame": 1}))
        kind, obj = conn.next_update(timeout=1.0)
        assert kind == "update" and obj == {"frame": 1}
    finally:
        conn.terminate()


def test_abort_turn_unblocks_infinite_wait(fake_agent):
    """无限等待中的 next_update 在 abort 后一拍内抛 TurnAborted。"""
    conn = acp_mod.AcpConnection("/fake/kimi", str(_TEMP_DIR), "fake acp")
    try:
        conn.begin_turn("session/prompt", {"sessionId": "fake-session",
                                           "prompt": [{"type": "text", "text": "x"}]})
        kind, _obj = conn.next_update(timeout=5.0)  # 吃掉假后端的 tool_call 帧
        assert kind == "update"
        conn.abort_turn()
        t0 = time.monotonic()
        with pytest.raises(acp_mod.TurnAborted):
            conn.next_update(timeout=None)
        assert time.monotonic() - t0 < _ABORT_ESCAPE_S
        conn.end_turn()
    finally:
        conn.terminate()


def test_begin_turn_clears_stale_abort(fake_agent):
    """上轮 cancel 残留的中断不得误伤新一轮（begin_turn 统一清除）。"""
    conn = acp_mod.AcpConnection("/fake/kimi", str(_TEMP_DIR), "fake acp")
    try:
        conn.abort_turn()
        conn.begin_turn("session/prompt", {"sessionId": "fake-session",
                                           "prompt": [{"type": "text", "text": "x"}]})
        kind, _obj = conn.next_update(timeout=5.0)
        assert kind == "update"  # 正常收到帧而非 TurnAborted
        conn.end_turn()
    finally:
        conn.terminate()


# ----------------------------------------------------------------------
# worker × provider 全链路：挂起轮次的停止收尾（复现固化）
# ----------------------------------------------------------------------
def test_request_stop_escapes_stuck_turn(qapp, fake_agent, kimi_provider):
    """假后端沉默（agent 丢失任务完成事件等价）时，request_stop 秒级
    收尾 stopped_by_user——修复前 worker 永久挂起、busy 永不解除。"""
    worker, events = _make_worker(kimi_provider)
    worker.start()
    try:
        assert _wait_for(lambda: any(k == "chunk" for k, _ in events)), \
            "假后端首帧未到达"
        worker.request_stop()
        assert _wait_for(lambda: not worker.isRunning(), timeout=5.0), \
            "停止后 worker 仍未收尾（挂起未解封）"
        assert ("stopped", None) in events
        assert not any(k == "finished" for k, _ in events)
        assert fake_agent.last is not None
        assert fake_agent.last.cancel_seen.is_set()  # 协议 cancel 照发
    finally:
        worker.wait(5000)


def test_normal_turn_unaffected_by_tick_slice(qapp, fake_agent, kimi_provider):
    """正常轮次不受拆拍影响：正文照常、正常收尾（finished 空错误串）。"""
    fake_agent.scenario = "normal"
    worker, events = _make_worker(kimi_provider)
    worker.start()
    try:
        assert _wait_for(lambda: any(k == "finished" for k, _ in events), 15.0), \
            "正常轮次未收尾"
        kinds = [k for k, _ in events]
        assert "chunk" in kinds
        assert ("finished", "") in events
    finally:
        worker.wait(5000)


# ----------------------------------------------------------------------
# 看门狗：超阈值自动中断
# ----------------------------------------------------------------------
class _StopRecorder:
    def __init__(self) -> None:
        self.calls = 0

    def request_stop(self) -> None:
        self.calls += 1

    def disconnect(self, *_args) -> None:
        pass  # ChatPanel.close() 桩接口

    def setParent(self, *_args) -> None:
        pass  # ChatPanel.close() 桩接口

    def wait(self, timeout=None) -> bool:
        return True  # _cleanup_blocking 桩接口：视作已退出


def _make_panel(fake_registry, isolated_settings) -> ChatPanel:
    return ChatPanel("fake-acp", "m1", str(_TEMP_DIR))


def test_stall_watchdog_auto_stops_and_hints(fake_registry, isolated_settings):
    panel = _make_panel(fake_registry, isolated_settings)
    messages: list[str] = []
    panel.output.append_message = lambda role, text: messages.append(f"{role}: {text}")
    recorder = _StopRecorder()
    panel._worker = recorder  # type: ignore[assignment]
    update_settings({KEY_TURN_STALL_TIMEOUT_MIN: 1})
    panel._last_chunk_at = time.monotonic() - 120  # 已闲置 2 分钟 > 阈值 1 分钟
    try:
        panel._stall_watchdog_tick()
        assert recorder.calls == 1
        assert any("已自动中断" in m for m in messages)
        # 单次触发后置空计时，紧接着的 tick 不重复触发
        panel._stall_watchdog_tick()
        assert recorder.calls == 1
    finally:
        panel.close()


def test_stall_watchdog_respects_threshold_and_zero(
        fake_registry, isolated_settings):
    panel = _make_panel(fake_registry, isolated_settings)
    recorder = _StopRecorder()
    panel._worker = recorder  # type: ignore[assignment]
    panel._last_chunk_at = time.monotonic() - 120
    try:
        # 阈值 0 = 关闭：闲置再久也不触发
        update_settings({KEY_TURN_STALL_TIMEOUT_MIN: 0})
        panel._stall_watchdog_tick()
        assert recorder.calls == 0
        # 闲置短于阈值：不触发
        update_settings({KEY_TURN_STALL_TIMEOUT_MIN: 120})
        panel._last_chunk_at = time.monotonic() - 30
        panel._stall_watchdog_tick()
        assert recorder.calls == 0
        # 无活跃轮次（worker=None）：不触发
        panel._worker = None
        panel._last_chunk_at = time.monotonic() - 120
        panel._stall_watchdog_tick()
        assert recorder.calls == 0
    finally:
        panel.close()


def test_stall_timeout_default_and_roundtrip(isolated_settings):
    assert DEFAULT_SETTINGS[KEY_TURN_STALL_TIMEOUT_MIN] == 10
    assert settings_mod.load_settings()[KEY_TURN_STALL_TIMEOUT_MIN] == 10
    update_settings({KEY_TURN_STALL_TIMEOUT_MIN: 3})
    assert settings_mod.load_settings()[KEY_TURN_STALL_TIMEOUT_MIN] == 3


def test_stall_watchdog_marks_provider_and_explains_manual_recovery(
        fake_registry, isolated_settings):
    panel = _make_panel(fake_registry, isolated_settings)
    messages: list[str] = []
    panel.output.append_message = lambda role, text: messages.append(text)

    class _Provider:
        stalled = 0

        def mark_stalled(self):
            self.stalled += 1

    provider = _Provider()
    panel._providers[panel._llm_name] = provider  # type: ignore[assignment]
    recorder = _StopRecorder()
    panel._worker = recorder  # type: ignore[assignment]
    update_settings({KEY_TURN_STALL_TIMEOUT_MIN: 1})
    panel._last_chunk_at = time.monotonic() - 120
    try:
        panel._stall_watchdog_tick()
        assert provider.stalled == 1
        assert recorder.calls == 1
        assert any("下一次手动发送将尝试恢复原会话" in m for m in messages)
        assert any("原请求不会自动重发" in m for m in messages)
    finally:
        panel.close()


def test_recovery_failure_cancels_pending_and_preserves_drafts(
        fake_registry, isolated_settings):
    panel = _make_panel(fake_registry, isolated_settings)
    panel._sent_text = "retry original session"
    panel._history = [{"role": "user", "content": panel._sent_text}]
    panel.input.setPlainText("queued draft")
    panel._pending_send = True
    try:
        panel._on_finished("原会话恢复失败，未发送消息；可重试恢复")
        assert panel.input.toPlainText() == "queued draft"
        assert not panel._pending_send
        assert not panel._busy
        assert panel._history == []
    finally:
        panel.close()


def test_recovery_failure_restores_sent_text_when_input_is_empty(
        fake_registry, isolated_settings):
    panel = _make_panel(fake_registry, isolated_settings)
    panel._sent_text = "retry original session"
    panel._history = [{"role": "user", "content": panel._sent_text}]
    try:
        panel._on_finished("原会话恢复失败，未发送消息；可重试恢复")
        assert panel.input.toPlainText() == "retry original session"
        assert panel._history == []
    finally:
        panel.close()
