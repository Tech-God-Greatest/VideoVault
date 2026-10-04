"""
Verify Tab for Video Vault 4K Desktop GUI.
Tests stream integrity, frame checksums, and crypto authentication without writing anything to disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from video_vault.core.config import DEFAULT_CONFIG
from video_vault.core.decoder import DecodeProgress, DecodeResult, VaultDecoder
from video_vault.gui.widgets import MetricCard


def _fmt_size(n: int) -> str:
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


class VerifyWorker(QThread):
    progress_updated = pyqtSignal(DecodeProgress)
    finished_result = pyqtSignal(DecodeResult)
    finished_error = pyqtSignal(str)

    def __init__(self, video_path: Path, password: Optional[str] = None):
        super().__init__()
        self.video_path = video_path
        self.password = password
        self.decoder: Optional[VaultDecoder] = None

    def run(self):
        try:
            self.decoder = VaultDecoder(
                video_path=self.video_path,
                dest_dir=None,
                password=self.password,
                config=DEFAULT_CONFIG,
                progress_callback=self._on_progress,
            )
            result = self.decoder.decode(verify_only=True)
            self.finished_result.emit(result)
        except Exception as e:
            self.finished_error.emit(str(e))

    def _on_progress(self, p: DecodeProgress):
        self.progress_updated.emit(p)

    def cancel(self):
        if self.decoder:
            self.decoder.cancel()


class VerifyTab(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.worker: Optional[VerifyWorker] = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(16, 16, 16, 16)

        grp_sel = QGroupBox("1. Select Video Vault Container to Verify")
        lay_sel = QVBoxLayout(grp_sel)

        row = QHBoxLayout()
        row.addWidget(QLabel("Video File:"))
        self.txt_video = QLineEdit()
        self.txt_video.setPlaceholderText("Select video container...")
        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.clicked.connect(self._browse)
        row.addWidget(self.txt_video)
        row.addWidget(self.btn_browse)
        lay_sel.addLayout(row)

        row_pwd = QHBoxLayout()
        row_pwd.addWidget(QLabel("Password (if encrypted):"))
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.setPlaceholderText("Optional decryption password")
        row_pwd.addWidget(self.txt_password)
        lay_sel.addLayout(row_pwd)

        layout.addWidget(grp_sel)

        grp_dash = QGroupBox("2. Integrity Verification Dashboard")
        lay_dash = QVBoxLayout(grp_dash)

        stat_row = QHBoxLayout()
        self.card_speed = MetricCard("Verification Speed", "-- MB/s", "0.0 fps")
        self.card_frames = MetricCard("Frames Scanned", "0 / 0", "Checksums: OK")
        self.card_status = MetricCard("Stream Status", "READY", "No errors")

        stat_row.addWidget(self.card_speed)
        stat_row.addWidget(self.card_frames)
        stat_row.addWidget(self.card_status)
        lay_dash.addLayout(stat_row)

        self.progress_bar = QProgressBar()
        lay_dash.addWidget(self.progress_bar)

        ctrl_row = QHBoxLayout()
        self.btn_start = QPushButton("🔍 VERIFY INTEGRITY")
        self.btn_start.setObjectName("PrimaryButton")
        self.btn_start.clicked.connect(self._start_verify)

        self.btn_cancel = QPushButton("⏹ Cancel")
        self.btn_cancel.setObjectName("DangerButton")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_verify)

        ctrl_row.addWidget(self.btn_start)
        ctrl_row.addWidget(self.btn_cancel)
        lay_dash.addLayout(ctrl_row)

        layout.addWidget(grp_dash)

        grp_log = QGroupBox("3. Integrity & Diagnostic Log")
        lay_log = QVBoxLayout(grp_log)
        self.txt_log = QTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setStyleSheet("background-color: #11111b; font-family: Consolas, monospace; font-size: 11px;")
        lay_log.addWidget(self.txt_log)
        layout.addWidget(grp_log)

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Video Vault Container",
            "",
            "Video Vault Files (*.mkv *.avi);;All Files (*.*)",
        )
        if path:
            self.txt_video.setText(path)

    def _start_verify(self):
        p = Path(self.txt_video.text().strip())
        if not p.is_file():
            QMessageBox.warning(self, "Invalid File", "Please select a valid video file.")
            return

        self.txt_log.clear()
        self.txt_log.append(f"Starting integrity verification for: {p}")
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setValue(0)
        self.card_status.set_value("VERIFYING", "Scanning frames...")

        self.worker = VerifyWorker(video_path=p, password=self.txt_password.text().strip() or None)
        self.worker.progress_updated.connect(self._on_progress)
        self.worker.finished_result.connect(self._on_result)
        self.worker.finished_error.connect(self._on_error)
        self.worker.start()

    def _cancel_verify(self):
        if self.worker:
            self.txt_log.append("Cancelling verification...")
            self.worker.cancel()

    def _on_progress(self, p: DecodeProgress):
        pct = int((p.current_frame / max(1, p.total_frames)) * 100)
        self.progress_bar.setValue(min(100, pct))
        self.card_speed.set_value(f"{p.mb_per_sec:.1f} MB/s", f"{p.fps:.1f} fps")
        self.card_frames.set_value(f"{p.current_frame} / {p.total_frames}", "CRC32 verified")

    def _on_result(self, res: DecodeResult):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress_bar.setValue(100)

        if res.success:
            self.card_status.set_value("100% OK", "Bit-for-bit intact")
            self.txt_log.append("\n✓ VERIFICATION SUCCESSFUL")
            self.txt_log.append(f"Frames verified: {res.total_frames_read}")
            self.txt_log.append(f"Total payload size: {_fmt_size(res.total_bytes_decoded)}")
            self.txt_log.append(f"Throughput: {res.average_speed_mb_s:.1f} MB/s")
            self.txt_log.append("All frame CRC32, SHA-256, and header envelopes verified intact.")
            QMessageBox.information(self, "Integrity Verified", "✓ Video container verified! 100% bit-for-bit lossless integrity intact.")
        else:
            self.card_status.set_value("CORRUPTED", f"{len(res.corruption_errors)} errors")
            self.txt_log.append(f"\n✗ CORRUPTIONS DETECTED ({len(res.corruption_errors)} errors):")
            for c in res.corruption_errors:
                self.txt_log.append(f"  - Frame {c.frame_seq} (time {c.timestamp_seconds:.2f}s): {c.error_type} - {c.details}")
            QMessageBox.warning(self, "Integrity Failure", f"Video container has errors! Detected {len(res.corruption_errors)} corruption events.")

    def _on_error(self, err_msg: str):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.card_status.set_value("ERROR", "Check logs")
        self.txt_log.append(f"\n✗ Error: {err_msg}")
        QMessageBox.critical(self, "Verification Error", err_msg)
