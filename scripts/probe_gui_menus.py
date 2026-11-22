"""ModelBar 与设置中心菜单的非模态 offscreen 抓帧探针。"""
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".temp" / "gui_menu_probe"
TIMEOUT_S = 60

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from ide.gui.settings_dialog import SettingsDialog  # noqa: E402
from ide.gui.theme import apply_theme  # noqa: E402


class _Context(QWidget):
    FONT_SIZE_MIN = 8
    FONT_SIZE_MAX = 32
    STATUS_MSG_TIMEOUT_MS = 3000

    def __init__(self, backend: str, model: str) -> None:
        super().__init__()
        self.chat_tabs = SimpleNamespace(
            current_backend=lambda: backend,
            current_version=lambda: model,
            is_busy=lambda: False,
        )

    def open_settings_file(self):
        pass

    def reset_settings(self):
        pass

    def preview_notify_banner(self, _kind):
        pass


def _save(widget, name: str) -> str:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / name
    image = widget.grab()
    assert not image.isNull() and image.width() > 0 and image.height() > 0
    assert image.save(str(path))
    return str(path.relative_to(ROOT))


def _child() -> None:
    from ide.gui.panels.chat.model_bar import ModelBar
    from ide.llm.registry import vendor_groups

    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    bar = ModelBar()
    context = None
    dialog = None
    menu = None
    settings_menu = None
    try:
        expected_vendors = list(vendor_groups())
        bar.resize(760, 48)
        bar.show()
        app.processEvents()
        labels = [action.text() for action in bar._vendor_group.actions()]
        assert [action.data() for action in bar._vendor_group.actions()] == expected_vendors
        assert all(label.strip() for label in labels)
        model_bar_png = _save(bar, "model_bar.png")

        menu = bar._vendor_button.menu()
        menu.resize(menu.sizeHint())
        menu.show()
        app.processEvents()
        menu_png = _save(menu, "model_bar_menu.png")
        menu.hide()

        backend = bar.current_backend() or "kimi-kap"
        context = _Context(backend, bar.current_version() or "")
        dialog = SettingsDialog(context)
        dialog.show()
        dialog._nav.setCurrentRow(1)
        for _ in range(5):
            app.processEvents()
        settings_labels = [dialog._vendor_combo.itemText(index)
                           for index in range(dialog._vendor_combo.count())]
        assert [dialog._vendor_combo.itemData(index)
                for index in range(dialog._vendor_combo.count())] == expected_vendors
        assert all(label.strip() for label in settings_labels)
        settings_png = _save(dialog, "settings_ai_model.png")

        settings_menu = dialog._vendor_combo.view()
        dialog._vendor_combo.showPopup()
        app.processEvents()
        height = sum(max(settings_menu.sizeHintForRow(index), 24)
                     for index in range(dialog._vendor_combo.count())) + 4
        settings_menu.resize(240, height)
        settings_menu.viewport().resize(240, height)
        app.processEvents()
        settings_menu_png = _save(
            settings_menu.viewport(), "settings_vendor_menu.png")
        dialog._vendor_combo.hidePopup()

        result = {
            "vendors": expected_vendors,
            "model_bar_labels": labels,
            "settings_labels": settings_labels,
            "screenshots": [
                model_bar_png, menu_png, settings_png, settings_menu_png,
            ],
        }
        (OUT / "summary.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        if settings_menu is not None:
            settings_menu.hide()
        if menu is not None:
            menu.hide()
        if dialog is not None:
            dialog.close()
        if context is not None:
            context.close()
        bar.close()
        app.processEvents()


def main() -> int:
    if "--child" in sys.argv:
        _child()
        return 0
    env = os.environ.copy()
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = str(ROOT / "src")
    subprocess.run(
        [str(ROOT / ".venv" / "bin" / "python"), str(Path(__file__).resolve()), "--child"],
        cwd=ROOT, env=env, timeout=TIMEOUT_S, check=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
