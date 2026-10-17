"""提示音播放：按事件读设置选文件，QSoundEffect 主路径 + 系统播放器兜底。

QSoundEffect 异步非阻塞且无需新增依赖（PySide6 QtMultimedia 已在依赖内）；
无声卡/缺音频后端时它不抛异常而是异步置 Error 态，故延迟一拍复查状态，
失败再降级 aplay/paplay/play 子进程（播放器探测链参考 kilocode sound.ts）。

事件 → 设置键映射决定播放哪个文件（assets/sounds/正在使用/ 下的 wav），
用户在设置中心「通知」页下拉框挑选；文件名缺失或文件不存在静默跳过。

防抖只按事件维度合并瞬间叠音：多窗口是多进程（一窗一根），进程间天然
互不干扰；日志行带 PID，供多进程共用日志文件时区分播报来源窗口。
"""
import logging
import os
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

logger = logging.getLogger(__name__)

#: 多窗口即多进程共用 config/logs/zen-studio.log，PID 是区分来源窗口的线索
_PID = os.getpid()

#: 事件名 → 设置键（持久化的是正在使用/ 下的文件名）
_EVENT_KEY = {
    "done": KEY_NOTIFY_SOUND_DONE,
    "error": KEY_NOTIFY_SOUND_ERROR,
    "question": KEY_NOTIFY_SOUND_QUESTION,
}

#: 同事件合并窗口（秒）：只挡同一事件的瞬间叠音（多标签同一拍收尾只响一声），
#: 跨事件互不压制、超窗各自出声；试听是人工显式动作，不经此闸门
_DEBOUNCE_S = 0.5

#: QSoundEffect 异步加载完成后复查状态的延迟（毫秒）
_STATUS_CHECK_MS = 500

_last_play_at: dict[str, float] = {}  # 按事件维度记录上次播放时刻
_effect: QSoundEffect | None = None  # 模块级单例：局部变量被 GC 会中断播放
_procs: list[subprocess.Popen] = []  # 兜底子进程持有：防退出后成僵尸
_replay_armed = False  # Loading 补播监听一次性武装标志


def play_event_sound(event: str) -> None:
    """播放指定事件的提示音（异步，不阻塞 GUI；未知事件/文件缺失静默跳过）。"""
    key = _EVENT_KEY.get(event)
    if key is None:
        return
    filename = load_settings()[key]
    if not filename:
        return
    _play_debounced(SOUNDS_ACTIVE_DIR / filename, event)


def play_completion_sound(success: bool) -> None:
    """任务完成提示音薄封装：成功/失败映射到 done/error 事件。"""
    play_event_sound("done" if success else "error")


def play_preview(filename: str) -> None:
    """试听指定文件（设置中心下拉框选中项；文件缺失静默跳过）。

    试听是人工显式动作，每次点击必播放，不经防抖闸门。
    """
    if not filename:
        return
    path = SOUNDS_ACTIVE_DIR / filename
    if not path.is_file():
        return
    _play(path)


def _play_debounced(path: Path, event: str) -> None:
    now = time.monotonic()
    last = _last_play_at.get(event, 0.0)
    if now - last < _DEBOUNCE_S:
        logger.info("提示音同事件合并：%s 距上次 %.2fs（pid=%d）", event, now - last, _PID)
        return
    _last_play_at[event] = now
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
        logger.info("提示音播放：%s（pid=%d）", path.name, _PID)
        if _effect.status() == QSoundEffect.Status.Loading:
            _arm_replay_when_ready()
        QTimer.singleShot(_STATUS_CHECK_MS, lambda: _fallback_if_failed(path))
    except (RuntimeError, OSError):
        # Qt 音频后端缺失时构造/播放抛 RuntimeError，降级系统播放器
        play_system(path)


def _arm_replay_when_ready() -> None:
    """Loading 期的 play 可能被后端丢弃：挂一次性监听，就绪后补播一次。"""
    global _replay_armed
    if _replay_armed or _effect is None:
        return
    _replay_armed = True

    def _on_status() -> None:
        global _replay_armed
        effect = _effect
        if effect is None:
            _replay_armed = False
            return
        status = effect.status()
        if status == QSoundEffect.Status.Loading:
            return
        try:
            effect.statusChanged.disconnect(_on_status)
        except RuntimeError:
            pass  # 已断开（单例被换/信号已断）：补播语义一次性，忽略
        _replay_armed = False
        if status == QSoundEffect.Status.Ready:
            logger.info("提示音 Loading 补播（pid=%d）", _PID)
            effect.play()

    _effect.statusChanged.connect(_on_status)


def _fallback_if_failed(path: Path) -> None:
    if _effect is not None and _effect.status() == QSoundEffect.Status.Error:
        logger.warning(
            "QSoundEffect Error 态，降级系统播放器：%s（pid=%d）", path.name, _PID)
        play_system(path)


def play_system(path: Path) -> bool:
    """系统播放器兜底：aplay/paplay/play 择一（均无则静默放弃，返回 False）。

    导出给媒体查看器复用：Qt Multimedia 后端不可用时保底出声。
    """
    _procs[:] = [p for p in _procs if p.poll() is None]  # 顺手收割已退出者
    for cmd in ("aplay", "paplay", "play"):
        exe = shutil.which(cmd)
        if exe:
            _procs.append(subprocess.Popen(
                [exe, str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
            logger.info("系统播放器兜底：%s %s（pid=%d）", exe, path.name, _PID)
            return True
    logger.warning("无可用系统播放器，放弃播放：%s（pid=%d）", path.name, _PID)
    return False
