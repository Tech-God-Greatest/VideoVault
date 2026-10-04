"""
Main desktop GUI window and entry point for Video Vault 4K.
"""

from __future__ import annotations

import sys
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from video_vault.gui.decode_tab import DecodeTab
from video_vault.gui.encode_tab import EncodeTab
from video_vault.gui.verify_tab import VerifyTab
from video_vault.gui.widgets import DARK_THEME_QSS
from video_vault.utils.ffmpeg_finder import verify_ffmpeg


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Video Vault 4K - Universal Lossless Video Data Vault")
        self.resize(880, 720)
        self.setMinimumSize(780, 620)

        # Central widget and tabs
        self.tab_widget = QTabWidget()
        self.encode_tab = EncodeTab()
        self.decode_tab = DecodeTab()
        self.verify_tab = VerifyTab()

        self.tab_widget.addTab(self.encode_tab, "💾  Encode to 4K Video")
        self.tab_widget.addTab(self.decode_tab, "📦  Decode & Reconstruct")
        self.tab_widget.addTab(self.verify_tab, "🔍  Verify Integrity")

        self.setCentralWidget(self.tab_widget)

        # Status bar
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self._init_status()

    def _init_status(self):
        try:
            info = verify_ffmpeg()
            self.status_bar.showMessage(f"✓ Video Engine: {info['version']} ({info['path']})")
        except Exception as e:
            self.status_bar.showMessage(f"⚠ Warning: {e}")


def run_gui() -> int:
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)

    app.setStyleSheet(DARK_THEME_QSS)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run_gui())
