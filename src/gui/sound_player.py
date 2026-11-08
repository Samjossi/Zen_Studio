"""提示音播放：按事件读设置选文件，交给独立系统播放器子进程。

原生音频设备切换可能令音频后端在子线程中段错误，Python 无法捕获；
播放必须隔离在短命子进程内，避免提示音故障带走 IDE 主进程。
依次探测 aplay/paplay/play（播放器探测链参考 kilocode sound.ts）。

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

from PySide6.QtCore import QTimer

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

#: 子进程退出后的非阻塞回收间隔；没有存活播放器时定时器停止
_REAP_CHECK_MS = 1000

_last_play_at: dict[str, float] = {}  # 按事件维度记录上次播放时刻
_procs: list[subprocess.Popen] = []
_reap_timer: QTimer | None = None


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
    play_system(path)


def _reap_procs() -> None:
    active: list[subprocess.Popen] = []
    for proc in _procs:
        returncode = proc.poll()
        if returncode is None:
            active.append(proc)
        elif returncode != 0:
            logger.warning(
                "系统播放器子进程异常结束：子进程 pid=%d，returncode=%d（IDE pid=%d）",
                proc.pid, returncode, _PID)
    _procs[:] = active
    if _procs:
        _schedule_reap()


def _schedule_reap() -> None:
    global _reap_timer
    if _reap_timer is None:
        _reap_timer = QTimer()
        _reap_timer.setSingleShot(True)
        _reap_timer.timeout.connect(_reap_procs)
    if not _reap_timer.isActive():
        _reap_timer.start(_REAP_CHECK_MS)


def play_system(path: Path) -> bool:
    """用独立系统播放器子进程出声；无法启动时记录警告并返回 False。

    导出给媒体查看器复用：Qt Multimedia 后端不可用时保底出声。
    """
    _reap_procs()
    for cmd in ("aplay", "paplay", "play"):
        exe = shutil.which(cmd)
        if not exe:
            continue
        try:
            proc = subprocess.Popen(
                [exe, str(path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as exc:
            logger.warning(
                "系统播放器启动失败：%s %s：%s（pid=%d）",
                exe, path.name, exc, _PID)
            continue
        _procs.append(proc)
        _schedule_reap()
        logger.info("系统播放器播放：%s %s（pid=%d）", exe, path.name, _PID)
        return True
    logger.warning("无可用系统播放器，放弃播放：%s（pid=%d）", path.name, _PID)
    return False
