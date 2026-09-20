"""A harmless selectable window for native-capture smoke tests.

It contains generated blocks only.  It never represents people, detections,
camera footage, model output, or production monitoring evidence.
"""

from __future__ import annotations

import sys
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QGridLayout, QLabel, QMainWindow, QVBoxLayout, QWidget


class SyntheticPane(QWidget):
    def __init__(self, camera_id: str) -> None:
        super().__init__()
        self.camera_id = camera_id
        self.phase = 0
        self.setMinimumSize(240, 140)

    def tick(self) -> None:
        self.phase = (self.phase + 7) % 180
        self.update()

    def paintEvent(self, event: object) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#27313d"))
        width = max(1, self.width() - 64)
        painter.fillRect(24 + self.phase % width, self.height() // 2 - 14, 40, 28, QColor("#46b7e8"))
        painter.setPen(Qt.white)
        painter.drawText(self.rect().adjusted(8, 8, -8, -8), Qt.AlignTop | Qt.AlignLeft,
                         f"{self.camera_id} · SYNTHETIC BLOCK")
        painter.setPen(QColor("#ffd36a"))
        painter.drawText(self.rect(), Qt.AlignCenter, "SYNTHETIC — NOT CAMERA FOOTAGE")


class DemoWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Factory Monitor Synthetic Source")
        self.resize(1000, 720)
        holder = QWidget()
        layout = QVBoxLayout(holder)
        banner = QLabel("SYNTHETIC SOURCE · LOCAL CAPTURE SMOKE TEST ONLY · NO PEOPLE / NO MODEL FACTS")
        banner.setAlignment(Qt.AlignCenter)
        banner.setStyleSheet("font-weight: bold; background: #b35d00; padding: 8px; color: white")
        layout.addWidget(banner)
        self.timestamp = QLabel()
        self.timestamp.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.timestamp)
        grid = QGridLayout()
        self.panes = [SyntheticPane(f"CAM{index:02d}") for index in range(1, 11)]
        for index, pane in enumerate(self.panes):
            grid.addWidget(pane, index // 2, index % 2)
        layout.addLayout(grid)
        self.setCentralWidget(holder)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        self.tick()

    def tick(self) -> None:
        self.timestamp.setText(f"generated timestamp: {time.strftime('%Y-%m-%d %H:%M:%S')}")
        for pane in self.panes:
            pane.tick()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DemoWindow()
    window.show()
    raise SystemExit(app.exec())
