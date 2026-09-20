"""Render a local GUI preview image for visual QA; it starts no runtime workers."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from factory_monitor.config import default_config, save_config
from factory_monitor.gui.app import MainWindow


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the non-running desktop GUI to a PNG for local QA.")
    parser.add_argument("--output", type=Path, default=Path("artifacts/gui-preview.png"))
    parser.add_argument("--active-demo-seconds", type=float, default=0,
                        help="run the real synthetic RuntimeController for this bounded duration before rendering")
    args = parser.parse_args()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    config_path = output.parent / "gui-preview-config.json"
    save_config(default_config(), config_path)
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(config_path, output.parent / "gui-preview-data")
    window.show()
    app.processEvents()
    if args.active_demo_seconds <= 0:
        saved = window.grab().save(str(output))
        window.close()
        return 0 if saved else 1
    if args.active_demo_seconds > 60:
        parser.error("--active-demo-seconds must be at most 60")
    window.start_runtime()

    result = {"saved": False}

    def finish() -> None:
        app.processEvents()
        result["saved"] = window.grab().save(str(output))
        window.stop_runtime()
        window.close()
        app.quit()

    QTimer.singleShot(round(args.active_demo_seconds * 1000), finish)
    app.exec()
    return 0 if result["saved"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
