"""设置菜单：AI 模型 ▸ / 字体大小 ▸ / 打开配置文件 / 恢复默认设置。

S3 混合双通道（文档/选型记录/2026-0720-0433 选型 §2.2）：高频项菜单直挂，
原始 settings.json 经「打开配置文件」入只读查看器（修改由 AI 落盘）。

AI 模型子菜单与 ModelBar 双向同步（计划任务 3.3）：
- 菜单 → ModelBar：action 触发 → MainWindow.apply_model_selection
  （收敛到 ChatPanel：恢复 UI + 写盘 + 后端同步），随后 sync() 刷新勾选态；
- ModelBar → 菜单：MainWindow 转交 selection_changed → sync()
  （setChecked 不触发 triggered，无回环）。
"""
from PySide6.QtGui import QAction, QActionGroup
from PySide6.QtWidgets import QMainWindow, QMenu, QMenuBar

from gui.menus.registry import ActionRegistry
from gui.settings import KEY_MODEL_BACKEND, KEY_MODEL_VERSION
from gui.theme import load_settings
from llm import BACKEND_KIMI_CLI, BACKEND_LABELS, kimi_available, list_kimi_models

#: 后端菜单项（显示名, registry 名）：派生自 llm.BACKEND_LABELS 单一映射
BACKEND_ITEMS = tuple((label, name) for name, label in BACKEND_LABELS.items())


class ModelMenu:
    """AI 模型子菜单控制器：后端/版本双互斥组，版本列表按后端联动重建。"""

    def __init__(self, menu: QMenu, ctx: QMainWindow) -> None:
        self._ctx = ctx
        self._menu = menu
        self._backend_group = QActionGroup(ctx)
        self._backend_group.setExclusive(True)
        self._version_group = QActionGroup(ctx)
        self._version_group.setExclusive(True)

        available = kimi_available()
        for label, name in BACKEND_ITEMS:
            action = menu.addAction(label if available else f"{label}（未检测到）")
            action.setCheckable(True)
            action.setData(name)
            action.setEnabled(available)
            self._backend_group.addAction(action)
            action.triggered.connect(lambda _checked=False, n=name: self._pick_backend(n))
        menu.addSeparator()
        # 版本区（动态重建）：初始按持久化后端构建
        self._rebuild_versions(load_settings().get(KEY_MODEL_BACKEND) or BACKEND_KIMI_CLI)
        self._check_backend(load_settings().get(KEY_MODEL_BACKEND) or BACKEND_KIMI_CLI)

    # ------------------------------------------------------------------
    # 菜单 → ModelBar
    # ------------------------------------------------------------------
    def _pick_backend(self, backend: str) -> None:
        """后端切换：版本取 None 由 ChatPanel 落到该后端版本列表首项。"""
        self._ctx.apply_model_selection(backend, None)

    def _pick_version(self, version: str) -> None:
        backend = self._current_backend()
        if backend:
            self._ctx.apply_model_selection(backend, version)

    # ------------------------------------------------------------------
    # ModelBar → 菜单（勾选态刷新；版本区随后端重建）
    # ------------------------------------------------------------------
    def sync(self, backend: str, version: str | None) -> None:
        """按当前选择刷新两组勾选态；后端变化时重建版本区。"""
        if backend != self._current_backend():
            self._check_backend(backend)
            self._rebuild_versions(backend)
        self._check_version(version)

    def set_enabled(self, is_enabled: bool) -> None:
        """发送中（busy）整组禁用，与 ModelBar 双下拉禁用对齐。"""
        self._menu.setEnabled(is_enabled)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _current_backend(self) -> str | None:
        action = self._backend_group.checkedAction()
        return action.data() if action is not None else None

    def _check_backend(self, backend: str) -> None:
        for action in self._backend_group.actions():
            if action.data() == backend:
                action.setChecked(True)
                return

    def _check_version(self, version: str | None) -> None:
        for action in self._version_group.actions():
            if action.data() == version:
                action.setChecked(True)
                return

    def _rebuild_versions(self, backend: str) -> None:
        """版本区按后端重建（清旧添新；勾选持久化版本）。"""
        for action in list(self._version_group.actions()):
            self._version_group.removeAction(action)
            self._menu.removeAction(action)
            action.deleteLater()
        current = load_settings().get(KEY_MODEL_VERSION)
        if backend in BACKEND_LABELS:  # kimi 系后端共用 kimi 模型别名列表
            for alias in list_kimi_models():
                action = self._menu.addAction(alias)
                action.setCheckable(True)
                action.setData(alias)
                action.setChecked(alias == current)
                self._version_group.addAction(action)
                action.triggered.connect(
                    lambda _checked=False, v=alias: self._pick_version(v))


def build(menubar: QMenuBar, ctx: QMainWindow, actions: ActionRegistry) -> ModelMenu:
    menu = menubar.addMenu("设置(&S)")

    model_menu = ModelMenu(menu.addMenu("AI 模型(&M)"), ctx)

    submenu = menu.addMenu("字体大小(&F)")
    for key, label, slot in (
        ("settings.font_increase", "增大", lambda: ctx.adjust_font_size(1)),
        ("settings.font_decrease", "减小", lambda: ctx.adjust_font_size(-1)),
        ("settings.font_reset", "重置默认", ctx.reset_font_size),
    ):
        action = submenu.addAction(label)
        action.triggered.connect(slot)
        actions.register(key, action)

    menu.addSeparator()

    action = menu.addAction("打开配置文件(&J)")
    action.triggered.connect(ctx.open_settings_file)
    actions.register("settings.open_json", action)

    action = menu.addAction("恢复默认设置(&D)…")
    action.setMenuRole(QAction.MenuRole.NoRole)  # 防 macOS 抢入应用菜单
    action.triggered.connect(ctx.reset_settings)
    actions.register("settings.reset", action)

    return model_menu
