"""媒体查看器音频播放回归测试（对应 2026-1013-0546 计划 T5）。

覆盖：
- open_media 置自动播放标志、武装加载超时定时器、启用播放按钮；
- LoadedMedia 触发自动播放并解除超时武装（双击即播链路）；
- 加载超时守卫：超时上抛 failed 且音频降级系统播放器、视频不降级；
- _on_error 空串静默、非空串上抛；同一轮打开失败只上抛一次；
- stop 解除超时武装并禁用播放按钮（生命周期红线）；
- sound_player.play_system 无可用播放器时返回 False。

媒体后端依赖运行环境（声卡/FFmpeg 插件），测试用假播放器替换
_ensure_player 产出，保证 offscreen/无声卡环境确定性。
临时数据按 AGENTS.md 纪律落项目内 .temp/，不用系统临时目录。
"""
import shutil
from pathlib import Path

import pytest
from PySide6.QtCore import QEventLoop, QTimer, QUrl
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication

import gui.sound_player as sound_player
from gui.panels.viewer.media_viewer import MediaViewer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKDIR = PROJECT_ROOT / ".temp" / "tests_media_viewer"
WAV = PROJECT_ROOT / "assets" / "sounds" / "备选池" / "alert-02.wav"

PALETTE = {"muted_text": "#888888", "window_bg": "#ffffff"}


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


def pump(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


class _FakePlayer:
    """确定性替代 QMediaPlayer：不触后端，play/pause 仅计数。"""

    def __init__(self) -> None:
        self.play_calls = 0
        self.pause_calls = 0
        self._source = QUrl()

    def play(self) -> None:
        self.play_calls += 1

    def pause(self) -> None:
        self.pause_calls += 1

    def stop(self) -> None:
        pass

    def setSource(self, url: QUrl) -> None:
        self._source = url

    def source(self) -> QUrl:
        return self._source

    def playbackState(self):
        return QMediaPlayer.PlaybackState.StoppedState


def make_viewer(monkeypatch) -> MediaViewer:
    """实例化 MediaViewer 并以假播放器替换 _ensure_player（绕开真实后端）。"""
    WORKDIR.mkdir(parents=True, exist_ok=True)
    viewer = MediaViewer(PALETTE)
    viewer.resize(400, 300)

    def fake_ensure() -> None:
        if viewer._player is None:
            viewer._player = _FakePlayer()
            viewer._video = viewer._video_host  # 占位宿主顶视频页，满足切页
            viewer._load_timer = QTimer(viewer)
            viewer._load_timer.setSingleShot(True)
            viewer._load_timer.timeout.connect(viewer._on_load_timeout)

    monkeypatch.setattr(viewer, "_ensure_player", fake_ensure)
    viewer.show()
    return viewer


def test_open_media_arms_autoplay_and_timeout(qapp, monkeypatch):
    viewer = make_viewer(monkeypatch)
    viewer.open_media(WAV)
    assert viewer._autoplay is True
    assert viewer._is_audio is True
    assert viewer._btn_play.isEnabled()
    assert viewer._load_timer.isActive(), "挂源后未武装加载超时定时器"
    assert viewer._audio_label.text().endswith("alert-02.wav")


def test_loaded_media_triggers_autoplay_and_disarms_timer(qapp, monkeypatch):
    viewer = make_viewer(monkeypatch)
    viewer.open_media(WAV)
    viewer._on_media_status(QMediaPlayer.MediaStatus.LoadedMedia)
    assert viewer._player.play_calls == 1, "LoadedMedia 未触发自动播放"
    assert viewer._autoplay is False
    assert not viewer._load_timer.isActive(), "加载就绪后超时定时器应解除武装"


def test_load_timeout_fails_and_degrades_audio(qapp, monkeypatch):
    degraded = []
    monkeypatch.setattr(
        "gui.panels.viewer.media_viewer.play_system",
        lambda p: degraded.append(p) or True)
    viewer = make_viewer(monkeypatch)
    failed = []
    viewer.failed.connect(failed.append)
    viewer.open_media(WAV)
    viewer._load_timer.setInterval(30)  # 缩短超时，模拟后端停滞
    pump(200)
    assert len(failed) == 1, "超时未上抛 failed"
    assert "超时" in failed[0]
    assert "已降级系统播放器" in failed[0], "音频超时未降级系统播放器"
    assert degraded == [WAV]


def test_load_timeout_does_not_degrade_video(qapp, monkeypatch):
    degraded = []
    monkeypatch.setattr(
        "gui.panels.viewer.media_viewer.play_system",
        lambda p: degraded.append(p) or True)
    viewer = make_viewer(monkeypatch)
    failed = []
    viewer.failed.connect(failed.append)
    viewer.open_media(WORKDIR / "movie.mp4")  # 后缀判定走视频分支，无需真实文件
    viewer._load_timer.setInterval(30)
    pump(200)
    assert len(failed) == 1
    assert "视频" in failed[0]
    assert "已降级" not in failed[0], "视频不应降级系统播放器"
    assert degraded == []


def test_error_empty_silent_nonempty_emits(qapp, monkeypatch):
    viewer = make_viewer(monkeypatch)
    failed = []
    viewer.failed.connect(failed.append)
    viewer._on_error(QMediaPlayer.Error.NoError, "")
    assert failed == [], "空错误消息不应上抛"
    viewer._on_error(QMediaPlayer.Error.ResourceError, "boom")
    assert failed == ["boom"]


def test_failure_reported_once_per_open(qapp, monkeypatch):
    monkeypatch.setattr(
        "gui.panels.viewer.media_viewer.play_system", lambda p: True)
    viewer = make_viewer(monkeypatch)
    failed = []
    viewer.failed.connect(failed.append)
    viewer.open_media(WAV)
    viewer._on_load_timeout()
    viewer._on_error(QMediaPlayer.Error.ResourceError, "boom")
    assert len(failed) == 1, "同一轮打开超时与错误并发时重复上抛"


def test_stop_disarms_timer_and_disables_button(qapp, monkeypatch):
    viewer = make_viewer(monkeypatch)
    viewer.open_media(WAV)
    viewer.stop()
    assert viewer._autoplay is False
    assert not viewer._load_timer.isActive(), "stop 未解除超时武装"
    assert not viewer._btn_play.isEnabled()


def test_play_system_false_without_players(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _cmd: None)
    assert sound_player.play_system(WAV) is False
