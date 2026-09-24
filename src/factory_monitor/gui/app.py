"""A deliberately conservative local operator interface.

The application renders what the local runtime reports.  It does not invent a
camera identity, a detection, or a model conclusion when a native dependency
is unavailable.
"""

from __future__ import annotations

import copy
import json
import sys
import time
from pathlib import Path
from typing import Any, Callable

from PySide6.QtCore import QRect, QPointF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QScrollArea,
    QSpinBox,
    QStyle,
    QSplitter,
    QSystemTrayIcon,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


RuntimeFactory = Callable[[dict[str, Any], Path], Any]


def _config_api() -> tuple[Callable[[], dict[str, Any]], Callable[[Path], dict[str, Any]], Callable[..., None]]:
    """Keep startup diagnostics available even when another subsystem is missing."""
    from factory_monitor.config import default_config, load_config, save_config

    return default_config, load_config, save_config


def _runtime_factory(config: dict[str, Any], data_dir: Path) -> Any:
    from factory_monitor.runtime import RuntimeController

    return RuntimeController(config, data_dir)


class CameraTile(QFrame):
    """One camera pane that always states whether it has a usable observation."""

    clicked = Signal(str)

    def __init__(self, camera: dict[str, Any], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.camera_id = str(camera["id"])
        self.setObjectName("cameraTile")
        self.setFrameShape(QFrame.StyledPanel)
        self.setMinimumSize(170, 105)
        self._image: QImage | None = None
        self._health = "unavailable"
        self._note = "等待本地运行时"
        self._candidate = False
        self._last_image_rect = QRect()
        self.setCursor(Qt.PointingHandCursor)

    def set_frame(self, image: Any) -> None:
        if image is None:
            return
        try:
            height, width = image.shape[:2]
            if len(image.shape) == 2:
                qimage = QImage(image.data, width, height, width, QImage.Format_Grayscale8)
            else:
                stride = image.strides[0]
                qimage = QImage(image.data, width, height, stride, QImage.Format_BGR888)
            self._image = qimage.copy()
        except (AttributeError, TypeError, ValueError):
            self._image = None
            self._note = "帧格式无法显示"
        self.update()

    def set_health(self, health: str, note: str = "") -> None:
        self._health = health or "unknown"
        if note:
            self._note = note
        self.update()

    def mark_candidate(self, active: bool = True) -> None:
        self._candidate = active
        self.update()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.camera_id)
        super().mousePressEvent(event)

    def paintEvent(self, event: Any) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        rect = self.contentsRect().adjusted(5, 5, -5, -5)
        if self._image and not self._image.isNull():
            scaled = self._image.scaled(rect.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            left = rect.left() + (rect.width() - scaled.width()) // 2
            top = rect.top() + (rect.height() - scaled.height()) // 2
            self._last_image_rect = QRect(left, top, scaled.width(), scaled.height())
            painter.fillRect(rect, QColor("#111820"))
            painter.drawImage(self._last_image_rect, scaled)
        else:
            painter.fillRect(rect, QColor("#27313d"))
            painter.setPen(QColor("#c7d0d9"))
            painter.drawText(rect, Qt.AlignCenter, "无可显示帧\n" + self._note)
        colour = {
            "observable": "#2db47c", "blind": "#e0a42c", "frozen": "#dc6c5b",
            "mapping_invalid": "#dc6c5b", "unavailable": "#dc6c5b",
        }.get(self._health, "#9ba8b5")
        painter.fillRect(rect.left(), rect.top(), 95, 23, QColor(colour))
        if self._candidate:
            painter.fillRect(rect.right() - 82, rect.top(), 82, 23, QColor("#e0a42c"))
            painter.setPen(QColor("#111820"))
            painter.drawText(rect.adjusted(0, 0, -4, 0), Qt.AlignRight | Qt.AlignVCenter, "候选待复核")
        painter.setPen(Qt.white)
        painter.drawText(rect.adjusted(5, 0, 0, 0), Qt.AlignLeft | Qt.AlignVCenter,
                         f"{self.camera_id}  {self._health}")


class RoiCanvas(QWidget):
    """Small normalized drawing surface for material/station ROIs and exit lines."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(300, 190)
        self.mode = "material"
        self.data: dict[str, list[list[float]]] = {"material_roi": [], "station_roi": [], "exit_line": []}
        self._preview: QPixmap | None = None

    def set_camera(self, camera: dict[str, Any]) -> None:
        self.data = {
            "material_roi": copy.deepcopy(camera.get("material_roi", [])),
            "station_roi": copy.deepcopy(camera.get("station_roi", [])),
            "exit_line": copy.deepcopy(camera.get("exit_line", [])),
        }
        self.update()

    def current_data(self) -> dict[str, list[list[float]]]:
        return copy.deepcopy(self.data)

    def set_preview(self, path: str) -> bool:
        preview = QPixmap(path) if path else QPixmap()
        self._preview = preview if not preview.isNull() else None
        self.update()
        return self._preview is not None

    def clear_current(self) -> None:
        key = "exit_line" if self.mode == "exit" else f"{self.mode}_roi"
        self.data[key] = []
        self.changed.emit()
        self.update()

    def mousePressEvent(self, event: Any) -> None:
        if event.button() != Qt.LeftButton:
            return
        rect = self.rect().adjusted(8, 8, -8, -8)
        if not rect.contains(event.position().toPoint()):
            return
        point = [(event.position().x() - rect.left()) / rect.width(),
                 (event.position().y() - rect.top()) / rect.height()]
        key = "exit_line" if self.mode == "exit" else f"{self.mode}_roi"
        if key == "exit_line" and len(self.data[key]) >= 2:
            self.data[key] = []
        self.data[key].append([round(point[0], 4), round(point[1], 4)])
        self.changed.emit()
        self.update()

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        rect = self.rect().adjusted(8, 8, -8, -8)
        painter.fillRect(rect, QColor("#202a35"))
        if self._preview:
            painter.drawPixmap(rect, self._preview)
        painter.setPen(QPen(QColor("#607080"), 1, Qt.DashLine))
        for fraction in (0.25, 0.5, 0.75):
            painter.drawLine(rect.left() + rect.width() * fraction, rect.top(),
                             rect.left() + rect.width() * fraction, rect.bottom())
            painter.drawLine(rect.left(), rect.top() + rect.height() * fraction,
                             rect.right(), rect.top() + rect.height() * fraction)
        for key, colour in (("material_roi", "#e0a42c"), ("station_roi", "#2db47c"), ("exit_line", "#dc6c5b")):
            points = self.data.get(key, [])
            if not points:
                continue
            qpoints = [QPointF(rect.left() + x * rect.width(), rect.top() + y * rect.height()) for x, y in points]
            painter.setPen(QPen(QColor(colour), 3))
            if len(qpoints) > 1:
                for first, second in zip(qpoints, qpoints[1:]):
                    painter.drawLine(first, second)
                if key != "exit_line" and len(qpoints) > 2:
                    painter.drawLine(qpoints[-1], qpoints[0])
            for point in qpoints:
                painter.setBrush(QColor(colour))
                painter.drawEllipse(point, 4, 4)
        painter.setPen(QColor("#dce5ec"))
        painter.drawText(rect, Qt.AlignCenter, "点击添加点；多边形至少三点。\n红线为出入口线（两点）。")


class FloorplanCanvas(QWidget):
    selected = Signal(str)
    placed = Signal(str, float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(360, 250)
        self._pixmap: QPixmap | None = None
        self._cameras: list[dict[str, Any]] = []
        self.placement_camera: str | None = None

    def set_floorplan(self, image_path: str, cameras: list[dict[str, Any]]) -> None:
        pixmap = QPixmap(image_path) if image_path else QPixmap()
        self._pixmap = pixmap if not pixmap.isNull() else None
        self._cameras = cameras
        self.update()

    def mousePressEvent(self, event: Any) -> None:
        if not self._cameras:
            return
        rect = self.rect().adjusted(8, 8, -8, -8)
        point = event.position()
        if self.placement_camera and rect.contains(point.toPoint()):
            self.placed.emit(
                self.placement_camera,
                max(0.0, min(1.0, (point.x() - rect.left()) / rect.width())),
                max(0.0, min(1.0, (point.y() - rect.top()) / rect.height())),
            )
            return
        closest = min(
            self._cameras,
            key=lambda camera: (rect.left() + camera.get("map_position", [0.5, 0.5])[0] * rect.width() - point.x()) ** 2
            + (rect.top() + camera.get("map_position", [0.5, 0.5])[1] * rect.height() - point.y()) ** 2,
        )
        self.selected.emit(str(closest["id"]))

    def paintEvent(self, event: Any) -> None:
        painter = QPainter(self)
        rect = self.rect().adjusted(8, 8, -8, -8)
        painter.fillRect(rect, QColor("#202a35"))
        if self._pixmap:
            painter.drawPixmap(rect, self._pixmap)
        else:
            painter.setPen(QColor("#c7d0d9"))
            painter.drawText(rect, Qt.AlignCenter, "未导入平面图\n可保存摄像头点位，但尚不代表现场校准。")
        for camera in self._cameras:
            x, y = camera.get("map_position", [0.5, 0.5])
            point = QPointF(rect.left() + x * rect.width(), rect.top() + y * rect.height())
            painter.setPen(QPen(QColor("#8cd5ff"), 2))
            painter.setBrush(QColor("#154e70"))
            painter.drawEllipse(point, 12, 12)
            painter.setPen(Qt.white)
            painter.drawText(point + QPointF(15, 5), str(camera["id"]))
        if self.placement_camera:
            painter.setPen(QColor("#f6d36c"))
            painter.drawText(rect.adjusted(8, 8, -8, -8), Qt.AlignTop | Qt.AlignLeft,
                             f"点击放置 {self.placement_camera} 的点位")


class MainWindow(QMainWindow):
    CROP_COLUMN = 3
    EVENT_REVIEW_COLUMN = 5
    MAX_DIAGNOSTIC_BLOCKS = 300
    MAX_COMPLETED_VISIBLE = 20

    def __init__(
        self, config_path: Path, data_dir: Path, *, runtime_factory: RuntimeFactory | None = None,
        notifier: Callable[[str, str], None] | None = None, beeper: Callable[[], None] | None = None,
        opener: Callable[[Path], bool] | None = None,
    ) -> None:
        super().__init__()
        self.config_path = Path(config_path)
        self.data_dir = Path(data_dir)
        self.runtime_factory = runtime_factory or _runtime_factory
        self.runtime: Any | None = None
        self.config = self._load_configuration()
        self.selected_camera = 0
        self._events: dict[str, dict[str, Any]] = {}
        self._last_health_messages: dict[str, tuple[str, str]] = {}
        self._last_store_reconcile = 0.0
        self._last_worker_summary = ""
        self._roi_full_preview: QPixmap | None = None
        self._notifier = notifier
        self._beeper = beeper or QApplication.beep
        self._opener = opener or (lambda path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))))
        self._tray: QSystemTrayIcon | None = None
        self.setWindowTitle("工厂监控复核台 · 本地演示")
        self.resize(1420, 920)
        self._build_ui()
        self._configure_notifications()
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.poll_runtime)
        self._refresh_mode_label()

    def _load_configuration(self) -> dict[str, Any]:
        default_config, load_config, save_config = _config_api()
        if not self.config_path.exists():
            config = default_config()
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            save_config(config, self.config_path)
            return config
        return load_config(self.config_path)

    def _build_ui(self) -> None:
        central = QWidget(self)
        outer = QVBoxLayout(central)
        header = QHBoxLayout()
        title = QLabel("工厂监控 · 人工复核工作台")
        title.setStyleSheet("font-size: 20px; font-weight: 700")
        header.addWidget(title)
        header.addStretch()
        self.source_banner = QLabel()
        self.source_banner.setObjectName("sourceBanner")
        header.addWidget(self.source_banner)
        self.status_label = QLabel("已停止；尚无现场验证")
        self.status_label.setMaximumWidth(460)
        self.status_label.setWordWrap(True)
        header.addWidget(self.status_label)
        outer.addLayout(header)

        controls = QHBoxLayout()
        controls.addWidget(QLabel("输入源"))
        self.source_selector = QComboBox()
        self.source_selector.addItems(["demo", "video", "live"])
        self.source_selector.currentTextChanged.connect(self._refresh_mode_label)
        controls.addWidget(self.source_selector)
        self.input_path = QLineEdit()
        self.input_path.setPlaceholderText("视频文件路径（仅 video 模式）")
        controls.addWidget(self.input_path, 1)
        self.choose_video_button = QPushButton("选择视频")
        self.choose_video_button.clicked.connect(self.choose_video)
        controls.addWidget(self.choose_video_button)
        self.start_button = QPushButton("开始")
        self.start_button.clicked.connect(self.start_runtime)
        controls.addWidget(self.start_button)
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.stop_runtime)
        controls.addWidget(self.stop_button)
        self.grid_button = QPushButton("返回九宫格/网格")
        self.grid_button.clicked.connect(self.return_grid)
        controls.addWidget(self.grid_button)
        outer.addLayout(controls)

        body = QSplitter(Qt.Horizontal)
        body.addWidget(self._make_monitor_panel())
        body.addWidget(self._make_configuration_panel())
        body.setSizes([800, 620])
        outer.addWidget(body, 1)
        self.setCentralWidget(central)
        self.setStyleSheet("""
            QMainWindow { background: #151b22; color: #e7edf2; }
            QLabel, QCheckBox { color: #e7edf2; }
            QScrollArea { background: #151b22; border: 0; }
            QGroupBox { color: #e7edf2; background: #202a35; border: 1px solid #455565;
                        border-radius: 4px; margin-top: 12px; padding-top: 8px; }
            QGroupBox::title { subcontrol-origin: margin; left: 9px; padding: 0 4px;
                               color: #e7edf2; background: #202a35; }
            QLineEdit, QComboBox, QTableWidget, QPlainTextEdit, QSpinBox { background: #ffffff; color: #111820; }
            QPushButton { padding: 6px 10px; } #sourceBanner { background: #b35d00; padding: 6px 10px; font-weight: 700; }
        """)

    def _make_monitor_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        self.camera_tiles = QGridLayout()
        self.tiles: dict[str, CameraTile] = {}
        for index, camera in enumerate(self.config["cameras"][:10]):
            tile = CameraTile(camera)
            tile.clicked.connect(self.request_view)
            self.tiles[str(camera["id"])] = tile
            self.camera_tiles.addWidget(tile, index // 4, index % 4)
        layout.addLayout(self.camera_tiles, 3)
        layout.addWidget(self._make_events_panel(), 2)
        return panel

    def _make_events_panel(self) -> QWidget:
        group = QGroupBox("候选事件与人工复核（候选不代表指控）")
        layout = QVBoxLayout(group)
        self.event_table = QTableWidget(0, 6)
        self.event_table.setHorizontalHeaderLabels(["时间", "摄像头", "候选类型", "分析状态", "证据", "人工标签"])
        self.event_table.itemSelectionChanged.connect(self.show_selected_event)
        layout.addWidget(self.event_table)
        review = QHBoxLayout()
        self.evidence_label = QLabel("选择事件以查看本地证据路径；证据缺失会明确标出。")
        review.addWidget(self.evidence_label, 1)
        self.open_evidence_button = QPushButton("用系统打开本地证据")
        self.open_evidence_button.setEnabled(False)
        self.open_evidence_button.clicked.connect(self.open_selected_evidence)
        review.addWidget(self.open_evidence_button)
        self.confirm_button = QPushButton("人工确认")
        self.confirm_button.clicked.connect(lambda: self.set_review_label("confirmed"))
        review.addWidget(self.confirm_button)
        self.false_alarm_button = QPushButton("标记误报")
        self.false_alarm_button.clicked.connect(lambda: self.set_review_label("false_alarm"))
        review.addWidget(self.false_alarm_button)
        layout.addLayout(review)
        self.event_details = QPlainTextEdit()
        self.event_details.setReadOnly(True)
        self.event_details.setMaximumHeight(105)
        self.event_details.setPlaceholderText("事件原因、分析结论、证据缺口与时间轴限制会显示在此处。")
        layout.addWidget(self.event_details)
        return group

    def _make_configuration_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        holder = QWidget()
        layout = QVBoxLayout(holder)
        self.capture_config_group = QGroupBox("捕获源与本地布局校准")
        capture = QFormLayout(self.capture_config_group)
        self.window_title = QLineEdit(self.config["source"].get("window_title", ""))
        self.capture_backend = QComboBox()
        self.capture_backend.addItems(["auto", "wgc", "screencapturekit", "display"])
        self.capture_backend.setCurrentText(self.config["source"].get("backend", "auto"))
        self.expected_width = QSpinBox(); self.expected_width.setRange(0, 16384)
        self.expected_height = QSpinBox(); self.expected_height.setRange(0, 16384)
        expected = self.config["source"].get("expected_size", [0, 0])
        self.expected_width.setValue(expected[0]); self.expected_height.setValue(expected[1])
        self.review_endpoint = QLineEdit(self.config["review"].get("endpoint", "http://127.0.0.1:11434"))
        capture.addRow("监控客户端窗口标题", self.window_title)
        capture.addRow("捕获后端（display 表示人工选择显示器）", self.capture_backend)
        capture.addRow("预期宽度", self.expected_width)
        capture.addRow("预期高度", self.expected_height)
        capture.addRow("本地复核服务地址", self.review_endpoint)
        self.calibration_confirmation = QCheckBox("我已逐项确认十个摄像头 ID、布局和目标窗口/显示器；保存此布局为本地校准")
        self.calibration_confirmation.setToolTip("此确认只记录本地布局校准，不代表相机身份、模型准确率或现场验收。")
        capture.addRow(self.calibration_confirmation)
        capture.addRow(QLabel("仅填尺寸或裁剪不会校准。必须有精确窗口标题，或明确选择 display，并主动勾选确认。"))
        template_box = QWidget()
        template_form = QFormLayout(template_box)
        self.template_camera = QComboBox()
        self.template_camera.addItems([str(camera["id"]) for camera in self.config["cameras"]])
        self.template_kind = QComboBox(); self.template_kind.addItems(["grid", "detail"])
        self.template_label = QLineEdit()
        self.template_label.setText(self.template_camera.currentText())
        self.template_label.setPlaceholderText("固定摄像头编号/名称，例如 CAM01")
        self.template_label_roi = QLineEdit()
        self.template_label_roi.setPlaceholderText("固定编号区域 x,y,w,h，例如 0,0,0.2,0.08")
        self.template_camera.currentTextChanged.connect(self._suggest_template_label)
        self.record_template_button = QPushButton("从已加载预览图记录本地模板并保存")
        self.record_template_button.clicked.connect(self.record_identity_template)
        self.template_status = QLabel()
        template_form.addRow("模板摄像头 ID", self.template_camera)
        template_form.addRow("截图类型", self.template_kind)
        template_form.addRow("预期固定编号/名称", self.template_label)
        template_form.addRow("固定编号区域 x,y,w,h", self.template_label_roi)
        template_form.addRow(self.record_template_button)
        template_form.addRow(self.template_status)
        capture.addRow("本地身份/网格模板", template_box)
        layout.addWidget(self.capture_config_group)

        cameras = QGroupBox("摄像头裁剪、关联视角与点位")
        camera_layout = QVBoxLayout(cameras)
        self.camera_table = QTableWidget(len(self.config["cameras"]), 6)
        self.camera_table.setHorizontalHeaderLabels(["ID", "名称", "启用", "裁剪 x,y,w,h", "关联摄像头 ID", "朝向"])
        for row, camera in enumerate(self.config["cameras"]):
            self._set_camera_row(row, camera)
        self.camera_table.itemSelectionChanged.connect(self.select_camera_from_table)
        camera_layout.addWidget(self.camera_table)
        layout.addWidget(cameras)

        roi_group = QGroupBox("规则区域：物料、工位与出入口")
        roi_layout = QVBoxLayout(roi_group)
        self.roi_canvas = RoiCanvas()
        self.roi_canvas.changed.connect(self.apply_roi_to_selected_camera)
        roi_layout.addWidget(self.roi_canvas)
        preview_row = QHBoxLayout()
        self.roi_preview_path = QLineEdit()
        self.roi_preview_path.setPlaceholderText("可选：本地静态预览图，用于可见 ROI 绘制")
        preview_row.addWidget(self.roi_preview_path, 1)
        self.load_roi_preview_button = QPushButton("加载预览图")
        self.load_roi_preview_button.clicked.connect(self.choose_roi_preview)
        preview_row.addWidget(self.load_roi_preview_button)
        roi_layout.addLayout(preview_row)
        mode_row = QHBoxLayout()
        self.roi_mode = QComboBox(); self.roi_mode.addItems(["material", "station", "exit"])
        self.roi_mode.currentTextChanged.connect(self.set_roi_mode)
        mode_row.addWidget(QLabel("绘制")); mode_row.addWidget(self.roi_mode)
        clear_roi = QPushButton("清除当前区域"); clear_roi.clicked.connect(self.roi_canvas.clear_current)
        mode_row.addWidget(clear_roi); mode_row.addStretch()
        roi_layout.addLayout(mode_row)
        self.active_schedule = QLineEdit(); self.break_schedule = QLineEdit()
        self.absence_seconds = QSpinBox(); self.absence_seconds.setRange(1, 36000)
        schedule_form = QFormLayout()
        schedule_form.addRow("工作时间 HH:MM-HH:MM，逗号分隔", self.active_schedule)
        schedule_form.addRow("休息时间 HH:MM-HH:MM，逗号分隔", self.break_schedule)
        schedule_form.addRow("离岗候选阈值（秒）", self.absence_seconds)
        roi_layout.addLayout(schedule_form)
        days_row = QHBoxLayout()
        days_row.addWidget(QLabel("工作日"))
        self.schedule_days: list[QCheckBox] = []
        for name in ("一", "二", "三", "四", "五", "六", "日"):
            box = QCheckBox(name)
            self.schedule_days.append(box)
            days_row.addWidget(box)
        days_row.addStretch()
        roi_layout.addLayout(days_row)
        layout.addWidget(roi_group)

        floor_group = QGroupBox("平面图与相关视角")
        floor_layout = QVBoxLayout(floor_group)
        floor_top = QHBoxLayout()
        self.floorplan_path = QLineEdit(self.config.get("floorplan", {}).get("image_path", ""))
        floor_top.addWidget(self.floorplan_path, 1)
        self.import_floor_button = QPushButton("导入平面图")
        self.import_floor_button.clicked.connect(self.choose_floorplan)
        floor_top.addWidget(self.import_floor_button)
        floor_layout.addLayout(floor_top)
        placement_row = QHBoxLayout()
        self.floor_place_selector = QComboBox()
        self.floor_place_selector.addItems([str(camera["id"]) for camera in self.config["cameras"]])
        placement_row.addWidget(QLabel("放置点位"))
        placement_row.addWidget(self.floor_place_selector)
        self.place_camera_button = QPushButton("点击平面图放置")
        self.place_camera_button.setCheckable(True)
        self.place_camera_button.toggled.connect(self.set_floorplan_placement)
        placement_row.addWidget(self.place_camera_button)
        placement_row.addStretch()
        floor_layout.addLayout(placement_row)
        self.floor_canvas = FloorplanCanvas()
        self.floor_canvas.selected.connect(self.select_camera_by_id)
        self.floor_canvas.placed.connect(self.place_camera_on_floorplan)
        floor_layout.addWidget(self.floor_canvas)
        floor_layout.addWidget(QLabel("点击点位将请求该视角；未校准时会显示拒绝原因。"))
        layout.addWidget(floor_group)

        self.save_button = QPushButton("验证并原子保存配置")
        self.save_button.clicked.connect(self.save_configuration)
        layout.addWidget(self.save_button)
        diagnostics_group = QGroupBox("本地诊断与健康状态")
        diag_layout = QVBoxLayout(diagnostics_group)
        self.diagnostics = QPlainTextEdit(); self.diagnostics.setReadOnly(True)
        diag_layout.addWidget(self.diagnostics)
        layout.addWidget(diagnostics_group)
        layout.addStretch()
        scroll.setWidget(holder)
        self._load_selected_camera()
        self.floor_canvas.set_floorplan(self.floorplan_path.text(), self.config["cameras"])
        self.refresh_template_status()
        return scroll

    @staticmethod
    def _item(value: Any, editable: bool = True) -> QTableWidgetItem:
        item = QTableWidgetItem(str(value))
        if not editable:
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
        return item

    def _set_camera_row(self, row: int, camera: dict[str, Any]) -> None:
        self.camera_table.setItem(row, 0, self._item(camera["id"], False))
        self.camera_table.setItem(row, 1, self._item(camera.get("name", "")))
        self.camera_table.setItem(row, 2, self._item("是" if camera.get("enabled", True) else "否"))
        self.camera_table.setItem(row, self.CROP_COLUMN, self._item(",".join(map(str, camera.get("crop", [])))))
        self.camera_table.setItem(row, 4, self._item(",".join(camera.get("related", []))))
        self.camera_table.setItem(row, 5, self._item(camera.get("heading", 0)))

    def _refresh_mode_label(self) -> None:
        source = self.source_selector.currentText() if hasattr(self, "source_selector") else "demo"
        if source == "demo":
            self.source_banner.setText("SYNTHETIC / DEMO · 非现场、非生产验证")
        elif source == "video":
            self.source_banner.setText("本地视频回放 · 不构成现场验收")
        else:
            self.source_banner.setText("本地 live 捕获 · 未通过现场验证")

    def choose_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择本地回放文件", str(Path.home()), "Video files (*)")
        if path:
            self.input_path.setText(path)

    def choose_floorplan(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "导入平面图", str(Path.home()), "Images (*.png *.jpg *.jpeg *.bmp)")
        if path:
            self.floorplan_path.setText(path)
            self.floor_canvas.set_floorplan(path, self.config["cameras"])

    def choose_roi_preview(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "选择本地校准预览图", str(Path.home()), "Images (*.png *.jpg *.jpeg *.bmp)")
        if path:
            self.load_roi_preview(Path(path))

    def load_roi_preview(self, path: Path) -> bool:
        """Display a local still behind ROI lines without treating it as a camera assertion."""
        resolved = Path(path)
        self.roi_preview_path.setText(str(resolved))
        preview = QPixmap(str(resolved))
        if preview.isNull():
            self.status_label.setText("无法加载预览图；请检查本地文件。")
            return False
        self._roi_full_preview = preview
        self._refresh_roi_preview()
        self.status_label.setText("预览图仅供绘制本地布局，不证明现场相机身份。")
        return True

    def _refresh_roi_preview(self) -> None:
        if self._roi_full_preview is None:
            return
        camera = self.config["cameras"][self.selected_camera]
        crop = camera.get("crop", [0, 0, 1, 1])
        if hasattr(self, "camera_table"):
            item = self.camera_table.item(self.selected_camera, self.CROP_COLUMN)
            if item:
                try:
                    candidate = [float(value.strip()) for value in item.text().split(",")]
                    if len(candidate) == 4:
                        crop = candidate
                except ValueError:
                    pass
        x, y, width, height = crop
        full = self._roi_full_preview
        rectangle = full.rect()
        left, top = round(x * rectangle.width()), round(y * rectangle.height())
        crop_width, crop_height = round(width * rectangle.width()), round(height * rectangle.height())
        cropped = full.copy(left, top, crop_width, crop_height)
        self.roi_canvas._preview = cropped if not cropped.isNull() else None
        self.roi_canvas.update()

    def refresh_template_status(self) -> None:
        verification = self.config.get("switching", {}).get("verification", {})
        enabled_ids = {camera["id"] for camera in self.config["cameras"] if camera.get("enabled")}
        grid = set(verification.get("grid_identities", {}))
        detail = set(verification.get("detail_identities", {}))
        if enabled_ids and enabled_ids.issubset(grid) and enabled_ids.issubset(detail):
            self.template_status.setText("网格与详情模板齐全；仍需运行时本地读回验证，切换尚未自动启用。")
        else:
            self.template_status.setText(
                f"映射未验证：网格模板 {len(grid)}/{len(enabled_ids)}，详情模板 {len(detail)}/{len(enabled_ids)}。"
            )

    def _suggest_template_label(self, camera_id: str) -> None:
        known_ids = {str(camera["id"]) for camera in self.config["cameras"]}
        if not self.template_label.text().strip() or self.template_label.text().strip() in known_ids:
            self.template_label.setText(camera_id)

    @staticmethod
    def _parse_normalized_rectangle(text: str) -> list[float]:
        values = [part.strip() for part in text.split(",")]
        if len(values) != 4:
            raise ValueError("固定编号区域必须为 x,y,w,h 四个数字")
        rectangle = [float(value) for value in values]
        x, y, width, height = rectangle
        if not 0 <= x <= 1 or not 0 <= y <= 1 or not 0 < width <= 1 or not 0 < height <= 1 or x + width > 1 or y + height > 1:
            raise ValueError("固定编号区域必须完全位于截图内")
        return rectangle

    def record_identity_template(self) -> None:
        """Persist an operator-selected local screenshot template; never claim it is verified live."""
        source_path = Path(self.roi_preview_path.text())
        if not source_path.is_file():
            self.status_label.setText("请先加载停止状态下的本地截图，再记录模板。")
            return
        try:
            import cv2
            image = cv2.imread(str(source_path))
            if image is None:
                raise ValueError("截图不能读取")
            config = self._read_editor_config()
            camera_id = self.template_camera.currentText()
            template_kind = self.template_kind.currentText()
            expected_label = self.template_label.text().strip()
            if not expected_label:
                raise ValueError("请填写截图中固定可见的摄像头编号或名称")
            roi = self._parse_normalized_rectangle(self.template_label_roi.text())
            height, width = image.shape[:2]
            x, y, crop_width, crop_height = roi
            template = image[round(y * height) : round((y + crop_height) * height),
                             round(x * width) : round((x + crop_width) * width)]
            if template.size == 0 or template.std() < 4:
                raise ValueError("固定编号区域为空或纹理不足；请选择紧贴摄像头编号/名称的清晰区域")
            templates_dir = self.data_dir / "calibration_templates"
            templates_dir.mkdir(parents=True, exist_ok=True)
            template_path = templates_dir / f"{camera_id}-{template_kind}-label.png"
            if not cv2.imwrite(str(template_path), template):
                raise OSError("无法写入本地模板图片")
            verification = config.setdefault("switching", {}).setdefault("verification", {})
            verification["source_size"] = [width, height]
            verification.pop("grid_signatures", None)
            verification.pop("camera_signatures", None)
            specification = {
                "roi": roi, "template_path": str(template_path), "min_score": 0.92,
                "min_margin": 0.05, "expected_label": expected_label,
            }
            target_key = "grid_identities" if template_kind == "grid" else "detail_identities"
            verification.setdefault(target_key, {})[camera_id] = specification
            _, _, save_config = _config_api()
            save_config(config, self.config_path)
        except (OSError, ValueError, StopIteration, ImportError) as exc:
            self.status_label.setText(f"模板未保存：{exc}")
            self.log(f"模板记录失败：{exc}")
            return
        self.config = config
        self.refresh_template_status()
        self.status_label.setText("本地模板已保存；映射仍未经过运行时读回验证，自动切换保持禁用。")

    def set_floorplan_placement(self, enabled: bool) -> None:
        self.floor_canvas.placement_camera = self.floor_place_selector.currentText() if enabled else None
        self.floor_canvas.update()

    def place_camera_on_floorplan(self, camera_id: str, x: float, y: float) -> None:
        for camera in self.config["cameras"]:
            if camera["id"] == camera_id:
                camera["map_position"] = [round(x, 4), round(y, 4)]
                self.floor_canvas.set_floorplan(self.floorplan_path.text(), self.config["cameras"])
                self.status_label.setText(f"{camera_id} 点位已修改，点击保存后写入配置。")
                return

    def select_camera_from_table(self) -> None:
        row = self.camera_table.currentRow()
        if row >= 0:
            self.selected_camera = row
            self._load_selected_camera()

    def select_camera_by_id(self, camera_id: str) -> None:
        for row, camera in enumerate(self.config["cameras"]):
            if camera["id"] == camera_id:
                self.camera_table.selectRow(row)
                self.selected_camera = row
                self._load_selected_camera()
                self.request_view(camera_id)
                return

    def _load_selected_camera(self) -> None:
        camera = self.config["cameras"][self.selected_camera]
        self.roi_canvas.set_camera(camera)
        self._refresh_roi_preview()
        schedule = camera.get("schedule", {})
        enabled_days = set(schedule.get("days", []))
        for day, box in enumerate(self.schedule_days):
            box.setChecked(day in enabled_days)
        self.active_schedule.setText(",".join("-".join(pair) for pair in schedule.get("active", [])))
        self.break_schedule.setText(",".join("-".join(pair) for pair in schedule.get("breaks", [])))
        self.absence_seconds.setValue(int(camera.get("absence_seconds", 300)))

    def set_roi_mode(self, mode: str) -> None:
        self.roi_canvas.mode = mode

    def apply_roi_to_selected_camera(self) -> None:
        camera = self.config["cameras"][self.selected_camera]
        camera.update(self.roi_canvas.current_data())

    @staticmethod
    def _parse_ranges(text: str) -> list[list[str]]:
        if not text.strip():
            return []
        ranges: list[list[str]] = []
        for part in text.split(","):
            start, separator, end = part.strip().partition("-")
            if not separator or len(start) != 5 or len(end) != 5:
                raise ValueError("时间段需为 HH:MM-HH:MM，多个时间段用逗号分隔")
            ranges.append([start, end])
        return ranges

    def _read_editor_config(self) -> dict[str, Any]:
        config = copy.deepcopy(self.config)
        source = config["source"]
        width, height = self.expected_width.value(), self.expected_height.value()
        source.update({
            "window_title": self.window_title.text().strip(),
            "backend": self.capture_backend.currentText(),
            "expected_size": [width, height],
        })
        config["review"]["endpoint"] = self.review_endpoint.text().strip()
        for row, camera in enumerate(config["cameras"]):
            values = [part.strip() for part in self.camera_table.item(row, self.CROP_COLUMN).text().split(",")]
            if len(values) != 4:
                raise ValueError(f"{camera['id']} 的裁剪必须为 x,y,w,h 四个数字")
            camera["name"] = self.camera_table.item(row, 1).text().strip()
            camera["enabled"] = self.camera_table.item(row, 2).text().strip() in ("是", "true", "True", "1")
            camera["crop"] = [float(value) for value in values]
            camera["related"] = [value.strip() for value in self.camera_table.item(row, 4).text().split(",") if value.strip()]
            camera["heading"] = float(self.camera_table.item(row, 5).text())
        selected = config["cameras"][self.selected_camera]
        selected["schedule"] = {
            "days": [day for day, box in enumerate(self.schedule_days) if box.isChecked()],
            "active": self._parse_ranges(self.active_schedule.text()),
            "breaks": self._parse_ranges(self.break_schedule.text()),
        }
        selected["absence_seconds"] = self.absence_seconds.value()
        config.setdefault("floorplan", {})["image_path"] = self.floorplan_path.text().strip()
        calibration_fields_changed = (
            source["window_title"] != self.config["source"].get("window_title", "")
            or source["backend"] != self.config["source"].get("backend", "auto")
            or source["expected_size"] != self.config["source"].get("expected_size", [0, 0])
            or any(camera["crop"] != previous["crop"] for camera, previous in zip(config["cameras"], self.config["cameras"]))
        )
        if calibration_fields_changed:
            config["switching"]["verification"] = {}
        source["calibrated"] = self.config["source"].get("calibrated", False) and not calibration_fields_changed
        if self.calibration_confirmation.isChecked():
            has_explicit_target = bool(source["window_title"]) or source["backend"] == "display"
            valid_layout = width > 0 and height > 0 and all(len(camera.get("crop", [])) == 4 for camera in config["cameras"])
            if not has_explicit_target:
                raise ValueError("校准需要精确窗口标题，或明确选择 display 捕获后端")
            if not valid_layout:
                raise ValueError("校准需要非零预期尺寸和十个完整裁剪")
            source["calibrated"] = True
        return config

    def save_configuration(self) -> None:
        if self.runtime and self.runtime.status().get("running"):
            self.status_label.setText("运行中不可修改配置；请先停止。")
            return
        try:
            config = self._read_editor_config()
            _, _, save_config = _config_api()
            save_config(config, self.config_path)
        except (ValueError, OSError, TypeError) as exc:
            self.status_label.setText(f"配置未保存：{exc}")
            self.log(f"配置验证失败：{exc}")
            return
        self.config = config
        self.floor_canvas.set_floorplan(self.floorplan_path.text(), self.config["cameras"])
        self.calibration_confirmation.setChecked(False)
        self.refresh_template_status()
        if self.config["source"].get("calibrated"):
            self.status_label.setText("配置已验证并原子保存；本地布局已确认，仍未完成现场验收。")
        else:
            self.status_label.setText("配置已验证并原子保存；未完成校准确认，live 仍会阻止。")
        self.log("配置保存成功（原子写入由配置模块执行）。")

    def start_runtime(self) -> None:
        source = self.source_selector.currentText()
        if source == "live" and not self.config["source"].get("calibrated", False):
            self.status_label.setText("live 未启动：请先停止后填写预期尺寸和十个裁剪并保存校准。")
            self.log("已阻止未校准的 live 捕获。")
            return
        if source == "video" and not self.input_path.text().strip():
            self.status_label.setText("video 未启动：请选择本地视频文件。")
            return
        try:
            self.runtime = self.runtime_factory(copy.deepcopy(self.config), self.data_dir)
            self.runtime.start(source=source, input_path=self.input_path.text().strip() or None)
        except Exception as exc:  # Native capture/model failures must be visible to an operator.
            self.runtime = None
            self.status_label.setText(f"未启动：{exc}")
            self.log(f"运行时启动失败：{exc}")
            return
        self._set_running(True)
        self.status_label.setText(f"{source} 已启动；等待本地工作进程健康状态。")
        self.poll_timer.start(250)

    def stop_runtime(self) -> None:
        stop_error: Exception | None = None
        runtime_status: dict[str, Any] = {}
        if self.runtime:
            try:
                self.runtime.stop()
            except Exception as exc:
                stop_error = exc
                self.log(f"停止时错误：{exc}")
            try:
                runtime_status = self.runtime.status()
            except Exception as exc:
                stop_error = stop_error or exc
                self.log(f"停止状态读取失败：{exc}")
        still_running = bool(runtime_status.get("running", stop_error is not None))
        if still_running:
            self.poll_timer.start(250)
        else:
            self.poll_timer.stop()
        self._set_running(still_running)
        self._stop_safe_to_close = stop_error is None and not still_running
        report = runtime_status.get("last_stop") or {}
        if stop_error is not None:
            self.status_label.setText(f"停止未确认：{stop_error}；请查看诊断并重试。")
            return
        if report and not report.get("confirmed", False):
            alive = ", ".join(report.get("alive_workers") or []) or "无"
            affected = ", ".join(report.get("affected_event_ids") or []) or "无"
            reasons = "; ".join(report.get("reasons") or ["停止协议未确认"])
            self.status_label.setText(f"停止收尾不完整：存活进程={alive}；受影响事件={affected}。")
            self.log(f"停止协议未确认：{reasons}")
            return
        self.status_label.setText("已停止；现在可修改并保存配置。")

    def _set_running(self, running: bool) -> None:
        self.start_button.setEnabled(not running)
        self.stop_button.setEnabled(running)
        self.source_selector.setEnabled(not running)
        self.input_path.setEnabled(not running)
        self.choose_video_button.setEnabled(not running)
        self.capture_config_group.setEnabled(not running)
        self.camera_table.setEnabled(not running)
        self.roi_canvas.setEnabled(not running)
        self.roi_mode.setEnabled(not running)
        self.roi_preview_path.setEnabled(not running)
        self.load_roi_preview_button.setEnabled(not running)
        self.active_schedule.setEnabled(not running)
        self.break_schedule.setEnabled(not running)
        self.absence_seconds.setEnabled(not running)
        for box in self.schedule_days:
            box.setEnabled(not running)
        self.floorplan_path.setEnabled(not running)
        self.import_floor_button.setEnabled(not running)
        self.floor_place_selector.setEnabled(not running)
        self.place_camera_button.setEnabled(not running)
        self.save_button.setEnabled(not running)

    def poll_runtime(self) -> None:
        if not self.runtime:
            return
        try:
            messages = self.runtime.poll()
            for message in messages:
                self.handle_runtime_message(message)
            status = self.runtime.status()
            self._render_status(status)
            if time.monotonic() - self._last_store_reconcile >= 2:
                self.reconcile_event_store()
                self._last_store_reconcile = time.monotonic()
        except Exception as exc:
            self.log(f"轮询运行时错误：{exc}")
            self.status_label.setText(f"运行时错误：{exc}")

    def _render_status(self, status: dict[str, Any]) -> None:
        workers = status.get("workers", {})
        worker_text = ", ".join(
            f"{name}:{value.get('state', '?') if isinstance(value, dict) else value}"
            for name, value in workers.items()
        )
        if worker_text != self._last_worker_summary:
            self._last_worker_summary = worker_text
            self.log("工作进程详情：" + json.dumps(workers, ensure_ascii=False, default=str))
        verified = "未现场验证" if not status.get("field_verified", False) else "字段标记异常：需审查"
        self.status_label.setText(f"{verified}；{worker_text or '等待工作进程'}")

    def handle_runtime_message(self, message: dict[str, Any]) -> None:
        kind = message.get("type")
        if kind == "frame":
            tile = self.tiles.get(str(message.get("camera_id", "")))
            if tile:
                tile.set_frame(message.get("image"))
        elif kind == "health":
            camera_id = str(message.get("camera_id", ""))
            tile = self.tiles.get(camera_id)
            health = str(message.get("health", "unknown"))
            if tile:
                tile.set_health(health, str(message.get("reason", "")))
            state = (health, str(message.get("reason", "")))
            if self._last_health_messages.get(camera_id) != state:
                self._last_health_messages[camera_id] = state
                self.log(f"{camera_id or 'runtime'} 健康状态：{health} {state[1]}")
        elif kind in ("candidate", "event_updated"):
            event = message.get("event") or message
            if event.get("id"):
                self.upsert_event(event, alert=kind == "candidate")
        elif kind == "analysis":
            event_id = str(message.get("event_id", ""))
            if event_id in self._events:
                event = {**self._events[event_id], **{
                    key: value for key, value in message.items()
                    if key not in {"type", "event_id"}
                }}
                self.upsert_event(event, alert=message.get("analysis_status") in {"uncertain", "timeout", "error"})
            else:
                self.log(f"分析更新缺少候选事件 {event_id}；保留运行时错误/状态供检查。")
        elif kind == "error":
            error = str(message.get("message") or message.get("error") or "本地运行时未知错误")
            self.log(f"错误：{error}")
            self.status_label.setText(f"运行时错误：{error}")
        elif kind in ("stats", "status"):
            self.log(json.dumps(message, ensure_ascii=False, default=str))

    def upsert_event(self, event: dict[str, Any], *, alert: bool = False) -> None:
        event_id = str(event["id"])
        self._events[event_id] = {**self._events.get(event_id, {}), **event}
        row = next((index for index in range(self.event_table.rowCount())
                    if self.event_table.item(index, 0) and self.event_table.item(index, 0).data(Qt.UserRole) == event_id), None)
        if row is None:
            row = self.event_table.rowCount()
            self.event_table.insertRow(row)
        timestamp = event.get("triggered_at", time.time())
        text_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(timestamp)))
        values = [text_time, event.get("camera_id", "?"), event.get("kind", "candidate"),
                  event.get("analysis_status", "pending"), event.get("evidence_path", "证据待生成"),
                  event.get("review_label", "pending")]
        for column, value in enumerate(values):
            item = self._item(value, False)
            if column == 0:
                item.setData(Qt.UserRole, event_id)
            self.event_table.setItem(row, column, item)
        tile = self.tiles.get(str(event.get("camera_id", "")))
        if tile:
            tile.mark_candidate(any(
                item.get("camera_id") == event.get("camera_id") and item.get("review_label", "pending") == "pending"
                for item in self._events.values()
            ))
        if alert:
            if event.get("analysis_status") in {"uncertain", "timeout", "error"}:
                self.notify_operator("需要人工复核", f"{event.get('camera_id', '?')} 的候选分析未确定：{event.get('analysis_status')}")
            else:
                self.notify_operator("出现候选事件", f"{event.get('camera_id', '?')}：{event.get('kind', 'candidate')}，请人工复核")
        self._trim_visible_events()
        if self.selected_event_id() == event_id:
            self.show_selected_event()

    @staticmethod
    def _is_finalized(event: dict[str, Any]) -> bool:
        return event.get("completed_at") is not None and event.get("analysis_status") != "pending"

    def _trim_visible_events(self) -> None:
        complete = sorted((event for event in self._events.values() if self._is_finalized(event)),
                          key=lambda event: (float(event.get("completed_at") or 0), str(event.get("id"))), reverse=True)
        keep = {str(event["id"]) for event in complete[:self.MAX_COMPLETED_VISIBLE]}
        keep.update(str(event_id) for event_id, event in self._events.items() if not self._is_finalized(event))
        for event_id in list(self._events):
            if event_id not in keep:
                del self._events[event_id]
        for row in range(self.event_table.rowCount() - 1, -1, -1):
            item = self.event_table.item(row, 0)
            if not item or str(item.data(Qt.UserRole)) not in keep:
                self.event_table.removeRow(row)

    def reconcile_event_store(self) -> None:
        """Read back durable state without deleting or changing runtime-owned events."""
        try:
            from factory_monitor.store import EventStore

            store = EventStore(self.data_dir / "events.sqlite3")
            try:
                events = store.list_events(limit=100)
            finally:
                store.close()
        except (OSError, ValueError) as exc:
            self.log(f"事件库只读同步失败：{exc}")
            return
        for event in events:
            self.upsert_event(event)
        self._trim_visible_events()

    def selected_event_id(self) -> str | None:
        rows = self.event_table.selectionModel().selectedRows() if self.event_table.selectionModel() else []
        if not rows:
            return None
        item = self.event_table.item(rows[0].row(), 0)
        return str(item.data(Qt.UserRole)) if item else None

    def show_selected_event(self) -> None:
        event_id = self.selected_event_id()
        if not event_id:
            return
        event = self._events[event_id]
        evidence = event.get("evidence_path") or event.get("preview_path")
        path = Path(str(evidence)) if evidence else None
        exists = path is not None and path.exists()
        if exists:
            self.evidence_label.setText(f"本地证据：{path}（仅请求系统打开；未声明本机 codec 可播放。）")
        else:
            self.evidence_label.setText("本地证据缺失/尚未生成；不能播放或打开。")
        self.open_evidence_button.setEnabled(bool(exists))
        analysis = event.get("analysis")
        if isinstance(analysis, dict):
            analysis_reason = analysis.get("reason") or analysis.get("error") or json.dumps(analysis, ensure_ascii=False)
        else:
            analysis_reason = str(analysis or "尚无本地分析结论")
        gaps = event.get("gaps") or []
        if isinstance(gaps, list) and gaps:
            gap_text = "; ".join(self._format_gap(item) for item in gaps)
        else:
            gap_text = "未报告时间缺口"
        self.event_details.setPlainText(
            f"规则原因：{event.get('reason', '未知')}\n"
            f"分析状态：{event.get('analysis_status', 'pending')}；{analysis_reason}\n"
            f"录制状态：{event.get('recording_status', event.get('status', 'unknown'))}\n"
            f"时间窗完整：{('是' if event['window_complete'] else '否') if event.get('window_complete') is not None else '待确认'}\n"
            f"时间轴缺口：{gap_text}\n"
            "时间为本地记录时间；缺口表示缺少帧，不以此界面声明视频已被系统 codec 成功播放。"
        )

    @staticmethod
    def _format_gap(gap: Any) -> str:
        if isinstance(gap, dict):
            start, end = gap.get("start"), gap.get("end")
            reason = str(gap.get("reason", "未说明"))
            if isinstance(start, (int, float)):
                span = f"{start:.3f}–{end:.3f}" if isinstance(end, (int, float)) else f"{start:.3f}–未知"
                return f"{span}（{reason}）"
            return f"时间未知（{reason}）"
        if isinstance(gap, list) and gap and isinstance(gap[0], (int, float)):
            return f"{gap[0]:.3f}–{gap[1]:.3f}" if len(gap) > 1 and isinstance(gap[1], (int, float)) else f"{gap[0]:.3f}–未知"
        return str(gap)

    def open_selected_evidence(self) -> None:
        event_id = self.selected_event_id()
        if not event_id:
            return
        event = self._events[event_id]
        evidence = event.get("evidence_path") or event.get("preview_path")
        path = Path(str(evidence)) if evidence else None
        if path is None or not path.exists():
            self.evidence_label.setText("本地证据缺失；没有可请求系统打开的文件。")
            self.open_evidence_button.setEnabled(False)
            return
        if self._opener(path):
            self.status_label.setText("已请求系统打开本地证据；此操作不验证视频 codec 播放。")
        else:
            self.status_label.setText("系统未能打开本地证据；请检查关联程序和文件权限。")

    def set_review_label(self, label: str) -> None:
        event_id = self.selected_event_id()
        if not event_id:
            self.status_label.setText("请先选择一个候选事件。")
            return
        event = self._events[event_id]
        try:
            from factory_monitor.store import EventStore

            store = EventStore(self.data_dir / "events.sqlite3")
            try:
                store.update_event(event_id, review_label=label)
            finally:
                store.close()
        except Exception as exc:
            self.log(f"人工标签未写入事件库：{exc}")
            self.status_label.setText("人工标签未保存；请检查本地事件库。")
            return
        event["review_label"] = label
        self.upsert_event(event)
        self.status_label.setText("人工复核标签已写入本地事件库。")
        self.notify_operator("人工复核已记录", f"{event.get('camera_id', '?')} 已标为 {label}")

    def request_view(self, camera_id: str) -> None:
        if not self.runtime:
            self.status_label.setText(f"{camera_id} 未切换：运行时未启动。")
            return
        try:
            result = self.runtime.request_view(camera_id)
        except Exception as exc:
            self.status_label.setText(f"{camera_id} 视角请求失败：{exc}")
            return
        self.status_label.setText(f"{camera_id}：{result.get('reason', '请求已提交')}")

    def return_grid(self) -> None:
        if not self.runtime:
            self.status_label.setText("未返回网格：运行时未启动。")
            return
        try:
            result = self.runtime.return_grid()
            self.status_label.setText(result.get("reason", "网格请求已提交"))
        except Exception as exc:
            self.status_label.setText(f"返回网格失败：{exc}")

    def log(self, text: str) -> None:
        self.diagnostics.appendPlainText(time.strftime("%H:%M:%S ") + text)

    def _configure_notifications(self) -> None:
        self.diagnostics.document().setMaximumBlockCount(self.MAX_DIAGNOSTIC_BLOCKS)
        if self._notifier is None and QSystemTrayIcon.isSystemTrayAvailable():
            tray = QSystemTrayIcon(self.style().standardIcon(QStyle.SP_ComputerIcon), self)
            tray.show()
            self._tray = tray

    def notify_operator(self, title: str, body: str) -> None:
        self.statusBar().showMessage(f"{title}：{body}", 8000)
        if self._notifier is not None:
            self._notifier(title, body)
        elif self._tray is not None:
            self._tray.showMessage(title, body, QSystemTrayIcon.Warning, 8000)
        self._beeper()

    def closeEvent(self, event: Any) -> None:
        self.stop_runtime()
        if not getattr(self, "_stop_safe_to_close", False):
            event.ignore()
            self.log("关窗已阻止：停止尚未安全确认，请保留窗口并重试。")
            return
        super().closeEvent(event)


def run_gui(config_path: Path, data_dir: Path, source: str = "demo", input_path: str | None = None) -> int:
    """Launch the local desktop app; no network or capture side effect occurs before Start."""
    app = QApplication.instance() or QApplication(sys.argv)
    window = MainWindow(Path(config_path), Path(data_dir))
    window.source_selector.setCurrentText(source)
    if input_path:
        window.input_path.setText(input_path)
    window.show()
    return app.exec()
