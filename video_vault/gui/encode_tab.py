"""
Encode Tab for Video Vault 4K Desktop GUI.
Provides file/folder selection, drag-and-drop, codec configuration,
asynchronous worker thread, live throughput metrics, and noise thumbnail rendering.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import numpy as np
from PyQt6.QtCore import QObject, QThread, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from video_vault.core.config import (
    CODEC_FFV1,
    CODEC_H264,
    CODEC_HUFFYUV,
    CODEC_PNG,
    CODEC_RAW,
    DEFAULT_CONFIG,
    PIX_FMT_BGR24,
    PIX_FMT_BGRA,
    PIX_FMT_RGB24,
    PIX_FMT_RGBA,
    PIX_FMT_YUV420P,
    StreamConfig,
)
from video_vault.core.encoder import EncodeProgress, EncodeResult, VaultEncoder
from video_vault.gui.widgets import DragDropListWidget, MetricCard, StaticPreviewWidget


def _fmt_size(n: int) -> str:
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"


class EncodeWorker(QThread):
    progress_updated = pyqtSignal(EncodeProgress)
    finished_success = pyqtSignal(EncodeResult)
    finished_error = pyqtSignal(str)

    def __init__(
        self,
        inputs: List[Path],
        output_path: Path,
        config: StreamConfig,
        password: Optional[str] = None,
    ):
        super().__init__()
        self.inputs = inputs
        self.output_path = output_path
        self.config = config
        self.password = password
        self.encoder: Optional[VaultEncoder] = None

    def run(self):
        try:
            self.encoder = VaultEncoder(
                inputs=self.inputs,
                output_path=self.output_path,
                config=self.config,
                password=self.password,
                progress_callback=self._on_progress,
            )
            result = self.encoder.encode()
            self.finished_success.emit(result)
        except Exception as e:
            self.finished_error.emit(str(e))

    def _on_progress(self, p: EncodeProgress):
        self.progress_updated.emit(p)

    def pause(self):
        if self.encoder:
            self.encoder.pause()

    def resume(self):
        if self.encoder:
            self.encoder.resume()

    def cancel(self):
        if self.encoder:
            self.encoder.cancel()


class EncodeTab(QWidget):
    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.worker: Optional[EncodeWorker] = None
        self.input_paths: List[Path] = []
        self._init_ui()

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(12)
        main_layout.setContentsMargins(16, 16, 16, 16)

        # 1. Inputs Section
        input_group = QGroupBox("1. Input Files & Directories")
        input_layout = QVBoxLayout(input_group)

        btn_row = QHBoxLayout()
        self.btn_add_files = QPushButton("＋ Add Files")
        self.btn_add_files.clicked.connect(self._add_files)
        self.btn_add_folder = QPushButton("📁 Add Folder")
        self.btn_add_folder.clicked.connect(self._add_folder)
        self.btn_clear = QPushButton("🗑 Clear All")
        self.btn_clear.clicked.connect(self._clear_inputs)

        btn_row.addWidget(self.btn_add_files)
        btn_row.addWidget(self.btn_add_folder)
        btn_row.addStretch()
        btn_row.addWidget(self.btn_clear)
        input_layout.addLayout(btn_row)

        self.list_widget = DragDropListWidget()
        self.list_widget.setMinimumHeight(110)
        self.list_widget.files_dropped.connect(self._handle_files_dropped)
        input_layout.addWidget(self.list_widget)

        self.lbl_input_summary = QLabel("No inputs selected (drag and drop files/folders here)")
        self.lbl_input_summary.setStyleSheet("color: #a6adc8; font-style: italic;")
        input_layout.addWidget(self.lbl_input_summary)

        main_layout.addWidget(input_group)

        # 2. Output & Settings Section
        settings_group = QGroupBox("2. Vault & Video Settings")
        settings_layout = QVBoxLayout(settings_group)

        out_row = QHBoxLayout()
        out_row.addWidget(QLabel("Output Video:"))
        self.txt_output_path = QLineEdit()
        self.txt_output_path.setPlaceholderText("Select output destination (e.g. vault4k_output.mkv)...")
        self.btn_browse_output = QPushButton("Browse...")
        self.btn_browse_output.clicked.connect(self._browse_output)
        out_row.addWidget(self.txt_output_path)
        out_row.addWidget(self.btn_browse_output)
        settings_layout.addLayout(out_row)

        opts_row = QHBoxLayout()

        # Codec
        opts_row.addWidget(QLabel("Lossless Codec:"))
        self.cmb_codec = QComboBox()
        self.cmb_codec.addItem("Microsoft MP4 Universal (.mp4)", CODEC_H264)
        self.cmb_codec.addItem("Microsoft AVI Lossless (.avi)", "ms_avi")
        self.cmb_codec.addItem("FFV1 Archival Lossless (MKV)", CODEC_FFV1)
        self.cmb_codec.addItem("Raw Lossless Video (MKV)", CODEC_RAW)
        self.cmb_codec.addItem("Lossless PNG Stream (MKV)", CODEC_PNG)
        self.cmb_codec.addItem("HuffYUV Lossless (AVI)", CODEC_HUFFYUV)
        self.cmb_codec.currentIndexChanged.connect(self._on_codec_changed)
        opts_row.addWidget(self.cmb_codec)

        # Pixel format
        opts_row.addWidget(QLabel("Pixel Space:"))
        self.cmb_pix_fmt = QComboBox()
        self.cmb_pix_fmt.addItem("YUV420p Luma (8.3 MB/frame - MS Native)", PIX_FMT_YUV420P)
        opts_row.addWidget(self.cmb_pix_fmt)

        # Password
        opts_row.addWidget(QLabel("Password (AES-256):"))
        self.txt_password = QLineEdit()
        self.txt_password.setEchoMode(QLineEdit.EchoMode.Password)
        self.txt_password.setPlaceholderText("Optional encryption password")
        opts_row.addWidget(self.txt_password)

        settings_layout.addLayout(opts_row)
        main_layout.addWidget(settings_group)

        # 3. Progress & Dashboard Section
        dash_group = QGroupBox("3. Encoding Dashboard")
        dash_layout = QVBoxLayout(dash_group)

        # Metric cards & live preview row
        top_dash = QHBoxLayout()

        self.card_speed = MetricCard("Throughput", "-- MB/s", "0.0 fps")
        self.card_frames = MetricCard("Frames", "0 / 0", "Est. 0.0s")
        self.card_data = MetricCard("Data Encoded", "0 B", "0 B total")
        self.card_eta = MetricCard("Time Remaining", "--:--", "Elapsed: 0s")

        top_dash.addWidget(self.card_speed)
        top_dash.addWidget(self.card_frames)
        top_dash.addWidget(self.card_data)
        top_dash.addWidget(self.card_eta)

        # Live visual noise preview
        self.preview_widget = StaticPreviewWidget(width=160, height=90)
        top_dash.addWidget(self.preview_widget)

        dash_layout.addLayout(top_dash)

        # Progress bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        dash_layout.addWidget(self.progress_bar)

        self.lbl_status = QLabel("Ready")
        self.lbl_status.setStyleSheet("color: #a6adc8;")
        dash_layout.addWidget(self.lbl_status)

        # Buttons
        ctrl_row = QHBoxLayout()
        self.btn_start = QPushButton("▶ START ENCODING")
        self.btn_start.setObjectName("PrimaryButton")
        self.btn_start.clicked.connect(self._start_encode)

        self.btn_pause = QPushButton("⏸ Pause")
        self.btn_pause.setEnabled(False)
        self.btn_pause.clicked.connect(self._toggle_pause)

        self.btn_cancel = QPushButton("⏹ Cancel")
        self.btn_cancel.setObjectName("DangerButton")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_encode)

        ctrl_row.addWidget(self.btn_start)
        ctrl_row.addWidget(self.btn_pause)
        ctrl_row.addWidget(self.btn_cancel)
        dash_layout.addLayout(ctrl_row)

        main_layout.addWidget(dash_group)
        main_layout.addStretch()

    def _add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Select Files to Encode")
        if files:
            self._handle_files_dropped([Path(f) for f in files])

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder to Encode")
        if folder:
            self._handle_files_dropped([Path(folder)])

    def _clear_inputs(self):
        self.input_paths.clear()
        self.list_widget.clear()
        self._update_input_summary()

    def _handle_files_dropped(self, paths: List[Path]):
        for p in paths:
            if p not in self.input_paths and p.exists():
                self.input_paths.append(p)
                kind = "📁 " if p.is_dir() else "📄 "
                self.list_widget.addItem(f"{kind}{p.name}  ({p})")

        self._update_input_summary()

        # Suggest default output path if empty
        if not self.txt_output_path.text() and self.input_paths:
            first = self.input_paths[0]
            codec = self.cmb_codec.currentData()
            ext = ".mp4" if codec == CODEC_H264 else (".avi" if codec in ("ms_avi", CODEC_HUFFYUV) else ".mkv")
            default_out = first.parent / f"{first.stem}_vault4k{ext}"
            self.txt_output_path.setText(str(default_out))

    def _on_codec_changed(self):
        codec = self.cmb_codec.currentData()
        cur_path = self.txt_output_path.text().strip()
        self.cmb_pix_fmt.clear()

        if codec == CODEC_H264:
            self.cmb_pix_fmt.addItem("YUV420p Luma (8.3 MB/frame - MS Native)", PIX_FMT_YUV420P)
            if cur_path:
                self.txt_output_path.setText(str(Path(cur_path).with_suffix(".mp4")))
        elif codec == "ms_avi":
            self.cmb_pix_fmt.addItem("BGR24 (3 bytes/px, 24.9 MB/frame - MS DIB)", PIX_FMT_BGR24)
            self.cmb_pix_fmt.addItem("BGRA (4 bytes/px, 33.2 MB/frame - MS DIB)", PIX_FMT_BGRA)
            if cur_path:
                self.txt_output_path.setText(str(Path(cur_path).with_suffix(".avi")))
        else:
            self.cmb_pix_fmt.addItem("RGBA (4 bytes/px, 33.2 MB/frame)", PIX_FMT_RGBA)
            self.cmb_pix_fmt.addItem("RGB24 (3 bytes/px, 24.9 MB/frame)", PIX_FMT_RGB24)
            if cur_path:
                ext = ".avi" if codec == CODEC_HUFFYUV else ".mkv"
                self.txt_output_path.setText(str(Path(cur_path).with_suffix(ext)))

    def _update_input_summary(self):
        if not self.input_paths:
            self.lbl_input_summary.setText("No inputs selected (drag and drop files/folders here)")
            return

        file_count = sum(1 for p in self.input_paths if p.is_file())
        dir_count = sum(1 for p in self.input_paths if p.is_dir())
        self.lbl_input_summary.setText(f"Selected: {file_count} file(s), {dir_count} folder(s)")

    def _browse_output(self):
        codec = self.cmb_codec.currentData()
        def_ext = ".mp4" if codec == CODEC_H264 else (".avi" if codec in ("ms_avi", CODEC_HUFFYUV) else ".mkv")
        cur = self.txt_output_path.text() or str(Path.home() / f"vault4k_output{def_ext}")
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Select Output Video Destination",
            cur,
            "MP4 Video (*.mp4);;Matroska Video (*.mkv);;AVI Video (*.avi);;All Files (*.*)",
        )
        if path:
            self.txt_output_path.setText(path)

    def _start_encode(self):
        if not self.input_paths:
            QMessageBox.warning(self, "No Inputs", "Please add at least one file or folder to encode.")
            return

        out_path_str = self.txt_output_path.text().strip()
        if not out_path_str:
            QMessageBox.warning(self, "No Output", "Please specify an output video file destination.")
            return

        out_path = Path(out_path_str)
        raw_codec = self.cmb_codec.currentData()
        codec = CODEC_RAW if raw_codec == "ms_avi" else raw_codec
        pix_fmt = self.cmb_pix_fmt.currentData()

        config = StreamConfig(
            codec=codec,
            pixel_format=pix_fmt,
        )

        password = self.txt_password.text().strip() or None

        self.btn_start.setEnabled(False)
        self.btn_pause.setEnabled(True)
        self.btn_pause.setText("⏸ Pause")
        self.btn_cancel.setEnabled(True)
        self.btn_add_files.setEnabled(False)
        self.btn_add_folder.setEnabled(False)
        self.btn_clear.setEnabled(False)

        self.progress_bar.setValue(0)
        self.lbl_status.setText("Scanning inputs and initializing 4K lossless video engine...")

        self.worker = EncodeWorker(
            inputs=self.input_paths,
            output_path=out_path,
            config=config,
            password=password,
        )
        self.worker.progress_updated.connect(self._on_progress)
        self.worker.finished_success.connect(self._on_success)
        self.worker.finished_error.connect(self._on_error)
        self.worker.start()

    def _toggle_pause(self):
        if not self.worker:
            return
        if self.btn_pause.text() == "⏸ Pause":
            self.worker.pause()
            self.btn_pause.setText("▶ Resume")
            self.lbl_status.setText("Encoding paused.")
        else:
            self.worker.resume()
            self.btn_pause.setText("⏸ Pause")
            self.lbl_status.setText("Resuming encoding...")

    def _cancel_encode(self):
        if self.worker:
            self.lbl_status.setText("Cancelling encoding...")
            self.worker.cancel()

    def _on_progress(self, p: EncodeProgress):
        pct = int((p.current_frame / max(1, p.total_frames_estimate)) * 100)
        self.progress_bar.setValue(min(100, pct))

        self.card_speed.set_value(f"{p.mb_per_sec:.1f} MB/s", f"{p.fps:.1f} fps")
        self.card_frames.set_value(f"{p.current_frame} / {p.total_frames_estimate}", f"{pct}%")
        self.card_data.set_value(_fmt_size(p.bytes_processed), f"of {_fmt_size(p.total_bytes)}")
        self.card_eta.set_value(f"ETA: {p.estimated_remaining_seconds:.1f}s", f"Elapsed: {p.elapsed_seconds:.1f}s")

        self.lbl_status.setText(f"Packaging: {p.current_file}")

        if p.preview_thumbnail is not None:
            self.preview_widget.update_frame(p.preview_thumbnail, p.current_frame)

    def _on_success(self, res: EncodeResult):
        self._reset_controls()
        self.progress_bar.setValue(100)
        self.lbl_status.setText(f"✓ Completed! {res.details}")
        QMessageBox.information(
            self,
            "Encoding Successful",
            f"Successfully encoded vault into 4K video!\n\n"
            f"Video Output: {res.output_path}\n"
            f"Total Frames: {res.total_frames}\n"
            f"Video Size: {_fmt_size(res.video_size_bytes)}\n"
            f"Average Speed: {res.average_speed_mb_s:.1f} MB/s\n"
            f"Global Stream SHA-256:\n{res.global_sha256}",
        )

    def _on_error(self, err_msg: str):
        self._reset_controls()
        self.lbl_status.setText(f"✗ Failed: {err_msg}")
        QMessageBox.critical(self, "Encoding Failed", f"An error occurred while encoding:\n\n{err_msg}")

    def _reset_controls(self):
        self.btn_start.setEnabled(True)
        self.btn_pause.setEnabled(False)
        self.btn_cancel.setEnabled(False)
        self.btn_add_files.setEnabled(True)
        self.btn_add_folder.setEnabled(True)
        self.btn_clear.setEnabled(True)
