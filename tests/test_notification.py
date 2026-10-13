"""事件提醒（声音 + 系统桌面通知）行为断言。

覆盖：
- 设置键默认值与 roundtrip（持久化文件隔离到 ./.temp/，不动真实配置）
- ChatPanel.turn_completed 发射语义：成功/失败发，用户中断不发，
  待发接续轮中途不发；question_asked：question 请求发、普通请求不发
- 声音事件 → 设置键 → 文件名映射与试听
- 桌面通知（desktop_notifier）事件态映射、未知事件/命令缺失静默、命令形态
- MainWindow 触发门控（mock 播放与通知发送，不建真窗口）

offscreen 平台由 conftest 注入；注册表 monkeypatch 伪造，不起真实 CLI。
"""
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

import gui.panels.chat.model_bar as model_bar_mod
import gui.settings as settings_mod
import gui.sound_player as sound_player_mod
from gui import main_window as main_window_mod
from gui import desktop_notifier
from gui.panels.chat.panel import ChatPanel
from gui.settings_dialog import SettingsDialog
from gui.settings import (
    DEFAULT_SETTINGS,
    KEY_NOTIFY_BANNER_ENABLED,
    KEY_NOTIFY_SOUND_DONE,
    KEY_NOTIFY_SOUND_ENABLED,
    KEY_NOTIFY_SOUND_ERROR,
    KEY_NOTIFY_SOUND_QUESTION,
    load_settings,
    update_settings,
)
from llm.registry import BackendSpec
import gui.panels.chat.panel as panel_mod

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
    # 各事件提示音文件名默认值
    assert settings[KEY_NOTIFY_SOUND_DONE] == "done.wav"
    assert settings[KEY_NOTIFY_SOUND_ERROR] == "error.wav"
    assert settings[KEY_NOTIFY_SOUND_QUESTION] == "question.wav"


def test_notify_keys_roundtrip(isolated_settings):
    update_settings({KEY_NOTIFY_SOUND_ENABLED: False})
    assert load_settings()[KEY_NOTIFY_SOUND_ENABLED] is False
    update_settings({KEY_NOTIFY_BANNER_ENABLED: False})
    settings = load_settings()
    assert settings[KEY_NOTIFY_BANNER_ENABLED] is False
    assert settings[KEY_NOTIFY_SOUND_ENABLED] is False  # 前次写入不回弹


def test_notify_sound_file_roundtrip(isolated_settings):
    update_settings({KEY_NOTIFY_SOUND_QUESTION: "yup-01.wav"})
    settings = load_settings()
    assert settings[KEY_NOTIFY_SOUND_QUESTION] == "yup-01.wav"
    assert settings[KEY_NOTIFY_SOUND_DONE] == "done.wav"  # 其余键不受影响


def test_notify_sound_file_type_guard(isolated_settings):
    """手改配置为非 str → 回退默认（load_settings 类型防御）。"""
    settings_file = isolated_settings
    settings_file.write_text(
        '{"notify_sound_done": 123}', encoding="utf-8")
    assert load_settings()[KEY_NOTIFY_SOUND_DONE] == "done.wav"


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
# question_asked 发射语义
# ----------------------------------------------------------------------
def test_question_asked_on_question_request(qapp, fake_registry, monkeypatch):
    """question 特判分支：进 QUESTION_BRIDGE 前发信号（mock 桥防阻塞）。"""
    panel = _make_panel(qapp, str(Path(__file__).resolve().parents[1]))
    monkeypatch.setattr(panel_mod.QUESTION_BRIDGE, "ask",
                        lambda params, tcid, parent: "opt-1")
    emitted: list[str] = []
    panel.question_asked.connect(lambda: emitted.append("q"))
    params = {"toolCall": {"toolCallId": "t1",
                           "rawInput": {"questions": [{"question": "选哪个？"}]}}}
    assert panel._ask_permission(params) == "opt-1"
    assert emitted == ["q"]


def test_question_asked_not_on_plain_request(qapp, fake_registry, isolated_settings):
    """普通权限请求（非 question）不发提问信号。"""
    panel = _make_panel(qapp, str(Path(__file__).resolve().parents[1]))
    emitted: list[str] = []
    panel.question_asked.connect(lambda: emitted.append("q"))
    params = {"toolCall": {"toolCallId": "t2", "title": "Bash",
                           "rawInput": {"command": "ls"}},
              "options": [{"optionId": "allow-1", "kind": "allow_once"}]}
    panel._ask_permission(params)  # auto_guarded 默认档非黑名单 → 自动放行
    assert emitted == []


# ----------------------------------------------------------------------
# 声音播放（防抖 / 缺文件静默；一律打桩，禁止真实出声——见 tests/README.md）
# ----------------------------------------------------------------------
def test_sound_debounce(qapp, isolated_settings, monkeypatch):
    calls: list[Path] = []
    monkeypatch.setattr(sound_player_mod, "_play", lambda p: calls.append(p))
    monkeypatch.setattr(sound_player_mod, "_last_play_at", 0.0)
    sound_player_mod.play_completion_sound(True)
    sound_player_mod.play_completion_sound(True)  # 1 秒内重复触发合并
    assert len(calls) == 1
    assert calls[0].name == "done.wav"


