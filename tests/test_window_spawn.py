"""新窗口进程启动命令、终端分离与返回值契约测试。"""
from types import SimpleNamespace
import subprocess
import sys

import ide.gui.main_window as main_window_mod
from ide.core.paths import PROJECT_ROOT


def _invoke_spawn(monkeypatch, folder, *, frozen=False, appimage=None):
    calls = []
    process = object()
    messages = []
    status_bar = SimpleNamespace(
        showMessage=lambda message, timeout: messages.append((message, timeout)))
    window = SimpleNamespace(
        statusBar=lambda: status_bar,
        STATUS_MSG_TIMEOUT_MS=main_window_mod.MainWindow.STATUS_MSG_TIMEOUT_MS,
    )

    def fake_popen(cmd, **kwargs):
        calls.append((cmd, kwargs))
        return process

    monkeypatch.setattr(main_window_mod, "IS_FROZEN", frozen)
    if appimage is None:
        monkeypatch.delenv("APPIMAGE", raising=False)
    else:
        monkeypatch.setenv("APPIMAGE", appimage)
    monkeypatch.setattr(main_window_mod.subprocess, "Popen", fake_popen)

    result = main_window_mod.MainWindow._spawn_window(window, folder)

    assert result is process
    assert len(calls) == 1
    return calls[0], messages


def _assert_detached(kwargs):
    assert kwargs == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "start_new_session": True,
    }


def test_development_workspace_spawn_detaches_terminal(monkeypatch):
    folder = str(PROJECT_ROOT)

    (cmd, kwargs), messages = _invoke_spawn(monkeypatch, folder)

    assert cmd == [sys.executable, str(PROJECT_ROOT / "main.py"), folder]
    _assert_detached(kwargs)
    assert messages == [(
        f"已在新窗口打开：{folder}",
        main_window_mod.MainWindow.STATUS_MSG_TIMEOUT_MS,
    )]


def test_development_blank_spawn_detaches_terminal(monkeypatch):
    (cmd, kwargs), messages = _invoke_spawn(monkeypatch, None)

    assert cmd == [sys.executable, str(PROJECT_ROOT / "main.py"), "--blank"]
    _assert_detached(kwargs)
    assert messages == [(
        "已新建空白窗口",
        main_window_mod.MainWindow.STATUS_MSG_TIMEOUT_MS,
    )]


def test_frozen_spawn_uses_existing_appimage_and_detaches_terminal(monkeypatch):
    appimage = str(PROJECT_ROOT / "main.py")
    folder = str(PROJECT_ROOT)

    (cmd, kwargs), messages = _invoke_spawn(
        monkeypatch, folder, frozen=True, appimage=appimage)

    assert cmd == [appimage, folder]
    _assert_detached(kwargs)
    assert messages == [(
        f"已在新窗口打开：{folder}",
        main_window_mod.MainWindow.STATUS_MSG_TIMEOUT_MS,
    )]


def test_frozen_spawn_falls_back_to_executable_and_detaches_terminal(monkeypatch):
    folder = str(PROJECT_ROOT)

    (cmd, kwargs), messages = _invoke_spawn(
        monkeypatch, folder, frozen=True, appimage=str(PROJECT_ROOT / ".temp"))

    assert cmd == [sys.executable, folder]
    _assert_detached(kwargs)
    assert messages == [(
        f"已在新窗口打开：{folder}",
        main_window_mod.MainWindow.STATUS_MSG_TIMEOUT_MS,
    )]
