"""提示音播放：按事件读设置选文件，QSoundEffect 主路径 + 系统播放器兜底。

QSoundEffect 异步非阻塞且无需新增依赖（PySide6 QtMultimedia 已在依赖内）；
无声卡/缺音频后端时它不抛异常而是异步置 Error 态，故延迟一拍复查状态，
失败再降级 aplay/paplay/play 子进程（播放器探测链参考 kilocode sound.ts）。

事件 → 设置键映射决定播放哪个文件（assets/sounds/正在使用/ 下的 wav），
用户在设置中心「通知」页下拉框挑选；文件名缺失或文件不存在静默跳过。
"""
import shutil
import subprocess
import time
from pathlib import Path

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtMultimedia import QSoundEffect

from core.paths import SOUNDS_ACTIVE_DIR
from gui.settings import (
    KEY_NOTIFY_SOUND_DONE,
    KEY_NOTIFY_SOUND_ERROR,
    KEY_NOTIFY_SOUND_QUESTION,
    load_settings,
)

#: 事件名 → 设置键（持久化的是正在使用/ 下的文件名）
_EVENT_KEY = {
    "done": KEY_NOTIFY_SOUND_DONE,
    "error": KEY_NOTIFY_SOUND_ERROR,
    "question": KEY_NOTIFY_SOUND_QUESTION,
}

#: 触发合并窗口（秒）：多标签同时收尾时只响一声，防叠音刺耳
_DEBOUNCE_S = 1.0

#: QSoundEffect 异步加载完成后复查状态的延迟（毫秒）
_STATUS_CHECK_MS = 500

_last_play_at = 0.0
_effect: QSoundEffect | None = None  # 模块级单例：局部变量被 GC 会中断播放
_procs: list[subprocess.Popen] = []  # 兜底子进程持有：防退出后成僵尸


def play_event_sound(event: str) -> None:
    """播放指定事件的提示音（异步，不阻塞 GUI；未知事件/文件缺失静默跳过）。"""
    key = _EVENT_KEY.get(event)
    if key is None:
        return
    filename = load_settings()[key]
    if not filename:
        return
    _play_debounced(SOUNDS_ACTIVE_DIR / filename)


def play_completion_sound(success: bool) -> None:
    """任务完成提示音薄封装：成功/失败映射到 done/error 事件。"""
    play_event_sound("done" if success else "error")


def play_preview(filename: str) -> None:
    """试听指定文件（设置中心下拉框选中项；文件缺失静默跳过）。"""
    if not filename:
        return
    _play_debounced(SOUNDS_ACTIVE_DIR / filename)


def _play_debounced(path: Path) -> None:
    global _last_play_at
    now = time.monotonic()
    if now - _last_play_at < _DEBOUNCE_S:
        return
    _last_play_at = now
    if not path.is_file():
        return
    _play(path)


def _play(path: Path) -> None:
    global _effect
    try:
        if _effect is None:
            _effect = QSoundEffect()
        _effect.setSource(QUrl.fromLocalFile(str(path)))
        _effect.play()
        QTimer.singleShot(_STATUS_CHECK_MS, lambda: _fallback_if_failed(path))
    except (RuntimeError, OSError):
        # Qt 音频后端缺失时构造/播放抛 RuntimeError，降级系统播放器
        _play_system(path)


def _fallback_if_failed(path: Path) -> None:
    if _effect is not None and _effect.status() == QSoundEffect.Status.Error:
        _play_system(path)


def _play_system(path: Path) -> None:
    """系统播放器兜底：aplay/paplay/play 择一（均无则静默放弃）。"""
    _procs[:] = [p for p in _procs if p.poll() is None]  # 顺手收割已退出者
    for cmd in ("aplay", "paplay", "play"):
        exe = shutil.which(cmd)
        if exe:
            _procs.append(subprocess.Popen(
                [exe, str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            return
