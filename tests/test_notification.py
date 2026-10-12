"""任务完成提醒（声音 + 顶部通知横幅）行为断言。

覆盖：
- 新设置键默认值与 roundtrip（持久化文件隔离到 ./.temp/，不动真实配置）
- ChatPanel.turn_completed 发射语义：成功/失败发，用户中断不发，
  待发接续轮中途不发
- NotificationBanner 显示/替换/自动消退
- MainWindow._on_turn_completed 开关门控（mock 播放与横幅，不建真窗口）

offscreen 平台由 conftest 注入；注册表 monkeypatch 伪造，不起真实 CLI。
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

import gui.panels.chat.model_bar as model_bar_mod
import gui.settings as settings_mod
import gui.sound_player as sound_player_mod
from gui import main_window as main_window_mod
from gui.notification_banner import NotificationBanner
from gui.panels.chat.panel import ChatPanel
from gui.settings import (
    DEFAULT_SETTINGS,
    KEY_NOTIFY_BANNER_ENABLED,
    KEY_NOTIFY_SOUND_ENABLED,
    load_settings,
    update_settings,
)
from llm.registry import BackendSpec

#: 持久化隔离目录（项目纪律：临时文件一律落 ./.temp/，禁用系统临时目录）
_TEMP_DIR = Path(__file__).resolve().parents[1] / ".temp" / "test_notification"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


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


def _make_panel(qapp, workspace_root: str) -> ChatPanel:
    return ChatPanel("fake-acp", "m1", workspace_root)


# ----------------------------------------------------------------------
# 设置键
# ----------------------------------------------------------------------
def test_notify_keys_default_on(isolated_settings):
    settings = load_settings()
    assert settings[KEY_NOTIFY_SOUND_ENABLED] is True
    assert settings[KEY_NOTIFY_BANNER_ENABLED] is True
    assert DEFAULT_SETTINGS[KEY_NOTIFY_SOUND_ENABLED] is True
    assert DEFAULT_SETTINGS[KEY_NOTIFY_BANNER_ENABLED] is True


def test_notify_keys_roundtrip(isolated_settings):
    update_settings({KEY_NOTIFY_SOUND_ENABLED: False})
    assert load_settings()[KEY_NOTIFY_SOUND_ENABLED] is False
    update_settings({KEY_NOTIFY_BANNER_ENABLED: False})
    settings = load_settings()
    assert settings[KEY_NOTIFY_BANNER_ENABLED] is False
    assert settings[KEY_NOTIFY_SOUND_ENABLED] is False  # 前次写入不回弹


# ----------------------------------------------------------------------
# turn_completed 发射语义
# ----------------------------------------------------------------------
def test_turn_completed_on_success(qapp, fake_registry):
    panel = _make_panel(qapp, str(Path(__file__).resolve().parents[1]))
    emitted: list[bool] = []
    panel.turn_completed.connect(emitted.append)
    panel._on_finished("")
    assert emitted == [True]


def test_turn_completed_on_error(qapp, fake_registry):
    panel = _make_panel(qapp, str(Path(__file__).resolve().parents[1]))
    panel._history.append({"role": "user", "content": "hi"})
    emitted: list[bool] = []
    panel.turn_completed.connect(emitted.append)
    panel._on_finished("boom")
    assert emitted == [False]


def test_turn_completed_not_on_user_stop(qapp, fake_registry):
    panel = _make_panel(qapp, str(Path(__file__).resolve().parents[1]))
    panel._history.append({"role": "user", "content": "hi"})
    emitted: list[bool] = []
    panel.turn_completed.connect(emitted.append)
    panel._on_stopped()
    assert emitted == []


def test_turn_completed_not_mid_continuation(qapp, fake_registry, monkeypatch):
    """待发接续：本轮结束即自动开下一轮，未真正闲下来 → 不提醒。"""
    panel = _make_panel(qapp, str(Path(__file__).resolve().parents[1]))
    panel._set_busy(True)  # 模拟轮次进行中
    panel._pending_send = True
    monkeypatch.setattr(panel, "_fire_pending_send", lambda: True)
    emitted: list[bool] = []
    panel.turn_completed.connect(emitted.append)
    panel._on_finished("")
    assert emitted == []
    assert panel._busy is True  # busy 不间断（接续轮进行中）


# ----------------------------------------------------------------------
# 声音播放（防抖 / 缺文件静默 / 无声环境不抛异常）
# ----------------------------------------------------------------------
def test_sound_debounce(qapp, monkeypatch):
    calls: list[Path] = []
    monkeypatch.setattr(sound_player_mod, "_play", lambda p: calls.append(p))
    monkeypatch.setattr(sound_player_mod, "_last_play_at", 0.0)
    sound_player_mod.play_completion_sound(True)
    sound_player_mod.play_completion_sound(True)  # 1 秒内重复触发合并
    assert len(calls) == 1
    assert calls[0].name == "done.wav"


def test_sound_missing_file_silent(qapp, monkeypatch):
    monkeypatch.setattr(sound_player_mod, "SOUNDS_ACTIVE_DIR", _TEMP_DIR / "no_such_dir")
    monkeypatch.setattr(sound_player_mod, "_last_play_at", 0.0)
    sound_player_mod.play_completion_sound(False)  # 文件缺失静默跳过，不抛异常


def test_sound_real_file_no_crash(qapp, monkeypatch):
    """真实 wav 路径：offscreen/无声卡环境下 QSoundEffect→兜底链全程不抛异常。"""
    monkeypatch.setattr(sound_player_mod, "_last_play_at", 0.0)
    sound_player_mod.play_completion_sound(True)
    QTest.qWait(600)  # 覆盖 _STATUS_CHECK_MS 复查分支
    qapp.processEvents()


# ----------------------------------------------------------------------
# 通知横幅
# ----------------------------------------------------------------------
def test_banner_show_and_hide(qapp):
    parent = QWidget()
    parent.resize(1200, 800)
    parent.show()
    banner = NotificationBanner(parent)
    banner.show_completion(True)
    assert not banner.isHidden()
    assert banner._title_label.text() == "任务完成"
    assert banner._hide_timer.isActive()  # 自动消退计时已启动
    # 顶部居中定位
    assert banner.y() == 12
    assert abs((parent.width() - banner.width()) // 2 - banner.x()) <= 1
    banner.show_completion(False, "请求超时")
    assert banner._title_label.text() == "任务失败"
    assert banner._body_label.text() == "请求超时"
    banner.hide_banner()
    assert banner.isHidden()


def test_banner_auto_fade_out(qapp):
    parent = QWidget()
    parent.resize(1200, 800)
    parent.show()
    banner = NotificationBanner(parent)
    banner.show_completion(True)
    QTest.qWait(4000 + 300 + 400)  # 展示时长 + 淡出时长 + 余量
    assert banner.isHidden()


# ----------------------------------------------------------------------
# MainWindow 触发门控（假 self + mock 播放/横幅，不建真窗口）
# ----------------------------------------------------------------------
def test_main_window_gating(qapp, monkeypatch):
    played: list[bool] = []
    shown: list[bool] = []
    monkeypatch.setattr(main_window_mod, "play_completion_sound",
                        lambda ok: played.append(ok))

    class FakeBanner:
        def show_completion(self, success):
            shown.append(success)

    monkeypatch.setattr(main_window_mod, "NotificationBanner",
                        lambda parent: FakeBanner())
    fake_self = SimpleNamespace(_notify_banner=None)

    monkeypatch.setattr(main_window_mod, "load_settings", lambda: {
        KEY_NOTIFY_SOUND_ENABLED: True, KEY_NOTIFY_BANNER_ENABLED: True})
    main_window_mod.MainWindow._on_turn_completed(fake_self, True)
    assert played == [True]
    assert shown == [True]

    played.clear()
    shown.clear()
    monkeypatch.setattr(main_window_mod, "load_settings", lambda: {
        KEY_NOTIFY_SOUND_ENABLED: False, KEY_NOTIFY_BANNER_ENABLED: False})
    main_window_mod.MainWindow._on_turn_completed(fake_self, True)
    assert played == []
    assert shown == []
