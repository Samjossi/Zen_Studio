"""系统级桌面通知：经 notify-send 交由桌面环境通知守护进程弹出。

替代原软件内顶部横幅（主窗口内 QFrame 子控件）——窗口最小化/被遮挡/切走
桌面时软件内横幅用户根本看不到，提醒失去意义；系统通知由守护进程统一
弹出，窗口不可见时仍能送达，且样式/消退由系统管理（用户可在系统设置
里统一治理）。

实现走外部命令 notify-send（freedesktop 通知规范的通用 CLI，桌面发行版
自带），同 sound_player 的 aplay/paplay/play 先例：命令缺失静默跳过，
不引新依赖；AppImage 调宿主机命令不受打包影响。
"""
import shutil
import subprocess

from core.paths import LOGO_DIR

#: 事件态 → (标题, 默认正文)；三态文案沿用原软件内横幅，保持用户既有认知
_EVENT_TEXT = {
    "success": ("任务完成", "AI 已完成本轮任务"),
    "error": ("任务失败", "AI 本轮请求失败"),
    "question": ("需要你的回答", "AI 发起了提问，请在对话卡片中作答"),
}

#: 通知驻留时长 hint（毫秒；守护进程可忽略，数值与原横幅展示时长对齐）
_EXPIRE_MS = 4000

_procs: list[subprocess.Popen] = []  # 子进程持有：防退出后成僵尸（sound_player 同例）


def notify_event(event: str, detail: str = "") -> None:
    """发指定事件态的桌面通知（未知事件或 notify-send 缺失静默跳过）。"""
    text = _EVENT_TEXT.get(event)
    if text is None:
        return
    title, body = text
    _send(title, detail or body)


def notify_completion(success: bool, detail: str = "") -> None:
    """任务完成/失败薄封装：映射到 success/error 事件态。"""
    notify_event("success" if success else "error", detail)


def _send(title: str, body: str) -> None:
    exe = shutil.which("notify-send")
    if exe is None:
        return
    _procs[:] = [p for p in _procs if p.poll() is None]  # 顺手收割已退出者
    cmd = [exe, "-a", "Zen Studio", "-t", str(_EXPIRE_MS)]
    icon = LOGO_DIR / "logo_64.png"
    if icon.is_file():
        cmd += ["-i", str(icon)]
    cmd += [title, body]
    try:
        _procs.append(subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    except OSError:
        pass  # 命令存在但 exec 失败（权限/会话无 D-Bus 等）：提醒不可达即放弃
