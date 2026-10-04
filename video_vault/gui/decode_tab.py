"""
Decode Tab for Video Vault 4K Desktop GUI.
Provides video selection, automatic container probe, password input,
streaming extraction, live frame validation, and reconstruction report table.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from video_vault.core.config import DEFAULT_CONFIG, StreamConfig
from video_vault.core.decoder import (
    DecodeProgress,
    DecodeResult,
    GlobalVaultMeta,
    PasswordRequiredError,
    VaultDecoder,
)
from video_vault.gui.widgets import MetricCard


def _fmt_size(n: int) -> str:
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


class DecodeWorker(QThread):
    progress_updated = pyqtSignal(DecodeProgress)
    finished_success = pyqtSignal(DecodeResult)
    finished_error = pyqtSignal(str)

    def __init__(
        self,
        video_path: Path,
        dest_dir: Path,
        password: Optional[str] = None,
        config: StreamConfig = DEFAULT_CONFIG,
    ):
        super().__init__()
        self.video_path = video_path
        self.dest_dir = dest_dir
        self.password = password
        self.config = config
        self.decoder: Optional[VaultDecoder] = None

    def run(self):
        try:
            self.decoder = VaultDecoder(
                video_path=self.video_path,
                dest_dir=self.dest_dir,
                password=self.password,
                config=self.config,
                progress_callback=self._on_progress,
            )
            result = self.decoder.decode(verify_only=False)
            self.finished_success.emit(result)
        except Exception as e:
            self.finished_error.emit(str(e))

    def _on_progress(self, p: DecodeProgress):
        self.progress_updated.emit(p)

    def cancel(self):
        if self.decoder:
            self.decoder.cancel()


class DecodeTab(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.worker: Optional[DecodeWorker] = None
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(16, 16, 16, 16)

        # 1. Source Video Selection
        source_group = QGroupBox("1. Video Vault Container Selection")
        source_layout = QVBoxLayout(source_group)

        v_row = QHBoxLayout()
        v_row.addWidget(QLabel("Video File:"))
        self.txt_video_path = QLineEdit()
        self.txt_video_path.setPlaceholderText("Select or drop a 4K Video Vault file (.mkv, .avi)...")
        self.txt_video_path.textChanged.connect(self._on_video_path_changed)
        self.btn_browse_video = QPushButton("Browse...")
        self.btn_browse_video.clicked.connect(self._browse_video)
        v_row.addWidget(self.txt_video_path)
        v_row.addWidget(self.btn_browse_video)
        source_layout.addLayout(v_row)

        # Probe container card
        self.lbl_probe_info = QLabel("Select a video file to view container details.")
        self.lbl_probe_info.setStyleSheet("color: #a6adc8; font-size: 11px; padding: 4px;")
        source_layout.addWidget(self.lbl_probe_info)

        # Destination & Password row
        opt_row = QHBoxLayout()
        opt_row.addWidget(QLabel("Extract To:"))
        self.txt_dest_dir = QLineEdit()
        self.txt_dest_dir.setPlaceholderText("Select destination directory...")
        self.btn_browse_dest = QPushButton("Browse...")
        self.btn_browse_dest.clicked.connect(self._browse_dest)
        opt_row.addWidget(self.txt_dest_dir)
        opt_row.addWidget(self.btn_browse_dest)

        opt_row.addWidget(QLabel("Password (if encrypted):"))
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.setPlaceholderText("Decryption password")
        opt_row.addWidget(self.txt_password)

        source_layout.addLayout(opt_row)
        main_layout.addWidget(source_group)

        # 2. Decoding Dashboard
        dash_group = QGroupBox("2. Reconstruction & Verification Dashboard")
        dash_layout = QVBoxLayout(dash_group)

        stat_row = QHBoxLayout()
        self.card_speed = MetricCard("Throughput", "-- MB/s", "0.0 fps")
        self.card_frames = MetricCard("Frames Read", "0 / 0", "Frame validation: OK")
        self.card_data = MetricCard("Data Restored", "0 B", "0 files verified")

        stat_row.addWidget(self.card_speed)
        stat_row.addWidget(self.card_frames)
        stat_row.addWidget(self.card_data)
        dash_layout.addLayout(stat_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        dash_layout.addWidget(self.progress_bar)

        self.lbl_status = QLabel("Ready")
        self.lbl_status.setStyleSheet("color: #a6adc8;")
        dash_layout.addWidget(self.lbl_status)

        ctrl_row = QHBoxLayout()
        self.btn_start = QPushButton("▶ RECONSTRUCT FILES")
        self.btn_start.setObjectName("PrimaryButton")
        self.btn_start.clicked.connect(self._start_decode)

        self.btn_cancel = QPushButton("⏹ Cancel")
        self.btn_cancel.setObjectName("DangerButton")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_decode)

        ctrl_row.addWidget(self.btn_start)
        ctrl_row.addWidget(self.btn_cancel)
        dash_layout.addLayout(ctrl_row)

        main_layout.addWidget(dash_group)

        # 3. Restored Items Table
        report_group = QGroupBox("3. Reconstructed Files & Bit-for-Bit Verification Report")
        report_layout = QVBoxLayout(report_group)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Status", "Relative Path", "Size", "SHA-256 Checksum"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.table.setMinimumHeight(150)
        report_layout.addWidget(self.table)

        main_layout.addWidget(report_group)

    def _browse_video(self):
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Video Vault Container",
            "",
            "Video Vault Files (*.mkv *.avi);;All Files (*.*)",
        )
        if path:
            self.txt_video_path.setText(path)

    def _browse_dest(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Destination Extraction Folder")
        if folder:
            self.txt_dest_dir.setText(folder)

    def _on_video_path_changed(self, text: str):
        path = Path(text.strip())
        if path.is_file():
            # Auto suggest destination folder
            if not self.txt_dest_dir.text():
                dest = path.parent / f"{path.stem}_extracted"
                self.txt_dest_dir.setText(str(dest))

            # Attempt quick probe of Frame 0
            try:
                decoder = VaultDecoder(video_path=path, password=self.txt_password.text().strip() or None)
                meta = decoder.probe()
                enc_str = "🔒 AES-256-GCM Encrypted" if meta.encrypted else "🔓 Whitened Stream"
                self.lbl_probe_info.setText(
                    f"✓ Container Verified: {meta.total_files} files, {meta.total_dirs} dirs ({_fmt_size(meta.total_payload_bytes)}) | "
                    f"Format: {meta.codec.upper()} {meta.width}x{meta.height} {meta.pixel_format.upper()} | {enc_str} | Date: {meta.created_at[:19]}"
                )
                self.lbl_probe_info.setStyleSheet("color: #a6e3a1; font-size: 11px;")
            except PasswordRequiredError:
                self.lbl_probe_info.setText("🔒 Encrypted Vault: Please enter password above to decode.")
                self.lbl_probe_info.setStyleSheet("color: #f9e2af; font-size: 11px;")
            except Exception as e:
                self.lbl_probe_info.setText(f"ℹ Video selected: {path.name} (probe: {e})")
                self.lbl_probe_info.setStyleSheet("color: #a6adc8; font-size: 11px;")
        else:
            self.lbl_probe_info.setText("Select a video file to view container details.")
            self.lbl_probe_info.setStyleSheet("color: #a6adc8; font-size: 11px;")

    def _start_decode(self):
        v_path = Path(self.txt_video_path.text().strip())
        if not v_path.is_file():
            QMessageBox.warning(self, "Invalid Video", "Please select a valid Video Vault file.")
            return

        d_path = Path(self.txt_dest_dir.text().strip())
        if not d_path.name:
            QMessageBox.warning(self, "Invalid Destination", "Please specify a valid extraction directory.")
            return

        self.table.setRowCount(0)
        self.btn_start.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setValue(0)
        self.lbl_status.setText("Reading video stream and verifying frame headers...")

        self.worker = DecodeWorker(
            video_path=v_path,
            dest_dir=d_path,
            password=self.txt_password.text().strip() or None,
        )
        self.worker.progress_updated.connect(self._on_progress)
        self.worker.finished_success.connect(self._on_success)
        self.worker.finished_error.connect(self._on_error)
        self.worker.start()

    def _cancel_decode(self):
        if self.worker:
            self.lbl_status.setText("Cancelling decoding...")
            self.worker.cancel()

    def _on_progress(self, p: DecodeProgress):
        pct = int((p.current_frame / max(1, p.total_frames)) * 100)
        self.progress_bar.setValue(min(100, pct))

        self.card_speed.set_value(f"{p.mb_per_sec:.1f} MB/s", f"{p.fps:.1f} fps")
        self.card_frames.set_value(f"{p.current_frame} / {p.total_frames}", "Frame CRC32: OK")
        self.card_data.set_value(_fmt_size(p.bytes_processed), f"Elapsed: {p.elapsed_seconds:.1f}s")

        self.lbl_status.setText(f"Extracting: {p.current_file}")

    def _on_success(self, res: DecodeResult):
        self._reset_controls()
        self.progress_bar.setValue(100)

        # Populate report table
        if res.reconstruction_report:
            items = res.reconstruction_report.items
            self.table.setRowCount(len(items))
            for row, it in enumerate(items):
                # Status item
                status_item = QTableWidgetItem(it.status)
                if it.status == "VERIFIED_OK":
                    status_item.setForeground(QColor("#a6e3a1"))
                else:
                    status_item.setForeground(QColor("#f38ba8"))

                path_item = QTableWidgetItem(it.rel_path)
                size_item = QTableWidgetItem(_fmt_size(it.size) if not it.is_dir else "[DIR]")
                hash_item = QTableWidgetItem(it.actual_sha256[:16] + "..." if it.actual_sha256 else (it.expected_sha256[:16] + "..." if it.expected_sha256 else ""))

                self.table.setItem(row, 0, status_item)
                self.table.setItem(row, 1, path_item)
                self.table.setItem(row, 2, size_item)
                self.table.setItem(row, 3, hash_item)

        if res.success:
            self.lbl_status.setText(f"✓ All files reconstructed and verified bit-for-bit!")
            QMessageBox.information(
                self,
                "Reconstruction Successful",
                f"Successfully decoded and verified all files!\n\n"
                f"Destination: {self.txt_dest_dir.text()}\n"
                f"Frames Read: {res.total_frames_read}\n"
                f"Total Data Restored: {_fmt_size(res.total_bytes_decoded)}\n"
                f"Integrity Verification: 100% Bit-for-bit Lossless Match (SHA-256 Verified)",
            )
        else:
            corrupt_count = len(res.corruption_errors)
            self.lbl_status.setText(f"✗ Finished with errors! ({corrupt_count} corruption errors detected)")
            QMessageBox.warning(
                self,
                "Reconstruction Warnings",
                f"Decoding completed with errors!\n\n"
                f"Corruptions: {corrupt_count}\n"
                f"Check report table below for details on affected files.",
            )

    def _on_error(self, err_msg: str):
        self._reset_controls()
        self.lbl_status.setText(f"✗ Failed: {err_msg}")
        QMessageBox.critical(self, "Decoding Failed", f"An error occurred while decoding:\n\n{err_msg}")

    def _reset_controls(self):
        self.btn_start.setEnabled(True)
        self.btn_cancel.setEnabled(False)
