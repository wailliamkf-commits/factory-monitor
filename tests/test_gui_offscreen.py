"""Offscreen GUI behaviour tests.

These tests exercise the real widgets.  The runtime fake only supplies the
controller boundary; it never emits fabricated model results.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QCloseEvent, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from factory_monitor.config import default_config, load_config, save_config
from factory_monitor.gui.app import MainWindow
from factory_monitor.store import EventStore


class RuntimeProbe:
    """A controller-boundary probe, deliberately without inference output."""

    def __init__(self, config: dict, data_dir: Path) -> None:
        self.config = config
        self.data_dir = data_dir
        self.started: tuple[str, str | None] | None = None
        self.running = False
        self.updated: list[tuple[str, dict]] = []

    def start(self, source: str = "demo", input_path: str | None = None) -> None:
        self.started = (source, input_path)
        self.running = True

    def stop(self) -> None:
        self.running = False

    def poll(self) -> list[dict]:
        return []

    def status(self) -> dict:
        return {"running": self.running, "source": self.started[0] if self.started else None,
                "field_verified": False, "workers": {"capture": "unavailable", "model": "unavailable"}}

    def request_view(self, camera_id: str) -> dict:
        return {"ok": False, "reason": "automatic view control is uncalibrated"}

    def return_grid(self) -> dict:
        return {"ok": False, "reason": "automatic view control is uncalibrated"}


class StopStatusProbe:
    def __init__(self, *, running: bool, confirmed: bool) -> None:
        self.running = running
        self.confirmed = confirmed

    def stop(self) -> None:
        pass

    def status(self) -> dict:
        return {
            "running": self.running,
            "last_stop": {
                "confirmed": self.confirmed,
                "alive_workers": ["evidence"] if self.running else [],
                "affected_event_ids": ["evt-stop"] if not self.confirmed else [],
                "reasons": ["synthetic stop not confirmed"] if not self.confirmed else [],
            },
        }


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    return QApplication.instance() or QApplication([])


@pytest.fixture
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "monitor.json"
    save_config(default_config(), path)
    return path


@pytest.fixture
def window(qapp: QApplication, config_path: Path, tmp_path: Path) -> MainWindow:
    probes: list[RuntimeProbe] = []
    notifications: list[tuple[str, str]] = []
    beeps: list[bool] = []
    opened: list[Path] = []

    def runtime_factory(config: dict, data_dir: Path) -> RuntimeProbe:
        probe = RuntimeProbe(config, data_dir)
        probes.append(probe)
        return probe

    instance = MainWindow(
        config_path, tmp_path / "data", runtime_factory=runtime_factory,
        notifier=lambda title, body: notifications.append((title, body)),
        beeper=lambda: beeps.append(True), opener=lambda path: opened.append(path) or True,
    )
    instance._test_probes = probes
    instance._test_notifications = notifications
    instance._test_beeps = beeps
    instance._test_opened = opened
    instance.show()
    QTest.qWait(10)
    yield instance
    instance.close()


def test_demo_labels_remain_explicitly_synthetic(window: MainWindow) -> None:
    assert "SYNTHETIC" in window.source_banner.text()
    assert "生产就绪" not in window.source_banner.text()
    assert window.camera_tiles.count() == 10
    assert window.camera_tiles.itemAtPosition(0, 3) is not None
    assert window.camera_tiles.itemAtPosition(2, 1) is not None


def test_close_is_rejected_while_stop_still_reports_a_live_worker(window: MainWindow) -> None:
    window.runtime = StopStatusProbe(running=True, confirmed=False)
    event = QCloseEvent()

    window.closeEvent(event)

    assert event.isAccepted() is False
    assert "停止收尾不完整" in window.status_label.text()
    window.runtime = None


@pytest.mark.parametrize("confirmed", [True, False])
def test_close_is_allowed_after_workers_exit_even_if_the_stop_report_is_incomplete(
    window: MainWindow, confirmed: bool
) -> None:
    window.runtime = StopStatusProbe(running=False, confirmed=confirmed)
    event = QCloseEvent()

    window.closeEvent(event)

    assert event.isAccepted() is True
    window.runtime = None


def test_camera_tile_letterboxes_frames_without_distorting_aspect(window: MainWindow) -> None:
    import numpy as np

    tile = window.tiles["CAM01"]
    tile.set_frame(np.zeros((100, 400, 3), dtype=np.uint8))
    tile.repaint()
    QTest.qWait(5)
    rectangle = tile._last_image_rect
    assert rectangle.width() / rectangle.height() == pytest.approx(4.0, rel=0.03)


def test_live_mode_is_blocked_before_start_when_uncalibrated(window: MainWindow) -> None:
    window.source_selector.setCurrentText("live")
    QTest.mouseClick(window.start_button, Qt.LeftButton)

    assert window._test_probes == []
    assert "校准" in window.status_label.text()
    assert window.start_button.isEnabled()


def test_calibration_save_persists_expected_size_and_crop(window: MainWindow, config_path: Path) -> None:
    window.window_title.setText("Factory Monitor Synthetic Source")
    window.expected_width.setValue(1920)
    window.expected_height.setValue(1080)
    window.camera_table.item(0, window.CROP_COLUMN).setText("0.0,0.0,0.25,0.33")
    window.calibration_confirmation.setChecked(True)
    QTest.mouseClick(window.save_button, Qt.LeftButton)

    saved = load_config(config_path)
    assert saved["source"]["expected_size"] == [1920, 1080]
    assert saved["cameras"][0]["crop"] == [0.0, 0.0, 0.25, 0.33]
    assert saved["source"]["calibrated"] is True


def test_dimensions_and_crops_alone_do_not_claim_calibration(window: MainWindow, config_path: Path) -> None:
    window.expected_width.setValue(1920)
    window.expected_height.setValue(1080)
    QTest.mouseClick(window.save_button, Qt.LeftButton)

    assert load_config(config_path)["source"]["calibrated"] is False
    assert "确认" in window.status_label.text()


def test_schedule_save_does_not_reset_existing_confirmed_layout(window: MainWindow, config_path: Path) -> None:
    window.window_title.setText("Factory Monitor Synthetic Source")
    window.expected_width.setValue(1920)
    window.expected_height.setValue(1080)
    window.calibration_confirmation.setChecked(True)
    QTest.mouseClick(window.save_button, Qt.LeftButton)
    assert load_config(config_path)["source"]["calibrated"] is True
    window.active_schedule.setText("08:00-17:00")
    QTest.mouseClick(window.save_button, Qt.LeftButton)
    assert load_config(config_path)["source"]["calibrated"] is True


def test_local_reviewer_endpoint_is_saved_only_when_stopped(window: MainWindow, config_path: Path) -> None:
    window.review_endpoint.setText("http://127.0.0.1:11435")
    QTest.mouseClick(window.save_button, Qt.LeftButton)

    assert load_config(config_path)["review"]["endpoint"] == "http://127.0.0.1:11435"


def test_roi_schedule_and_floorplan_placement_persist_on_save(window: MainWindow, config_path: Path) -> None:
    canvas = window.roi_canvas
    for point in ((40, 40), (170, 40), (100, 140)):
        QTest.mouseClick(canvas, Qt.LeftButton, pos=QPoint(*point))
    window.active_schedule.setText("08:00-12:00,13:00-17:00")
    window.break_schedule.setText("12:00-13:00")
    window.schedule_days[5].setChecked(False)
    window.schedule_days[6].setChecked(False)
    window.floor_canvas.placement_camera = "CAM02"
    QTest.mouseClick(window.floor_canvas, Qt.LeftButton, pos=window.floor_canvas.rect().center())
    QTest.mouseClick(window.save_button, Qt.LeftButton)

    saved = load_config(config_path)
    assert len(saved["cameras"][0]["material_roi"]) == 3
    assert saved["cameras"][0]["schedule"]["active"] == [["08:00", "12:00"], ["13:00", "17:00"]]
    assert saved["cameras"][0]["schedule"]["days"] == [0, 1, 2, 3, 4]
    assert saved["cameras"][1]["map_position"] == pytest.approx([0.5, 0.5], abs=0.01)


def test_local_preview_image_is_loaded_for_roi_drawing(window: MainWindow, tmp_path: Path) -> None:
    preview = QImage(80, 50, QImage.Format_RGB32)
    preview.fill(QColor("#2db47c"))
    path = tmp_path / "calibration-preview.png"
    assert preview.save(str(path))

    assert window.load_roi_preview(path)
    assert window.roi_preview_path.text() == str(path)


def test_roi_preview_is_cropped_to_selected_camera_coordinates(window: MainWindow, tmp_path: Path) -> None:
    preview = QImage(400, 300, QImage.Format_RGB32)
    preview.fill(QColor("#2468d9"))
    painter = QPainter(preview)
    painter.fillRect(0, 0, 100, 100, QColor("#dc6c5b"))
    painter.end()
    path = tmp_path / "two-camera-grid.png"
    assert preview.save(str(path))

    assert window.load_roi_preview(path)
    assert window.roi_canvas._preview.toImage().pixelColor(10, 10) == QColor("#dc6c5b")
    window.camera_table.selectRow(1)
    assert window.roi_canvas._preview.toImage().pixelColor(10, 10) == QColor("#2468d9")


def test_operator_template_records_local_signature_without_claiming_mapping_verified(
    window: MainWindow, config_path: Path, tmp_path: Path
) -> None:
    preview = QImage(240, 180, QImage.Format_RGB32)
    preview.fill(QColor("#2db47c"))
    painter = QPainter(preview)
    painter.fillRect(0, 0, 120, 90, QColor("#101820"))
    painter.end()
    path = tmp_path / "stopped-grid.png"
    assert preview.save(str(path))
    assert window.load_roi_preview(path)
    window.template_camera.setCurrentText("CAM01")
    window.template_kind.setCurrentText("grid")
    window.template_label.setText("CAM01")
    window.template_label_roi.setText("0,0,1,1")
    QTest.mouseClick(window.record_template_button, Qt.LeftButton)

    verification = load_config(config_path)["switching"]["verification"]
    template = verification["grid_identities"]["CAM01"]
    assert Path(template["template_path"]).is_file()
    assert template["expected_label"] == "CAM01"
    assert template["min_score"] == 0.92
    assert "映射未验证" in window.template_status.text()

    window.expected_width.setValue(1920)
    window.expected_height.setValue(1080)
    QTest.mouseClick(window.save_button, Qt.LeftButton)
    assert load_config(config_path)["switching"]["verification"] == {}


def test_configuration_locks_during_running_session(window: MainWindow) -> None:
    QTest.mouseClick(window.start_button, Qt.LeftButton)

    assert not window.capture_config_group.isEnabled()
    assert not window.source_selector.isEnabled()
    assert not window.floor_place_selector.isEnabled()
    assert not window.save_button.isEnabled()
    QTest.mouseClick(window.stop_button, Qt.LeftButton)
    assert window.capture_config_group.isEnabled()


def test_error_is_visible_and_event_review_writes_store(window: MainWindow, tmp_path: Path) -> None:
    window.handle_runtime_message({"type": "error", "message": "本机模型不可用：手动复核"})
    assert "模型不可用" in window.diagnostics.toPlainText()

    event = {"id": "evt-1", "camera_id": "CAM01", "kind": "station_absence", "triggered_at": 1.0,
             "status": "candidate", "reason": "test", "layout_version": 1}
    store = EventStore(window.data_dir / "events.sqlite3")
    store.create_event(event)
    store.close()
    window.handle_runtime_message({"type": "candidate", "event": event})
    window.event_table.selectRow(0)
    QTest.mouseClick(window.false_alarm_button, Qt.LeftButton)

    assert window.event_table.item(0, window.EVENT_REVIEW_COLUMN).text() == "false_alarm"
    store = EventStore(window.data_dir / "events.sqlite3")
    assert store.get_event("evt-1")["review_label"] == "false_alarm"
    store.close()


def test_runtime_analysis_message_updates_existing_candidate(window: MainWindow) -> None:
    event = {"id": "evt-analysis", "camera_id": "CAM01", "kind": "station_absence", "triggered_at": 1.0,
             "status": "candidate", "reason": "test", "layout_version": 1, "analysis_status": "pending"}
    window.handle_runtime_message({"type": "candidate", "event": event})
    window.handle_runtime_message({"type": "analysis", "event_id": "evt-analysis", "analysis_status": "timeout",
                                   "analysis": {"error": "local reviewer unavailable"}, "latency_ms": 15.0})

    assert window.event_table.item(0, 3).text() == "timeout"
    assert window._events["evt-analysis"]["analysis"]["error"] == "local reviewer unavailable"


def test_selected_event_exposes_gaps_and_requests_system_open_only_for_existing_evidence(
    window: MainWindow, tmp_path: Path
) -> None:
    evidence = tmp_path / "evidence.mp4"
    evidence.write_bytes(b"not asserted playable")
    event = {"id": "evt-evidence", "camera_id": "CAM01", "kind": "station_absence", "triggered_at": 1,
             "status": "incomplete", "reason": "absence", "layout_version": 1, "evidence_path": str(evidence),
             "analysis": {"error": "review unavailable"}, "gaps": [[10.0, 12.5], [15.0, None], {"start": 20.0, "end": None, "reason": "capture stalled"}],
             "recording_status": "incomplete", "window_complete": False}
    window.upsert_event(event)
    window.event_table.selectRow(0)
    assert "10.000–12.500" in window.event_details.toPlainText()
    assert "capture stalled" in window.event_details.toPlainText()
    assert "时间窗完整：否" in window.event_details.toPlainText()
    assert "不以此界面声明" in window.event_details.toPlainText()
    QTest.mouseClick(window.open_evidence_button, Qt.LeftButton)
    assert window._test_opened == [evidence]

    window.upsert_event({**event, "window_complete": True, "gaps": []})
    window.event_table.selectRow(0)
    assert "时间窗完整：是" in window.event_details.toPlainText()

    window.upsert_event({**event, "id": "evt-missing", "evidence_path": str(tmp_path / "missing.mp4"), "window_complete": None})
    window.event_table.selectRow(1)
    assert not window.open_evidence_button.isEnabled()
    assert "时间窗完整：待确认" in window.event_details.toPlainText()
    assert "缺失" in window.evidence_label.text()


def test_candidate_and_manual_review_states_notify_and_health_logs_are_bounded(window: MainWindow) -> None:
    event = {"id": "evt-notify", "camera_id": "CAM01", "kind": "station_absence", "triggered_at": 1,
             "status": "candidate", "reason": "absence", "layout_version": 1}
    window.handle_runtime_message({"type": "candidate", "event": event})
    assert window.tiles["CAM01"]._candidate
    assert window._test_notifications and window._test_beeps
    window.handle_runtime_message({"type": "analysis", "event_id": "evt-notify", "analysis_status": "timeout",
                                   "analysis": {"error": "manual review required"}})
    assert any("人工复核" in body for _, body in window._test_notifications)
    for _ in range(600):
        window.handle_runtime_message({"type": "health", "camera_id": "CAM01", "health": "observable"})
    assert window.diagnostics.document().blockCount() <= window.MAX_DIAGNOSTIC_BLOCKS


def test_reconcile_keeps_all_inflight_and_only_twenty_completed_events(window: MainWindow) -> None:
    store = EventStore(window.data_dir / "events.sqlite3")
    for index in range(25):
        store.create_event({"id": f"complete-{index}", "camera_id": "CAM01", "kind": "station_absence",
                            "triggered_at": float(index), "status": "complete", "reason": "done", "layout_version": 1,
                            "completed_at": float(index), "analysis_status": "supported"})
    store.create_event({"id": "inflight", "camera_id": "CAM01", "kind": "station_absence", "triggered_at": 99,
                        "status": "recording", "reason": "working", "layout_version": 1, "analysis_status": "pending"})
    store.close()
    window.reconcile_event_store()
    assert len(window._events) == 21
    assert "inflight" in window._events
