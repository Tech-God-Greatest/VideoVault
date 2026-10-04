"""
Custom UI widgets and Dark Mode styling for Video Vault 4K.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import numpy as np
from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDragEnterEvent, QDropEvent, QFont, QImage, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QWidget,
)

DARK_THEME_QSS = """
QMainWindow, QDialog {
    background-color: #181825;
    color: #cdd6f4;
}

QWidget {
    font-family: 'Segoe UI', system-ui, -apple-system, sans-serif;
    font-size: 13px;
    color: #cdd6f4;
}

QTabWidget::pane {
    border: 1px solid #313244;
    background-color: #1e1e2e;
    border-radius: 8px;
    margin-top: -1px;
}

QTabBar::tab {
    background-color: #181825;
    color: #a6adc8;
    padding: 10px 24px;
    border: 1px solid #313244;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    margin-right: 4px;
    font-weight: 600;
}

QTabBar::tab:selected {
    background-color: #1e1e2e;
    color: #89b4fa;
    border-bottom: 2px solid #89b4fa;
}

QTabBar::tab:hover:!selected {
    background-color: #242438;
    color: #cdd6f4;
}

QGroupBox {
    border: 1px solid #313244;
    border-radius: 8px;
    margin-top: 14px;
    padding: 14px 10px 10px 10px;
    font-weight: bold;
    color: #89b4fa;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    padding: 0 4px;
}

QPushButton {
    background-color: #313244;
    color: #cdd6f4;
    border: 1px solid #45475a;
    border-radius: 6px;
    padding: 8px 16px;
    font-weight: 600;
}

QPushButton:hover {
    background-color: #45475a;
    border-color: #585b70;
}

QPushButton:pressed {
    background-color: #585b70;
}

QPushButton:disabled {
    background-color: #232334;
    color: #6c7086;
    border-color: #313244;
}

QPushButton#PrimaryButton {
    background-color: #89b4fa;
    color: #11111b;
    border: 1px solid #89b4fa;
    font-size: 14px;
    font-weight: bold;
    padding: 10px 20px;
}

QPushButton#PrimaryButton:hover {
    background-color: #b4befe;
    border-color: #b4befe;
}

QPushButton#PrimaryButton:disabled {
    background-color: #45475a;
    color: #7f849c;
    border-color: #45475a;
}

QPushButton#DangerButton {
    background-color: #f38ba8;
    color: #11111b;
    border: 1px solid #f38ba8;
    font-weight: bold;
}

QPushButton#DangerButton:hover {
    background-color: #eba0ac;
}

QLineEdit, QComboBox {
    background-color: #181825;
    color: #cdd6f4;
    border: 1px solid #313244;
    border-radius: 6px;
    padding: 6px 10px;
    selection-background-color: #89b4fa;
    selection-color: #11111b;
}

QLineEdit:focus, QComboBox:focus {
    border: 1px solid #89b4fa;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 24px;
    border-left: 1px solid #313244;
}

QListWidget, QTableWidget {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 6px;
    color: #cdd6f4;
    padding: 4px;
}

QListWidget::item:hover, QTableWidget::item:hover {
    background-color: #252538;
}

QListWidget::item:selected, QTableWidget::item:selected {
    background-color: #313244;
    color: #89b4fa;
}

QProgressBar {
    background-color: #181825;
    border: 1px solid #313244;
    border-radius: 6px;
    text-align: center;
    color: #ffffff;
    font-weight: bold;
    height: 20px;
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #89b4fa, stop:1 #a6e3a1);
    border-radius: 5px;
}

QStatusBar {
    background-color: #11111b;
    color: #a6adc8;
    border-top: 1px solid #313244;
}
"""


class DragDropListWidget(QListWidget):
    """
    ListWidget accepting drag and drop of files and folders directly from OS explorer.
    """
    files_dropped = pyqtSignal(list)

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        paths = [Path(url.toLocalFile()) for url in urls if url.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()


class StaticPreviewWidget(QFrame):
    """
    Renders live visual noise thumbnail of the current frame being encoded or decoded.
    """

    def __init__(self, width: int = 160, height: int = 90, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._thumb_width = width
        self._thumb_height = height
        self.setFixedSize(width, height)
        self.setStyleSheet(
            "background-color: #11111b; border: 1px solid #45475a; border-radius: 6px;"
        )
        self._pixmap: Optional[QPixmap] = None
        self._frame_seq = 0

    def update_frame(self, thumb_rgb: np.ndarray, frame_seq: int):
        """
        Updates the preview image from an RGB numpy array (H, W, 3).
        """
        self._frame_seq = frame_seq
        h, w, ch = thumb_rgb.shape
        bytes_per_line = ch * w
        # QImage takes RGB888
        qimg = QImage(thumb_rgb.data, w, h, bytes_per_line, QImage.Format.Format_RGB888)
        self._pixmap = QPixmap.fromImage(qimg).scaled(
            self._thumb_width,
            self._thumb_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        painter = QPainter(self)
        if self._pixmap:
            x = (self.width() - self._pixmap.width()) // 2
            y = (self.height() - self._pixmap.height()) // 2
            painter.drawPixmap(x, y, self._pixmap)
        else:
            # Draw placeholder static hint
            painter.setPen(QColor("#585b70"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "4K Live Stream")

        # Overlay frame index
        if self._frame_seq > 0:
            painter.setPen(QColor(0, 0, 0, 180))
            painter.fillRect(4, self.height() - 20, 70, 16, QColor(0, 0, 0, 180))
            painter.setPen(QColor("#a6e3a1"))
            painter.setFont(QFont("Consolas", 8, QFont.Weight.Bold))
            painter.drawText(6, self.height() - 8, f"F#{self._frame_seq}")


class MetricCard(QFrame):
    """
    Clean styled dashboard metric card (Title, Main Value, Subtitle).
    """

    def __init__(self, title: str, default_val: str = "--", sub_text: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setStyleSheet(
            "background-color: #181825; border: 1px solid #313244; border-radius: 6px; padding: 6px;"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        self.title_lbl = QLabel(title.upper())
        self.title_lbl.setStyleSheet("color: #a6adc8; font-size: 10px; font-weight: bold; border: none;")

        self.val_lbl = QLabel(default_val)
        self.val_lbl.setStyleSheet("color: #89b4fa; font-size: 18px; font-weight: bold; border: none;")

        self.sub_lbl = QLabel(sub_text)
        self.sub_lbl.setStyleSheet("color: #6c7086; font-size: 10px; border: none;")

        layout.addWidget(self.title_lbl)
        layout.addWidget(self.val_lbl)
        layout.addWidget(self.sub_lbl)

    def set_value(self, value: str, sub_text: Optional[str] = None):
        self.val_lbl.setText(value)
        if sub_text is not None:
            self.sub_lbl.setText(sub_text)