def test_sound_event_file_mapping(qapp, isolated_settings, monkeypatch):
    """事件 → 设置键 → 文件名：用户改选后播放路径跟随。"""
    update_settings({KEY_NOTIFY_SOUND_QUESTION: "yup-01.wav"})
    calls: list[Path] = []
    monkeypatch.setattr(sound_player_mod, "_play", lambda p: calls.append(p))
    monkeypatch.setattr(sound_player_mod, "_last_play_at", 0.0)
    sound_player_mod.play_event_sound("question")
    assert calls[0].name == "yup-01.wav"
    monkeypatch.setattr(sound_player_mod, "_last_play_at", 0.0)
    sound_player_mod.play_completion_sound(False)
    assert calls[-1].name == "error.wav"


def test_sound_unknown_event_silent(qapp, monkeypatch):
    calls: list[Path] = []
    monkeypatch.setattr(sound_player_mod, "_play", lambda p: calls.append(p))
    sound_player_mod.play_event_sound("no_such_event")  # 未知事件静默跳过
    assert calls == []


def test_sound_missing_file_silent(qapp, isolated_settings, monkeypatch):
    monkeypatch.setattr(sound_player_mod, "SOUNDS_ACTIVE_DIR", _TEMP_DIR / "no_such_dir")
    monkeypatch.setattr(sound_player_mod, "_last_play_at", 0.0)
    sound_player_mod.play_completion_sound(False)  # 文件缺失静默跳过，不抛异常


def test_sound_real_file_no_crash(qapp, isolated_settings, monkeypatch):
    """真实 wav 路径全链路不抛异常；QSoundEffect 换假后端——offscreen 不关
    音频，真后端会真实外放（事故见 tests/README.md），故只验证链路不验证出声。"""
    played: list[str] = []

    class _FakeEffect:
        def setSource(self, url) -> None:
            played.append(url.toLocalFile())

        def play(self) -> None:
            pass

        def status(self):
            return QSoundEffect.Status.Ready  # 非 Error：不触发系统播放器兜底

    monkeypatch.setattr(sound_player_mod, "QSoundEffect", _FakeEffect)
    monkeypatch.setattr(sound_player_mod, "_effect", None)  # 清真实单例，强制走假后端
    monkeypatch.setattr(sound_player_mod, "_last_play_at", 0.0)
    sound_player_mod.play_completion_sound(True)
    QTest.qWait(600)  # 覆盖 _STATUS_CHECK_MS 复查分支
    qapp.processEvents()
    assert played and played[0].endswith("done.wav")


# ----------------------------------------------------------------------
# 设置中心「通知」页下拉框（选中即试听；reload 回读不播放）
# ----------------------------------------------------------------------
def _make_settings_dialog() -> SettingsDialog:
    """桩 ctx 构建设置中心（模型页对 chat_tabs=None 有守卫，不起真后端）。"""

    class _FakeBar:
        def showMessage(self, *a, **k):
            pass

    ctx = QWidget()
    ctx.statusBar = lambda: _FakeBar()
    ctx.STATUS_MSG_TIMEOUT_MS = 2000
    ctx.STATUS_MSG_LONG_MS = 5000
    ctx.FONT_SIZE_MIN = 8
    ctx.FONT_SIZE_MAX = 24
    ctx.apply_model_selection = lambda *a: None
    ctx.switch_theme = lambda *a: None
    ctx.set_font_size = lambda *a: None
    ctx.set_terminal_swap_copy_paste = lambda *a: None
    ctx.set_chat_renderer = lambda *a: None
    ctx.open_settings_file = lambda: None
    ctx.reset_settings = lambda: None
    ctx.chat_tabs = None
    return SettingsDialog(ctx)


def test_notify_combo_select_autoplays(qapp, isolated_settings, monkeypatch):
    played: list[str] = []
    monkeypatch.setattr(sound_player_mod, "play_preview",
                        lambda filename: played.append(filename))
    dialog = _make_settings_dialog()
    dialog.show()  # reload 挂 showEvent（settings_dialog.showEvent），show 后控件态才回读
    assert played == []  # reload 回读不播放
    combo = dialog._notify_sound_combos["question"]
    combo.setCurrentText("yup-01.wav")
    assert played == ["yup-01.wav"]  # 选中即自动试听一次
    assert load_settings()[KEY_NOTIFY_SOUND_QUESTION] == "yup-01.wav"


def test_banner_preview_buttons(qapp, isolated_settings):
    """试看按钮：逐行触发对应事件态，且不受横幅开关影响（不经门控直发）。"""
    update_settings({KEY_NOTIFY_BANNER_ENABLED: False})
    shown: list[str] = []
    dialog = _make_settings_dialog()
    dialog._ctx.preview_notify_banner = lambda kind: shown.append(kind)
    dialog.show()
    for kind in ("success", "error", "question"):
        dialog._notify_banner_previews[kind].click()
    assert shown == ["success", "error", "question"]
    assert load_settings()[KEY_NOTIFY_BANNER_ENABLED] is False  # 试看不改动开关


