# ================================================== #
# 本文件播放状态机（进度拖动暂停/恢复、信号驱动进度/时间更新、
# 播放态图标切换）移植自 PYGPT 包                              #
# 源文件: src/pygpt_net/tools/media_player/ui/widgets.py     #
# Website: https://pygpt.net                                 #
# GitHub:  https://github.com/szczyglis-dev/py-gpt           #
# MIT License                                                #
# Copyright (c) Marcin Szczygliński                          #
# ================================================== #
"""媒体查看器：QtMultimedia 承载视频 / 音频的就地播放。

（2026-07-29，见 work plans/2026-0729-1120_音视频播放功能实施计划 T1–T2）
形态：内嵌 ViewerPanel 媒体页（QStackedLayout 与文本页/图片页切换）。
能力：播放/暂停、进度拖动、时间标签、音量；纯音频显示文件名占位页；
解码失败经 failed 信号上抛，由面板回落文本页占位提示。

设计原则（计划 §3.0 OOP 模块化）：控件自封装，对外仅暴露
open_media / stop / apply_theme / failed 信号；不 import panel，
错误经信号上抛避免环依赖。

协议合规（见 work options/2026-0729-1028_theia实质代码审计与协议补全建议.md）：
- theia-zen（EPL-2.0）：未实现媒体播放，零接触。
- PyGPT（MIT，© Marcin Szczygliński）：播放状态机事件处理模式移植
  （见文件头部版权声明），已剥离其 window.tools/trans() 依赖、
  QDialog 形态重构为内嵌 QWidget；扩展名表参考其
  core/filesystem/types.py 并按计划 §1 裁减为主流子集。
- Multi_Cli_Studio：视频归类 binary-unsupported，零借鉴。
"""
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSlider,
    QStackedLayout,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

#: 可播放视频扩展名（不含点；参考 PyGPT 表按计划 §1 裁减为主流子集）
VIDEO_EXTS: frozenset[str] = frozenset({"mp4", "webm", "mov", "mkv", "avi"})
#: 可播放音频扩展名（不含点；ogg 按计划归入音频）
AUDIO_EXTS: frozenset[str] = frozenset({"mp3", "wav", "ogg", "flac"})
#: 默认音量（0.0–1.0；计划 §3.2）
DEFAULT_VOLUME = 0.7