# ----------------------------------------------------------------------
# 系统桌面通知（desktop_notifier；mock 发送点，不发真通知）
# ----------------------------------------------------------------------
def test_notifier_event_mapping(qapp, monkeypatch):
    """事件态 → 标题/默认正文映射（三态文案沿用原软件内横幅）；detail 覆盖正文。"""
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(desktop_notifier, "_send",
                        lambda title, body: sent.append((title, body)))
    desktop_notifier.notify_completion(True)
    desktop_notifier.notify_completion(False)
    desktop_notifier.notify_event("question")
    desktop_notifier.notify_event("success", "请求超时")
    assert sent == [
        ("任务完成", "AI 已完成本轮任务"),
        ("任务失败", "AI 本轮请求失败"),
        ("需要你的回答", "AI 发起了提问，请在对话卡片中作答"),
        ("任务完成", "请求超时"),
    ]


def test_notifier_unknown_event_silent(qapp, monkeypatch):
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(desktop_notifier, "_send",
                        lambda *a: sent.append(a))
    desktop_notifier.notify_event("no_such_event")  # 未知事件静默跳过
    assert sent == []


def test_notifier_missing_command_silent(qapp, monkeypatch):
    """极简环境无 notify-send：静默跳过，不抛异常。"""
    monkeypatch.setattr(desktop_notifier, "shutil",
                        SimpleNamespace(which=lambda cmd: None))
    desktop_notifier.notify_event("success")


def test_notifier_command_shape(qapp, monkeypatch):
    """发送命令形态：notify-send -a 应用名 -t 驻留时长 [-i 图标] 标题 正文。"""
    spawned: list[list[str]] = []

    class _FakeProc:
        def poll(self):
            return 0

    def _fake_popen(cmd, **_kw):
        spawned.append(cmd)
        return _FakeProc()

    monkeypatch.setattr(desktop_notifier, "shutil",
                        SimpleNamespace(which=lambda cmd: f"/usr/bin/{cmd}"))
    monkeypatch.setattr(desktop_notifier, "subprocess",
                        SimpleNamespace(Popen=_fake_popen, DEVNULL=None))
    desktop_notifier.notify_event("error", "网络错误")
    cmd = spawned[0]
    assert cmd[0].endswith("notify-send")
    assert cmd[-2:] == ["任务失败", "网络错误"]
    assert "-a" in cmd and "Zen Studio" in cmd


# ----------------------------------------------------------------------
# MainWindow 触发门控（假 self + mock 播放/通知发送，不建真窗口）
# ----------------------------------------------------------------------
def test_main_window_gating(qapp, monkeypatch):
    played: list[bool] = []
    shown: list[bool] = []
    monkeypatch.setattr(main_window_mod, "play_completion_sound",
                        lambda ok: played.append(ok))
    monkeypatch.setattr(main_window_mod, "notify_completion",
                        lambda ok: shown.append(ok))

    monkeypatch.setattr(main_window_mod, "load_settings", lambda: {
        KEY_NOTIFY_SOUND_ENABLED: True, KEY_NOTIFY_BANNER_ENABLED: True})
    main_window_mod.MainWindow._on_turn_completed(SimpleNamespace(), True)
    assert played == [True]
    assert shown == [True]

    played.clear()
    shown.clear()
    monkeypatch.setattr(main_window_mod, "load_settings", lambda: {
        KEY_NOTIFY_SOUND_ENABLED: False, KEY_NOTIFY_BANNER_ENABLED: False})
    main_window_mod.MainWindow._on_turn_completed(SimpleNamespace(), True)
    assert played == []
    assert shown == []


def test_main_window_question_gating(qapp, monkeypatch):
    """提问提醒门控：与完成提醒共用同一组开关。"""
    played: list[str] = []
    shown: list[str] = []
    monkeypatch.setattr(main_window_mod, "play_event_sound",
                        lambda event: played.append(event))
    monkeypatch.setattr(main_window_mod, "notify_event",
                        lambda kind: shown.append(kind))

    monkeypatch.setattr(main_window_mod, "load_settings", lambda: {
        KEY_NOTIFY_SOUND_ENABLED: True, KEY_NOTIFY_BANNER_ENABLED: True})
    main_window_mod.MainWindow._on_question_asked(SimpleNamespace())
    assert played == ["question"]
    assert shown == ["question"]

    played.clear()
    shown.clear()
    monkeypatch.setattr(main_window_mod, "load_settings", lambda: {
        KEY_NOTIFY_SOUND_ENABLED: False, KEY_NOTIFY_BANNER_ENABLED: False})
    main_window_mod.MainWindow._on_question_asked(SimpleNamespace())
    assert played == []
    assert shown == []


def test_main_window_preview_banner(qapp, monkeypatch):
    """试看横幅：不经开关门控，直发指定事件态（与试听先例对齐）。"""
    shown: list[str] = []
    monkeypatch.setattr(main_window_mod, "notify_event",
                        lambda kind: shown.append(kind))
    main_window_mod.MainWindow.preview_notify_banner(SimpleNamespace(), "success")
    assert shown == ["success"]