class MediaViewer(QWidget):
    """媒体播放控件：QVideoWidget 画面区 + 控制条（播放/进度/时间/音量）。"""

    #: 解码失败上抛（原因字符串），由面板回落文本页占位提示
    failed = Signal(str)

    def __init__(self, palette: dict, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._palette = palette
        # 播放器组件懒加载（计划 §3.2：首次打开媒体才初始化后端，探针/纯文本场景零开销）
        self._player = None
        self._audio = None
        self._video = None
        self._seeking = False       # 拖动进度条中（防信号回写打架，PyGPT 模式）
        self._was_playing = False   # 拖动前播放态（松手恢复）
        self._autoplay = False      # 打开后待 LoadedMedia 自动播放（双击即播）
        self._is_audio = False

        self._build_ui(palette)

    # ------------------------------------------------------------------
    # UI 装配（全部私有，外部不触及内部控件）
    # ------------------------------------------------------------------
    def _build_ui(self, palette: dict) -> None:
        """画面区（视频占位 / 音频占位双页）+ 控制条。"""
        # 画面区：页 0 = 视频宿主（懒加载时替换为 QVideoWidget）；页 1 = 音频占位
        self._video_host = QWidget(self)
        self._audio_label = QLabel("", self)
        self._audio_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._audio_label.setStyleSheet(
            f"color: {palette['muted_text']}; background: {palette['window_bg']};")
        self._view_stack = QStackedLayout()
        self._view_stack.addWidget(self._video_host)
        self._view_stack.addWidget(self._audio_label)

        # 控制条：播放/暂停 + 进度 + 时间 + 音量（样式走主题 qss 令牌，不新增）
        self._btn_play = QToolButton(self)
        self._btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self._btn_play.setToolTip("播放/暂停")
        self._btn_play.setEnabled(False)
        self._btn_play.clicked.connect(self._toggle_play)

        self._slider = QSlider(Qt.Orientation.Horizontal, self)
        self._slider.setRange(0, 0)
        self._slider.sliderPressed.connect(self._on_slider_pressed)
        self._slider.sliderReleased.connect(self._on_slider_released)
        self._slider.sliderMoved.connect(
            lambda v: self._time_label.setText(f"{self._fmt(v)} / {self._fmt(self._slider.maximum())}"))

        self._time_label = QLabel("--:-- / --:--", self)

        self._volume = QSlider(Qt.Orientation.Horizontal, self)
        self._volume.setRange(0, 100)
        self._volume.setValue(round(DEFAULT_VOLUME * 100))
        self._volume.setFixedWidth(90)
        self._volume.setToolTip("音量")
        self._volume.valueChanged.connect(self._on_volume)

        controls = QHBoxLayout()
        controls.addWidget(self._btn_play)
        controls.addWidget(self._slider, 1)
        controls.addWidget(self._time_label)
        controls.addWidget(self._volume)
        controls.setContentsMargins(4, 2, 4, 2)

        layout = QVBoxLayout(self)
        layout.addLayout(self._view_stack, 1)
        layout.addLayout(controls)
        layout.setContentsMargins(0, 0, 0, 0)

    def _ensure_player(self) -> None:
        """懒加载多媒体组件：QMediaPlayer + QAudioOutput + QVideoWidget（PyGPT 模式）。

        Qt 6 要求显式挂音频输出否则无声；视频页创建后替换画面区占位宿主。
        """
        if self._player is not None:
            return
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        from PySide6.QtMultimediaWidgets import QVideoWidget

        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._audio.setVolume(self._volume.value() / 100.0)
        self._player.setAudioOutput(self._audio)
        self._video = QVideoWidget(self)
        idx = self._view_stack.indexOf(self._video_host)
        self._view_stack.removeWidget(self._video_host)
        self._video_host.deleteLater()
        self._video_host = self._video
        self._view_stack.insertWidget(idx, self._video)
        self._player.setVideoOutput(self._video)

        self._player.positionChanged.connect(self._on_position)
        self._player.durationChanged.connect(self._on_duration)
        self._player.playbackStateChanged.connect(self._on_state)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.errorOccurred.connect(self._on_error)

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def open_media(self, path: Path) -> None:
        """打开媒体文件：停旧源 → 挂新源 → 加载就绪后自动播放（双击即播）。

        解码失败异步经 failed 信号上抛；自动播放等 LoadedMedia 才触发
        （PyGPT autoplay 模式，防止源未解析时 play 被吞）。
        """
        self.stop()
        self._ensure_player()
        self._is_audio = path.suffix.lower().lstrip(".") in AUDIO_EXTS
        self._view_stack.setCurrentWidget(self._audio_label if self._is_audio else self._video)
        if self._is_audio:  # 纯音频无画面：文件名占位页
            self._audio_label.setText(f"音频\n{path.name}")
        self._autoplay = True
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._btn_play.setEnabled(True)

    def stop(self) -> None:
        """停播释放（生命周期红线：切换文件/回落占位/离开媒体页必调）。"""
        self._autoplay = False
        if self._player is not None and self._player.source().isValid():
            self._player.stop()
            self._player.setSource(QUrl())
        self._seeking = False
        self._was_playing = False
        self._btn_play.setEnabled(False)
        self._btn_play.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self._slider.setRange(0, 0)
        self._slider.setValue(0)
        self._time_label.setText("--:-- / --:--")

    def apply_theme(self, palette: dict) -> None:
        """切换主题：音频占位页配色自调色板现有令牌派生（不新增令牌）。"""
        self._palette = palette
        self._audio_label.setStyleSheet(
            f"color: {palette['muted_text']}; background: {palette['window_bg']};")

    # ------------------------------------------------------------------
    # 生命周期守卫
    # ------------------------------------------------------------------
    def hideEvent(self, event) -> None:
        """离开媒体页自动暂停（防后台继续出声；与 stop 的释放语义互补）。"""
        if self._player is not None:
            self._player.pause()
        super().hideEvent(event)

    # ------------------------------------------------------------------
    # 播放状态机（PyGPT 移植模式：拖动暂停/松手恢复、信号驱动 UI）
    # ------------------------------------------------------------------
    def _toggle_play(self) -> None:
        if self._player is None:
            return
        from PySide6.QtMultimedia import QMediaPlayer
        if self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def _on_slider_pressed(self) -> None:
        """拖动开始：暂停并标记 seeking，防 positionChanged 回写打架。"""
        if self._player is None:
            return
        from PySide6.QtMultimedia import QMediaPlayer
        self._seeking = True
        self._was_playing = (
            self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)
        if self._was_playing:
            self._player.pause()

    def _on_slider_released(self) -> None:
        """拖动结束：seek 到目标位置，恢复拖动前播放态。"""
        if self._player is None:
            return
        self._player.setPosition(self._slider.value())
        self._seeking = False
        if self._was_playing:
            self._player.play()

    def _on_position(self, position: int) -> None:
        if not self._seeking:
            self._slider.setValue(position)
            self._time_label.setText(
                f"{self._fmt(position)} / {self._fmt(self._slider.maximum())}")

    def _on_duration(self, duration: int) -> None:
        self._slider.setRange(0, duration)
        self._time_label.setText(f"00:00 / {self._fmt(duration)}")

    def _on_state(self) -> None:
        """播放态切换 → 播放/暂停图标互换。"""
        from PySide6.QtMultimedia import QMediaPlayer
        playing = (self._player is not None and self._player.playbackState()
                   == QMediaPlayer.PlaybackState.PlayingState)
        self._btn_play.setIcon(self.style().standardIcon(
            QStyle.StandardPixmap.SP_MediaPause if playing
            else QStyle.StandardPixmap.SP_MediaPlay))

    def _on_media_status(self, status) -> None:
        """LoadedMedia → 自动播放（若 open_media 置位）；InvalidMedia → 失败上抛。"""
        from PySide6.QtMultimedia import QMediaPlayer
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            if self._autoplay:
                self._autoplay = False
                self._player.play()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._autoplay = False
            self.failed.emit("无法解码（格式不受支持或缺少解码器）")

    def _on_error(self, _error, message: str) -> None:
        if message:  # 空串为错误清除，不上抛
            self.failed.emit(message)

    def _on_volume(self, value: int) -> None:
        if self._audio is not None:
            self._audio.setVolume(value / 100.0)

    @staticmethod
    def _fmt(ms: int) -> str:
        """毫秒 → mm:ss（超 1 小时 hh:mm:ss）。"""
        seconds = max(0, ms // 1000)
        minutes, sec = divmod(seconds, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{sec:02d}"
        return f"{minutes:02d}:{sec:02d}"
